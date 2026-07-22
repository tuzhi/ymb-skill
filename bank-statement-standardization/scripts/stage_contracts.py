"""流水线阶段间的轻量公开契约。"""

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


YAML_ROUTE_FIELDS = (
    "fingerprint_id",
    "series_family",
    "router_bank",
    "yaml_match_status",
)


def yaml_route_summary(report):
    """从阶段一内存报告提取供 manifest/阶段二使用的最小路由事实。"""
    image = (report or {}).get("文件画像") or {}
    fingerprint_id = str(image.get("fingerprint_id") or "").strip()
    decision = str(image.get("decision") or "").strip()
    if decision not in {"matched", "unmatched", "ambiguous", "failed"}:
        decision = "matched" if fingerprint_id else "unmatched"
    return {
        "fingerprint_id": fingerprint_id,
        "series_family": str(image.get("series_family") or "").strip(),
        "router_bank": str(image.get("router_bank") or image.get("bank") or "未识别").strip(),
        "yaml_match_status": decision,
    }


@dataclass(frozen=True)
class IntegrationContext:
    customer: str
    inputs: tuple[str, ...]
    out_dir: str | None = None
    self_accounts: tuple[str, ...] = field(default_factory=tuple)
    file_routes: Mapping[str, Mapping[str, str]] = field(default_factory=dict)

    @classmethod
    def create(cls, customer: str, inputs: Iterable[str], out_dir=None, self_accounts=(), file_routes=None):
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
