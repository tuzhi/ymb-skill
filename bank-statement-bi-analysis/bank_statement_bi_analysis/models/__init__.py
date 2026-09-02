"""BI Service DTO 的稳定导入边界。"""

from .analysis import (
    AIAnalysisSummaryDTO,
    BiAnalysisRequest,
    BiAnalysisResult,
    ServiceError,
)
from .charts import ChartBundle, ChartSpec, OmittedChart, SeriesSpec

__all__ = [
    "AIAnalysisSummaryDTO",
    "BiAnalysisRequest",
    "BiAnalysisResult",
    "ServiceError",
    "ChartBundle",
    "ChartSpec",
    "OmittedChart",
    "SeriesSpec",
]
