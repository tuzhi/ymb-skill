from __future__ import annotations

import re

from .base import DetectionContext, Span


class IdNumberDetector:
    """中国大陆身份证号识别器。

    只输出通过生日范围和校验位校验的号码，避免把普通 18 位数字误判为身份证。
    """

    def detect(self, text: str, context: DetectionContext | None = None) -> list[Span]:
        spans: list[Span] = []
        for match in re.finditer(r"(?<![0-9A-Za-z])\d{17}[\dXx](?![0-9A-Za-z])", text):
            value = match.group(0)
            if valid_id_number(value):
                spans.append(
                    Span(
                        label="id_number",
                        start=match.start(),
                        end=match.end(),
                        text=value,
                    )
                )
        return spans


def valid_id_number(value: str) -> bool:
    if len(value) != 18:
        return False
    birthday = value[6:14]
    year = int(birthday[:4])
    month = int(birthday[4:6])
    day = int(birthday[6:8])
    if not (1900 <= year <= 2099 and 1 <= month <= 12 and 1 <= day <= 31):
        return False
    factors = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    checks = "10X98765432"
    total = sum(int(value[index]) * factors[index] for index in range(17))
    return checks[total % 11] == value[-1].upper()
