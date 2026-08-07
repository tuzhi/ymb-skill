"""流水标准化 Service 对外请求与结果 DTO。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class ServiceError:
    code: str
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InputFile:
    """标准化服务可读取的单个输入文件。"""

    file_name: str
    file_path: str
    file_md5: str = ""


@dataclass(frozen=True)
class StandardizationRequest:
    """同步标准化请求；Run ID 仍由 Python Runner 生成。"""

    client_name: str | None
    files: tuple[InputFile, ...]
    parent_run_id: str | None = None
    remove_file_ids: tuple[str, ...] = ()
    file_passwords: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class StandardizationResult:
    """同步标准化的完整、可入库结果。"""

    run_id: str
    parent_run_id: str
    client_name: str
    status: str
    rules_version: str
    file_results: list[dict[str, Any]]
    stages: dict[str, Any]
    qc: dict[str, Any]
    stage_summaries: dict[str, Any]
    artifacts: list[dict[str, Any]]
    run_result: dict[str, Any]
    error: ServiceError | None
