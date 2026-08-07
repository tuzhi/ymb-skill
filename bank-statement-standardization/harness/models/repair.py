"""隔离 Repair Agent 的请求模型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..protocols import render_protocol


CONTRACT_VERSION = 1
STAGE_ID = "stage_1_standardize"
REPAIR = "repair"


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
