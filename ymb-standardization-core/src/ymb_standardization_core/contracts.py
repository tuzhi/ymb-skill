"""阶段一公开契约。

这些对象只稳定调用边界，不改变现有字典产物和命令行参数。
"""

from dataclasses import dataclass, field
from typing import Any, Mapping


class RouteDecision(dict):
    """可 JSON 序列化的路由决策，同时保留现有 dict 兼容性。"""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None):
        if isinstance(value, cls):
            return value
        return cls(value or {})

    @property
    def fingerprint_id(self):
        return str(self.get("fingerprint_id") or self.get("id") or "")

    @property
    def bank(self):
        return str(self.get("bank") or "")

    @property
    def account_type(self):
        return str(self.get("account_type") or "")

    @property
    def reader_id(self):
        return str(self.get("reader_id") or "")

    @property
    def confidence(self):
        return float(self.get("confidence") or (1.0 if self.get("decision") == "matched" else 0.0))

    @property
    def evidence(self):
        values = list(self.get("identity_evidence") or []) + list(self.get("columns_evidence") or [])
        return tuple(str(value) for value in values)

    @property
    def transform_ids(self):
        names = (
            "dedupe_chars", "header_merge", "repeated_header",
            "preamble_mapping", "preamble_extractors",
            "conditional_mapping", "extract_mapping", "direction_from_column",
            "drop_rows", "split_amount_balance", "amount_columns",
            "extract_patterns", "source_order",
        )
        return tuple(name for name in names if self.get(name))

    @property
    def reader_options(self):
        excluded = {
            "id", "fingerprint_id", "bank", "account_type", "reader_id",
            "decision", "confidence", "identity_evidence", "columns_evidence",
        }
        return {key: value for key, value in self.items() if key not in excluded}


@dataclass(frozen=True)
class StandardizationContext:
    """单文件标准化的稳定输入；旧 ``standardize(...)`` 入口继续兼容。"""

    path: str
    out_dir: str | None = None
    bank: str | None = None
    account_type: str | None = None
    header_row: int | None = None
    overrides: Mapping[str, str] = field(default_factory=dict)
    write_mapping: bool = True
