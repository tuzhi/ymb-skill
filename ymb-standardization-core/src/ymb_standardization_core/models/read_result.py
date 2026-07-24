"""Reader 统一返回模型。"""

from dataclasses import dataclass
from typing import Any

from ymb_standardization_core.contracts import RouteDecision


@dataclass
class ReadResult:
    """原始载体读取结果，不包含标准字段归一和业务推断。"""

    kind: str
    preamble: str
    rows: list[list[Any]]
    route_info: RouteDecision
