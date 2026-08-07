"""BI Service DTO 的稳定导入边界。"""

from .analysis import BiAnalysisRequest, BiAnalysisResult, ServiceError

__all__ = [
    "BiAnalysisRequest",
    "BiAnalysisResult",
    "ServiceError",
]
