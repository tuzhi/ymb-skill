#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WorkBuddy inline Skill entrypoint with no third-party imports."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence


CONTRACT_VERSION = 1
REQUEST_USER = "REQUEST_USER"
INPUT_SOURCE_INVALID = "INPUT_SOURCE_INVALID"


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


def build_orchestrator_argv(input_path: Path, run_root: Path) -> list[str]:
    orchestrator = Path(__file__).resolve().with_name("orchestrator.py")
    return [
        sys.executable,
        str(orchestrator),
        "run",
        "--folder",
        str(input_path),
        "--run-root",
        str(run_root),
    ]


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
    os.execv(sys.executable, build_orchestrator_argv(input_path, run_root))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
