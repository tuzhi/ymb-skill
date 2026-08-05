"""流水线对薄 Skill 暴露的紧凑结果契约。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping
import json
import os
import tempfile


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

INPUT_PASSWORD_REQUIRED = "INPUT_PASSWORD_REQUIRED"
INPUT_PASSWORD_INVALID = "INPUT_PASSWORD_INVALID"
INPUT_SOURCE_INVALID = "INPUT_SOURCE_INVALID"
ROUTE_UNMATCHED = "ROUTE_UNMATCHED"
ROUTE_AMBIGUOUS = "ROUTE_AMBIGUOUS"
READER_FAILED = "READER_FAILED"
TRANSFORM_FAILED = "TRANSFORM_FAILED"
MAPPING_FAILED = "MAPPING_FAILED"
VALIDATION_FAILED = "VALIDATION_FAILED"
QC_HARD_FAILURE = "QC_HARD_FAILURE"
UNKNOWN = "UNKNOWN"
DOWNSTREAM_STAGE_FAILURE = "DOWNSTREAM_STAGE_FAILURE"

MAX_PASSWORD_ATTEMPTS = 3
MAX_AI_REPAIR_ATTEMPTS = 2


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


def atomic_write_json(path: str | os.PathLike[str], value: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(dict(value), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_run_result(path: str | os.PathLike[str], result: RunResult) -> RunResult:
    atomic_write_json(path, result.to_dict())
    return result


def _failure_records(records: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [record for record in records if isinstance(record, Mapping)]


def _joined_text(error: BaseException | str, records: Iterable[Mapping[str, Any]]) -> str:
    values = [str(error)]
    if isinstance(error, BaseException):
        values.append(type(error).__name__)
    for record in _failure_records(records):
        values.extend((
            str(record.get("message") or ""),
            str(record.get("reason_code") or ""),
        ))
    return "\n".join(values).lower()


def _declared_reason(records: Iterable[Mapping[str, Any]]) -> str:
    priorities = (
        INPUT_PASSWORD_INVALID,
        INPUT_PASSWORD_REQUIRED,
        INPUT_SOURCE_INVALID,
        ROUTE_AMBIGUOUS,
        ROUTE_UNMATCHED,
        MAPPING_FAILED,
        TRANSFORM_FAILED,
        READER_FAILED,
        VALIDATION_FAILED,
        QC_HARD_FAILURE,
    )
    declared = {
        str(record.get("reason_code") or "")
        for record in _failure_records(records)
    }
    return next((reason for reason in priorities if reason in declared), "")


def classify_failure(
    stage_id: str,
    error: BaseException | str,
    records: Iterable[Mapping[str, Any]] = (),
    *,
    password_attempt: int = 0,
    skipped_inputs: Iterable[Mapping[str, Any]] = (),
) -> FailureRoute:
    """把程序事实归一为稳定 reason_code；不读取 Prompt 或业务 reference。"""
    if stage_id != "stage_1_standardize":
        return FailureRoute(
            DOWNSTREAM_STAGE_FAILURE,
            REPORT_ERROR,
            f"{stage_id} 失败，确定性流水线已停止",
        )

    records = list(_failure_records(records))
    declared = _declared_reason(records)
    text = _joined_text(error, records)
    skipped_text = "\n".join(
        str(item.get("reason") or "")
        for item in skipped_inputs
        if isinstance(item, Mapping)
    ).lower()

    if declared in {INPUT_PASSWORD_REQUIRED, INPUT_PASSWORD_INVALID}:
        reason = declared
    elif any(marker in text for marker in (
        "pdfpasswordincorrect",
        "password incorrect",
        "incorrect password",
        "invalid password",
        "密码错误",
        "密码不正确",
        "密码无效",
        "decryption failed",
        "key verification failed",
    )):
        reason = INPUT_PASSWORD_INVALID if password_attempt else INPUT_PASSWORD_REQUIRED
    elif any(marker in text for marker in (
        "password required",
        "password is required",
        "file is encrypted",
        "encrypted file",
        "需要密码",
        "文件已加密",
        "加密文件",
    )):
        reason = INPUT_PASSWORD_REQUIRED
    elif declared:
        reason = declared
    elif "ambiguous" in text or "命中多个已发布 yaml 指纹" in text:
        reason = ROUTE_AMBIGUOUS
    elif any(marker in text for marker in (
        "yaml_route_required",
        "未唯一命中已发布 yaml 指纹",
        "维护 yaml 草稿",
        "创建或维护 yaml 草稿",
    )):
        reason = ROUTE_UNMATCHED
    elif any(marker in text for marker in ("字段映射", "mapping")):
        reason = MAPPING_FAILED
    elif any(marker in text for marker in ("transform", "转换失败", "表头合并")):
        reason = TRANSFORM_FAILED
    elif any(marker in text for marker in ("reader", "解析失败", "读取失败")):
        reason = READER_FAILED
    elif any(marker in text for marker in ("validationerror", "validator", "验收失败", "校验失败")):
        reason = VALIDATION_FAILED
    elif any(str(record.get("status") or "") == "BLOCKED" for record in records):
        reason = QC_HARD_FAILURE
    elif skipped_text:
        reason = INPUT_SOURCE_INVALID
    else:
        reason = UNKNOWN

    if reason == INPUT_PASSWORD_INVALID and password_attempt >= MAX_PASSWORD_ATTEMPTS:
        return FailureRoute(reason, REPORT_ERROR, "密码尝试次数已达上限")
    if reason == INPUT_PASSWORD_REQUIRED:
        return FailureRoute(reason, REQUEST_USER, "文件需要打开密码")
    if reason == INPUT_PASSWORD_INVALID:
        return FailureRoute(reason, REQUEST_USER, "打开密码无效，请重新提供")
    if reason == INPUT_SOURCE_INVALID:
        return FailureRoute(reason, REQUEST_USER, "请提供受支持的银行原始导出文件")
    if reason == QC_HARD_FAILURE:
        return FailureRoute(reason, REQUEST_USER, "原始文件未通过硬性质量门禁，请修正或替换文件")
    return FailureRoute(reason, NEED_REPAIR, "Stage 1 异常需要隔离 Repair Agent 处理")
