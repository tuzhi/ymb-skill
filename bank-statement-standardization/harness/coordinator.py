"""一个确定性 Coordinator 调度两个隔离 AI 会话。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
import hashlib
import json

from runtime.run_result import atomic_write_json

from .contracts import (
    AUDIT,
    CHILD_RUN_READY,
    CONTRACT_VERSION,
    FALLBACK,
    MAINTAINER_REQUIRED,
    NEED_AUDIT,
    NEED_FALLBACK,
    REQUEST_USER,
    STAGE_ID,
    STOPPED,
    UNSUPPORTED,
    RoleTask,
    validate_role_payload,
)
from .policy_gate import evaluate_routing_draft


REQUEST_CONTRACT = "bank-statement-standardization.fallback-request/v2"
SKILL_ROOT = Path(__file__).resolve().parents[1]
ROLE_PROMPTS = {
    FALLBACK: SKILL_ROOT / "roles" / "fallback.md",
    AUDIT: SKILL_ROOT / "roles" / "audit.md",
}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"缺少文件：{path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 必须是 object：{path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class FallbackCoordinator:
    """从不可变 artifact 推导状态，不维护共享角色聊天历史。"""

    def __init__(self, run_dir: str | Path):
        self.run_dir = Path(run_dir).resolve()
        self.request_path = self.run_dir / "fallback" / STAGE_ID / "fallback_request.json"
        self.request = _read_json(self.request_path)
        self._validate_request()
        self.run_id = self.run_dir.name
        self.attempt = int(self.request["attempt"])
        self.attempt_root = self.request_path.parent / f"attempt-{self.attempt:02d}"
        self.receipt_root = self.attempt_root / "session-receipts"

    def _validate_request(self) -> None:
        if self.request.get("contract_version") != REQUEST_CONTRACT:
            raise ValueError("fallback request contract_version 无效")
        if self.request.get("run_id") != self.run_dir.name:
            raise ValueError("fallback request 与 Run 目录不一致")
        if self.request.get("stage_id") != STAGE_ID:
            raise ValueError("Coordinator 只允许处理 Stage 1")
        if self.request.get("next_action") != "AI_FALLBACK":
            raise ValueError("当前 Run 不允许启动 AI Fallback")
        evidence = self.run_dir / str(self.request.get("evidence_ref") or "")
        if self.run_dir not in evidence.resolve().parents or not evidence.is_file():
            raise ValueError("fallback evidence_ref 无效")

    def _relative(self, path: Path) -> str:
        resolved = path.resolve()
        if self.run_dir not in resolved.parents:
            raise ValueError(f"路径不在 Run 内：{path}")
        return resolved.relative_to(self.run_dir).as_posix()

    def _task(self, role: str, input_refs: list[str], output_path: Path) -> RoleTask:
        prompt_path = ROLE_PROMPTS.get(role)
        if prompt_path is None or not prompt_path.is_file():
            raise FileNotFoundError(f"缺少角色协议：{prompt_path}")
        role_prompt_ref = prompt_path.resolve().as_posix()
        seed = {
            "run_id": self.run_id,
            "attempt": self.attempt,
            "role": role,
            "role_prompt_ref": role_prompt_ref,
            "input_refs": input_refs,
            "output_path": self._relative(output_path),
        }
        return RoleTask(
            task_id=f"{self.run_id}:{self.attempt}:{role}:{_canonical_hash(seed)[:16]}",
            run_id=self.run_id,
            attempt=self.attempt,
            role=role,
            role_prompt_ref=role_prompt_ref,
            input_refs=tuple(input_refs),
            output_path=self._relative(output_path),
        )

    def _persist_task(self, path: Path, task: RoleTask) -> None:
        value = task.to_dict()
        if path.is_file():
            if _read_json(path) != value:
                raise RuntimeError(f"不可变 task 内容冲突：{path}")
            return
        atomic_write_json(path, value)

    def _role_paths(self, role: str) -> tuple[Path, Path, RoleTask]:
        if role == FALLBACK:
            output = self.attempt_root / "fallback_result.json"
            task_path = self.attempt_root / "fallback_task.json"
            input_refs = [
                self._relative(self.request_path),
                str(self.request["evidence_ref"]),
            ]
        elif role == AUDIT:
            output = self.attempt_root / "audit_result.json"
            task_path = self.attempt_root / "audit_task.json"
            input_refs = [
                str(self.request["evidence_ref"]),
                self._relative(self.attempt_root / "fallback_result.json"),
                self._relative(self.attempt_root / "policy_gate.json"),
            ]
        else:
            raise ValueError(f"未知角色：{role}")
        return task_path, output, self._task(role, input_refs, output)

    def _validated_result(self, role: str) -> dict[str, Any]:
        task_path, output_path, task = self._role_paths(role)
        if _read_json(task_path) != task.to_dict():
            raise RuntimeError(f"{role} task 已变化")
        receipt = _read_json(self.receipt_root / f"{role}.json")
        if receipt.get("task_id") != task.task_id or receipt.get("role") != role:
            raise RuntimeError(f"{role} receipt 与 task 不匹配")
        if receipt.get("output_path") != task.output_path:
            raise RuntimeError(f"{role} receipt 输出路径不匹配")
        if receipt.get("output_sha256") != _sha256(output_path):
            raise RuntimeError(f"{role} 输出 checksum 不匹配")
        return validate_role_payload(role, _read_json(output_path), task)

    def _outcome(self, status: str, **extra: Any) -> dict[str, Any]:
        value = {
            "contract_version": CONTRACT_VERSION,
            "run_id": self.run_id,
            "attempt": self.attempt,
            "status": status,
        }
        value.update(extra)
        return value

    def next(self) -> dict[str, Any]:
        if self.attempt > int(self.request.get("max_attempts") or 0):
            return self._outcome(STOPPED, message="AI 修复次数已达上限")

        fallback_task_path, fallback_output, fallback_task = self._role_paths(FALLBACK)
        if not fallback_output.is_file():
            self._persist_task(fallback_task_path, fallback_task)
            return self._outcome(
                NEED_FALLBACK,
                role=FALLBACK,
                task=fallback_task.to_dict(),
            )
        fallback = self._validated_result(FALLBACK)
        request_ids = {
            str(item.get("file_id") or "")
            for item in self.request.get("files", [])
            if isinstance(item, Mapping) and item.get("file_id")
        }
        affected = set(fallback.get("affected_file_ids") or [])
        if not affected <= request_ids:
            raise ValueError("Fallback affected_file_ids 越界")

        status = fallback["status"]
        if status == "REQUEST_USER":
            return self._outcome(REQUEST_USER, message=str(fallback.get("user_request") or ""))
        if status == "UNSUPPORTED":
            return self._outcome(UNSUPPORTED, message=str(fallback.get("reason") or ""))
        if status in {"MAINTAINER_REQUIRED", "INSUFFICIENT_EVIDENCE"}:
            return self._outcome(MAINTAINER_REQUIRED, message=str(fallback.get("reason") or ""))

        gate_path = self.attempt_root / "policy_gate.json"
        gate = (
            _read_json(gate_path)
            if gate_path.is_file()
            else evaluate_routing_draft(
                run_dir=self.run_dir,
                attempt_root=self.attempt_root,
                fallback_request=self.request,
                fallback_result=fallback,
            )
        )
        if gate.get("status") != "ACCEPTED":
            return self._outcome(
                MAINTAINER_REQUIRED,
                policy_gate=self._relative(gate_path),
                message="routing 草稿未通过确定性门禁",
            )

        audit_task_path, audit_output, audit_task = self._role_paths(AUDIT)
        if not audit_output.is_file():
            self._persist_task(audit_task_path, audit_task)
            return self._outcome(
                NEED_AUDIT,
                role=AUDIT,
                task=audit_task.to_dict(),
            )
        audit = self._validated_result(AUDIT)
        if audit["status"] != "ACCEPTED":
            return self._outcome(
                MAINTAINER_REQUIRED,
                message=str(audit.get("reason") or "Audit 未接受修复"),
            )

        child_request_path = self.attempt_root / "child_run_request.json"
        child_request = {
            "contract_version": 1,
            "parent_run_id": self.run_id,
            "routing_rules_snapshot": gate["snapshot_ref"],
            "routing_rules_sha256": gate["snapshot_sha256"],
            "authorized_by": {
                "policy_gate": self._relative(gate_path),
                "audit": self._relative(audit_output),
            },
        }
        if child_request_path.is_file():
            if _read_json(child_request_path) != child_request:
                raise RuntimeError("不可变 child_run_request 内容冲突")
        else:
            atomic_write_json(child_request_path, child_request)
        return self._outcome(
            CHILD_RUN_READY,
            child_run_request=self._relative(child_request_path),
        )

    def submit(
        self,
        role: str,
        *,
        session_id: str,
        payload: Mapping[str, Any],
        usage: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if role not in {FALLBACK, AUDIT}:
            raise ValueError("role 只允许 fallback 或 audit")
        if not session_id.strip():
            raise ValueError("session_id 不能为空")
        task_path, output_path, task = self._role_paths(role)
        if not task_path.is_file() or _read_json(task_path) != task.to_dict():
            raise RuntimeError(f"必须先由 Coordinator 创建 {role} task")
        value = validate_role_payload(role, payload, task)

        self.receipt_root.mkdir(parents=True, exist_ok=True)
        for receipt_path in self.receipt_root.glob("*.json"):
            receipt = _read_json(receipt_path)
            if receipt.get("session_id") == session_id:
                raise RuntimeError("Fallback 与 Audit 必须使用不同的新会话")
        if output_path.exists() or (self.receipt_root / f"{role}.json").exists():
            raise RuntimeError(f"{role} 输出不可覆盖")
        atomic_write_json(output_path, value)

        usage_value = dict(usage or {})
        normalized_usage = {}
        for key in ("input_tokens", "output_tokens", "cached_input_tokens"):
            amount = int(usage_value.get(key) or 0)
            if amount < 0:
                raise ValueError("token usage 不能为负数")
            normalized_usage[key] = amount
        normalized_usage["model"] = str(usage_value.get("model") or "")
        receipt = {
            "contract_version": 1,
            "run_id": self.run_id,
            "attempt": self.attempt,
            "role": role,
            "task_id": task.task_id,
            "session_id": session_id,
            "output_path": task.output_path,
            "output_sha256": _sha256(output_path),
            "usage": normalized_usage,
        }
        receipt_path = self.receipt_root / f"{role}.json"
        atomic_write_json(receipt_path, receipt)
        self._record_usage(role, session_id, normalized_usage, self._relative(receipt_path))
        return receipt

    def _record_usage(
        self,
        role: str,
        session_id: str,
        usage: Mapping[str, Any],
        receipt_ref: str,
    ) -> None:
        path = self.run_dir / "token_usage.json"
        value = _read_json(path) if path.is_file() else {
            "contract_version": 1,
            "run_id": self.run_id,
            "measurement_scope": "fallback_and_audit_sessions_only",
            "ai_session_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_input_tokens": 0,
            "sessions": [],
        }
        value.setdefault("measurement_scope", "fallback_and_audit_sessions_only")
        session = {
            "attempt": self.attempt,
            "role": role,
            "session_id": session_id,
            "model": usage.get("model", ""),
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "cached_input_tokens": int(usage.get("cached_input_tokens") or 0),
            "receipt_ref": receipt_ref,
        }
        value.setdefault("sessions", []).append(session)
        value["ai_session_count"] = len(value["sessions"])
        for key in ("input_tokens", "output_tokens", "cached_input_tokens"):
            value[key] = sum(int(item.get(key) or 0) for item in value["sessions"])
        atomic_write_json(path, value)
