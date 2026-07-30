"""银行流水单文件标准化内核。

该包只负责把原始文件解析并映射为统一标准字段；状态机、整合、打标和交付物组装仍留在外层脚本。
"""

from .contracts import RouteDecision, StandardizationContext
from .core import (
    NotABankStatement,
    SourceFormatQualityError,
    YamlRouteRequiredError,
    standardize,
    standardize_file,
)
from .models import ReadResult

__all__ = [
    "NotABankStatement",
    "SourceFormatQualityError",
    "YamlRouteRequiredError",
    "RouteDecision",
    "ReadResult",
    "StandardizationContext",
    "standardize",
    "standardize_file",
]
