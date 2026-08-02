#!/usr/bin/env python3
"""确定性 Fallback Coordinator 命令行入口。"""

from __future__ import annotations

from pathlib import Path
import argparse
import json
import sys


SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from harness.coordinator import FallbackCoordinator  # noqa: E402
from harness.contracts import CHILD_RUN_READY  # noqa: E402
from runtime.run_result import atomic_write_json  # noqa: E402
from services.statement_service import StatementService  # noqa: E402


def _read_object(path: str) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("输入必须是 JSON object")
    return value


def _start_child_run(coordinator: FallbackCoordinator, request_ref: str) -> dict:
    """幂等创建 Child Run，并直接返回新的紧凑 RunResult。"""
    launch_path = coordinator.attempt_root / "child_run_launch.json"
    service = StatementService(coordinator.run_dir.parent, submit=lambda execute: execute())
    if launch_path.is_file():
        launch = _read_object(str(launch_path))
        if launch.get("parent_run_id") != coordinator.run_id:
            raise RuntimeError("child_run_launch parent_run_id 不匹配")
        if launch.get("request_ref") != request_ref:
            raise RuntimeError("child_run_launch request_ref 不匹配")
        child_run_id = str(launch.get("child_run_id") or "")
        if not child_run_id:
            raise RuntimeError("child_run_launch 缺少 child_run_id")
    else:
        reference = service.start_child_run_from_request(coordinator.run_id, request_ref)
        child_run_id = reference.run_id
        atomic_write_json(launch_path, {
            "contract_version": 1,
            "parent_run_id": coordinator.run_id,
            "request_ref": request_ref,
            "child_run_id": child_run_id,
        })
    detail = service.get_run(child_run_id)
    if not detail.run_result:
        raise RuntimeError(f"Child Run 尚未生成 RunResult：{child_run_id}")
    return dict(detail.run_result)


def _advance(coordinator: FallbackCoordinator) -> dict:
    outcome = coordinator.next()
    if outcome.get("status") != CHILD_RUN_READY:
        return outcome
    return _start_child_run(coordinator, str(outcome["child_run_request"]))


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 1 Fallback 确定性协调器")
    subparsers = parser.add_subparsers(dest="command", required=True)
    next_parser = subparsers.add_parser("next")
    next_parser.add_argument("--run-dir", required=True)
    submit_parser = subparsers.add_parser("submit")
    submit_parser.add_argument("--run-dir", required=True)
    submit_parser.add_argument("--role", choices=["fallback", "audit"], required=True)
    submit_parser.add_argument("--session-id", required=True)
    submit_parser.add_argument("--result", required=True)
    submit_parser.add_argument("--usage")
    child_parser = subparsers.add_parser("run-child")
    child_parser.add_argument("--run-dir", required=True)
    child_parser.add_argument("--request", required=True)
    password_parser = subparsers.add_parser("retry-password")
    password_parser.add_argument("--run-dir", required=True)
    password_parser.add_argument("--file", required=True, help="Run input 内的相对路径")
    password_parser.add_argument(
        "--password-stdin",
        action="store_true",
        required=True,
        help="从 stdin 读取一行密码，避免密码出现在命令行",
    )
    args = parser.parse_args()

    if args.command == "next":
        coordinator = FallbackCoordinator(args.run_dir)
        result = _advance(coordinator)
    elif args.command == "submit":
        coordinator = FallbackCoordinator(args.run_dir)
        coordinator.submit(
            args.role,
            session_id=args.session_id,
            payload=_read_object(args.result),
            usage=_read_object(args.usage) if args.usage else {},
        )
        result = _advance(coordinator)
    elif args.command == "run-child":
        run_dir = Path(args.run_dir).resolve()
        service = StatementService(run_dir.parent, submit=lambda execute: execute())
        reference = service.start_child_run_from_request(run_dir.name, args.request)
        detail = service.get_run(reference.run_id)
        result = {
            "run_id": reference.run_id,
            "parent_run_id": reference.parent_run_id,
            "status": detail.status,
            "run_result": detail.run_result,
        }
    else:
        password = sys.stdin.readline().rstrip("\r\n")
        if not password:
            raise ValueError("密码不能为空")
        run_dir = Path(args.run_dir).resolve()
        service = StatementService(run_dir.parent, submit=lambda execute: execute())
        reference = service.start_run(
            None,
            [],
            parent_run_id=run_dir.name,
            file_passwords={args.file: password},
        )
        detail = service.get_run(reference.run_id)
        if not detail.run_result:
            raise RuntimeError(f"密码 Child Run 尚未生成 RunResult：{reference.run_id}")
        result = dict(detail.run_result)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
