"""Coordinator、Fallback 与 Audit 的紧凑 JSON 契约。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .protocols import normalize_protocol, render_protocol


CONTRACT_VERSION = 1
STAGE_ID = "stage_1_standardize"
FALLBACK = "fallback"
AUDIT = "audit"

NEED_FALLBACK = "NEED_FALLBACK"
NEED_AUDIT = "NEED_AUDIT"
REQUEST_USER = "REQUEST_USER"
UNSUPPORTED = "UNSUPPORTED"
MAINTAINER_REQUIRED = "MAINTAINER_REQUIRED"
CHILD_RUN_READY = "CHILD_RUN_READY"
STOPPED = "STOPPED"
ROLE_RESULT_PROTOCOLS = {
    FALLBACK: "fallback-result",
    AUDIT: "audit-result",
}


@dataclass(frozen=True)
class RoleTask:
    task_id: str
    run_id: str
    attempt: int
    role: str
    role_prompt_ref: str
    input_refs: tuple[str, ...]
    output_path: str
    output_contract_ref: str
    fresh_session_required: bool = True
    inherit_chat_history: bool = False
    stage_id: str = STAGE_ID
    contract_version: int = CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return render_protocol("role-task", {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "attempt": self.attempt,
            "role": self.role,
            "role_prompt_ref": self.role_prompt_ref,
            "input_refs": list(self.input_refs),
            "output_path": self.output_path,
            "output_contract_ref": self.output_contract_ref,
            "fresh_session_required": self.fresh_session_required,
            "inherit_chat_history": self.inherit_chat_history,
            "stage_id": self.stage_id,
            "contract_version": self.contract_version,
        })


def _validate_common(payload: Mapping[str, Any], task: RoleTask) -> None:
    expected = {
        "contract_version": CONTRACT_VERSION,
        "run_id": task.run_id,
        "stage_id": STAGE_ID,
        "role": task.role,
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise ValueError(f"{task.role} 输出 {key} 无效")


def _string_list(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{name} 必须是字符串数组")
    return list(value)


def validate_role_payload(role: str, payload: Mapping[str, Any], task: RoleTask) -> dict[str, Any]:
    protocol = ROLE_RESULT_PROTOCOLS.get(role)
    if protocol is None:
        raise ValueError(f"未知角色：{role}")
    value = normalize_protocol(protocol, payload)
    _validate_common(value, task)
    affected = _string_list(value.get("affected_file_ids", []), "affected_file_ids")

    if role == FALLBACK:
        statuses = {
            "REQUEST_USER",
            "UNSUPPORTED",
            "MAINTAINER_REQUIRED",
            "REPAIR_PROPOSED",
            "INSUFFICIENT_EVIDENCE",
        }
        if value.get("status") not in statuses:
            raise ValueError("Fallback status 无效")
        if not isinstance(value.get("classification"), str):
            raise ValueError("Fallback classification 必须是字符串")
        if value["status"] == "REPAIR_PROPOSED":
            if not affected:
                raise ValueError("修复建议必须声明 affected_file_ids")
            if value.get("repair_type") != "ROUTING_RULE_DRAFT":
                raise ValueError("客户 Run 当前只接受 ROUTING_RULE_DRAFT")
            if not isinstance(value.get("repair_payload"), Mapping):
                raise ValueError("repair_payload 必须是 object")
    elif role == AUDIT:
        if value.get("status") not in {"ACCEPTED", "REJECTED", "INCONCLUSIVE"}:
            raise ValueError("Audit status 无效")
    return value
