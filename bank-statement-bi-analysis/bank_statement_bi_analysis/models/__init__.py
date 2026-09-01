"""BI Service DTO 的稳定导入边界。"""

from .analysis import (
    AIAnalysisSummaryDTO,
    BiAnalysisRequest,
    BiAnalysisResult,
    ServiceError,
)

__all__ = [
    "AIAnalysisSummaryDTO",
    "BiAnalysisRequest",
    "BiAnalysisResult",
    "ServiceError",
]
