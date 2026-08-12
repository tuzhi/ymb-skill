"""隔离 Repair Agent 的请求模型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from ..protocols import normalize_protocol, render_protocol


CONTRACT_VERSION = 1
STAGE_ID = "stage_1_standardize"
REPAIR = "repair"
REPAIR_RESULT_PROTOCOL = "repair-result"


class RepairStatus(StrEnum):
    """Repair Agent 可以返回的三种业务结果。"""

    REPAIRED = "REPAIRED"
    REQUEST_USER = "REQUEST_USER"
    MAINTAINER_REQUIRED = "MAINTAINER_REQUIRED"


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

    @property
    def identity(self) -> tuple[int, str, int, str, str]:
        """跨会话契约身份，用于阻止结果串 Run、串轮次或串角色。"""
        return self.contract_version, self.run_id, self.attempt, self.stage_id, self.role

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RepairRequest":
        """从已持久化的跨会话请求恢复，不回读 Stage 结果文件。"""
        return cls(
            request_id=str(value["request_id"]),
            run_id=str(value["run_id"]),
            run_dir=str(value["run_dir"]),
            attempt=int(value["attempt"]),
            role_prompt_ref=str(value["role_prompt_ref"]),
            input_refs=tuple(str(item) for item in value.get("input_refs") or ()),
            failed_files=tuple(dict(item) for item in value.get("failed_files") or ()),
            repair_dir=str(value["repair_dir"]),
            output_contract_ref=str(value["output_contract_ref"]),
            fresh_session_required=bool(value.get("fresh_session_required", True)),
            inherit_chat_history=bool(value.get("inherit_chat_history", False)),
            role=str(value.get("role") or REPAIR),
            stage_id=str(value.get("stage_id") or STAGE_ID),
            contract_version=int(value.get("contract_version") or CONTRACT_VERSION),
        )

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


@dataclass(frozen=True)
class RepairResult:
    """Repair Agent 的强类型输出契约。"""

    run_id: str
    attempt: int
    status: RepairStatus
    outputs: tuple[Mapping[str, Any], ...] = ()
    message: str = ""
    role: str = REPAIR
    stage_id: str = STAGE_ID
    contract_version: int = CONTRACT_VERSION

    @property
    def identity(self) -> tuple[int, str, int, str, str]:
        return self.contract_version, self.run_id, self.attempt, self.stage_id, self.role

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RepairResult":
        value = normalize_protocol(REPAIR_RESULT_PROTOCOL, payload)
        try:
            status = RepairStatus(value.get("status"))
        except ValueError as exc:
            raise ValueError("Repair status 无效") from exc
        outputs = value.get("outputs")
        if not isinstance(outputs, list):
            raise ValueError("Repair outputs 必须是 array")
        if status is RepairStatus.REPAIRED and not outputs:
            raise ValueError("REPAIRED 必须提交标准化 CSV")
        if status is not RepairStatus.REPAIRED and outputs:
            raise ValueError("非 REPAIRED 状态不得提交 outputs")
        return cls(
            run_id=str(value.get("run_id") or ""),
            attempt=int(value.get("attempt") or 0),
            status=status,
            outputs=tuple(dict(item) if isinstance(item, Mapping) else item for item in outputs),
            message=str(value.get("message") or ""),
            role=str(value.get("role") or ""),
            stage_id=str(value.get("stage_id") or ""),
            contract_version=int(value.get("contract_version") or 0),
        )

    def validate_for(self, request: RepairRequest) -> None:
        if self.identity != request.identity:
            raise ValueError("Repair 输出契约身份与请求不一致")

    def with_outputs(self, outputs: list[Mapping[str, Any]]) -> "RepairResult":
        return RepairResult(
            run_id=self.run_id,
            attempt=self.attempt,
            status=self.status,
            outputs=tuple(outputs),
            message=self.message,
            role=self.role,
            stage_id=self.stage_id,
            contract_version=self.contract_version,
        )

    def to_dict(self) -> dict[str, Any]:
        return render_protocol(REPAIR_RESULT_PROTOCOL, {
            "contract_version": self.contract_version,
            "run_id": self.run_id,
            "attempt": self.attempt,
            "stage_id": self.stage_id,
            "role": self.role,
            "status": self.status.value,
            "outputs": [dict(item) for item in self.outputs],
            "message": self.message,
        })
