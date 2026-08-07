"""Coordinator 与单一 Repair Agent 的紧凑 JSON 契约。"""

from __future__ import annotations

from typing import Any, Mapping

from .models.repair import CONTRACT_VERSION, REPAIR, STAGE_ID, RepairRequest
from .protocols import normalize_protocol


NEED_REPAIR = "NEED_REPAIR"
REQUEST_USER = "REQUEST_USER"
UNSUPPORTED = "UNSUPPORTED"
MAINTAINER_REQUIRED = "MAINTAINER_REQUIRED"
CHILD_RUN_READY = "CHILD_RUN_READY"
REPAIR_RESULT_PROTOCOL = "repair-result"


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
