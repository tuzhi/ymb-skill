"""把流水线失败事实分类为确定性的后续动作。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .models.run_result import (
    NextAction,
    ReasonCode,
    RunDecision,
)


@dataclass(frozen=True)
class RetryPolicy:
    """流水线的人机重试上限。"""

    max_password_attempts: int = 3
    max_repair_attempts: int = 2


DEFAULT_RETRY_POLICY = RetryPolicy()
REASON_CODE_VALUES = frozenset(code.value for code in ReasonCode)

INPUT_PATH_REQUIRED = RunDecision(
    NextAction.REQUEST_USER,
    ReasonCode.INPUT_SOURCE_INVALID,
    "请提供客户流水目录或 zip 路径",
)
INPUT_PATH_NOT_FOUND = RunDecision(
    NextAction.REQUEST_USER,
    ReasonCode.INPUT_SOURCE_INVALID,
    "输入路径不存在",
)
INPUT_PATH_INVALID = RunDecision(
    NextAction.REQUEST_USER,
    ReasonCode.INPUT_SOURCE_INVALID,
    "请输入流水目录或 zip 文件",
)


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


def _declared_reason(records: Iterable[Mapping[str, Any]]) -> ReasonCode:
    priorities = (
        ReasonCode.INPUT_PASSWORD_INVALID,
        ReasonCode.INPUT_PASSWORD_REQUIRED,
        ReasonCode.ZERO_TRANSACTION_STATEMENT,
        ReasonCode.INPUT_SOURCE_INVALID,
        ReasonCode.ROUTE_AMBIGUOUS,
        ReasonCode.ROUTE_UNMATCHED,
        ReasonCode.MAPPING_FAILED,
        ReasonCode.TRANSFORM_FAILED,
        ReasonCode.READER_FAILED,
        ReasonCode.VALIDATION_FAILED,
        ReasonCode.QC_HARD_FAILURE,
    )
    declared = {
        ReasonCode(record.get("reason_code") or "")
        for record in _failure_records(records)
        if str(record.get("reason_code") or "") in REASON_CODE_VALUES
    }
    return next((reason for reason in priorities if reason in declared), ReasonCode.NONE)


def classify_failure(
    stage_id: str,
    error: BaseException | str,
    records: Iterable[Mapping[str, Any]] = (),
    *,
    password_attempt: int = 0,
    retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
) -> RunDecision:
    """把程序事实归一为稳定 reason_code；不读取 Prompt 或业务 reference。"""
    if stage_id != "stage_1_standardize":
        return RunDecision(
            NextAction.REPORT_ERROR,
            ReasonCode.DOWNSTREAM_STAGE_FAILURE,
            f"{stage_id} 失败，确定性流水线已停止",
        )

    records = list(_failure_records(records))
    declared = _declared_reason(records)
    text = _joined_text(error, records)
    if declared in {ReasonCode.INPUT_PASSWORD_REQUIRED, ReasonCode.INPUT_PASSWORD_INVALID}:
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
        reason = ReasonCode.INPUT_PASSWORD_INVALID if password_attempt else ReasonCode.INPUT_PASSWORD_REQUIRED
    elif any(marker in text for marker in (
        "password required",
        "password is required",
        "file is encrypted",
        "encrypted file",
        "需要密码",
        "文件已加密",
        "加密文件",
    )):
        reason = ReasonCode.INPUT_PASSWORD_REQUIRED
    elif declared:
        reason = declared
    elif any(marker in text for marker in (
        "不支持的输入格式",
        "无可处理的银行流水文件",
        "重复文件",
    )):
        reason = ReasonCode.INPUT_SOURCE_INVALID
    elif "ambiguous" in text or "命中多个已发布 yaml 指纹" in text:
        reason = ReasonCode.ROUTE_AMBIGUOUS
    elif any(marker in text for marker in (
        "yaml_route_required",
        "未唯一命中已发布 yaml 指纹",
        "维护 yaml 草稿",
        "创建或维护 yaml 草稿",
    )):
        reason = ReasonCode.ROUTE_UNMATCHED
    elif any(marker in text for marker in ("字段映射", "mapping")):
        reason = ReasonCode.MAPPING_FAILED
    elif any(marker in text for marker in ("transform", "转换失败", "表头合并")):
        reason = ReasonCode.TRANSFORM_FAILED
    elif any(marker in text for marker in ("reader", "解析失败", "读取失败")):
        reason = ReasonCode.READER_FAILED
    elif any(marker in text for marker in ("validationerror", "validator", "验收失败", "校验失败")):
        reason = ReasonCode.VALIDATION_FAILED
    elif any(str(record.get("status") or "") == "BLOCKED" for record in records):
        reason = ReasonCode.QC_HARD_FAILURE
    else:
        reason = ReasonCode.UNKNOWN

    if reason == ReasonCode.INPUT_PASSWORD_INVALID and password_attempt >= retry_policy.max_password_attempts:
        return RunDecision(NextAction.REPORT_ERROR, reason, "密码尝试次数已达上限")
    if reason == ReasonCode.INPUT_PASSWORD_REQUIRED:
        return RunDecision(NextAction.REQUEST_USER, reason, "文件需要打开密码")
    if reason == ReasonCode.INPUT_PASSWORD_INVALID:
        return RunDecision(NextAction.REQUEST_USER, reason, "打开密码无效，请重新提供")
    if reason == ReasonCode.ZERO_TRANSACTION_STATEMENT:
        return RunDecision(
            NextAction.REQUEST_USER,
            reason,
            "文件已识别但本期无交易，请确认或补充包含交易明细的流水",
        )
    if reason == ReasonCode.INPUT_SOURCE_INVALID:
        return RunDecision(NextAction.REQUEST_USER, reason, "请提供受支持的银行原始导出文件")
    if reason == ReasonCode.QC_HARD_FAILURE:
        return RunDecision(NextAction.REQUEST_USER, reason, "原始文件未通过硬性质量门禁，请修正或替换文件")
    return RunDecision(NextAction.NEED_REPAIR, reason, "Stage 1 异常需要隔离 Repair Agent 处理")
