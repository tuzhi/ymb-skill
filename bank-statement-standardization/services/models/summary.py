"""业务汇总中的稳定分布项 DTO。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LabelDistributionDTO:
    """一级标签及对应交易笔数。"""

    label: str
    transaction_count: int


@dataclass(frozen=True)
class FieldDistributionDTO:
    """规则命中字段及对应交易笔数。"""

    field: str
    transaction_count: int
