"""流水线最终结果及失败路由模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
from typing import Any, Mapping


CONTRACT_VERSION = 1


class NextAction(StrEnum):
    """RunResult 的唯一后续动作定义。"""

    DELIVER = "DELIVER"  # 全部处理及验收通过，交付结果
    REQUEST_USER = "REQUEST_USER"  # 需要用户补充密码、文件或参数
    EXECUTE_PIPELINE = "EXECUTE_PIPELINE"  # 执行确定性流水线
    NEED_REPAIR = "NEED_REPAIR"  # Stage 1 可修复失败，进入隔离 Repair
    MAINTAINER_REQUIRED = "MAINTAINER_REQUIRED"  # 超出 Repair 能力或重试上限
    REPORT_ERROR = "REPORT_ERROR"  # 不可恢复的程序错误或下游阶段错误


class ReasonCode(StrEnum):
    """RunResult 的唯一失败原因定义。"""

    NONE = ""  # 无失败原因
    INPUT_PASSWORD_REQUIRED = "INPUT_PASSWORD_REQUIRED"  # 文件需要密码
    INPUT_PASSWORD_INVALID = "INPUT_PASSWORD_INVALID"  # 密码错误
    INPUT_SOURCE_INVALID = "INPUT_SOURCE_INVALID"  # 输入路径或格式无效
    ZERO_TRANSACTION_STATEMENT = "ZERO_TRANSACTION_STATEMENT"  # 已识别但本期无交易
    ROUTE_UNMATCHED = "ROUTE_UNMATCHED"  # 未命中解析规则
    ROUTE_AMBIGUOUS = "ROUTE_AMBIGUOUS"  # 同时命中多个解析规则
    READER_FAILED = "READER_FAILED"  # 文件读取或版式解析失败
    TRANSFORM_FAILED = "TRANSFORM_FAILED"  # 数据转换失败
    MAPPING_FAILED = "MAPPING_FAILED"  # 标准字段映射失败
    VALIDATION_FAILED = "VALIDATION_FAILED"  # Validator 验收失败
    QC_HARD_FAILURE = "QC_HARD_FAILURE"  # 硬性质量门禁失败
    EXECUTION_PLAN_INVALID = "EXECUTION_PLAN_INVALID"  # 执行计划无法创建或恢复
    PIPELINE_ALREADY_RUNNING = "PIPELINE_ALREADY_RUNNING"  # 同一输入已有运行中的任务
    UNKNOWN = "UNKNOWN"  # 未归类异常
    DOWNSTREAM_STAGE_FAILURE = "DOWNSTREAM_STAGE_FAILURE"  # Stage 2～4 失败


NEXT_ACTIONS = frozenset(NextAction)
REASON_CODES = frozenset(ReasonCode)


@dataclass(frozen=True)
class RunDecision:
    """确定性策略给出的动作、原因和默认中文提示。"""

    next_action: NextAction
    reason_code: ReasonCode
    message: str

    def with_message(self, message: str) -> "RunDecision":
        return replace(self, message=message)


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: str
    next_action: NextAction
    reason_code: ReasonCode = ReasonCode.NONE
    artifact_refs: tuple[str, ...] = field(default_factory=tuple)
    context_ref: str = ""
    message: str = ""
    action: Mapping[str, Any] = field(default_factory=dict)
    summary: Mapping[str, Any] = field(default_factory=dict)
    contract_version: int = CONTRACT_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "next_action", NextAction(self.next_action))
        object.__setattr__(self, "reason_code", ReasonCode(self.reason_code))
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

    @classmethod
    def from_pipeline_result(cls, value: Mapping[str, Any]) -> "RunResult":
        """从 pipeline_result.json 的顶层控制字段恢复 RunResult。"""
        deliverables = value.get("deliverables")
        artifact_refs = value.get("artifact_refs")
        refs = deliverables if isinstance(deliverables, list) else artifact_refs
        return cls(
            run_id=str(value.get("run_id") or ""),
            status=str(value.get("status") or ""),
            next_action=NextAction(value.get("next_action")),
            reason_code=ReasonCode(value.get("reason_code") or ""),
            artifact_refs=tuple(str(item) for item in refs or ()),
            context_ref=str(value.get("context_ref") or ""),
            message=str(value.get("message") or ""),
            action=dict(value.get("action") or {}),
            summary=dict(value.get("summary") or {}),
            contract_version=int(value.get("schema_version") or CONTRACT_VERSION),
        )
