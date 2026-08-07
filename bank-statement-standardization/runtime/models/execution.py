"""完整流水线执行的内存交接模型。"""

from __future__ import annotations

from dataclasses import dataclass
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
