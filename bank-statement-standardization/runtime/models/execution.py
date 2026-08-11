"""完整流水线执行的内存交接模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class PipelineExecutionResult:
    """一次完整流水线执行的内存聚合结果。

    整体状态持久化到 pipeline_result.json；逐文件结果和 QC 分别保存在
    stage_1_results.json、qc_results.json，Service 路径直接使用这里的内存值。
    """

    exit_code: int
    run_id: str
    client_name: str
    parent_run_id: str
    status: str
    file_results: Mapping[str, Any]
    stages: Mapping[str, Any]
    stage_summaries: Mapping[str, Any]
    qc: Mapping[str, Any]
    artifacts: tuple[Mapping[str, Any], ...]
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
