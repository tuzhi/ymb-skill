"""完整流水线执行的内存交接模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class PipelineExecutionResult:
    """一次完整流水线执行的内存聚合结果。

    轻量可序列化结果统一持久化到 pipeline_result.json；Service 路径
    直接使用这里的内存值，不回读独立的 Stage 1/QC 文件。
    """

    exit_code: int
    run_id: str
    client_name: str
    parent_run_id: str
    status: str
    file_results: Mapping[str, Any]
    stages: Mapping[str, Any]
    qc: Mapping[str, Any]
    run_result: Mapping[str, Any]
    error: str | None = None
    integration_report: Mapping[str, Any] = field(default_factory=dict)
    balance_report: Mapping[str, Any] = field(default_factory=dict)
    tag_report: Mapping[str, Any] = field(default_factory=dict)
    dataset: Mapping[str, Any] = field(default_factory=dict)
    deliverable: Mapping[str, Any] = field(default_factory=dict)

    def to_summary_dict(self) -> dict[str, Any]:
        """返回 SDK/Harness 共用的轻量终态，不包含 DataFrame 和完整业务报告。"""
        control = dict(self.run_result or {})
        return {
            **control,
            "run_id": self.run_id,
            "status": self.status,
            "client_name": self.client_name,
            "parent_run_id": self.parent_run_id,
            "exit_code": self.exit_code,
            "file_results": dict(self.file_results),
            "stages": {key: dict(value) for key, value in self.stages.items()},
            "qc": dict(self.qc),
            "deliverable": dict(self.deliverable),
            "error": self.error,
        }
