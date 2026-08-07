"""Stage 之间传递的轻量内存模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class IntegrationContext:
    customer: str
    inputs: tuple[str, ...]
    out_dir: str | None = None
    self_accounts: tuple[str, ...] = field(default_factory=tuple)
    file_routes: Mapping[str, Mapping[str, str]] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        customer: str,
        inputs: Iterable[str],
        out_dir=None,
        self_accounts=(),
        file_routes=None,
    ):
        return cls(
            customer=customer,
            inputs=tuple(inputs),
            out_dir=out_dir,
            self_accounts=tuple(self_accounts or ()),
            file_routes=dict(file_routes or {}),
        )


class StageResult(dict):
    """可直接写入 receipt 的阶段结果，并携带明确的阶段身份。"""

    def __init__(self, stage_id: str, values: dict[str, Any] | None = None, **kwargs):
        super().__init__(values or {}, **kwargs)
        self.stage_id = stage_id
