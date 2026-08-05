"""Coordinator 与单一 Repair Agent 的紧凑 JSON 契约。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .protocols import normalize_protocol, render_protocol


CONTRACT_VERSION = 1
STAGE_ID = "stage_1_standardize"
REPAIR = "repair"

NEED_REPAIR = "NEED_REPAIR"
REQUEST_USER = "REQUEST_USER"
UNSUPPORTED = "UNSUPPORTED"
MAINTAINER_REQUIRED = "MAINTAINER_REQUIRED"
CHILD_RUN_READY = "CHILD_RUN_READY"
REPAIR_RESULT_PROTOCOL = "repair-result"


@dataclass(frozen=True)
class RepairRequest:
    request_id: str
    run_id: str
    run_dir: str
    attempt: int
    role_prompt_ref: str
    input_refs: tuple[str, ...]
    failed_files: tuple[Mapping[str, Any], ...]
    repair_dir: str
    output_contract_ref: str
    fresh_session_required: bool = True
    inherit_chat_history: bool = False
    role: str = REPAIR
    stage_id: str = STAGE_ID
    contract_version: int = CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return render_protocol("repair-request", {
            "request_id": self.request_id,
            "run_id": self.run_id,
            "run_dir": self.run_dir,
            "attempt": self.attempt,
            "role": self.role,
            "role_prompt_ref": self.role_prompt_ref,
            "input_refs": list(self.input_refs),
            "failed_files": [dict(item) for item in self.failed_files],
            "repair_dir": self.repair_dir,
            "output_contract_ref": self.output_contract_ref,
            "fresh_session_required": self.fresh_session_required,
            "inherit_chat_history": self.inherit_chat_history,
            "stage_id": self.stage_id,
            "contract_version": self.contract_version,
        })


def validate_repair_payload(
    payload: Mapping[str, Any],
    request: RepairRequest,
) -> dict[str, Any]:
    value = normalize_protocol(REPAIR_RESULT_PROTOCOL, payload)
    expected = {
        "contract_version": CONTRACT_VERSION,
        "run_id": request.run_id,
        "attempt": request.attempt,
        "stage_id": STAGE_ID,
        "role": REPAIR,
    }
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise ValueError(f"Repair 输出 {key} 无效")
    statuses = {"REPAIRED", REQUEST_USER, UNSUPPORTED, MAINTAINER_REQUIRED}
    if value.get("status") not in statuses:
        raise ValueError("Repair status 无效")
    outputs = value.get("outputs")
    if not isinstance(outputs, list):
        raise ValueError("Repair outputs 必须是 array")
    if value["status"] == "REPAIRED" and not outputs:
        raise ValueError("REPAIRED 必须提交标准化 CSV")
    if value["status"] != "REPAIRED" and outputs:
        raise ValueError("非 REPAIRED 状态不得提交 outputs")
    return value
