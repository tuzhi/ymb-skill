from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class DetectionContext:
    """Detector 运行时的字段语境。"""

    column: str = ""
    mode: str = "free_text"


@dataclass(frozen=True)
class Span:
    """规则命中的敏感片段。

    Span 只在内存中参与替换计算，API 默认不返回原文片段和精确位置，
    避免把检测过程本身变成敏感信息泄露面。
    """

    label: str
    start: int
    end: int
    text: str
    confidence: float = 1.0
    source: str = "rule"
    rule_id: str = ""


class EntityDetector(Protocol):
    """可组合的敏感实体识别器接口。"""

    def detect(self, text: str, context: DetectionContext) -> list[Span]:
        ...
