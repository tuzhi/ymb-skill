"""确定性 Coordinator：把 Stage 1 失败交给一个隔离 Repair Agent。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
import csv
import hashlib
import json

from runtime import failure_policy as F
from runtime.models import run_result as R
from runtime.result_store import atomic_write_json

from .contracts import (
    CHILD_RUN_READY,
    CONTRACT_VERSION,
    MAINTAINER_REQUIRED,
    NEED_REPAIR,
    REPAIR,
    REQUEST_USER,
    UNSUPPORTED,
    validate_repair_payload,
)
from .models import RepairRequest
from .protocols import protocol_path


SKILL_ROOT = Path(__file__).resolve().parents[1]
REPAIR_PROMPT = SKILL_ROOT / "roles" / "repair.md"
TOKEN_USAGE_KEYS = ("input_tokens", "output_tokens", "cached_input_tokens")


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


def _md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "md5:" + digest.hexdigest()


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_token_usage(usage: Mapping[str, Any]) -> tuple[dict[str, int | None], str]:
    normalized = {
        key: max(0, int(usage[key])) if usage.get(key) is not None else None
        for key in TOKEN_USAGE_KEYS
    }
    known_count = sum(value is not None for value in normalized.values())
    if known_count == len(TOKEN_USAGE_KEYS):
        status = "available"
    elif known_count == 0:
        status = "unavailable"
    else:
        status = "partial"
    return normalized, status


class RepairCoordinator:
    """从 RunResult 和 stage_1_results 推导一次 Repair 请求。"""

    def __init__(self, run_dir: str | Path):
        self.run_dir = Path(run_dir).resolve()
        self.run_id = self.run_dir.name
        self.run_result = _read_json(self.run_dir / "run_result.json")
        self.manifest = _read_json(self.run_dir / "manifest.json")
        self.stage_results = _read_json(self.run_dir / "stage_1_results.json")
        self.input_root = (self.run_dir / "input").resolve()
        self.attempt = int(self.manifest.get("ai_repair_attempt") or 0) + 1
        self.repair_root = self.run_dir / "repair" / f"attempt-{self.attempt:02d}"
        self.request_path = self.repair_root / "repair_request.json"
        self.receipt_path = self.repair_root / "session-receipt.json"
        self.output_path = self.repair_root / "repair_result.json"
        self._validate_run()

    def _validate_run(self) -> None:
        if self.run_result.get("run_id") != self.run_id:
            raise ValueError("RunResult 与 Run 目录不一致")
        if self.run_result.get("next_action") != R.NEED_REPAIR:
            raise ValueError("当前 Run 不需要 AI Repair")
        if self.attempt > F.MAX_AI_REPAIR_ATTEMPTS:
            raise RuntimeError("AI 修复次数已达上限")
        if not self.input_root.is_dir():
            raise FileNotFoundError("父 Run 缺少 input 快照")
        if not REPAIR_PROMPT.is_file():
            raise FileNotFoundError(f"缺少 Repair 角色说明：{REPAIR_PROMPT}")

    def _failed_files(self) -> list[dict[str, Any]]:
        files = self.stage_results.get("files")
        if not isinstance(files, dict):
            raise ValueError("stage_1_results.json 缺少 files 对象")
        failed = []
        for file_id, record in files.items():
            if not isinstance(record, Mapping) or record.get("status") not in {"ERROR", "BLOCKED"}:
                continue
            relative = str(record.get("relative_path") or "").strip()
            if not relative:
                raise ValueError(f"失败文件缺少 relative_path：{record.get('name') or ''}")
            path = (self.input_root / relative).resolve()
            if self.input_root not in path.parents or not path.is_file():
                raise ValueError(f"失败文件路径无效：{relative}")
            actual_file_id = _md5(path)
            if file_id != actual_file_id:
                raise ValueError(f"失败文件 file_id 与原文件不一致：{relative}")
            failed.append({
                "file_id": file_id,
                "source_md5": actual_file_id,
                "name": str(record.get("name") or path.name),
                "input_ref": path.relative_to(self.run_dir).as_posix(),
                "status": str(record.get("status") or ""),
                "reason_code": str(record.get("reason_code") or ""),
                "message": str(record.get("message") or ""),
            })
        if not failed:
            raise ValueError("Stage 1 没有可交给 Repair Agent 的失败文件")
        return failed

    def request(self) -> RepairRequest:
        failed_files = self._failed_files()
        input_refs = ["stage_1_results.json", *(item["input_ref"] for item in failed_files)]
        output_contract = protocol_path("repair-result").resolve()
        seed = {
            "run_id": self.run_id,
            "attempt": self.attempt,
            "failed_files": failed_files,
            "role_prompt_sha256": _sha256(REPAIR_PROMPT),
            "output_contract_sha256": _sha256(output_contract),
        }
        return RepairRequest(
            request_id=f"{self.run_id}:{self.attempt}:repair:{_canonical_hash(seed)[:16]}",
            run_id=self.run_id,
            run_dir=self.run_dir.as_posix(),
            attempt=self.attempt,
            role_prompt_ref=REPAIR_PROMPT.resolve().as_posix(),
            input_refs=tuple(input_refs),
            failed_files=tuple(failed_files),
            repair_dir=self.repair_root.resolve().as_posix(),
            output_contract_ref=output_contract.as_posix(),
        )

    def decision(self) -> dict[str, Any]:
        request = self.request()
        request_value = request.to_dict()
        self.repair_root.mkdir(parents=True, exist_ok=True)
        if self.request_path.is_file() and _read_json(self.request_path) != request_value:
            raise RuntimeError("repair_request.json 不可覆盖")
        atomic_write_json(self.request_path, request_value)
        return {
            "contract_version": CONTRACT_VERSION,
            "run_id": self.run_id,
            "attempt": self.attempt,
            "status": NEED_REPAIR,
            "role": REPAIR,
            "request": request_value,
            "action": {
                "handler": "repair_coordinator",
                "entrypoint": (SKILL_ROOT / "scripts" / "repair_coordinator.py").as_posix(),
                "operation": "submit",
                "run_dir": self.run_dir.as_posix(),
                "request_id": request.request_id,
            },
        }

    def submit(
        self,
        *,
        request_id: str,
        session_id: str,
        payload: Mapping[str, Any],
        usage: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = self.request()
        if request_id != request.request_id:
            raise RuntimeError("repair request_id 与当前请求不匹配")
        if not str(session_id or "").strip():
            raise ValueError("session_id 不能为空")
        self._assert_fresh_session(session_id)
        value = validate_repair_payload(payload, request)
        if value["status"] == "REPAIRED":
            value["outputs"] = self._validate_outputs(value.get("outputs") or [], request)
        self.repair_root.mkdir(parents=True, exist_ok=True)
        if self.output_path.is_file() and _read_json(self.output_path) != value:
            raise RuntimeError("repair_result.json 不可覆盖")
        atomic_write_json(self.output_path, value)
        self._write_receipt(request, session_id, usage or {})

        status = value["status"]
        if status == REQUEST_USER:
            return self._outcome(REQUEST_USER, message=value.get("message") or "请补充修复所需信息")
        if status == UNSUPPORTED:
            return self._outcome(UNSUPPORTED, message=value.get("message") or "当前输入不支持自动修复")
        if status == MAINTAINER_REQUIRED:
            return self._outcome(MAINTAINER_REQUIRED, message=value.get("message") or "需要维护者处理")

        return self._outcome(
            CHILD_RUN_READY,
            repair_result_ref=self.output_path.relative_to(self.run_dir).as_posix(),
            repair_result_sha256=_sha256(self.output_path),
        )

    def _validate_outputs(
        self,
        outputs: list[Any],
        request: RepairRequest,
    ) -> list[dict[str, Any]]:
        expected = {str(item["file_id"]): dict(item) for item in request.failed_files}
        normalized = []
        seen = set()
        seen_paths = set()
        required = {"file_id", "source_md5", "standardized_csv", "row_count", "sha256"}
        for raw in outputs:
            if not isinstance(raw, Mapping) or set(raw) != required:
                raise ValueError("Repair output 字段无效")
            file_id = str(raw.get("file_id") or "")
            if file_id in seen or file_id not in expected:
                raise ValueError(f"Repair output file_id 无效：{file_id}")
            seen.add(file_id)
            source_md5 = str(raw.get("source_md5") or "")
            if source_md5 != expected[file_id]["source_md5"]:
                raise ValueError(f"Repair output source_md5 无效：{file_id}")
            relative = Path(str(raw.get("standardized_csv") or ""))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"Repair CSV 必须使用 repair_dir 内相对路径：{file_id}")
            if not relative.parts or relative.parts[0] != "standardized":
                raise ValueError(f"Repair CSV 必须位于 standardized 目录：{file_id}")
            if relative.as_posix() in seen_paths:
                raise ValueError(f"Repair CSV 路径重复：{relative.as_posix()}")
            seen_paths.add(relative.as_posix())
            path = (self.repair_root / relative).resolve()
            if self.repair_root.resolve() not in path.parents or not path.is_file():
                raise FileNotFoundError(f"Repair CSV 不存在：{relative.as_posix()}")
            if not path.name.endswith("__standardized.csv"):
                raise ValueError(f"Repair CSV 命名无效：{relative.as_posix()}")
            checksum = str(raw.get("sha256") or "")
            if not checksum or checksum != _sha256(path):
                raise ValueError(f"Repair CSV sha256 无效：{file_id}")
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                row_count = sum(1 for _ in csv.DictReader(stream))
            if row_count <= 0 or row_count != int(raw.get("row_count") or 0):
                raise ValueError(f"Repair CSV row_count 无效：{file_id}")
            normalized.append({
                "file_id": file_id,
                "source_md5": source_md5,
                "standardized_csv": relative.as_posix(),
                "row_count": row_count,
                "sha256": checksum,
            })
        if seen != set(expected):
            raise ValueError(f"Repair outputs 未覆盖全部失败文件：{sorted(set(expected) - seen)}")
        return normalized

    def _outcome(self, status: str, **extra: Any) -> dict[str, Any]:
        value = {"contract_version": CONTRACT_VERSION, "run_id": self.run_id, "attempt": self.attempt, "status": status}
        value.update(extra)
        return value

    def _assert_fresh_session(self, session_id: str) -> None:
        for receipt_path in self.run_dir.parent.glob("*/repair/attempt-*/session-receipt.json"):
            receipt = _read_json(receipt_path)
            if receipt.get("session_id") == session_id and receipt_path.resolve() != self.receipt_path.resolve():
                raise RuntimeError("每个 Repair attempt 必须使用不同的新会话")

    def _write_receipt(self, request: RepairRequest, session_id: str, usage: Mapping[str, Any]) -> None:
        normalized_usage, measurement_status = _normalize_token_usage(usage)
        receipt = {
            "contract_version": CONTRACT_VERSION,
            "run_id": self.run_id,
            "attempt": self.attempt,
            "role": REPAIR,
            "request_id": request.request_id,
            "session_id": session_id,
            "output_path": self.output_path.relative_to(self.run_dir).as_posix(),
            "output_sha256": _sha256(self.output_path),
            "measurement_status": measurement_status,
            "usage": normalized_usage,
        }
        if self.receipt_path.is_file() and _read_json(self.receipt_path) != receipt:
            raise RuntimeError("Repair session receipt 不可覆盖")
        atomic_write_json(self.receipt_path, receipt)
        self._record_usage(session_id, normalized_usage, measurement_status)

    def _record_usage(
        self,
        session_id: str,
        usage: Mapping[str, int | None],
        measurement_status: str,
    ) -> None:
        path = self.run_dir / "token_usage.json"
        data = _read_json(path) if path.is_file() else {
            "contract_version": 1,
            "run_id": self.run_id,
            "measurement_scope": "repair_sessions_only",
            "measurement_status": "not_started",
            "ai_session_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_input_tokens": 0,
            "sessions": [],
        }
        if any(item.get("session_id") == session_id for item in data.get("sessions", [])):
            return
        entry = {
            "session_id": session_id,
            "attempt": self.attempt,
            "role": REPAIR,
            "measurement_status": measurement_status,
            **usage,
        }
        data.setdefault("sessions", []).append(entry)
        data["measurement_scope"] = "repair_sessions_only"
        data["ai_session_count"] = len(data["sessions"])
        data["measurement_status"] = measurement_status
        data.update(usage)
        atomic_write_json(path, data)
