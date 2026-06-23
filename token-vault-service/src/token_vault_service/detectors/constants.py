from __future__ import annotations

from .base import DetectionContext, Span


SUBJECT_COLUMN_LABELS = {
    "本方名称": "subject_name",
    "本方账户": "subject_account",
    "本方账号": "subject_account",
}


class ConstantDetector:
    """本方主体、本方账户等常量字段识别器。

    常量字段来自标准化结构化列，不做猜测；单元格整体就是原码。
    例如“本方名称”直接视为本方主体，“本方账号/本方账户”直接视为本方账户。
    """

    def detect(self, text: str, context: DetectionContext) -> list[Span]:
        if context.mode != "structured_cell":
            return []
        label = SUBJECT_COLUMN_LABELS.get(context.column)
        if label is None:
            return []
        value = text.strip()
        if not value or value.lower() in {"-", "--", "无", "未知", "nan", "none", "null"}:
            return []
        return [
            Span(
                label=label,
                start=0,
                end=len(text),
                text=value,
                rule_id="constant_structured_field",
            )
        ]
