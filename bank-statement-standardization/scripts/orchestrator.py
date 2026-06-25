#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Production entrypoint: audit, execute, validate, and package failures."""
import argparse
import csv
import glob
import hashlib
import json
import locale
import os
import platform
import re
import shutil
import subprocess
import sys
import traceback
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import integrate as I
import package_deliverable as P
import portfolio_balance as PB
import standardize as S
import tag as T
import validate_stage as V


IMPORTS = ("pandas", "openpyxl", "xlrd", "pdfplumber")
DONE = "DONE"
ERROR = "ERROR"
MAX_AI_FALLBACK_RETRY = 2
LOCAL_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
MAX_JOINED_CLIENT_NAME_PART_LEN = 20
TECHNICAL_ARCHIVE_NAMES = {"tokenized_batch_bundle", "batch", "bundle"}
SOURCE_SNAPSHOT_EXCLUDE_DIRS = {".git", ".claude", "__pycache__", "dist", "runs", "testdata", "tests", "build"}
SOURCE_SNAPSHOT_EXTS = {".py", ".md", ".json", ".csv", ".txt"}
DEFAULT_SKILL_NAME = "bank-statement-standardization"
DEFAULT_SKILL_VERSION = "0.0.0"
TOKEN_VAULT_SECRET_FILENAMES = {"token_vault_manifest.json"}
MANIFEST_TEMPLATE_RELATIVE_PATH = os.path.join("assets", "manifest.template.json")


def configure_console():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def now():
    return datetime.now(LOCAL_TZ).isoformat()


def safe_name(value):
    return "".join(c if c not in '\\/:*?"<>|' else "_" for c in value)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json_if_exists(path, default=None):
    if not os.path.isfile(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def manifest_template_path(skill_dir):
    return os.path.join(skill_dir, MANIFEST_TEMPLATE_RELATIVE_PATH)


def load_skill_metadata(skill_dir):
    data = read_json_if_exists(manifest_template_path(skill_dir), {})
    skill = data.get("skill") if isinstance(data, dict) else {}
    if not isinstance(skill, dict):
        skill = {}
    return {
        "name": str(skill.get("name") or DEFAULT_SKILL_NAME),
        "version": str(skill.get("version") or DEFAULT_SKILL_VERSION),
    }


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


def run_git_capture(args, cwd):
    try:
        cp = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:
        return ""
    if cp.returncode:
        return ""
    return cp.stdout.strip()


def collect_skill_source_snapshot(skill_dir):
    skill_dir = os.path.abspath(skill_dir)
    file_sha256 = {}
    for root, dirs, files in os.walk(skill_dir):
        dirs[:] = [
            d for d in dirs
            if d not in SOURCE_SNAPSHOT_EXCLUDE_DIRS and not d.endswith(".egg-info")
        ]
        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            if ext not in SOURCE_SNAPSHOT_EXTS:
                continue
            path = os.path.join(root, filename)
            rel = normalize_relpath(os.path.relpath(path, skill_dir))
            file_sha256[rel] = sha256(path)

    status = run_git_capture(["status", "--short", "--", skill_dir], skill_dir)
    modified_files = []
    for line in status.splitlines():
        if not line.strip():
            continue
        rel = normalize_relpath(line[3:].strip().strip('"'))
        marker = "bank-statement-standardization/"
        if marker in rel:
            rel = rel.split(marker, 1)[1]
        if rel.split("/", 1)[0] in SOURCE_SNAPSHOT_EXCLUDE_DIRS:
            continue
        modified_files.append(rel)

    return {
        "git_commit": run_git_capture(["rev-parse", "HEAD"], skill_dir),
        "dirty": bool(modified_files),
        "modified_files": modified_files,
        "file_sha256": dict(sorted(file_sha256.items())),
    }


def load_parent_run_context(run_root, parent_run_id):
    if not parent_run_id:
        return None
    parent_dir = os.path.abspath(os.path.join(run_root, parent_run_id))
    if not os.path.isdir(parent_dir):
        raise RuntimeError(f"parent run 不存在：{parent_dir}")

    stage_manifest = read_json_if_exists(os.path.join(parent_dir, "manifest.json"), {})
    run_manifest = read_json_if_exists(os.path.join(parent_dir, "run_manifest.json"), {})
    inherited_fallbacks = []
    for stage_id, spec in stage_manifest.items():
        if not str(stage_id).startswith("stage_"):
            continue
        if not isinstance(spec, dict):
            continue
        if not (spec.get("ai_fallback_used") or spec.get("ai_fallback_dir") or spec.get("ai_fallback_artifacts")):
            continue
        inherited_fallbacks.append({
            "stage": stage_id,
            "name": spec.get("name", ""),
            "script": spec.get("script", ""),
            "validator": spec.get("validator", ""),
            "parent_status": spec.get("status", ""),
            "parent_fallback_dir": spec.get("ai_fallback_dir", ""),
            "parent_fallback_artifacts": spec.get("ai_fallback_artifacts", []),
        })

    return {
        "parent_run_id": parent_run_id,
        "parent_run_dir": parent_dir,
        "parent_status": run_manifest.get("status", ""),
        "parent_error": run_manifest.get("error", ""),
        "inherited_fallbacks": inherited_fallbacks,
    }


def parse_balance(value):
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "none"}:
        return None
    text = text.replace(",", "").replace("，", "").replace("￥", "").replace("¥", "")
    try:
        return float(text)
    except ValueError:
        return None


