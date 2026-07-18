"""流水线阶段间的轻量公开契约。"""

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class IntegrationContext:
    customer: str
    inputs: tuple[str, ...]
    out_dir: str | None = None
    self_accounts: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def create(cls, customer: str, inputs: Iterable[str], out_dir=None, self_accounts=()):
        return cls(
            customer=customer,
            inputs=tuple(inputs),
            out_dir=out_dir,
            self_accounts=tuple(self_accounts or ()),
        )


class StageResult(dict):
    """可直接写入 receipt 的阶段结果，并携带明确的阶段身份。"""

    def __init__(self, stage_id: str, values: dict[str, Any] | None = None, **kwargs):
        super().__init__(values or {}, **kwargs)
        self.stage_id = stage_id
