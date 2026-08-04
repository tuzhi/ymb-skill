#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Production entrypoint: audit, execute, validate, and package failures."""
import argparse
import csv
import glob
import hashlib
import json
import os
import re
import shutil
import sys
import time
import traceback
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
for path in (SCRIPT_DIR, SKILL_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

from runtime import deliverable as P
from runtime import integrate as I
from runtime import portfolio_balance as PB
from runtime import qc as Q
from runtime import run_result as R
from runtime import standardize as S
from runtime import tag as T
from runtime import validators as V
from runtime.contracts import YAML_ROUTE_FIELDS, IntegrationContext, StageResult, yaml_route_summary
from harness.protocols import render_protocol
from ymb_standardization_core.readers.routing.rule_loader import build_routing_rules_snapshot


DONE = "DONE"
ERROR = "ERROR"
BLOCKED = "BLOCKED"
PENDING = "PENDING"
LOCAL_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
TOKEN_VAULT_SECRET_FILENAMES = {"token_vault_manifest.json"}
MANIFEST_TEMPLATE_RELATIVE_PATH = os.path.join("assets", "manifest.template.json")
RUN_ID_PATTERN = re.compile(r"^\d{8}T\d{6}[+-]\d{4}-[0-9a-f]{8}$")
PLAN_KEY_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def configure_console():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def now():
    return datetime.now(LOCAL_TZ).isoformat()


def claim_planned_run(run_root, run_id):
    """原子认领预分配 Run；重复执行只能等待同一个 Run。"""
    if not RUN_ID_PATTERN.fullmatch(str(run_id or "")):
        raise ValueError("预分配 run_id 无效")
    root = resolve_run_root(run_root)
    os.makedirs(root, exist_ok=True)
    run_dir = os.path.join(root, run_id)
    try:
        os.mkdir(run_dir)
    except FileExistsError:
        return run_dir, False
    return run_dir, True


def wait_for_run_result(run_dir, timeout_seconds, poll_seconds=0.25):
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    result_path = os.path.join(run_dir, "run_result.json")
    while time.monotonic() <= deadline:
        result = read_json_if_exists(result_path, {})
        if result:
            return result
        time.sleep(poll_seconds)
    return {}


def release_execution_plan(run_root, plan_key, run_id):
    if not plan_key:
        return
    if not PLAN_KEY_PATTERN.fullmatch(str(plan_key)):
        raise ValueError("execution plan key 无效")
    plan_path = os.path.join(resolve_run_root(run_root), ".harness-plans", f"{plan_key}.json")
    plan = read_json_if_exists(plan_path, {})
    if plan and plan.get("run_id") == run_id:
        os.unlink(plan_path)


def safe_name(value):
    return "".join(c if c not in '\\/:*?"<>|' else "_" for c in value)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def valid_yaml_route(route):
    if not isinstance(route, dict) or set(route) != set(YAML_ROUTE_FIELDS):
        return False
    status = route.get("yaml_match_status")
    if status not in {"matched", "unmatched", "ambiguous", "failed"}:
        return False
    return status != "matched" or bool(str(route.get("fingerprint_id") or "").strip())


def failure_route_summary(route_info):
    """只保留路由诊断所需字段，避免把原文件正文写入 evidence。"""
    route = dict(route_info or {})
    candidates = []
    for item in route.get("candidate_fingerprints") or route.get("candidates") or []:
        candidate = item if isinstance(item, dict) else {"fingerprint_id": item}
        candidate_id = str(candidate.get("fingerprint_id") or candidate.get("id") or "")
        if candidate_id and candidate_id not in candidates:
            candidates.append(candidate_id)
    def compact_values(value, limit=30):
        if isinstance(value, dict):
            return {
                str(key)[:120]: str(item)[:240]
                for key, item in list(value.items())[:limit]
            }
        if not isinstance(value, (list, tuple)):
            return []
        return [str(item)[:240] for item in list(value)[:limit]]

    def compact_structure(value, depth=0):
        if depth >= 4:
            return str(value)[:240]
        if isinstance(value, dict):
            return {
                str(key)[:120]: compact_structure(item, depth + 1)
                for key, item in list(value.items())[:30]
            }
        if isinstance(value, (list, tuple)):
            return [compact_structure(item, depth + 1) for item in list(value)[:30]]
        if isinstance(value, (bool, int, float)) or value is None:
            return value
        return str(value)[:240]

    return {
        "fingerprint_id": str(route.get("fingerprint_id") or route.get("id") or ""),
        "series_family": str(route.get("series_family") or ""),
        "router_bank": str(route.get("bank") or "未识别"),
        "yaml_match_status": str(route.get("decision") or "unmatched"),
        "reader_id": str(route.get("reader_id") or ""),
        "candidate_fingerprints": candidates,
        "identity_evidence": compact_values(route.get("identity_evidence")),
        "columns_evidence": compact_values(route.get("columns_evidence")),
        "metadata_evidence": compact_values(route.get("metadata_evidence")),
        "missing_required_columns": compact_values(route.get("missing_required_columns")),
        "routing_evidence": compact_structure(route.get("routing_evidence") or {}),
    }


def has_open_password_hint(path):
    try:
        from ymb_standardization_core.file_hints import load_file_hints_for_path

        hints = load_file_hints_for_path(path).for_file(path)
        return bool(hints.get("open_password"))
    except Exception:
        return False


def reusable_stage_1_route(route, source_name):
    """原始文件只复用唯一命中 YAML 的结果；声明式标准化 CSV 是明确例外。"""
    if not valid_yaml_route(route):
        return False
    if str(source_name or "").lower().endswith("__standardized.csv"):
        return route.get("yaml_match_status") in {"matched", "unmatched"}
    return route.get("yaml_match_status") == "matched"


def recognized_type(report):
    """返回供任务详情展示的保守文件识别类型。"""
    image = report.get("文件画像") if isinstance(report, dict) else {}
    image = image if isinstance(image, dict) else {}
    bank = str(image.get("router_bank") or "未识别").strip()
    account_type = str(image.get("账户类型") or "").strip()
    return " · ".join(value for value in (bank, account_type) if value)


def read_json_if_exists(path, default=None):
    if not os.path.isfile(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def manifest_template_path(skill_dir):
    return os.path.join(skill_dir, MANIFEST_TEMPLATE_RELATIVE_PATH)


def resolve_run_root(explicit_run_root, cwd=None):
    if explicit_run_root:
        return os.path.abspath(explicit_run_root)
    return os.path.abspath(os.path.join(cwd or os.getcwd(), "runs"))


def normalize_relpath(path):
    return path.replace(os.sep, "/")


def is_token_vault_secret_artifact(path):
    name = os.path.basename(str(path)).lower()
    if name in TOKEN_VAULT_SECRET_FILENAMES:
        return True
    return name.endswith("_token_vault.json") and not name.endswith("_token_vault_ref.json")


def load_parent_run_context(run_root, parent_run_id):
    if not parent_run_id:
        return None
    parent_dir = os.path.abspath(os.path.join(run_root, parent_run_id))
    if not os.path.isdir(parent_dir):
        raise RuntimeError(f"parent run 不存在：{parent_dir}")

    stage_manifest = read_json_if_exists(os.path.join(parent_dir, "manifest.json"), {})
    # 兼容历史 run；新 run 的运行上下文已合并到 manifest.json。
    legacy_run_manifest = read_json_if_exists(os.path.join(parent_dir, "run_manifest.json"), {})
    inherited_fallbacks = []
    stage_statuses = []
    parent_error = ""
    for stage_id, spec in stage_manifest.items():
        if not str(stage_id).startswith("stage_"):
            continue
        if not isinstance(spec, dict):
            continue
        status = spec.get("status", "")
        stage_statuses.append(status)
        if not (spec.get("ai_fallback_used") or spec.get("ai_fallback_dir") or spec.get("ai_fallback_artifacts")):
            continue
        fallback_dir = os.path.join(parent_dir, "fallback", stage_id)
        fallback_request = read_json_if_exists(os.path.join(fallback_dir, "fallback_request.json"), {})
        if status == ERROR and not parent_error:
            evidence = {}
            evidence_ref = str(fallback_request.get("evidence_ref") or "")
            if evidence_ref:
                evidence_path = os.path.abspath(os.path.join(parent_dir, evidence_ref))
                if os.path.commonpath([parent_dir, evidence_path]) == parent_dir:
                    evidence = read_json_if_exists(evidence_path, {})
            parent_error = str(
                evidence.get("error_summary")
                or fallback_request.get("error")
                or fallback_request.get("message")
                or ""
            )
        inherited_fallbacks.append({
            "stage": stage_id,
            "name": spec.get("name", ""),
            "parent_status": status,
            "parent_fallback_dir": fallback_dir,
            "parent_fallback_artifacts": spec.get("ai_fallback_artifacts", []),
        })

    if any(status == ERROR for status in stage_statuses):
        parent_status = "error"
    elif stage_statuses and all(status == DONE for status in stage_statuses):
        parent_status = "success"
    else:
        parent_status = "running"

    return {
        "parent_run_id": parent_run_id,
        "parent_run_dir": parent_dir,
        "parent_client": stage_manifest.get("client") or legacy_run_manifest.get("client", ""),
        "parent_status": legacy_run_manifest.get("status") or parent_status,
        "parent_error": legacy_run_manifest.get("error") or parent_error,
        "password_attempt": int(stage_manifest.get("password_attempt") or 0),
        "ai_repair_attempt": int(stage_manifest.get("ai_repair_attempt") or 0),
        "inherited_fallbacks": inherited_fallbacks,
    }


def inventory(folder):
    rows = []
    if not folder or not os.path.isdir(folder):
        return rows
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__"}]
        for name in sorted(files):
            path = os.path.join(root, name)
            if is_token_vault_secret_artifact(path):
                continue
            try:
                rows.append({
                    "path": os.path.relpath(path, folder),
                    "size": os.path.getsize(path),
                    "sha256": sha256(path),
                })
            except OSError:
                rows.append({"path": os.path.relpath(path, folder), "error": "unreadable"})
    return rows


def collect_input_files(folder):
    """递归、确定性收集客户目录内的文件，供阶段一筛选。"""
    rows = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d not in {".git", "__pycache__"})
        rows.extend(os.path.join(root, name) for name in sorted(files))
    return rows


def decode_zip_member_name(name):
    """按常见中文 zip 编码顺序解码成员名，并记录采用的策略。"""
    for encoding in ("gbk", "gb18030"):
        try:
            return name.encode("cp437").decode(encoding), f"cp437->{encoding}"
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return name, "original"


def safe_zip_relpath(name):
    """把 zip 内路径规范化为安全相对路径，防止解包逃逸 run/input。"""
    text = str(name or "").replace("\\", "/").strip().rstrip("/")
    if not text:
        return ""
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        raise RuntimeError(f"非法 zip 路径：{name}")
    parts = [part for part in text.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise RuntimeError(f"非法 zip 路径：{name}")
    return "/".join(parts)


def common_zip_root(paths):
    """只有所有文件共享单一顶层目录时，才剥离该父目录。"""
    file_paths = [path for path in paths if path and "/" in path]
    if not file_paths:
        return ""
    roots = {path.split("/", 1)[0] for path in file_paths}
    if len(roots) != 1:
        return ""
    root = next(iter(roots))
    if any(path and path != root and not path.startswith(root + "/") for path in paths):
        return ""
    return root


def strip_common_root(path, root):
    if not root:
        return path
    if path == root:
        return ""
    if path.startswith(root + "/"):
        return path[len(root) + 1:]
    return path


def extract_zip_to_input(zip_path, run_dir):
    """将 zip 确定性解到本次 run/input，并返回可审计的解包清单。"""
    source_zip = os.path.abspath(zip_path)
    target_root = os.path.abspath(os.path.join(run_dir, "input"))
    if not os.path.isfile(source_zip):
        raise RuntimeError(f"输入 zip 不存在：{source_zip}")
    if os.path.isdir(target_root):
        shutil.rmtree(target_root)
    os.makedirs(target_root, exist_ok=True)

    details = {
        "input_kind": "zip",
        "original_zip": source_zip,
        "zip_sha256": sha256(source_zip),
        "extract_dir": target_root,
        "encoding_strategy": "cp437->gbk, cp437->gb18030, original",
        "common_root_stripped": "",
        "directories": [],
        "extracted_files": [],
        "skipped": [],
        "collisions": [],
    }
    entries = []
    with zipfile.ZipFile(source_zip, "r") as zf:
        for info in zf.infolist():
            decoded, strategy = decode_zip_member_name(info.filename)
            rel = safe_zip_relpath(decoded)
            entries.append({
                "info": info,
                "raw_name": info.filename,
                "decoded_name": decoded,
                "encoding": strategy,
                "safe_path": rel,
                "is_dir": info.is_dir() or not rel,
                "size": info.file_size,
            })

        root = common_zip_root([entry["safe_path"] for entry in entries])
        details["common_root_stripped"] = root

        seen_files = {}
        for entry in entries:
            output_rel = strip_common_root(entry["safe_path"], root)
            entry["output_path"] = output_rel
            if entry["is_dir"]:
                if output_rel:
                    details["directories"].append({
                        "decoded_name": entry["decoded_name"],
                        "output_path": output_rel,
                    })
                continue
            if not output_rel:
                details["skipped"].append({
                    "decoded_name": entry["decoded_name"],
                    "reason": "empty output path after common root strip",
                })
                continue
            dest = os.path.abspath(os.path.join(target_root, *output_rel.split("/")))
            if dest != target_root and not dest.startswith(target_root + os.sep):
                raise RuntimeError(f"非法 zip 路径：{entry['decoded_name']}")
            key = os.path.normcase(dest)
            if key in seen_files:
                details["collisions"].append({
                    "output_path": output_rel,
                    "first": seen_files[key],
                    "second": entry["decoded_name"],
                })
                raise RuntimeError(f"zip 解包目标重名：{output_rel}")
            seen_files[key] = entry["decoded_name"]

        for entry in entries:
            output_rel = entry.get("output_path", "")
            if entry["is_dir"]:
                if output_rel:
                    os.makedirs(os.path.join(target_root, *output_rel.split("/")), exist_ok=True)
                continue
            if not output_rel:
                continue
            dest = os.path.abspath(os.path.join(target_root, *output_rel.split("/")))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(entry["info"]) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)
            details["extracted_files"].append({
                "zip_name": entry["raw_name"],
                "decoded_name": entry["decoded_name"],
                "encoding": entry["encoding"],
                "output_path": output_rel,
                "size": os.path.getsize(dest),
                "sha256": sha256(dest),
            })
    return target_root, details


def snapshot_input_folder(source_folder, run_dir):
    """复制本次输入到 run_dir/input，后续阶段只读取该快照目录。"""
    source_root = os.path.abspath(source_folder)
    run_root = os.path.abspath(run_dir)
    target_root = os.path.abspath(os.path.join(run_dir, "input"))
    if not os.path.isdir(source_root):
        raise RuntimeError(f"输入目录不存在：{source_root}")
    if os.path.abspath(source_root) == target_root:
        return target_root

    if os.path.isdir(target_root):
        shutil.rmtree(target_root)
    os.makedirs(target_root, exist_ok=True)
    for root, dirs, files in os.walk(source_root):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__"}]
        dirs[:] = [
            d for d in dirs
            if os.path.abspath(os.path.join(root, d)) != run_root
            and not os.path.abspath(os.path.join(root, d)).startswith(run_root + os.sep)
            and not os.path.abspath(os.path.join(root, d)).startswith(target_root + os.sep)
        ]
        rel_dir = os.path.relpath(root, source_root)
        if rel_dir == ".":
            rel_dir = ""
        dest_dir = os.path.join(target_root, rel_dir)
        os.makedirs(dest_dir, exist_ok=True)
        for name in files:
            source_path = os.path.join(root, name)
            if is_token_vault_secret_artifact(source_path):
                continue
            shutil.copy2(source_path, os.path.join(dest_dir, name))
    return target_root


def prepare_input_snapshot(source, run_dir):
    """准备本次输入快照；目录复制、zip 解包都只落到 run/input。"""
    source_path = os.path.abspath(source)
    if os.path.isfile(source_path) and source_path.lower().endswith(".zip"):
        return extract_zip_to_input(source_path, run_dir)
    target = snapshot_input_folder(source_path, run_dir)
    return target, {
        "input_kind": "folder",
        "original_folder": source_path,
        "snapshot_dir": target,
        "copied_files": inventory(target),
    }


class Runner:
    def __init__(self, args):
        self.args = args
        self.skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.template_manifest_path = manifest_template_path(self.skill_dir)
        planned_run_id = str(getattr(args, "run_id", "") or "")
        if planned_run_id and not RUN_ID_PATTERN.fullmatch(planned_run_id):
            raise ValueError("预分配 run_id 无效")
        stamp = datetime.now(LOCAL_TZ).strftime("%Y%m%dT%H%M%S%z")
        self.run_id = planned_run_id or f"{stamp}-{uuid.uuid4().hex[:8]}"
        root = resolve_run_root(args.run_root)
        self.run_dir = os.path.join(root, self.run_id)
        self.out_dir = os.path.join(self.run_dir, "artifacts")
        self.receipt_dir = os.path.join(self.run_dir, "receipts")
        self.event_path = os.path.join(self.run_dir, "events.jsonl")
        self.manifest_path = os.path.join(self.run_dir, "manifest.json")
        self.stage_1_results_path = os.path.join(self.run_dir, "stage_1_results.json")
        self.qc_results_path = os.path.join(self.run_dir, "qc_results.json")
        self.run_result_path = os.path.join(self.run_dir, "run_result.json")
        self.token_usage_path = os.path.join(self.run_dir, "token_usage.json")
        os.makedirs(self.out_dir, exist_ok=True)
        os.makedirs(self.receipt_dir, exist_ok=True)
        self.warning_events = []
        self.receipt_sequence = 0
        # 阶段一校验结果仅用于本次执行内的回执和兜底判断。
        self.stage_validation_results = {}
        # 最终交付验收在 stage_4_package 内完成，execute 复用结果，避免重复打开 XLSX。
        self.final_validation_result = None
        self.original_input_folder = os.path.abspath(args.folder)
        parent_context = load_parent_run_context(root, args.parent_run_id) if args.parent_run_id else None
        self.parent_context = parent_context
        if parent_context and parent_context.get("parent_client"):
            parent_client = parent_context["parent_client"]
            if args.client_arg_provided and args.client != parent_client:
                raise RuntimeError(
                    f"父运行客户名称不一致：父运行={parent_client}，本次显式参数={args.client}"
                )
            args.client = parent_client
        self.input_dir, _ = prepare_input_snapshot(self.original_input_folder, self.run_dir)
        self.args.folder = self.input_dir
        self.copy_stage_manifest()
        self.manifest["client"] = args.client
        self.manifest["parent_run_id"] = args.parent_run_id or ""
        self.manifest["rerun_reason"] = args.rerun_reason or ""
        self.manifest["password_attempt"] = int(
            (parent_context or {}).get("password_attempt") or 0
        ) + int(getattr(args, "password_attempt_increment", 0) or 0)
        self.manifest["ai_repair_attempt"] = int(
            (parent_context or {}).get("ai_repair_attempt") or 0
        ) + int(getattr(args, "ai_repair_attempt_increment", 0) or 0)
        self.routing_rules_snapshot = None
        snapshot_source = str(getattr(args, "routing_rules_snapshot", "") or "").strip()
        if snapshot_source:
            if not parent_context:
                raise RuntimeError("Run 内 routing snapshot 只允许用于显式 Child Run")
            source_path = os.path.abspath(snapshot_source)
            parent_root = os.path.abspath(parent_context["parent_run_dir"])
            if os.path.commonpath([parent_root, source_path]) != parent_root:
                raise RuntimeError("routing snapshot 必须位于直接父 Run 内")
            expected_sha256 = str(getattr(args, "routing_rules_sha256", "") or "")
            if not expected_sha256 or sha256(source_path) != expected_sha256:
                raise RuntimeError("routing snapshot checksum 无效")
            content = open(source_path, "r", encoding="utf-8").read()
            self.routing_rules_snapshot = build_routing_rules_snapshot(content)
            snapshot_dir = os.path.join(self.run_dir, "routing")
            os.makedirs(snapshot_dir, exist_ok=True)
            snapshot_path = os.path.join(snapshot_dir, "routing_rules.yaml")
            with open(snapshot_path, "w", encoding="utf-8") as stream:
                stream.write(content)
            self.manifest["routing_rules_snapshot"] = {
                "path": self.run_relative_ref(snapshot_path),
                "sha256": expected_sha256,
                "scope": "run_only",
            }
            self.manifest["routing_rules_version"] = S.routing_rules_version(content)
        else:
            self.manifest["routing_rules_version"] = S.routing_rules_version()
        self.manifest["skipped_inputs"] = []
        self.write_manifest()
        Q.atomic_write_json(self.stage_1_results_path, {"files": {}})
        Q.atomic_write_json(self.qc_results_path, Q.empty_results())
        R.atomic_write_json(self.token_usage_path, {
            "contract_version": 1,
            "run_id": self.run_id,
            "measurement_scope": "fallback_and_audit_sessions_only",
            "ai_session_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_input_tokens": 0,
            "sessions": [],
        })
        self.run_result = None

    def copy_stage_manifest(self):
        if not os.path.isfile(self.template_manifest_path):
            raise RuntimeError(f"缺少 skill 资源模板 {MANIFEST_TEMPLATE_RELATIVE_PATH}：{self.template_manifest_path}")
        with open(self.template_manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.manifest = self.sanitize_stage_manifest_template(data)
        self.write_manifest()

    def sanitize_stage_manifest_template(self, data):
        """Reset runtime-only fields before creating a new runtime manifest."""
        for stage_id, spec in data.items():
            if not str(stage_id).startswith("stage_"):
                continue
            spec.pop("script", None)
            spec.pop("validator", None)
            spec["status"] = ""
            if stage_id == "stage_1_standardize":
                spec["ai_fallback_used"] = False
                spec["ai_fallback_artifacts"] = []
                spec.pop("route_artifact", None)
                spec.pop("file_routes", None)
            spec.pop("ai_fallback_dir", None)
            spec.pop("started_at", None)
            spec["duration_seconds"] = None
        return data

    def update_stage_status(self, stage_id, status, duration_seconds=None):
        if stage_id not in self.manifest:
            raise RuntimeError(f"runtime manifest 缺少阶段：{stage_id}")
        self.manifest[stage_id]["status"] = status
        if duration_seconds is not None:
            self.manifest[stage_id]["duration_seconds"] = duration_seconds
        self.write_manifest()

    def write_run_result(
        self,
        *,
        status,
        next_action,
        reason_code="",
        artifact_refs=(),
        context_ref="",
        message="",
        action=None,
        summary=None,
    ):
        run_id = getattr(self, "run_id", os.path.basename(os.path.abspath(self.run_dir)))
        result = R.RunResult(
            run_id=run_id,
            status=status,
            next_action=next_action,
            reason_code=reason_code,
            artifact_refs=tuple(artifact_refs),
            context_ref=context_ref,
            message=message,
            action=dict(action or {}),
            summary=dict(summary or {}),
        )
        result_path = getattr(self, "run_result_path", os.path.join(self.run_dir, "run_result.json"))
        self.run_result_path = result_path
        self.run_result = R.write_run_result(result_path, result)
        return self.run_result

    def coordinator_action(self, operation, **extra):
        action = {
            "handler": "fallback_coordinator",
            "entrypoint": os.path.join(SKILL_DIR, "scripts", "fallback_coordinator.py"),
            "operation": operation,
            "run_dir": os.path.realpath(self.run_dir),
        }
        action.update(extra)
        return action

    def password_file_refs(self, failed_files):
        refs = {
            str(item.get("relative_path") or "").strip()
            for item in failed_files
            if isinstance(item, dict) and str(item.get("relative_path") or "").strip()
        }
        if refs:
            return sorted(refs)
        return sorted(
            os.path.relpath(path, self.input_dir).replace(os.sep, "/")
            for path in glob.glob(os.path.join(self.input_dir, "**", "*"), recursive=True)
            if os.path.isfile(path) and os.path.basename(path) != "_file_hints.yaml"
        )

    def mark_stage_ai_fallback_used(self, stage_id, artifacts=None):
        if stage_id not in self.manifest:
            raise RuntimeError(f"runtime manifest 缺少阶段：{stage_id}")
        self.manifest[stage_id]["ai_fallback_used"] = True
        self.manifest[stage_id]["ai_fallback_artifacts"] = (
            artifacts or self.manifest[stage_id].get("ai_fallback_artifacts", [])
        )
        self.write_manifest()

    def first_pending_stage(self):
        for stage_id, spec in self.manifest.items():
            if not str(stage_id).startswith("stage_"):
                continue
            status = spec.get("status", "")
            if status == ERROR:
                raise RuntimeError(f"阶段已处于 ERROR，必须先处理失败原因：{stage_id}")
            if status != DONE:
                return stage_id, spec
        return None, None

    def write_manifest(self):
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(self.manifest, f, ensure_ascii=False, indent=2)

    def emit(self, level, code, message, **extra):
        event = {"at": now(), "level": level, "code": code, "message": message}
        event.update(extra)
        with open(self.event_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        if bool(getattr(self.args, "verbose", False)):
            print(f"[{level}][{code}] {message}")
        if level == "WARNING":
            self.warning_events.append(event)

    def receipt(self, stage, status, details=None):
        row = {"stage": stage, "status": status, "at": now(), "details": details or {}}
        self.receipt_sequence += 1
        path = os.path.join(self.receipt_dir, f"{self.receipt_sequence:02d}-{stage}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(row, f, ensure_ascii=False, indent=2)

    def stage_1_results_file(self, run_dir=None):
        if run_dir is not None:
            return os.path.join(run_dir, "stage_1_results.json")
        return getattr(
            self,
            "stage_1_results_path",
            os.path.join(self.run_dir, "stage_1_results.json"),
        )

    def qc_results_file(self):
        run_dir = getattr(self, "run_dir", "") or os.path.dirname(self.out_dir)
        return getattr(
            self,
            "qc_results_path",
            os.path.join(run_dir, "qc_results.json"),
        )

    def load_stage_1_results(self, run_dir=None):
        path = self.stage_1_results_file(run_dir)
        data = read_json_if_exists(path, {"files": {}})
        if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
            raise RuntimeError(f"阶段一文件结果结构无效：{path}")
        return data

    def write_stage_1_results(self, data):
        if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
            raise RuntimeError("阶段一文件结果必须包含 files 对象")
        Q.atomic_write_json(self.stage_1_results_file(), data)

    def load_qc_results(self):
        return Q.load_results(self.qc_results_file())

    def delivery_summary(self):
        """生成成功交付所需的紧凑事实，避免宿主回读 manifest/QC。"""
        stage_files = self.load_stage_1_results()["files"]
        processed_file_count = sum(
            1
            for record in stage_files.values()
            if isinstance(record, dict) and record.get("status") == DONE
        )
        skipped_file_count = len(self.manifest.get("skipped_inputs") or [])
        qc_results = self.load_qc_results()

        warnings = []
        seen = set()
        rule_groups = [qc_results.get("customer") or {}]
        rule_groups.extend(
            rules or {}
            for _file_id, rules in sorted((qc_results.get("files") or {}).items())
        )
        for rules in rule_groups:
            for _rule_id, value in sorted(rules.items()):
                if not isinstance(value, dict):
                    continue
                if value.get("level") != Q.SOFT or value.get("passed") is not False:
                    continue
                message = str(value.get("message") or "QC 软性告警").strip()
                if message and message not in seen:
                    seen.add(message)
                    warnings.append(message)

        return {
            "input_file_count": processed_file_count + skipped_file_count,
            "processed_file_count": processed_file_count,
            "qc_status": str(qc_results.get("status") or ""),
            "warning_count": len(warnings),
            "warning_summary": warnings[:5],
        }

    def write_qc_results(self, data):
        Q.atomic_write_json(self.qc_results_file(), data)

    def run_file_qc(self, file_id, checkpoint, context):
        results = self.load_qc_results()
        Q.execute_checkpoint(results, Q.FILE, checkpoint, context, file_id=file_id)
        Q.update_status(results)
        self.write_qc_results(results)
        return Q.has_hard_failure(results, file_id=file_id)

    def remove_file_qc(self, file_id):
        results = self.load_qc_results()
        results.get("files", {}).pop(file_id, None)
        Q.update_status(results)
        self.write_qc_results(results)

    def file_qc_failure_message(self, file_id):
        rules = self.load_qc_results().get("files", {}).get(file_id, {})
        messages = [
            str(value.get("message") or rule_id)
            for rule_id, value in rules.items()
            if value.get("level") == Q.HARD and not value.get("passed")
        ]
        return "；".join(messages) or "文件级 HARD QC 未通过"

    def run_customer_qc(self, checkpoint, context, final=False):
        results = self.load_qc_results()
        Q.execute_checkpoint(results, Q.CUSTOMER, checkpoint, context)
        Q.update_status(results, final=final)
        self.write_qc_results(results)
        return any(
            value.get("level") == Q.HARD and not value.get("passed")
            for value in results.get("customer", {}).values()
        )

    def current_skill_version(self):
        return str((self.manifest.get("skill") or {}).get("version") or "")

    def parent_skill_version(self):
        parent = getattr(self, "parent_context", None) or {}
        parent_dir = parent.get("parent_run_dir")
        if not parent_dir:
            return ""
        manifest = read_json_if_exists(os.path.join(parent_dir, "manifest.json"), {})
        return str((manifest.get("skill") or {}).get("version") or "")

    def resolve_result_output(self, run_dir, output):
        relpath = str(output or "").strip()
        if not relpath:
            return ""
        root = os.path.abspath(run_dir)
        path = os.path.abspath(relpath if os.path.isabs(relpath) else os.path.join(root, relpath))
        if os.path.commonpath([root, path]) != root:
            raise RuntimeError(f"阶段一结果路径越界：{relpath}")
        return path

    def work_dir(self):
        return os.path.join(self.out_dir, "_工作区", safe_name(self.args.client))

    def fallback_dir(self, stage_id):
        return os.path.join(self.run_dir, "fallback", stage_id)

    def run_relative_ref(self, path):
        root = os.path.abspath(self.run_dir)
        resolved = os.path.abspath(path)
        if os.path.commonpath([root, resolved]) != root:
            raise RuntimeError(f"Run artifact 路径越界：{path}")
        return normalize_relpath(os.path.relpath(resolved, root))

    def input_relative_path(self, path):
        input_root = os.path.abspath(
            getattr(self, "input_dir", getattr(self.args, "folder", self.run_dir))
        )
        resolved = os.path.abspath(path)
        if os.path.commonpath([input_root, resolved]) != input_root:
            return os.path.basename(resolved)
        return normalize_relpath(os.path.relpath(resolved, input_root))

    def compact_evidence_bundle(self, stage_id, reason_code, failed_files, error):
        def compact_text(value, limit):
            text = str(value or "")
            return text if len(text) <= limit else text[:limit] + "…"

        qc = self.load_qc_results()
        failed_qc = []
        for file_id, rules in (qc.get("files") or {}).items():
            for rule_id, value in (rules or {}).items():
                if value.get("passed"):
                    continue
                failed_qc.append({
                    "file_id": file_id,
                    "rule_id": rule_id,
                    "level": value.get("level", ""),
                    "message": compact_text(value.get("message", ""), 500),
                })
        compact_files = [
            {
                "file_id": item.get("file_id", ""),
                "name": item.get("name", ""),
                "relative_path": item.get("relative_path", ""),
                "status": item.get("status", ""),
                "reason_code": item.get("reason_code", ""),
                "message": compact_text(item.get("message", ""), 1000),
                "route": item.get("route", {}),
            }
            for item in failed_files
        ]
        return render_protocol("evidence-bundle", {
            "run_id": self.run_id,
            "stage_id": stage_id,
            "reason_code": reason_code,
            "failed_files": compact_files,
            "qc_summary": failed_qc,
            "skipped_inputs": list(self.manifest.get("skipped_inputs") or []),
            "error_summary": compact_text(error, 2000),
        })

    def latest_artifact(self, pattern):
        hits = sorted(glob.glob(os.path.join(self.work_dir(), pattern)))
        if not hits:
            raise RuntimeError(f"缺少阶段输入产物：{pattern}")
        return hits[-1]

    def upstream_manifest_path(self):
        return os.path.join(self.args.folder, "summary", "manifest.json")

    def declared_stage_1(self, manifest):
        return manifest.get("stage_1_standardize") or {}

    def declared_stage_1_outputs(self, manifest):
        stage_1 = self.declared_stage_1(manifest)
        if isinstance(stage_1, dict) and stage_1.get("outputs"):
            return stage_1.get("outputs") or []
        return manifest.get("standardized_outputs") or []

    def load_declared_standardized_manifest(self):
        # 上游 manifest 只作为阶段状态声明读取；orch 不根据文件名反推状态，
        # 也不在这里生成上游 manifest。没有声明时继续走原始阶段一流程。
        manifest_path = self.upstream_manifest_path()
        if not os.path.isfile(manifest_path):
            return None, manifest_path

        data = read_json_if_exists(manifest_path, {})
        is_current_manifest = data.get("schema_version") == "bank-statement-standardization.manifest/v1"
        is_legacy_manifest = data.get("bundle_type") == "tokenized_standardized_batch"
        if not (is_current_manifest or is_legacy_manifest):
            return None, manifest_path

        stage_1 = self.declared_stage_1(data)
        legacy_state = data.get("pipeline_state") or {}
        stage_1_status = stage_1.get("status") if isinstance(stage_1, dict) else None
        if stage_1_status != DONE and legacy_state.get("stage_1_standardize") != DONE:
            raise RuntimeError(f"Token Vault manifest 未声明阶段一完成：{manifest_path}")
        outputs = self.declared_stage_1_outputs(data)
        if not outputs:
            raise RuntimeError(f"Token Vault manifest 缺少阶段一输出文件：{manifest_path}")
        return data, manifest_path

    def resolve_declared_standardized_outputs(self, manifest, manifest_path):
        # outputs 是相对 summary/manifest.json 的路径，例如 ../001_xxx__standardized.csv。
        # 这里只做路径越界、存在性和标准化命名校验，不改变上游 manifest。
        base_dir = os.path.abspath(os.path.dirname(manifest_path))
        bundle_dir = os.path.abspath(self.args.folder)
        resolved = []
        for relpath in self.declared_stage_1_outputs(manifest):
            rel_text = str(relpath)
            path = os.path.abspath(rel_text if os.path.isabs(rel_text) else os.path.join(base_dir, rel_text))
            try:
                common = os.path.commonpath([bundle_dir, path])
            except ValueError as exc:
                raise RuntimeError(f"Token Vault manifest 输出路径非法：{rel_text}") from exc
            if common != bundle_dir:
                raise RuntimeError(f"Token Vault manifest 输出路径越界：{rel_text}")
            if not os.path.isfile(path):
                raise RuntimeError(f"Token Vault manifest 指向的标准化文件不存在：{rel_text}")
            if not os.path.basename(path).endswith("__standardized.csv"):
                raise RuntimeError(f"Token Vault manifest 输出文件名不符合标准化命名：{rel_text}")
            resolved.append((rel_text, path))
        return resolved

    def count_csv_rows(self, path):
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            return sum(1 for _ in csv.DictReader(f))

    def write_stage_1_routes(self, work, file_routes):
        """旧调用兼容：把路由写入新的 stage_1_results.json，不再生成路由文件。"""
        results = {"files": {}}
        for csv_name, route in file_routes.items():
            path = os.path.join(work, csv_name)
            if not os.path.isfile(path):
                raise RuntimeError(f"阶段一标准化 CSV 不存在：{path}")
            file_id = f"md5:{md5(path)}"
            results["files"][file_id] = {
                "name": csv_name,
                "status": DONE,
                "output": normalize_relpath(os.path.relpath(path, self.run_dir)),
                "route": dict(route),
            }
        self.write_stage_1_results(results)
        return self.stage_1_results_file()

    def load_stage_1_routes(self):
        results = self.load_stage_1_results()
        if results["files"]:
            routes = {}
            for record in results["files"].values():
                if record.get("status") != DONE:
                    continue
                output = self.resolve_result_output(self.run_dir, record.get("output"))
                routes[os.path.basename(output)] = dict(record.get("route") or {})
            return routes

        # 兼容读取历史 run；新 run 不再写 route_artifact。
        relpath = str(self.manifest["stage_1_standardize"].get("route_artifact") or "").strip()
        if not relpath:
            return {}
        run_dir = os.path.abspath(self.run_dir)
        path = os.path.abspath(os.path.join(run_dir, relpath))
        if os.path.commonpath([run_dir, path]) != run_dir:
            raise RuntimeError(f"阶段一路由产物路径越界：{relpath}")
        routes = read_json_if_exists(path, None)
        if not isinstance(routes, dict):
            raise RuntimeError(f"阶段一路由产物无效：{path}")
        return routes

    def standardized_paths_from_results(self):
        if not getattr(self, "run_dir", None):
            return []
        paths = []
        for record in self.load_stage_1_results()["files"].values():
            if record.get("status") != DONE:
                continue
            path = self.resolve_result_output(self.run_dir, record.get("output"))
            if not os.path.isfile(path):
                raise RuntimeError(f"阶段一 DONE 产物不存在：{path}")
            paths.append(path)
        return sorted(paths)

    def declared_stage_1_file_routes(self, manifest, manifest_path):
        stage_1 = self.declared_stage_1(manifest)
        legacy_routes = stage_1.get("file_routes") if isinstance(stage_1, dict) else None
        if isinstance(legacy_routes, dict):
            return legacy_routes
        relpath = str(stage_1.get("route_artifact") or "").strip() if isinstance(stage_1, dict) else ""
        if not relpath:
            return {}
        base_dir = os.path.abspath(os.path.dirname(manifest_path))
        bundle_dir = os.path.abspath(self.args.folder)
        path = os.path.abspath(os.path.join(base_dir, relpath))
        if os.path.commonpath([bundle_dir, path]) != bundle_dir:
            raise RuntimeError(f"Token Vault manifest 路由产物路径越界：{relpath}")
        routes = read_json_if_exists(path, None)
        if not isinstance(routes, dict):
            raise RuntimeError(f"Token Vault manifest 路由产物无效：{path}")
        return routes

    def stage_1_from_declared_standardized_manifest(self, work):
        # 快速完成阶段一：消费上游 manifest 声明的标准化产物，
        # 然后按阶段一 receipt/validate_standardize/DONE 的路径继续进入阶段二。
        upstream_manifest, manifest_path = self.load_declared_standardized_manifest()
        if not upstream_manifest:
            return None

        processed = []
        stage_results = {"files": {}}
        upstream_routes = self.declared_stage_1_file_routes(upstream_manifest, manifest_path)
        for relpath, source_path in self.resolve_declared_standardized_outputs(upstream_manifest, manifest_path):
            file_id = f"md5:{md5(source_path)}"
            blocked = self.run_file_qc(
                file_id,
                Q.BEFORE_STAGE_1,
                {"path": source_path},
            )
            if blocked:
                stage_results["files"][file_id] = {
                    "name": os.path.basename(source_path),
                    "status": "BLOCKED",
                    "message": "文件级前置 HARD QC 未通过",
                }
                self.write_stage_1_results(stage_results)
                continue
            try:
                target_csv = os.path.join(work, os.path.basename(source_path))
                shutil.copy2(source_path, target_csv)
                row_count = V.validate_standardized_file(target_csv)["standardized_rows"]
                route_key = os.path.basename(target_csv)
                route = upstream_routes.get(route_key) or upstream_routes.get(str(relpath)) or {}
                route_summary = {
                    "fingerprint_id": str(route.get("fingerprint_id") or ""),
                    "series_family": str(route.get("series_family") or ""),
                    "router_bank": str(route.get("router_bank") or "未识别"),
                    "yaml_match_status": str(route.get("yaml_match_status") or "unmatched"),
                }
                if not valid_yaml_route(route_summary):
                    raise V.ValidationError(f"阶段一文件路由字段不合法：{route_key}")
                post_blocked = self.run_file_qc(
                    file_id,
                    Q.AFTER_STAGE_1,
                    {"path": source_path, "output": target_csv, "source_format_error": ""},
                )
                if post_blocked:
                    stage_results["files"][file_id] = {
                        "name": os.path.basename(source_path),
                        "status": BLOCKED,
                        "message": self.file_qc_failure_message(file_id),
                    }
                else:
                    stage_results["files"][file_id] = {
                        "name": os.path.basename(source_path),
                        "status": DONE,
                        "output": normalize_relpath(os.path.relpath(target_csv, self.run_dir)),
                        "route": route_summary,
                    }
                    processed.append({
                        "input": source_path,
                        "csv": target_csv,
                        "rows": row_count,
                    })
            except Exception as exc:
                stage_results["files"][file_id] = {
                    "name": os.path.basename(source_path),
                    "status": ERROR,
                    "message": str(exc),
                }
            self.write_stage_1_results(stage_results)

        self.manifest["skipped_inputs"] = []
        self.write_stage_1_results(stage_results)
        self.manifest["upstream_manifest"] = {
            "path": manifest_path,
            "schema_version": upstream_manifest.get("schema_version", ""),
            "producer": upstream_manifest.get("producer", ""),
            "archive_id": upstream_manifest.get("archive_id", ""),
            "archive_name_present": bool(str(upstream_manifest.get("archive_name") or "").strip()),
            "stage_1_status": self.declared_stage_1(upstream_manifest).get("status", ""),
        }
        self.write_manifest()
        return StageResult("stage_1_standardize", {
            "mode": "manifest_declared_standardized_input",
            "upstream_manifest": self.manifest["upstream_manifest"],
            "processed_files": len(processed),
            "standardized": processed,
            "stage_1_results": self.stage_1_results_file(),
        })

    def stage_1_standardize(self):
        work = self.work_dir()
        if os.path.isdir(work):
            shutil.rmtree(work)
        os.makedirs(work, exist_ok=True)
        stage_results = {"files": {}}
        self.write_stage_1_results(stage_results)

        declared_result = self.stage_1_from_declared_standardized_manifest(work)
        if declared_result:
            done_paths = self.standardized_paths_from_results()
            customer_hard_failed = self.run_customer_qc(
                Q.AFTER_STAGE_1,
                {"standardized_paths": done_paths, "stage_1_results": self.load_stage_1_results()},
            )
            failed = [
                record for record in self.load_stage_1_results()["files"].values()
                if record.get("status") in {BLOCKED, ERROR}
            ]
            if customer_hard_failed:
                raise RuntimeError("阶段一客户级 HARD QC 未通过")
            if failed:
                raise RuntimeError(f"阶段一存在 {len(failed)} 个失败文件")
            return declared_result

        raw_files, skipped = S.screen_files(collect_input_files(self.args.folder))
        self.manifest["skipped_inputs"] = [{"name": n, "reason": w} for n, w in skipped]
        self.write_manifest()
        if not raw_files:
            detail = "；".join(f"{n}（{w}）" for n, w in skipped) or "目录内无候选文件"
            raise RuntimeError(f"客户「{self.args.client}」无可处理的银行流水文件。已跳过：{detail}")

        processed = []
        decisions = {}
        added = []
        reused = []
        rerun = []
        parent = getattr(self, "parent_context", None) or {}
        parent_dir = parent.get("parent_run_dir")
        parent_results = (
            self.load_stage_1_results(parent_dir)
            if parent_dir and os.path.isfile(self.stage_1_results_file(parent_dir))
            else {"files": {}}
        )
        parent_files = parent_results["files"]
        versions_match = bool(
            self.current_skill_version()
            and self.current_skill_version() == self.parent_skill_version()
        )
        descriptors = []
        seen_file_ids = {}
        for path in raw_files:
            file_id = f"md5:{md5(path)}"
            if file_id in seen_file_ids:
                skipped.append((
                    os.path.basename(path),
                    f"与 {seen_file_ids[file_id]} 内容 MD5 相同，按重复文件跳过",
                ))
                continue
            seen_file_ids[file_id] = os.path.basename(path)
            descriptors.append((file_id, path, os.path.basename(path)))
        current_ids = {file_id for file_id, _path, _name in descriptors}
        removed = sorted(set(parent_files) - current_ids)

        file_sleep_seconds = max(0.0, float(getattr(self.args, "file_sleep_seconds", 0) or 0))
        for index, (file_id, path, name) in enumerate(descriptors, 1):
            parent_record = parent_files.get(file_id)
            decision = "ADDED" if parent_record is None else "RERUN"
            reason = "new_md5" if parent_record is None else "parent_result_not_reusable"
            stage_results["files"][file_id] = {"name": name, "status": PENDING}
            self.write_stage_1_results(stage_results)

            if self.run_file_qc(file_id, Q.BEFORE_STAGE_1, {"path": path}):
                stage_results["files"][file_id] = {
                    "name": name,
                    "status": BLOCKED,
                    "message": "文件级前置 HARD QC 未通过",
                }
                decisions[file_id] = {
                    "decision": decision,
                    "reason": reason,
                    "result_status": BLOCKED,
                }
                (added if decision == "ADDED" else rerun).append(file_id)
                self.write_stage_1_results(stage_results)
                continue

            parent_output = ""
            if parent_record is not None:
                if parent_record.get("name") != name:
                    reason = "same_md5_different_name"
                elif parent_record.get("status") != DONE:
                    reason = "parent_file_not_done"
                elif not versions_match:
                    reason = "skill_version_mismatch"
                elif not reusable_stage_1_route(parent_record.get("route"), name):
                    reason = "parent_route_invalid"
                else:
                    parent_output = self.resolve_result_output(parent_dir, parent_record.get("output"))
                    if not os.path.isfile(parent_output):
                        parent_output = ""
                        reason = "parent_output_missing"
                    else:
                        decision = "REUSED"
                        reason = "same_md5_same_name_parent_done"

            if decision == "REUSED":
                target_csv = os.path.join(work, os.path.basename(parent_output))
                reuse_succeeded = False
                try:
                    try:
                        os.link(parent_output, target_csv)
                    except OSError:
                        shutil.copy2(parent_output, target_csv)
                    row_count = V.validate_standardized_file(target_csv)["standardized_rows"]
                    post_blocked = self.run_file_qc(
                        file_id,
                        Q.AFTER_STAGE_1,
                        {"path": path, "output": target_csv, "source_format_error": ""},
                    )
                    reused.append(file_id)
                    if post_blocked:
                        stage_results["files"][file_id] = {
                            "name": name,
                            "status": BLOCKED,
                            "message": self.file_qc_failure_message(file_id),
                        }
                        result_status = BLOCKED
                    else:
                        stage_results["files"][file_id] = {
                            "name": name,
                            "status": DONE,
                            "output": normalize_relpath(os.path.relpath(target_csv, self.run_dir)),
                            "route": dict(parent_record.get("route") or {}),
                            "recognized_type": str(
                                parent_record.get("recognized_type")
                                or (parent_record.get("route") or {}).get("router_bank")
                                or "未识别"
                            ),
                            "record_count": row_count,
                        }
                        processed.append({"input": path, "csv": target_csv, "rows": row_count, "reused": True})
                        result_status = DONE
                    reuse_succeeded = True
                except Exception as exc:
                    decision = "RERUN"
                    reason = f"parent_output_reuse_failed:{exc}"
                    try:
                        if os.path.isfile(target_csv):
                            os.unlink(target_csv)
                    except OSError:
                        pass
                if reuse_succeeded:
                    decisions[file_id] = {
                        "decision": decision,
                        "reason": reason,
                        "result_status": result_status,
                    }
                    self.write_stage_1_results(stage_results)
                    if file_sleep_seconds and index < len(descriptors):
                        time.sleep(file_sleep_seconds)
                    continue

            (added if decision == "ADDED" else rerun).append(file_id)
            try:
                csv_path, _json_path, report = S.standardize_file(S.StandardizationContext(
                    path=path,
                    out_dir=work,
                    account_type=self.args.account_type,
                    write_mapping=False,
                    route_rules=getattr(self, "routing_rules_snapshot", None),
                ))
                row_count = int(report["标准化统计"]["交易笔数"])
                V.validate_standardized_file(csv_path)
                route_summary = yaml_route_summary(report)
                if not valid_yaml_route(route_summary):
                    raise V.ValidationError(f"阶段一文件路由字段不合法：{name}")
                post_blocked = self.run_file_qc(
                    file_id,
                    Q.AFTER_STAGE_1,
                    {"path": path, "output": csv_path, "source_format_error": ""},
                )
                if post_blocked:
                    stage_results["files"][file_id] = {
                        "name": name,
                        "relative_path": self.input_relative_path(path),
                        "status": BLOCKED,
                        "message": self.file_qc_failure_message(file_id),
                        "reason_code": R.QC_HARD_FAILURE,
                    }
                else:
                    stage_results["files"][file_id] = {
                        "name": name,
                        "status": DONE,
                        "output": normalize_relpath(os.path.relpath(csv_path, self.run_dir)),
                        "route": route_summary,
                        "recognized_type": recognized_type(report),
                        "record_count": row_count,
                    }
                    processed.append({
                        "input": path,
                        "csv": csv_path,
                        "rows": row_count,
                    })
            except S.YamlRouteRequiredError as exc:
                route = failure_route_summary(getattr(exc, "route_info", {}))
                reason_code = (
                    R.ROUTE_AMBIGUOUS
                    if route.get("yaml_match_status") == "ambiguous"
                    else R.ROUTE_UNMATCHED
                )
                stage_results["files"][file_id] = {
                    "name": name,
                    "relative_path": self.input_relative_path(path),
                    "status": ERROR,
                    "message": exc.reason,
                    "reason_code": reason_code,
                    "route": route,
                }
            except S.SourceFormatQualityError as exc:
                self.run_file_qc(
                    file_id,
                    Q.AFTER_STAGE_1,
                    {"path": path, "source_format_error": exc.reason},
                )
                stage_results["files"][file_id] = {
                    "name": name,
                    "relative_path": self.input_relative_path(path),
                    "status": BLOCKED,
                    "message": exc.reason,
                    "reason_code": R.INPUT_SOURCE_INVALID,
                }
            except S.NotABankStatement as exc:
                skipped.append((name, exc.reason))
                stage_results["files"].pop(file_id, None)
                self.remove_file_qc(file_id)
            except Exception as exc:
                password_attempt = max(
                    int(self.manifest.get("password_attempt") or 0),
                    1 if has_open_password_hint(path) else 0,
                )
                failure = R.classify_failure(
                    "stage_1_standardize",
                    exc,
                    password_attempt=password_attempt,
                )
                stage_results["files"][file_id] = {
                    "name": name,
                    "relative_path": self.input_relative_path(path),
                    "status": ERROR,
                    "message": str(exc),
                    "reason_code": failure.reason_code,
                }
            result_status = (
                stage_results["files"].get(file_id, {}).get("status")
                if file_id in stage_results["files"]
                else "SKIPPED"
            )
            decisions[file_id] = {
                "decision": decision,
                "reason": reason,
                "result_status": result_status,
            }
            self.write_stage_1_results(stage_results)
            if file_sleep_seconds and index < len(descriptors):
                time.sleep(file_sleep_seconds)

        self.manifest["skipped_inputs"] = [{"name": n, "reason": w} for n, w in skipped]
        self.write_manifest()
        done_paths = self.standardized_paths_from_results()
        customer_hard_failed = self.run_customer_qc(
            Q.AFTER_STAGE_1,
            {"standardized_paths": done_paths, "stage_1_results": stage_results},
        )
        failed_records = {
            file_id: record
            for file_id, record in stage_results["files"].items()
            if record.get("status") in {BLOCKED, ERROR}
        }
        if customer_hard_failed:
            failed_records["customer"] = {"status": BLOCKED, "message": "客户级 HARD QC 未通过"}
        decision_summary = {
            "added": sorted(added),
            "reused": sorted(reused),
            "rerun": sorted(rerun),
            "removed": removed,
            "decisions": decisions,
        }
        if getattr(self, "receipt_dir", None):
            self.receipt(
                "stage_1_files",
                "partial" if failed_records else "ok",
                decision_summary,
            )
        if failed_records:
            detail = "；".join(
                f"{record.get('name', file_id)}：{record.get('message', record.get('status'))}"
                for file_id, record in failed_records.items()
            )
            raise RuntimeError(f"阶段一处理完成但存在失败文件：{detail}")
        if not processed:
            detail = "；".join(f"{n}（{w}）" for n, w in skipped) or "无成功标准化文件"
            raise RuntimeError(f"阶段一没有生成标准化产物：{detail}")

        return StageResult(
            "stage_1_standardize",
            {
                "processed_files": len(processed),
                "standardized": processed,
                "stage_1_results": self.stage_1_results_file(),
                **decision_summary,
            },
        )

    def stage_2_integrate(self):
        import pandas as pd
        standardized_inputs = self.standardized_paths_from_results()
        if not standardized_inputs:
            # 兼容历史 run；新 run 必须由 stage_1_results.json 提供 DONE 文件。
            standardized_inputs = [self.work_dir()]
        int_csv, int_json, report = I.integrate_context(IntegrationContext.create(
            self.args.client,
            standardized_inputs,
            out_dir=self.work_dir(),
            file_routes=self.load_stage_1_routes(),
        ))
        overview = report["客户整合概览"]
        integrated_rows = len(pd.read_csv(int_csv, dtype=str))
        if integrated_rows != int(overview["整合交易数"]):
            raise RuntimeError(
                f"阶段二整合交易数不一致：报告 {overview['整合交易数']}，CSV {integrated_rows}"
            )
        return StageResult("stage_2_integrate", {
            "integrated_csv": int_csv,
            "integrated_report": int_json,
            "integrated_rows": integrated_rows,
            "accounts": overview["整合账户数"],
        })

    def stage_2b_portfolio_balance(self):
        int_csv = self.latest_artifact("*__整合流水.csv")
        daily_csv, report_json, report = PB.run(int_csv, out_dir=self.work_dir())
        return StageResult("stage_2b_portfolio_balance", {
            "portfolio_csv": daily_csv if os.path.isfile(daily_csv) else "",
            "portfolio_report": report_json,
            "accounts": report["数据范围"]["账户数"],
            "warning_accounts": report["账户余额校验"]["预警账户数"],
        })

    def stage_3_tag(self):
        import pandas as pd
        int_csv = self.latest_artifact("*__整合流水.csv")
        rules = os.path.join(self.skill_dir, "assets", "tag_rules.csv")
        tag_csv, tag_json, report = T.tag(int_csv, rules, out_dir=self.work_dir())
        summary = report["标签梳理概览"]
        integrated_rows = len(pd.read_csv(int_csv, dtype=str))
        tagged = pd.read_csv(tag_csv, dtype=str)
        if len(tagged) != integrated_rows:
            raise RuntimeError(f"阶段三打标前后交易数不一致：{integrated_rows} != {len(tagged)}")
        required_tags = {"收支方向", "一级标签", "二级标签", "三级标签", "标签来源"}
        missing = sorted(required_tags - set(tagged.columns))
        if missing:
            raise RuntimeError(f"阶段三打标产物缺少必需字段：{', '.join(missing)}")
        return StageResult("stage_3_tag", {
            "tagged_csv": tag_csv,
            "tag_report": tag_json,
            "tagged_rows": len(tagged),
            "rule_hit_rate": summary["规则命中率"],
        })

    def stage_4_package(self):
        import pandas as pd
        int_json = self.latest_artifact("*__整合报告.json")
        tag_csv = self.latest_artifact("*__打标流水.csv")
        tag_json = self.latest_artifact("*__标签报告.json")
        balance_json = self.latest_artifact("*__余额校验.json")
        daily_hits = sorted(glob.glob(os.path.join(self.work_dir(), "*__组合日余额.csv")))
        with open(int_json, encoding="utf-8") as f:
            irep = json.load(f)
        with open(tag_json, encoding="utf-8") as f:
            srep = json.load(f)
        with open(balance_json, encoding="utf-8") as f:
            pbrep = json.load(f)
        tagged = pd.read_csv(tag_csv, dtype=str)
        daily = pd.read_csv(daily_hits[-1]) if daily_hits else pd.DataFrame()
        skipped = [(row.get("name", ""), row.get("reason", "")) for row in self.manifest.get("skipped_inputs", [])]
        qc_summary = self.load_qc_results()
        Q.update_status(qc_summary, final=True)
        P.finalize_deliverable(
            self.args.client,
            tagged,
            daily,
            irep,
            srep,
            pbrep,
            self.out_dir,
            skipped,
            qc_results=qc_summary,
        )
        self.final_validation_result = V.validate_final(
            self.out_dir,
            self.args.client,
            tagged_rows=len(tagged),
            require_daily_balance=bool(daily_hits),
        )
        return StageResult("stage_4_package", self.final_validation_result)

    def execute_stage_script(self, stage_id):
        handlers = {
            "stage_1_standardize": self.stage_1_standardize,
            "stage_2_integrate": self.stage_2_integrate,
            "stage_2b_portfolio_balance": self.stage_2b_portfolio_balance,
            "stage_3_tag": self.stage_3_tag,
            "stage_4_package": self.stage_4_package,
        }
        if stage_id not in handlers:
            raise RuntimeError(f"未知阶段：{stage_id}")
        return handlers[stage_id]()

    def stage_handler_name(self, stage_id):
        return f"Runner.{stage_id}"

    def validate_stage(self, stage_id):
        work = self.work_dir()
        if stage_id == "stage_1_standardize":
            return V.validate_standardize(
                work,
                skipped_inputs=self.manifest.get("skipped_inputs", []),
                file_routes=self.load_stage_1_routes(),
                stage_1_results=self.load_stage_1_results(),
                run_dir=self.run_dir,
            )
        raise RuntimeError(f"未知阶段验证器：{stage_id}")

    def bundle(self, level):
        bundle = self.bundle_path(level)
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(self.run_dir):
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                for name in files:
                    path = os.path.join(root, name)
                    if os.path.abspath(path) == os.path.abspath(bundle):
                        continue
                    zf.write(path, os.path.join("diagnostics", os.path.relpath(path, self.run_dir)))
            if self.args.error_bundle_mode == "full":
                for root, dirs, files in os.walk(self.args.folder):
                    dirs[:] = [d for d in dirs if d not in {".git", "__pycache__"}]
                    dirs[:] = [d for d in dirs
                               if not os.path.abspath(os.path.join(root, d)).startswith(
                                   os.path.abspath(self.run_dir) + os.sep)]
                    for name in files:
                        path = os.path.join(root, name)
                        if os.path.abspath(path).startswith(os.path.abspath(self.run_dir) + os.sep):
                            continue
                        if is_token_vault_secret_artifact(path):
                            continue
                        zf.write(path, os.path.join("raw_inputs", os.path.relpath(path, self.args.folder)))
        return bundle

    def bundle_path(self, level):
        return os.path.join(self.run_dir, f"{self.run_id}__{level}__{self.args.error_bundle_mode}.zip")

    def handle_stage_failure(self, stage_id, spec, exc, duration_seconds=None):
        if stage_id != "stage_1_standardize":
            self.emit(
                "ERROR",
                "STAGE_ERROR",
                f"阶段 {stage_id} 执行失败，确定性流水线已中止",
                stage=stage_id,
                error=str(exc),
            )
            self.update_stage_status(
                stage_id,
                ERROR,
                duration_seconds=duration_seconds,
            )
            self.receipt(stage_id, "error", {
                "orchestrator_handler": self.stage_handler_name(stage_id),
                "error": str(exc),
            })
            self.write_run_result(
                status=ERROR,
                next_action=R.REPORT_ERROR,
                reason_code=R.DOWNSTREAM_STAGE_FAILURE,
                context_ref="manifest.json",
                message=f"{stage_id} 失败，确定性流水线已停止",
            )
            return

        failed_files = []
        try:
            for file_id, record in self.load_stage_1_results()["files"].items():
                if record.get("status") not in {BLOCKED, ERROR}:
                    continue
                failed_files.append({
                    "file_id": file_id,
                    "name": record.get("name", ""),
                    "relative_path": record.get("relative_path", ""),
                    "status": record.get("status", ""),
                    "message": record.get("message", ""),
                    "reason_code": record.get("reason_code", ""),
                    "route": record.get("route", {}),
                })
        except Exception:
            failed_files = []
        failure = R.classify_failure(
            stage_id,
            exc,
            failed_files,
            password_attempt=int(self.manifest.get("password_attempt") or 0),
            skipped_inputs=self.manifest.get("skipped_inputs") or [],
        )
        if (
            failure.next_action == R.AI_FALLBACK
            and int(self.manifest.get("ai_repair_attempt") or 0) >= R.MAX_AI_REPAIR_ATTEMPTS
        ):
            failure = R.FailureRoute(
                failure.reason_code,
                R.REPORT_ERROR,
                "AI 修复次数已达上限",
            )
        self.manifest[stage_id]["reason_code"] = failure.reason_code
        self.update_stage_status(
            stage_id,
            ERROR,
            duration_seconds=duration_seconds,
        )

        if failure.next_action != R.AI_FALLBACK:
            event_code = (
                "USER_INPUT_REQUIRED"
                if failure.next_action == R.REQUEST_USER
                else "STAGE_ERROR"
            )
            self.emit(
                "WARNING" if failure.next_action == R.REQUEST_USER else "ERROR",
                event_code,
                failure.message,
                stage=stage_id,
                reason_code=failure.reason_code,
            )
            self.receipt(stage_id, "error", {
                "orchestrator_handler": self.stage_handler_name(stage_id),
                "reason_code": failure.reason_code,
                "next_action": failure.next_action,
                "error": str(exc),
            })
            action = {}
            if failure.reason_code in {R.INPUT_PASSWORD_REQUIRED, R.INPUT_PASSWORD_INVALID}:
                action = self.coordinator_action(
                    "retry-password",
                    file_refs=self.password_file_refs(failed_files),
                    input_transport="stdin",
                )
            elif failure.next_action == R.REQUEST_USER:
                action = {
                    "handler": "user",
                    "operation": "provide_supported_input",
                }
            self.write_run_result(
                status=ERROR,
                next_action=failure.next_action,
                reason_code=failure.reason_code,
                artifact_refs=("stage_1_results.json", "qc_results.json"),
                context_ref="stage_1_results.json",
                message=failure.message,
                action=action,
            )
            return

        fallback_dir = self.fallback_dir(stage_id)
        os.makedirs(fallback_dir, exist_ok=True)
        evidence_path = os.path.join(fallback_dir, "evidence_bundle.json")
        evidence = self.compact_evidence_bundle(
            stage_id,
            failure.reason_code,
            failed_files,
            exc,
        )
        R.atomic_write_json(evidence_path, evidence)
        fallback_request = render_protocol("fallback-request", {
            "run_id": self.run_id,
            "client": self.args.client,
            "stage_id": stage_id,
            "name": spec.get("name", ""),
            "reason_code": failure.reason_code,
            "attempt": int(self.manifest.get("ai_repair_attempt") or 0) + 1,
            "max_attempts": R.MAX_AI_REPAIR_ATTEMPTS,
            "next_action": R.AI_FALLBACK,
            "evidence_ref": self.run_relative_ref(evidence_path),
            "message": failure.message,
            "files": [
                {"file_id": item.get("file_id", ""), "name": item.get("name", "")}
                for item in failed_files
            ],
            "created_at": now(),
        })
        fallback_request_path = os.path.join(fallback_dir, "fallback_request.json")
        R.atomic_write_json(fallback_request_path, fallback_request)
        fallback_artifacts = ["fallback_request.json", "evidence_bundle.json"]
        self.emit(
            "WARNING",
            "AI_FALLBACK_REQUIRED",
            f"阶段 {stage_id} 失败，需要独立 Fallback 会话处理紧凑 evidence",
            stage=stage_id,
            reason_code=failure.reason_code,
            ai_fallback_dir=fallback_dir,
            max_retry=R.MAX_AI_REPAIR_ATTEMPTS,
        )
        self.mark_stage_ai_fallback_used(stage_id, fallback_artifacts)
        self.receipt(stage_id, "error", {
            "orchestrator_handler": self.stage_handler_name(stage_id),
            "reason_code": failure.reason_code,
            "next_action": R.AI_FALLBACK,
            "ai_fallback_used": True,
            "ai_fallback_artifacts": fallback_artifacts,
            "error": str(exc),
        })
        self.write_run_result(
            status=ERROR,
            next_action=R.AI_FALLBACK,
            reason_code=failure.reason_code,
            artifact_refs=(
                self.run_relative_ref(fallback_request_path),
                self.run_relative_ref(evidence_path),
            ),
            context_ref=self.run_relative_ref(fallback_request_path),
            message=failure.message,
            action=self.coordinator_action("next"),
        )

    def run_manifest_stages(self):
        while True:
            stage_id, spec = self.first_pending_stage()
            if not stage_id:
                return
            stage_started = time.perf_counter()
            self.emit(
                "INFO",
                "STAGE_START",
                f"开始阶段 {stage_id}：{spec.get('name', '')}",
                stage=stage_id,
                name=spec.get("name", ""),
                status=spec.get("status", ""),
            )
            try:
                script_result = self.execute_stage_script(stage_id)
                self.receipt(stage_id, "script_ok", {
                    "orchestrator_handler": self.stage_handler_name(stage_id),
                    "result": script_result,
                })
                if stage_id == "stage_1_standardize":
                    validate_result = self.validate_stage(stage_id)
                    self.stage_validation_results[stage_id] = validate_result
                    self.receipt(f"{stage_id}__validator", "ok", {
                        "result": validate_result,
                    })
                else:
                    checkpoint = {
                        "stage_2_integrate": Q.AFTER_STAGE_2,
                        "stage_2b_portfolio_balance": Q.AFTER_STAGE_2B,
                        "stage_3_tag": Q.AFTER_STAGE_3,
                        "stage_4_package": Q.AFTER_STAGE_4,
                    }[stage_id]
                    if self.run_customer_qc(
                        checkpoint,
                        {
                            "stage_id": stage_id,
                            "work_dir": self.work_dir(),
                            "script_result": script_result,
                        },
                        final=stage_id == "stage_4_package",
                    ):
                        raise RuntimeError(f"{checkpoint} 存在客户级 HARD QC 失败")
                duration_seconds = round(
                    max(0.0, time.perf_counter() - stage_started),
                    3,
                )
                self.update_stage_status(
                    stage_id,
                    DONE,
                    duration_seconds=duration_seconds,
                )
                message = (
                    f"阶段 {stage_id} 已通过脚本和检测"
                    if stage_id == "stage_1_standardize"
                    else f"阶段 {stage_id} 程序执行完成"
                )
                self.emit("INFO", "STAGE_DONE", message, stage=stage_id)
            except Exception as exc:
                duration_seconds = round(
                    max(0.0, time.perf_counter() - stage_started),
                    3,
                )
                self.handle_stage_failure(
                    stage_id,
                    spec,
                    exc,
                    duration_seconds=duration_seconds,
                )
                raise

    def execute(self):
        try:
            self.run_manifest_stages()
            final = self.final_validation_result
            if final is None:
                raise RuntimeError("最终交付物未执行验收")
            self.receipt(
                "validate_final",
                "ok",
                final,
            )
            self.emit("INFO", "PIPELINE_SUCCESS", f"正式交付物已通过核验：{final['deliverable']}")
            deliverable_ref = self.run_relative_ref(final["deliverable"])
            self.write_run_result(
                status=DONE,
                next_action=R.DELIVER,
                artifact_refs=(deliverable_ref,),
                context_ref="manifest.json",
                message="全部 Pipeline、QC 和 Validator 已通过",
                summary=self.delivery_summary(),
            )
            if self.warning_events:
                bundle = self.bundle_path("WARNING")
                self.emit("INFO", "WARNING_BUNDLE_READY", f"告警任务已完成归档：{bundle}")
                self.bundle("WARNING")
            return 0
        except Exception as exc:
            with open(os.path.join(self.run_dir, "traceback.txt"), "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
            bundle = self.bundle_path("ERROR")
            self.emit("ERROR", "PIPELINE_ABORTED", f"{exc}；错误包：{bundle}")
            self.bundle("ERROR")
            if self.run_result is None:
                self.write_run_result(
                    status=ERROR,
                    next_action=R.REPORT_ERROR,
                    reason_code=R.UNKNOWN,
                    context_ref="traceback.txt",
                    message="流水线在生成结构化失败路由前中止",
                )
            return 1


def main():
    configure_console()
    ap = argparse.ArgumentParser(description="银行流水标准化正式生产编排器")
    ap.add_argument("command", choices=["run"], help="正式执行流水线")
    ap.add_argument("--client",
                    help="客户名称兼交付物归档名；未传时使用原始输入文件夹名称")
    ap.add_argument("--folder", required=True)
    ap.add_argument("--run-root", help="每次运行的独立归档目录，默认 ./runs")
    ap.add_argument("--account-type", choices=["对公", "个人", "未知"])
    ap.add_argument(
        "--file-sleep-seconds",
        type=float,
        default=0,
        help="阶段一相邻原始文件之间的暂停秒数；默认不暂停",
    )
    ap.add_argument("--parent-run-id",
                    help="可选：AI 兜底修复后重跑时，记录关联的上一轮失败 run_id")
    ap.add_argument("--rerun-reason",
                    help="可选：重跑原因，例如 ai_fallback_after_stage_failure")
    ap.add_argument("--error-bundle-mode", choices=["full", "safe"], default="full",
                    help="full 包含完整原始流水；safe 仅包含诊断信息。默认 full")
    ap.add_argument("--verbose", action="store_true", help="同时把阶段事件打印到 stdout")
    ap.add_argument("--password-attempt-increment", type=int, default=0, help=argparse.SUPPRESS)
    ap.add_argument("--ai-repair-attempt-increment", type=int, default=0, help=argparse.SUPPRESS)
    ap.add_argument("--routing-rules-snapshot", help=argparse.SUPPRESS)
    ap.add_argument("--routing-rules-sha256", help=argparse.SUPPRESS)
    ap.add_argument("--run-id", help=argparse.SUPPRESS)
    ap.add_argument("--execution-plan-key", help=argparse.SUPPRESS)
    ap.add_argument("--attach-timeout-seconds", type=float, default=600, help=argparse.SUPPRESS)
    args = ap.parse_args()
    args.client_arg_provided = bool(args.client)
    if not args.client:
        args.client = os.path.basename(os.path.abspath(args.folder).rstrip(os.sep)) or "未命名客户"
    if args.run_id:
        run_dir, claimed = claim_planned_run(args.run_root, args.run_id)
        if not claimed:
            result = wait_for_run_result(run_dir, args.attach_timeout_seconds)
            if not result:
                result = {
                    "run_id": args.run_id,
                    "status": "RUNNING",
                    "next_action": R.REPORT_ERROR,
                    "reason_code": "PIPELINE_ALREADY_RUNNING",
                    "artifact_refs": [],
                    "context_ref": "",
                    "message": "同一执行计划仍在运行；未创建重复 Run",
                    "contract_version": R.CONTRACT_VERSION,
                }
            print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
            return 0 if result.get("next_action") == R.DELIVER else 1
    runner = Runner(args)
    status = runner.execute()
    result = read_json_if_exists(runner.run_result_path, {})
    release_execution_plan(args.run_root, args.execution_plan_key, runner.run_id)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return status


if __name__ == "__main__":
    sys.exit(main())