def is_archive_name_candidate(name):
    text = str(name or "").strip()
    if not text or text.lower() in {"nan", "none"}:
        return False
    if text in {"称", "户名", "账户名称", "本方名称"}:
        return False
    if any(mark in text for mark in ("/", "\\", "*", "＊")):
        return False
    if re.search(r"\d{6,}", text):
        return False
    if re.fullmatch(r"[A-Za-z0-9_\- ]+", text) and not is_english_person_name(text):
        return False
    return True


def is_english_person_name(text):
    """允许有账户/余额证据支撑的英文个人姓名作为归档名。"""
    parts = [part for part in re.split(r"\s+", str(text or "").strip()) if part]
    if len(parts) < 2:
        return False
    return all(re.fullmatch(r"[A-Za-z][A-Za-z.'-]*", part) for part in parts)


def is_technical_archive_name(name):
    text = str(name or "").strip().lower()
    if not text:
        return False
    if text in TECHNICAL_ARCHIVE_NAMES:
        return True
    return text.startswith(("tokenized_", "batch_", "bundle_")) or text.endswith("_batch_bundle")


def rank_client_name_candidates(work_roots):
    """按本方账户聚合归档名候选；只使用有本方账户和账户余额的标准化行。"""
    by_account = {}
    for work_root in work_roots:
        for root, _, files in os.walk(work_root):
            for filename in files:
                if not filename.endswith("__standardized.csv"):
                    continue
                path = os.path.join(root, filename)
                with open(path, "r", encoding="utf-8-sig", newline="") as f:
                    for row in csv.DictReader(f):
                        name = (row.get("本方名称") or "").strip()
                        account = (row.get("本方账户") or "").strip()
                        balance = parse_balance(row.get("账户余额"))
                        if not account or balance is None or not is_archive_name_candidate(name):
                            continue
                        source = (row.get("来源文件名") or filename).strip()
                        bucket = by_account.setdefault(account, {})
                        stats = bucket.setdefault(name, {"rows": 0, "sources": set()})
                        stats["rows"] += 1
                        if source:
                            stats["sources"].add(source)

    candidates = {}
    for account, names in by_account.items():
        if not names:
            continue
        name, account_stats = max(names.items(), key=lambda item: (item[1]["rows"], len(item[1]["sources"]), item[0]))
        stats = candidates.setdefault(name, {"accounts": set(), "rows": 0, "sources": set()})
        stats["accounts"].add(account)
        stats["rows"] += account_stats["rows"]
        stats["sources"].update(account_stats["sources"])

    ranked = []
    for name, stats in candidates.items():
        account_count = len(stats["accounts"])
        source_count = len(stats["sources"])
        row_count = stats["rows"]
        ranked.append({
            "name": name,
            "account_count": account_count,
            "balance_row_count": row_count,
            "source_file_count": source_count,
            "score": account_count * 100 + source_count * 10 + min(row_count, 99),
        })
    return sorted(ranked, key=lambda item: (-item["score"], -item["account_count"], -item["source_file_count"], item["name"]))


def joined_short_client_name_candidates(ranked):
    names = []
    for item in ranked:
        name = item.get("name", "").strip()
        if name and len(name) <= MAX_JOINED_CLIENT_NAME_PART_LEN and name not in names:
            names.append(name)
    return "_".join(names) if names else None


