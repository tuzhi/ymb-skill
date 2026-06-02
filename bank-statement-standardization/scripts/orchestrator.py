#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Production entrypoint: audit, execute, validate, and package failures."""
import argparse
import hashlib
import json
import locale
import os
import platform
import shutil
import subprocess
import sys
import traceback
import uuid
import zipfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import validate_stage as V


EXPECTED_PYTHON = (3, 8, 6)
IMPORTS = ("pandas", "openpyxl", "xlrd", "pdfplumber")


def configure_console():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def now():
    return datetime.now(timezone.utc).isoformat()


def safe_name(value):
    return "".join(c if c not in '\\/:*?"<>|' else "_" for c in value)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def inventory(folder):
    rows = []
    if not folder or not os.path.isdir(folder):
        return rows
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__"}]
        for name in sorted(files):
            path = os.path.join(root, name)
            try:
                rows.append({
                    "path": os.path.relpath(path, folder),
                    "size": os.path.getsize(path),
                    "sha256": sha256(path),
                })
            except OSError:
                rows.append({"path": os.path.relpath(path, folder), "error": "unreadable"})
    return rows


class Runner:
    def __init__(self, args):
        self.args = args
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.run_id = f"{stamp}-{uuid.uuid4().hex[:8]}"
        root = os.path.abspath(args.run_root or os.path.join(os.getcwd(), "runs"))
        self.run_dir = os.path.join(root, self.run_id)
        self.out_dir = os.path.join(self.run_dir, "artifacts")
        self.receipt_dir = os.path.join(self.run_dir, "receipts")
        self.event_path = os.path.join(self.run_dir, "events.jsonl")
        self.manifest_path = os.path.join(self.run_dir, "manifest.json")
        os.makedirs(self.out_dir, exist_ok=True)
        os.makedirs(self.receipt_dir, exist_ok=True)
        self.manifest = {
            "run_id": self.run_id,
            "status": "running",
            "mode": "production",
            "started_at": now(),
            "client": args.client,
            "input_folder": os.path.abspath(args.folder),
            "error_bundle_mode": args.error_bundle_mode,
            "python": platform.python_version(),
            "model": os.environ.get("SKILL_ACTIVE_MODEL", ""),
            "stages": [],
            "warnings": [],
            "input_inventory": inventory(args.folder),
        }
        self.write_manifest()

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

    def preflight(self):
        current = sys.version_info[:3]
        if current != EXPECTED_PYTHON:
            msg = f"需要 Python 3.8.6，当前为 {platform.python_version()}"
            if self.args.allow_python_mismatch:
                self.emit("WARNING", "PYTHON_VERSION_MISMATCH", msg)
            else:
                raise RuntimeError(msg)
        model = os.environ.get("SKILL_ACTIVE_MODEL", "")
        if self.args.require_model:
            if not model:
                raise RuntimeError(
                    f"宿主未提供 SKILL_ACTIVE_MODEL，无法核验当前模型是否为 {self.args.require_model}")
            if model.lower() != self.args.require_model.lower():
                raise RuntimeError(f"模型不符合要求：期望 {self.args.require_model}，当前 {model}")
        if self.args.install_requirements:
            req = os.path.join(os.path.dirname(os.path.dirname(__file__)), "requirements.txt")
            subprocess.run([sys.executable, "-m", "pip", "install", "-r", req], check=True)
        for name in IMPORTS:
            __import__(name)
        probe = os.path.join(self.run_dir, ".write-probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write(self.run_id)
        os.remove(probe)
        self.receipt("preflight", "ok", {
            "python": platform.python_version(),
            "imports": list(IMPORTS),
            "script_execution_challenge": self.run_id,
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
        self.emit("INFO", "PIPELINE_START", "开始执行正式流水线", command=cmd)
        cp = subprocess.run(cmd, text=True, encoding=locale.getpreferredencoding(False), errors="replace",
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with open(os.path.join(self.run_dir, "pipeline.stdout.log"), "w", encoding="utf-8") as f:
            f.write(cp.stdout)
        with open(os.path.join(self.run_dir, "pipeline.stderr.log"), "w", encoding="utf-8") as f:
            f.write(cp.stderr)
        if cp.returncode:
            raise RuntimeError(f"业务流水线失败，退出码 {cp.returncode}：{cp.stderr[-1000:] or cp.stdout[-1000:]}")
        self.receipt("package_deliverable", "ok", {"command": cmd})

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
                        zf.write(path, os.path.join("raw_inputs", os.path.relpath(path, self.args.folder)))
        return bundle

    def bundle_path(self, level):
        return os.path.join(self.run_dir, f"{self.run_id}__{level}__{self.args.error_bundle_mode}.zip")

    def execute(self):
        try:
            self.preflight()
            self.run_pipeline()
            final = self.validate()
            self.manifest.update({
                "status": "success", "finished_at": now(),
                "artifact_inventory": inventory(self.out_dir),
                "deliverable": final["deliverable"],
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
    ap.add_argument("--client", required=True)
    ap.add_argument("--folder", required=True)
    ap.add_argument("--run-root", help="每次运行的独立归档目录，默认 ./runs")
    ap.add_argument("--account-type", choices=["对公", "个人", "未知"])
    ap.add_argument("--force-name", action="store_true")
    ap.add_argument("--require-model",
                    help="可选：要求宿主通过 SKILL_ACTIVE_MODEL 提供并匹配模型 ID")
    ap.add_argument("--install-requirements", action="store_true", help="执行 pip install -r requirements.txt")
    ap.add_argument("--allow-python-mismatch", action="store_true", help="仅供迁移测试，不阻断非 3.8.6")
    ap.add_argument("--error-bundle-mode", choices=["full", "safe"], default="full",
                    help="full 包含完整原始流水；safe 仅包含诊断信息。默认 full")
    args = ap.parse_args()
    return Runner(args).execute()


if __name__ == "__main__":
    sys.exit(main())
