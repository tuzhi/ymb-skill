#!/usr/bin/env python3
"""单 Repair Agent 的确定性 Coordinator 命令行入口。"""

from __future__ import annotations

from pathlib import Path
import argparse
import json
import sys


SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from harness.contracts import CHILD_RUN_READY  # noqa: E402
from harness.coordinator import RepairCoordinator  # noqa: E402
from runtime.result_store import atomic_write_json  # noqa: E402
from services.statement_service import StatementService  # noqa: E402


def _read_object(path: str) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("输入必须是 JSON object")
    return value


def _start_child_run(coordinator: RepairCoordinator, outcome: dict) -> dict:
    """幂等创建 Child Run，并返回 Child Run 的紧凑 RunResult。"""
    snapshot_ref = str(outcome.get("repair_result_ref") or "")
    snapshot = (coordinator.run_dir / snapshot_ref).resolve()
    checksum = str(outcome.get("repair_result_sha256") or "")
    launch_path = coordinator.repair_root / "child_run_launch.json"
    service = StatementService(coordinator.run_dir.parent, submit=lambda execute: execute())
    if launch_path.is_file():
        launch = _read_object(str(launch_path))
        if launch.get("parent_run_id") != coordinator.run_id:
            raise RuntimeError("child_run_launch parent_run_id 不匹配")
        if launch.get("repair_result_sha256") != checksum:
            raise RuntimeError("child_run_launch Repair checksum 不匹配")
        child_run_id = str(launch.get("child_run_id") or "")
        if not child_run_id:
            raise RuntimeError("child_run_launch 缺少 child_run_id")
    else:
        reference = service._start_run(
            None,
            [],
            parent_run_id=coordinator.run_id,
            repair_result_snapshot=snapshot,
            repair_result_sha256=checksum,
        )
        child_run_id = reference.run_id
        atomic_write_json(launch_path, {
            "contract_version": 1,
            "parent_run_id": coordinator.run_id,
            "repair_result_ref": snapshot_ref,
            "repair_result_sha256": checksum,
            "child_run_id": child_run_id,
        })
    detail = service._get_run(child_run_id)
    if not detail.run_result:
        raise RuntimeError(f"Child Run 尚未生成 RunResult：{child_run_id}")
    result = dict(detail.run_result)
    if result.get("next_action") == "NEED_REPAIR":
        child_dir = coordinator.run_dir.parent / child_run_id
        decision = RepairCoordinator(child_dir).decision()
        print(
            f"[COORDINATOR][NEED_REPAIR] run_id={decision['run_id']} "
            f"attempt={decision['attempt']}",
            file=sys.stderr,
        )
        return decision
    return result


def _advance(coordinator: RepairCoordinator, outcome: dict) -> dict:
    if outcome.get("status") != CHILD_RUN_READY:
        return outcome
    print(
        f"[COORDINATOR][CHILD_RUN_READY] run_id={coordinator.run_id} "
        f"attempt={getattr(coordinator, 'attempt', outcome.get('attempt', ''))}",
        file=sys.stderr,
    )
    return _start_child_run(coordinator, outcome)


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 1 Repair 确定性协调器")
    subparsers = parser.add_subparsers(dest="command", required=True)
    submit_parser = subparsers.add_parser("submit")
    submit_parser.add_argument("--run-dir", required=True)
    submit_parser.add_argument("--request-id", required=True)
    submit_parser.add_argument("--session-id", required=True)
    submit_parser.add_argument("--result", required=True)
    submit_parser.add_argument("--usage")
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

    if args.command == "submit":
        coordinator = RepairCoordinator(args.run_dir)
        result_path = Path(args.result).resolve()
        if coordinator.run_dir in result_path.parents:
            raise ValueError("--result 必须位于 Run 目录外")
        outcome = coordinator.submit(
            request_id=args.request_id,
            session_id=args.session_id,
            payload=_read_object(str(result_path)),
            usage=_read_object(args.usage) if args.usage else {},
        )
        print(
            f"[COORDINATOR][SUBMIT] run_id={coordinator.run_id} "
            f"attempt={coordinator.attempt} status={outcome['status']}",
            file=sys.stderr,
        )
        result = _advance(coordinator, outcome)
    else:
        password = sys.stdin.readline().rstrip("\r\n")
        if not password:
            raise ValueError("密码不能为空")
        run_dir = Path(args.run_dir).resolve()
        service = StatementService(run_dir.parent, submit=lambda execute: execute())
        reference = service._start_run(
            None,
            [],
            parent_run_id=run_dir.name,
            file_passwords={args.file: password},
        )
        detail = service._get_run(reference.run_id)
        if not detail.run_result:
            raise RuntimeError(f"密码 Child Run 尚未生成 RunResult：{reference.run_id}")
        result = dict(detail.run_result)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
