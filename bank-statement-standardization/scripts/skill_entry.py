#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WorkBuddy inline Skill planner with no third-party imports."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence


CONTRACT_VERSION = 1
REQUEST_USER = "REQUEST_USER"
EXECUTE_PIPELINE = "EXECUTE_PIPELINE"
INPUT_SOURCE_INVALID = "INPUT_SOURCE_INVALID"
LOCAL_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
PLAN_DIR_NAME = ".harness-plans"
PIPELINE_TIMEOUT_MS = 600_000


def request_user(message: str) -> dict[str, object]:
    return {
        "run_id": "",
        "status": "BLOCKED",
        "next_action": REQUEST_USER,
        "reason_code": INPUT_SOURCE_INVALID,
        "artifact_refs": [],
        "context_ref": "",
        "message": message,
        "contract_version": CONTRACT_VERSION,
    }


def print_result(result: dict[str, object]) -> None:
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


def new_run_id() -> str:
    stamp = datetime.now(LOCAL_TZ).strftime("%Y%m%dT%H%M%S%z")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def plan_key(input_path: Path) -> str:
    return hashlib.sha256(str(input_path).encode("utf-8")).hexdigest()


def load_or_create_plan(input_path: Path, run_root: Path) -> tuple[str, str]:
    """同一工作空间、同一输入在执行完成前复用一个计划。"""
    key = plan_key(input_path)
    plan_dir = run_root / PLAN_DIR_NAME
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / f"{key}.json"

    while True:
        run_id = new_run_id()
        payload = {
            "contract_version": CONTRACT_VERSION,
            "plan_key": key,
            "run_id": run_id,
            "input_path": str(input_path),
            "created_at": datetime.now(LOCAL_TZ).isoformat(),
        }
        try:
            descriptor = os.open(plan_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            try:
                existing = json.loads(plan_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raise RuntimeError(f"执行计划损坏：{plan_path}")
            existing_run_id = str(existing.get("run_id") or "").strip()
            if not existing_run_id or existing.get("input_path") != str(input_path):
                raise RuntimeError(f"执行计划内容无效：{plan_path}")
            return existing_run_id, key
        else:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            return run_id, key


def build_orchestrator_argv(
    input_path: Path,
    run_root: Path,
    run_id: str,
    execution_plan_key: str,
) -> list[str]:
    orchestrator = Path(__file__).resolve().with_name("orchestrator.py")
    return [
        sys.executable,
        str(orchestrator),
        "run",
        "--folder",
        str(input_path),
        "--run-root",
        str(run_root),
        "--run-id",
        run_id,
        "--execution-plan-key",
        execution_plan_key,
    ]


def shell_command(argv: Sequence[str]) -> str:
    values = [str(value) for value in argv]
    if os.name == "nt":
        values = [value.replace("\\", "/") for value in values]
    return shlex.join(values)


def execution_plan(input_path: Path, run_root: Path) -> dict[str, object]:
    run_id, execution_plan_key = load_or_create_plan(input_path, run_root)
    command_argv = build_orchestrator_argv(
        input_path,
        run_root,
        run_id,
        execution_plan_key,
    )
    return {
        "run_id": run_id,
        "status": "READY",
        "next_action": EXECUTE_PIPELINE,
        "reason_code": "",
        "artifact_refs": [],
        "context_ref": "",
        "message": "执行计划已生成；请按 action 运行一次 Pipeline 并等待最终 RunResult",
        "action": {
            "handler": "orchestrator",
            "operation": "run",
            "command": shell_command(command_argv),
            "argv": command_argv,
            "timeout_ms": PIPELINE_TIMEOUT_MS,
        },
        "contract_version": CONTRACT_VERSION,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--input", default="")
    parser.add_argument("--run-root", default="./runs")
    args = parser.parse_args(argv)

    raw_input = str(args.input or "").strip()
    if not raw_input or raw_input == "$ARGUMENTS":
        print_result(request_user("请提供客户流水文件、目录或 zip 路径"))
        return 0

    input_path = Path(raw_input).expanduser().resolve()
    if not input_path.exists():
        print_result(request_user(f"输入路径不存在：{input_path}"))
        return 0
    if not input_path.is_dir() and input_path.suffix.lower() != ".zip":
        print_result(request_user("请输入流水目录或 zip 文件"))
        return 0

    run_root = Path(args.run_root).expanduser().resolve()
    try:
        print_result(execution_plan(input_path, run_root))
    except RuntimeError as exc:
        result = request_user(str(exc))
        result["next_action"] = "REPORT_ERROR"
        result["reason_code"] = "EXECUTION_PLAN_INVALID"
        print_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
