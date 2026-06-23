from __future__ import annotations

import re

from .base import DetectionContext, Span


class PhoneDetector:
    """大陆手机号识别器。

    该 detector 可用于结构化单元格，也可用于附言、来源文件名等自由文本。
    """

    def detect(self, text: str, context: DetectionContext | None = None) -> list[Span]:
        return [
            Span(label="phone", start=match.start(), end=match.end(), text=match.group(0))
            for match in re.finditer(r"(?<!\d)1[3-9]\d{9}(?!\d)", text)
        ]

    @staticmethod
    def fullmatch(text: str) -> str | None:
        value = text.strip().replace(" ", "")
        if re.fullmatch(r"1[3-9]\d{9}", value):
            return value
        return None
