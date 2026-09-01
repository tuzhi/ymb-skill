"""BI 分析 Service 对外请求与结果 DTO。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class ServiceError:
    code: str
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BiAnalysisRequest:
    """同步 BI 分析请求。"""

    bi_run_id: str
    statement_run_id: str
    standardized_file_path: str
    client_name: str
    whitelist_path: str = ""
    loans_path: str = ""
    new_loan: tuple[float, float, int] | None = None
    dataset: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AIAnalysisSummaryDTO:
    """对外稳定输出的 AI 分析摘要。"""

    effective_inflow: float = 0.0
    effective_outflow: float = 0.0
    annual_sales_median: float = 0.0
    attention_counterparty_count: int = 0
    night_sensitive_expense_count: int = 0
    data_quality_score: float = 0.0
    data_quality_grade: str = ""


@dataclass(frozen=True)
class BiAnalysisResult:
    """可由上层直接入库的 BI 分析结果。"""

    bi_run_id: str
    statement_run_id: str
    status: str
    artifacts: Mapping[str, str] = field(default_factory=dict)
    ai_analysis_summary: AIAnalysisSummaryDTO = field(
        default_factory=AIAnalysisSummaryDTO
    )
    chart_data: Mapping[str, Any] = field(default_factory=dict)
    error: ServiceError | None = None
