"""流水线最终结果及失败路由模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


CONTRACT_VERSION = 1

DELIVER = "DELIVER"
REQUEST_USER = "REQUEST_USER"
EXECUTE_PIPELINE = "EXECUTE_PIPELINE"
NEED_REPAIR = "NEED_REPAIR"
MAINTAINER_REQUIRED = "MAINTAINER_REQUIRED"
REPORT_ERROR = "REPORT_ERROR"
NEXT_ACTIONS = {
    DELIVER,
    REQUEST_USER,
    EXECUTE_PIPELINE,
    NEED_REPAIR,
    MAINTAINER_REQUIRED,
    REPORT_ERROR,
}


@dataclass(frozen=True)
class FailureRoute:
    reason_code: str
    next_action: str
    message: str


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: str
    next_action: str
    reason_code: str = ""
    artifact_refs: tuple[str, ...] = field(default_factory=tuple)
    context_ref: str = ""
    message: str = ""
    action: Mapping[str, Any] = field(default_factory=dict)
    summary: Mapping[str, Any] = field(default_factory=dict)
    contract_version: int = CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.next_action not in NEXT_ACTIONS:
            raise ValueError(f"next_action 无效：{self.next_action}")
        if not isinstance(self.action, Mapping):
            raise ValueError("action 必须是 object")
        if not isinstance(self.summary, Mapping):
            raise ValueError("summary 必须是 object")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["artifact_refs"] = list(self.artifact_refs)
        if not self.action:
            value.pop("action")
        if not self.summary:
            value.pop("summary")
        return value
