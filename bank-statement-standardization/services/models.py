"""服务层输入输出对象。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO


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
    analysis: dict[str, Any]
    artifacts: list[dict[str, Any]]
    fallback: dict[str, Any]
    error: str | None


@dataclass(frozen=True)
class ArtifactStream:
    artifact_id: str
    filename: str
    content_type: str
    size: int
    _path: Path = field(repr=False)

    def open(self) -> BinaryIO:
        return self._path.open("rb")

    def read(self) -> bytes:
        return self._path.read_bytes()


@dataclass(frozen=True)
class RuleDraft:
    content: str
    tested: bool


@dataclass(frozen=True)
class RuleTestResult:
    run_id: str
    passed: bool
    files: list[dict[str, Any]]
    error: str | None = None


@dataclass(frozen=True)
class RuleVersion:
    version: str
    based_on: str
