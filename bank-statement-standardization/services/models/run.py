"""Runner、Repair 和历史恢复使用的内部 Run DTO。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RunReference:
    run_id: str
    parent_run_id: str
    status: str = "RUNNING"


@dataclass(frozen=True)
class RunDetail:
    run_id: str
    parent_run_id: str
    client_name: str
    status: str
    files: list[dict[str, Any]]
    stages: dict[str, Any]
    stage_1_results: dict[str, Any]
    qc: dict[str, Any]
    artifacts: list[dict[str, Any]]
    run_result: dict[str, Any]
    error: str | None
