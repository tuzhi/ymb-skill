"""同一输入的确定性执行计划与 Run 原子认领。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import hashlib
import json
import os
import re
import time
import uuid

from .models.run_result import CONTRACT_VERSION, NextAction, RunResult
from . import result_store as RS


LOCAL_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
RUN_ID_PATTERN = re.compile(r"^\d{8}T\d{6}[+-]\d{4}-[0-9a-f]{8}$")
PLAN_KEY_PATTERN = re.compile(r"^[0-9a-f]{64}$")
PLAN_DIR_NAME = ".harness-plans"


def new_run_id() -> str:
    stamp = datetime.now(LOCAL_TZ).strftime("%Y%m%dT%H%M%S%z")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def execution_plan_key(input_path: str | os.PathLike[str]) -> str:
    return hashlib.sha256(os.path.realpath(input_path).encode("utf-8")).hexdigest()


def _run_root(value: str | os.PathLike[str] | None) -> Path:
    return Path(value or Path.cwd() / "runs").resolve()


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load_or_create_execution_plan(input_path, run_root):
    """同一工作空间、同一输入在当前执行完成前复用一个 Run。"""
    source = os.path.realpath(input_path)
    root = _run_root(run_root)
    key = execution_plan_key(source)
    plan_dir = root / PLAN_DIR_NAME
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / f"{key}.json"

    while True:
        run_id = new_run_id()
        payload = {
            "contract_version": CONTRACT_VERSION,
            "plan_key": key,
            "run_id": run_id,
            "input_path": source,
            "created_at": datetime.now(LOCAL_TZ).isoformat(),
        }
        try:
            descriptor = os.open(plan_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            try:
                existing = _read_json(plan_path, {})
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"执行计划损坏：{plan_path}") from exc
            existing_run_id = str(existing.get("run_id") or "").strip()
            if not existing_run_id or existing.get("input_path") != source:
                raise RuntimeError(f"执行计划内容无效：{plan_path}")
            return existing_run_id, key
        else:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            return run_id, key


def claim_planned_run(run_root, run_id):
    """原子认领预分配 Run；重复执行只能等待同一个 Run。"""
    if not RUN_ID_PATTERN.fullmatch(str(run_id or "")):
        raise ValueError("预分配 run_id 无效")
    root = _run_root(run_root)
    root.mkdir(parents=True, exist_ok=True)
    run_dir = root / run_id
    try:
        run_dir.mkdir()
    except FileExistsError:
        return str(run_dir), False
    return str(run_dir), True


def wait_for_run_result(run_dir, timeout_seconds, poll_seconds=0.25):
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    root = Path(run_dir)
    result_path = root / RS.PIPELINE_RESULT_FILENAME
    while time.monotonic() <= deadline:
        if result_path.is_file():
            pipeline_result = RS.load_pipeline_result(root)
            result = RunResult.from_pipeline_result(pipeline_result).to_dict()
        else:
            result = {}
        if result and result.get("next_action") != NextAction.EXECUTE_PIPELINE:
            return result
        time.sleep(poll_seconds)
    return {}


def release_execution_plan(run_root, plan_key, run_id):
    if not plan_key:
        return
    if not PLAN_KEY_PATTERN.fullmatch(str(plan_key)):
        raise ValueError("execution plan key 无效")
    plan_path = _run_root(run_root) / PLAN_DIR_NAME / f"{plan_key}.json"
    plan = _read_json(plan_path, {})
    if plan and plan.get("run_id") == run_id:
        plan_path.unlink()
