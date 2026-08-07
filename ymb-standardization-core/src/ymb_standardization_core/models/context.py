"""单文件标准化的输入模型。"""

from dataclasses import dataclass, field
from typing import Any, Mapping


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
    route_rules: Any = None