def infer_unique_client_name(work_roots):
    ranked = rank_client_name_candidates(work_roots)
    if not ranked:
        return None, ranked
    if len(ranked) == 1:
        return ranked[0]["name"], ranked
    top, second = ranked[0], ranked[1]
    if top["score"] >= second["score"] * 2 and top["account_count"] > second["account_count"]:
        return top["name"], ranked
    return joined_short_client_name_candidates(ranked), ranked


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
        self.skill_metadata = load_skill_metadata(self.skill_dir)
        stamp = datetime.now(LOCAL_TZ).strftime("%Y%m%dT%H%M%S%z")
        self.run_id = f"{stamp}-{uuid.uuid4().hex[:8]}"
        root = resolve_run_root(args.run_root)
        self.run_root = root
        self.run_dir = os.path.join(root, self.run_id)
        self.out_dir = os.path.join(self.run_dir, "artifacts")
        self.receipt_dir = os.path.join(self.run_dir, "receipts")
        self.event_path = os.path.join(self.run_dir, "events.jsonl")
        self.stage_manifest_path = os.path.join(self.run_dir, "manifest.json")
        self.manifest_path = os.path.join(self.run_dir, "run_manifest.json")
        os.makedirs(self.out_dir, exist_ok=True)
        os.makedirs(self.receipt_dir, exist_ok=True)
        self.original_input_folder = os.path.abspath(args.folder)
        self.input_dir, self.input_snapshot_details = prepare_input_snapshot(self.original_input_folder, self.run_dir)
        self.args.folder = self.input_dir
        self.copy_stage_manifest()
        self.manifest = {
            "run_id": self.run_id,
            "status": "running",
            "mode": "production",
            "started_at": now(),
            "skill": self.skill_metadata["name"],
            "skill_version": self.skill_metadata["version"],
            "client": args.client,
            "client_arg_provided": args.client_arg_provided,
            "client_confirmed": args.client_explicit,
            "original_input_folder": self.original_input_folder,
            "input_folder": self.input_dir,
            "error_bundle_mode": args.error_bundle_mode,
            "python": platform.python_version(),
            "model": os.environ.get("SKILL_ACTIVE_MODEL", ""),
            "parent_run_id": args.parent_run_id or "",
            "rerun_reason": args.rerun_reason or "",
            "parent_run": load_parent_run_context(root, args.parent_run_id) if args.parent_run_id else None,
            "skill_source": collect_skill_source_snapshot(self.skill_dir),
            "stages": [],
            "warnings": [],
            "input_inventory": inventory(self.input_dir),
            "input_snapshot": self.input_snapshot_details,
        }
        self.write_manifest()

    def copy_stage_manifest(self):
        if not os.path.isfile(self.template_manifest_path):
            raise RuntimeError(f"缺少 skill 资源模板 {MANIFEST_TEMPLATE_RELATIVE_PATH}：{self.template_manifest_path}")
        with open(self.template_manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.write_stage_manifest(self.sanitize_stage_manifest_template(data))

    def sanitize_stage_manifest_template(self, data):
        """Reset runtime-only fields before creating a new run manifest."""
        for stage_id, spec in data.items():
            if not str(stage_id).startswith("stage_"):
                continue
            spec["ai_fallback_used"] = False
            spec["ai_fallback_dir"] = ""
            spec["ai_fallback_artifacts"] = []
            spec["started_at"] = ""
            spec["duration_seconds"] = None
            spec["status"] = ""
        return data

    def read_stage_manifest(self):
        with open(self.stage_manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def write_stage_manifest(self, data):
        with open(self.stage_manifest_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def update_stage_status(self, stage_id, status):
        data = self.read_stage_manifest()
        if stage_id not in data:
            raise RuntimeError(f"runtime manifest 缺少阶段：{stage_id}")
        data[stage_id]["status"] = status
        self.write_stage_manifest(data)

    def mark_stage_started(self, stage_id):
        data = self.read_stage_manifest()
        if stage_id not in data:
            raise RuntimeError(f"runtime manifest 缺少阶段：{stage_id}")
        if not data[stage_id].get("started_at"):
            data[stage_id]["started_at"] = now()
        self.write_stage_manifest(data)

    def mark_stage_done(self, stage_id):
        data = self.read_stage_manifest()
        if stage_id not in data:
            raise RuntimeError(f"runtime manifest 缺少阶段：{stage_id}")
        finished_at = datetime.now(LOCAL_TZ)
        started_at = data[stage_id].get("started_at")
        if started_at:
            started_dt = datetime.fromisoformat(started_at)
            data[stage_id]["duration_seconds"] = round((finished_at - started_dt).total_seconds(), 3)
        else:
            data[stage_id]["duration_seconds"] = None
        self.write_stage_manifest(data)

    def mark_stage_ai_fallback_used(self, stage_id, fallback_dir, artifacts=None):
        data = self.read_stage_manifest()
        if stage_id not in data:
            raise RuntimeError(f"runtime manifest 缺少阶段：{stage_id}")
        data[stage_id]["ai_fallback_used"] = True
        data[stage_id]["ai_fallback_dir"] = fallback_dir
        data[stage_id]["ai_fallback_artifacts"] = artifacts or data[stage_id].get("ai_fallback_artifacts", [])
        self.write_stage_manifest(data)

    def first_pending_stage(self):
        data = self.read_stage_manifest()
        for stage_id, spec in data.items():
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
        print(f"[{level}][{code}] {message}")
        if level == "WARNING":
            self.manifest["warnings"].append(event)
        self.write_manifest()

    def receipt(self, stage, status, details=None):
        row = {"stage": stage, "status": status, "at": now(), "details": details or {}}
        path = os.path.join(self.receipt_dir, f"{len(self.manifest['stages']) + 1:02d}-{stage}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(row, f, ensure_ascii=False, indent=2)
        self.manifest["stages"].append(row)
        self.write_manifest()

    def work_dir(self):
        return os.path.join(self.out_dir, "_工作区", safe_name(self.args.client))

    def fallback_dir(self, stage_id):
        return os.path.join(self.run_dir, "fallback", stage_id)

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

    def write_manifest_mapping(self, mapping_path, source_relpath, row_count):
        # 现有阶段一 validator 要求每个标准化 CSV 有对应 mapping JSON。
        # Token Vault 已完成标准化时，这里只补最小运行产物，供本次 orch 工作区验收使用。
        mapping = {
            "file_image": {
                "matched_template": "manifest_declared_standardized_input",
                "source": "token_vault_service",
            },
            "standardization_stats": {
                "transaction_count": row_count,
                "amount_structure": "already_standardized",
            },
            "source_manifest_output": normalize_relpath(str(source_relpath)),
            "note": "由 Token Vault manifest 声明为已完成阶段一标准化，orchestrator 仅复制并补充最小映射报告。",
        }
        with open(mapping_path, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=2)

    def stage_1_from_declared_standardized_manifest(self, work):
        # 快速完成阶段一：消费上游 manifest 声明的标准化产物，
        # 然后让原状态机按 receipt/validator/DONE 的路径继续进入阶段二。
        upstream_manifest, manifest_path = self.load_declared_standardized_manifest()
        if not upstream_manifest:
            return None

        processed = []
        for relpath, source_path in self.resolve_declared_standardized_outputs(upstream_manifest, manifest_path):
            target_csv = os.path.join(work, os.path.basename(source_path))
            shutil.copy2(source_path, target_csv)
            stem = os.path.splitext(os.path.basename(target_csv))[0]
            if stem.endswith("__standardized"):
                stem = stem[:-len("__standardized")]
            target_mapping = os.path.join(work, f"{stem}__mapping.json")
            row_count = self.count_csv_rows(target_csv)
            self.write_manifest_mapping(target_mapping, relpath, row_count)
            processed.append({
                "input": source_path,
                "csv": target_csv,
                "mapping": target_mapping,
                "rows": row_count,
            })

        self.apply_upstream_archive_metadata(upstream_manifest)
        self.manifest["skipped_inputs"] = []
        self.manifest["upstream_manifest"] = {
            "path": manifest_path,
            "schema_version": upstream_manifest.get("schema_version", ""),
            "producer": upstream_manifest.get("producer", ""),
            "archive_id": upstream_manifest.get("archive_id", ""),
            "client_alias": upstream_manifest.get("client_alias", ""),
            "archive_name_present": bool(str(upstream_manifest.get("archive_name") or "").strip()),
            "stage_1_standardize": self.declared_stage_1(upstream_manifest),
        }
        self.write_manifest()
        return {
            "mode": "manifest_declared_standardized_input",
            "upstream_manifest": self.manifest["upstream_manifest"],
            "processed_files": len(processed),
            "standardized": processed,
        }

    def apply_upstream_archive_metadata(self, upstream_manifest):
        if self.args.client_explicit:
            return
        alias = str(upstream_manifest.get("client_alias") or "").strip()
        if alias and not is_technical_archive_name(alias):
            self.set_client_name(
                alias,
                code="CLIENT_ALIAS_FROM_UPSTREAM_MANIFEST",
                message=f"已从上游 Token Vault manifest 接收非敏归档别名：{alias}",
            )
            return
        if is_technical_archive_name(self.args.client):
            self.emit(
                "WARNING",
                "CLIENT_NAME_UNCONFIRMED",
                f"上游 manifest 未提供可用的非敏归档别名，当前仅保留技术目录名：{self.args.client}",
                upstream_archive_name_present=bool(str(upstream_manifest.get("archive_name") or "").strip()),
                upstream_archive_id=upstream_manifest.get("archive_id", ""),
            )

    def set_client_name(self, client, *, code, message, extra=None, warning=False):
        old_client = self.args.client
        if client == old_client:
            return
        old_work = os.path.join(self.out_dir, "_工作区", safe_name(old_client))
        new_work = os.path.join(self.out_dir, "_工作区", safe_name(client))
        old_xlsx = os.path.join(self.out_dir, f"{old_client}_已清洗_待分析.xlsx")
        new_xlsx = os.path.join(self.out_dir, f"{client}_已清洗_待分析.xlsx")
        if os.path.exists(old_xlsx):
            os.replace(old_xlsx, new_xlsx)
        if os.path.isdir(old_work) and os.path.abspath(old_work) != os.path.abspath(new_work):
            os.replace(old_work, new_work)
        self.args.client = client
        self.manifest["client"] = client
        self.write_manifest()
        payload = extra or {}
        if warning:
            self.emit("WARNING", code, message, **payload)
        else:
            self.emit("INFO", code, message, **payload)

    def stage_1_standardize(self):
        work = self.work_dir()
        if os.path.isdir(work):
            shutil.rmtree(work)
        os.makedirs(work, exist_ok=True)

        declared_result = self.stage_1_from_declared_standardized_manifest(work)
        if declared_result:
            return declared_result

        raw_files, skipped = S.screen_files(sorted(glob.glob(os.path.join(self.args.folder, "*"))))
        self.manifest["skipped_inputs"] = [{"name": n, "reason": w} for n, w in skipped]
        self.write_manifest()
        if not raw_files:
            detail = "；".join(f"{n}（{w}）" for n, w in skipped) or "目录内无候选文件"
            raise RuntimeError(f"客户「{self.args.client}」无可处理的银行流水文件。已跳过：{detail}")

        processed = []
        for path in raw_files:
            try:
                csv_path, json_path, report = S.standardize(
                    path,
                    out_dir=work,
                    customer=self.args.client if self.args.force_name else None,
                    account_type=self.args.account_type,
                    force_customer=self.args.force_name,
                )
                processed.append({
                    "input": path,
                    "csv": csv_path,
                    "mapping": json_path,
                    "rows": report["标准化统计"]["交易笔数"],
                })
            except S.NotABankStatement as exc:
                skipped.append((os.path.basename(path), exc.reason))
            except Exception as exc:
                raise RuntimeError(f"标准化失败：{os.path.basename(path)}：{exc}") from exc

        if not processed:
            detail = "；".join(f"{n}（{w}）" for n, w in skipped) or "无成功标准化文件"
            raise RuntimeError(f"阶段一没有生成标准化产物：{detail}")

        self.manifest["skipped_inputs"] = [{"name": n, "reason": w} for n, w in skipped]
        self.write_manifest()
        if self.args.client_arg_provided and not self.args.client_explicit:
            self.emit(
                "WARNING",
                "UNCONFIRMED_CLIENT_ARG_USED",
                f"--client 未经 --client-confirmed 确认，仅作为临时归档名参与启动：{self.args.client}",
                client_arg=self.args.client,
            )
        old_work = work
        if not self.args.client_explicit:
            self.refine_client_name()
        new_work = self.work_dir()
        if os.path.abspath(old_work) != os.path.abspath(new_work):
            for row in processed:
                for key in ("csv", "mapping"):
                    if row[key].startswith(old_work):
                        row[key] = new_work + row[key][len(old_work):]
        return {"processed_files": len(processed), "standardized": processed}

    def stage_2_integrate(self):
        int_csv, int_json, report = I.integrate(self.args.client, [self.work_dir()], out_dir=self.work_dir())
        overview = report["客户整合概览"]
        return {
            "integrated_csv": int_csv,
            "integrated_report": int_json,
            "integrated_rows": overview["整合交易数"],
            "accounts": overview["整合账户数"],
        }

    def stage_2b_portfolio_balance(self):
        int_csv = self.latest_artifact("*__整合流水.csv")
        daily_csv, report_json, report = PB.run(int_csv, out_dir=self.work_dir())
        return {
            "portfolio_csv": daily_csv,
            "portfolio_report": report_json,
            "accounts": report["数据范围"]["账户数"],
            "warning_accounts": report["账户余额校验"]["预警账户数"],
        }

    def stage_3_tag(self):
        int_csv = self.latest_artifact("*__整合流水.csv")
        rules = os.path.join(self.skill_dir, "assets", "tag_rules.csv")
        tag_csv, tag_json, report = T.tag(int_csv, rules, out_dir=self.work_dir())
        summary = report["标签梳理概览"]
        return {
            "tagged_csv": tag_csv,
            "tag_report": tag_json,
            "tagged_rows": summary["交易总数"],
            "rule_hit_rate": summary["规则命中率"],
        }

    def _subjects_from_standardized(self):
        import pandas as pd
        names = []
        for path in sorted(glob.glob(os.path.join(self.work_dir(), "*__standardized.csv"))):
            try:
                df = pd.read_csv(path, dtype=str, encoding="utf-8-sig", usecols=lambda col: col == "本方名称")
            except Exception:
                continue
            if "本方名称" not in df:
                continue
            for name in df["本方名称"].dropna().astype(str).str.strip().unique():
                if name and name.lower() not in {"nan", "none"} and name not in names:
                    names.append(name)
        return [(name, []) for name in (names or [self.args.client])]

    def stage_4_package(self):
        import pandas as pd
        work = self.work_dir()
        int_csv = self.latest_artifact("*__整合流水.csv")
        int_json = self.latest_artifact("*__整合报告.json")
        tag_csv = self.latest_artifact("*__打标流水.csv")
        tag_json = self.latest_artifact("*__标签报告.json")
        with open(int_json, encoding="utf-8") as f:
            irep = json.load(f)
        with open(tag_json, encoding="utf-8") as f:
            srep = json.load(f)
        tagged = pd.read_csv(tag_csv, dtype=str)
        skipped = [(row.get("name", ""), row.get("reason", "")) for row in self.manifest.get("skipped_inputs", [])]
        deliverable = P._finalize(
            self.args.client,
            int_csv,
            tagged,
            irep,
            srep,
            work,
            self.out_dir,
            self._subjects_from_standardized(),
            skipped,
        )
        return {"deliverable": deliverable}

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
            return V.validate_standardize(work)
        if stage_id == "stage_2_integrate":
            return V.validate_integrate(work)
        if stage_id == "stage_2b_portfolio_balance":
            return V.validate_portfolio(work)
        if stage_id == "stage_3_tag":
            integrated = V.validate_integrate(work)
            return V.validate_tag(work, integrated_rows=integrated["integrated_rows"])
        if stage_id == "stage_4_package":
            tag = V.validate_tag(work)
            return V.validate_final(self.out_dir, self.args.client, tagged_rows=tag["tagged_rows"])
        raise RuntimeError(f"未知阶段验证器：{stage_id}")

    def preflight(self):
        model = os.environ.get("SKILL_ACTIVE_MODEL", "")
        if self.args.require_model:
            if not model:
                raise RuntimeError(
                    f"宿主未提供 SKILL_ACTIVE_MODEL，无法核验当前模型是否为 {self.args.require_model}")
            if model.lower() != self.args.require_model.lower():
                raise RuntimeError(f"模型不符合要求：期望 {self.args.require_model}，当前 {model}")
        missing = []
        for name in IMPORTS:
            try:
                __import__(name)
            except ImportError:
                missing.append(name)
        if missing:
            raise RuntimeError(
                "缺少 Python 依赖："
                + ", ".join(missing)
                + "；请先执行 python -m pip install -r requirements.txt 后重试。"
                  "该文件使用兼容范围约束，适配 Python 3.11+ / 3.13。"
            )
        probe = os.path.join(self.run_dir, ".write-probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write(self.run_id)
        os.remove(probe)
        self.receipt("preflight", "ok", {
            "python": platform.python_version(),
            "imports": list(IMPORTS),
            "script_execution_challenge": self.run_id,
            "input_snapshot": self.input_snapshot_details,
        })

    def run_pipeline(self):
        script = os.path.join(os.path.dirname(__file__), "package_deliverable.py")
        cmd = [
            sys.executable, script, "--client", self.args.client,
            "--folder", os.path.abspath(self.args.folder), "--out-dir", self.out_dir,
        ]
        if self.args.account_type:
            cmd += ["--account-type", self.args.account_type]
        if self.args.force_name:
            cmd.append("--force-name")
        if not self.args.client_explicit:
            cmd.append("--infer-client-name")
        self.emit("INFO", "PIPELINE_START", "开始执行正式流水线", command=cmd)
        lines = []
        cp = subprocess.Popen(
            cmd,
            text=True,
            encoding=locale.getpreferredencoding(False),
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        for line in cp.stdout:
            lines.append(line)
            print(line, end="", flush=True)
        returncode = cp.wait()
        output = "".join(lines)
        with open(os.path.join(self.run_dir, "pipeline.stdout.log"), "w", encoding="utf-8") as f:
            f.write(output)
        with open(os.path.join(self.run_dir, "pipeline.stderr.log"), "w", encoding="utf-8") as f:
            f.write("stderr 已合并到 pipeline.stdout.log，以便实时显示。\n")
        if returncode:
            raise RuntimeError(f"业务流水线失败，退出码 {returncode}：{output[-1000:]}")
        self.receipt("package_deliverable", "ok", {"command": cmd})

    def refine_client_name(self):
        """客户名未经确认时，用主流程已解析出的唯一户名替换暂存目录名。"""
        if self.args.client_explicit:
            return
        old_client = self.args.client
        old_work = os.path.join(self.out_dir, "_工作区", safe_name(old_client))
        work_roots = [old_work] if os.path.isdir(old_work) else []
        if not work_roots:
            work_roots = [
                path for path in glob.glob(os.path.join(self.out_dir, "_工作区", "*"))
                if os.path.isdir(path)
            ]
        client, ranked = infer_unique_client_name(work_roots)
        if not client:
            if ranked:
                self.emit("WARNING", "CLIENT_NAME_AMBIGUOUS",
                          f"未形成唯一高分归档名候选，保留输入文件夹名作为归档名：{old_client}",
                          candidate_scores=ranked[:10])
            return
        if client == old_client:
            return
        self.set_client_name(
            client,
            code="CLIENT_NAME_INFERRED",
            message=f"已从流水识别归档名：{client}",
        )

    def validate(self):
        work = os.path.join(self.out_dir, "_工作区", safe_name(self.args.client))
        checks = [
            ("validate_stage_1", lambda: V.validate_standardize(work)),
            ("validate_stage_2", lambda: V.validate_integrate(work)),
            ("validate_stage_2b", lambda: V.validate_portfolio(work)),
        ]
        integrated_rows = None
        for stage, fn in checks:
            result = fn()
            if stage == "validate_stage_2":
                integrated_rows = result["integrated_rows"]
            self.receipt(stage, "ok", result)
        tag = V.validate_tag(work, integrated_rows=integrated_rows)
        self.receipt("validate_stage_3", "ok", tag)
        final = V.validate_final(self.out_dir, self.args.client, tagged_rows=tag["tagged_rows"])
        self.receipt("validate_final", "ok", final)
        return final

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

    def handle_stage_failure(self, stage_id, spec, exc):
        fallback_dir = self.fallback_dir(stage_id)
        os.makedirs(fallback_dir, exist_ok=True)
        fallback_request = {
            "stage": stage_id,
            "name": spec.get("name", ""),
            "script": spec.get("script", ""),
            "validator": spec.get("validator", ""),
            "ai_fallback_refs": spec.get("ai_fallback_refs", []),
            "error": str(exc),
            "created_at": now(),
            "instruction": "AI 兜底产生的脚本、补丁、参数文件必须保存在本目录，并追加记录到运行时 manifest 的 ai_fallback_artifacts。",
        }
        fallback_request_path = os.path.join(fallback_dir, "fallback_request.json")
        with open(fallback_request_path, "w", encoding="utf-8") as f:
            json.dump(fallback_request, f, ensure_ascii=False, indent=2)
        fallback_artifacts = ["fallback_request.json"]
        self.emit(
            "WARNING",
            "AI_FALLBACK_REQUIRED",
            f"阶段 {stage_id} 失败，需要 AI 按 ai_fallback_refs 读取兜底资料；未产生确定性修正前不自动重跑",
            stage=stage_id,
            ai_fallback_refs=spec.get("ai_fallback_refs", []),
            ai_fallback_dir=fallback_dir,
            max_retry=MAX_AI_FALLBACK_RETRY,
            error=str(exc),
        )
        self.mark_stage_ai_fallback_used(stage_id, fallback_dir, fallback_artifacts)
        self.update_stage_status(stage_id, ERROR)
        self.receipt(stage_id, "error", {
            "script": spec.get("script", ""),
            "orchestrator_handler": self.stage_handler_name(stage_id),
            "validator": spec.get("validator", ""),
            "ai_fallback_refs": spec.get("ai_fallback_refs", []),
            "ai_fallback_used": True,
            "ai_fallback_dir": fallback_dir,
            "ai_fallback_artifacts": fallback_artifacts,
            "error": str(exc),
        })

    def run_manifest_stages(self):
        while True:
            stage_id, spec = self.first_pending_stage()
            if not stage_id:
                return
            self.mark_stage_started(stage_id)
            self.emit(
                "INFO",
                "STAGE_START",
                f"开始阶段 {stage_id}：{spec.get('name', '')}",
                stage=stage_id,
                name=spec.get("name", ""),
                script=spec.get("script", ""),
                validator=spec.get("validator", ""),
                status=spec.get("status", ""),
            )
            try:
                script_result = self.execute_stage_script(stage_id)
                self.receipt(stage_id, "script_ok", {
                    "script": spec.get("script", ""),
                    "orchestrator_handler": self.stage_handler_name(stage_id),
                    "result": script_result,
                })
                validate_result = self.validate_stage(stage_id)
                self.receipt(f"{stage_id}__validator", "ok", {
                    "validator": spec.get("validator", ""),
                    "result": validate_result,
                })
                self.mark_stage_done(stage_id)
                self.update_stage_status(stage_id, DONE)
                self.emit("INFO", "STAGE_DONE", f"阶段 {stage_id} 已通过脚本和检测", stage=stage_id)
            except Exception as exc:
                self.handle_stage_failure(stage_id, spec, exc)
                raise

    def execute(self):
        try:
            self.preflight()
            self.run_manifest_stages()
            tag = V.validate_tag(self.work_dir())
            final = V.validate_final(self.out_dir, self.args.client, tagged_rows=tag["tagged_rows"])
            self.receipt("validate_final", "ok", final)
            self.manifest.update({
                "status": "success", "finished_at": now(),
                "artifact_inventory": inventory(self.out_dir),
                "deliverable": final["deliverable"],
                "stage_manifest": self.stage_manifest_path,
            })
            self.write_manifest()
            self.emit("INFO", "PIPELINE_SUCCESS", f"正式交付物已通过核验：{final['deliverable']}")
            if self.manifest["warnings"]:
                bundle = self.bundle_path("WARNING")
                self.manifest["warning_bundle"] = bundle
                self.write_manifest()
                self.emit("INFO", "WARNING_BUNDLE_READY", f"告警任务已完成归档：{bundle}")
                self.bundle("WARNING")
            return 0
        except Exception as exc:
            with open(os.path.join(self.run_dir, "traceback.txt"), "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
            self.manifest.update({
                "status": "error", "finished_at": now(), "error": str(exc),
                "artifact_inventory": inventory(self.out_dir),
            })
            self.write_manifest()
            bundle = self.bundle_path("ERROR")
            self.manifest["error_bundle"] = bundle
            self.write_manifest()
            self.emit("ERROR", "PIPELINE_ABORTED", f"{exc}；错误包：{bundle}")
            self.bundle("ERROR")
            return 1


def main():
    configure_console()
    ap = argparse.ArgumentParser(description="银行流水标准化正式生产编排器")
    ap.add_argument("command", choices=["run"], help="正式执行流水线")
    ap.add_argument("--client",
                    help="临时归档名；只有同时传 --client-confirmed 时才视为人工确认名")
    ap.add_argument("--client-confirmed", action="store_true",
                    help="确认 --client 是人工核实后的真实归档名；未传时仍会优先使用流水证据推断")
    ap.add_argument("--folder", required=True)
    ap.add_argument("--run-root", help="每次运行的独立归档目录，默认 ./runs")
    ap.add_argument("--account-type", choices=["对公", "个人", "未知"])
    ap.add_argument("--force-name", action="store_true")
    ap.add_argument("--require-model",
                    help="可选：要求宿主通过 SKILL_ACTIVE_MODEL 提供并匹配模型 ID")
    ap.add_argument("--parent-run-id",
                    help="可选：AI 兜底修复后重跑时，记录关联的上一轮失败 run_id")
    ap.add_argument("--rerun-reason",
                    help="可选：重跑原因，例如 ai_fallback_after_stage_failure")
    ap.add_argument("--error-bundle-mode", choices=["full", "safe"], default="full",
                    help="full 包含完整原始流水；safe 仅包含诊断信息。默认 full")
    args = ap.parse_args()
    args.client_arg_provided = bool(args.client)
    if args.client_confirmed and not args.client_arg_provided:
        ap.error("--client-confirmed 必须与 --client 同时使用")
    if args.force_name and not args.client_confirmed:
        ap.error("--force-name 会影响本方名称兜底/覆盖，必须同时传 --client 和 --client-confirmed")
    args.client_explicit = bool(args.client_confirmed)
    if not args.client:
        args.client = os.path.basename(os.path.abspath(args.folder).rstrip(os.sep)) or "未命名客户"
    return Runner(args).execute()


if __name__ == "__main__":
    sys.exit(main())
