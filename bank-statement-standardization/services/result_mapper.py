"""把 Pipeline 内部执行结果映射为对外 StandardizationResult。"""

from __future__ import annotations

from typing import Any, Mapping

from runtime.models import PipelineExecutionResult
from runtime.models.run_result import NextAction

from .models import StandardizationResult


def _qc_contract(
    rules: Mapping[str, Any],
    overall_status: Any = None,
    *,
    rules_key: str = "rules",
) -> dict[str, Any]:
    normalized = {
        str(rule_id): {
            "level": str(value.get("level") or ""),
            "passed": bool(value.get("passed")),
            "message": str(value.get("message") or ""),
        }
        for rule_id, value in sorted(rules.items())
        if isinstance(value, Mapping)
    }
    summary = {}
    for level in ("HARD", "SOFT"):
        values = [value for value in normalized.values() if value["level"] == level]
        key = level.lower()
        summary[f"{key}_total"] = len(values)
        summary[f"{key}_passed"] = sum(value["passed"] for value in values)
        summary[f"{key}_failed"] = sum(not value["passed"] for value in values)
    if any(value["level"] == "HARD" and not value["passed"] for value in normalized.values()):
        status = "BLOCKED"
    elif any(value["level"] == "SOFT" and not value["passed"] for value in normalized.values()):
        status = "PASS_WITH_WARNINGS"
    else:
        status = "PASS"
    if overall_status in {"BLOCKED", "PASS_WITH_WARNINGS", "PASS"}:
        status = str(overall_status)
    return {"status": status, "summary": summary, rules_key: normalized}


def _file_results(
    stage_1_results: Mapping[str, Any],
    qc: Mapping[str, Any],
) -> list[dict[str, Any]]:
    files = stage_1_results.get("files") or {}
    if not isinstance(files, Mapping):
        return []
    results = []
    file_qc = qc.get("files") or {}
    for file_id, record in sorted(files.items()):
        if not isinstance(record, Mapping):
            continue
        route = record.get("route") or {}
        results.append({
            "file_id": str(file_id),
            "file_name": str(record.get("name") or ""),
            "status": str(record.get("status") or ""),
            "route": {
                "fingerprint_id": str(route.get("fingerprint_id") or ""),
                "reader_id": str(route.get("reader_id") or ""),
                "bank_name": str(route.get("router_bank") or "未识别"),
                "account_type": str(route.get("account_type") or "未知"),
            },
            "qc_file": _qc_contract(file_qc.get(file_id) or {}, rules_key="rules"),
        })
    return results


def _stages(stages: Mapping[str, Any]) -> dict[str, Any]:
    aliases = {
        "stage_1_standardize": "stage_1",
        "stage_2_integrate": "stage_2",
        "stage_2b_portfolio_balance": "stage_2b",
        "stage_3_tag": "stage_3",
        "stage_4_package": "stage_4",
    }
    return {
        aliases.get(stage_id, stage_id): {
            "name": str(spec.get("name") or ""),
            "status": str(spec.get("status") or ""),
            "duration_seconds": spec.get("duration_seconds"),
            "error": spec.get("reason_code"),
        }
        for stage_id, spec in stages.items()
        if isinstance(spec, Mapping)
    }


def _frame_len(value: Any) -> int:
    try:
        return int(len(value))
    except TypeError:
        return 0


def _money_total(transactions: Any, analysis: str, raw: str) -> float:
    if transactions is None or not hasattr(transactions, "columns"):
        return 0.0
    import pandas as pd

    column = analysis if analysis in transactions.columns else raw
    return round(float(pd.to_numeric(transactions.get(column), errors="coerce").fillna(0).sum()), 2)


def _summary(
    run_result: Mapping[str, Any],
    file_results: list[dict[str, Any]],
    qc: Mapping[str, Any],
    integration: Mapping[str, Any],
    balance: Mapping[str, Any],
    tagging: Mapping[str, Any],
    dataset: Mapping[str, Any],
) -> dict[str, Any]:
    delivery = run_result.get("summary") or {}
    overview = integration.get("客户整合概览") or {}
    virtual = balance.get("组合虚拟账户") or {}
    checks = balance.get("账户余额校验") or {}
    tag_overview = tagging.get("标签梳理概览") or {}
    transactions = dataset.get("transactions")
    inflow = _money_total(transactions, "分析收入金额", "收入金额")
    outflow = _money_total(transactions, "分析支出金额", "支出金额")
    warnings = []
    failed_rules = 0
    for rules in [qc.get("customer") or {}, *(qc.get("files") or {}).values()]:
        for value in (rules or {}).values():
            if isinstance(value, Mapping) and not value.get("passed"):
                failed_rules += 1
                if value.get("level") == "SOFT" and value.get("message"):
                    warnings.append(str(value["message"]))
    period = overview.get("交易期间") or {}
    input_count = int(delivery.get("input_file_count") or len(file_results))
    return {
        "input_file_count": input_count,
        "processed_file_count": sum(item["status"] == "DONE" for item in file_results),
        "skipped_file_count": input_count - len(file_results),
        "failed_file_count": sum(item["status"] != "DONE" for item in file_results),
        "account_count": int(overview.get("整合账户数") or 0),
        "source_transaction_count": int(overview.get("原始交易数") or 0),
        "deduplicated_transaction_count": int(overview.get("跨文件去重笔数") or 0),
        "transaction_count": int(overview.get("整合交易数") or 0),
        "date_range": {
            "start_date": period.get("开始日期", ""),
            "end_date": period.get("结束日期", ""),
        },
        "total_inflow": inflow,
        "total_outflow": outflow,
        "net_amount": round(inflow - outflow, 2),
        "closing_virtual_balance": virtual.get("期末合计余额"),
        "peak_virtual_balance": virtual.get("峰值合计余额"),
        "minimum_virtual_balance": virtual.get("谷值合计余额"),
        "balance_passed_account_count": int(checks.get("通过账户数") or 0),
        "balance_warning_account_count": int(checks.get("预警账户数") or 0),
        "balance_breakpoint_count": int(checks.get("余额断点合计") or 0),
        "tag_rule_match_rate": tag_overview.get("规则命中率", 0),
        "duplicate_candidate_group_count": len(integration.get("疑似重复交易组") or []),
        "internal_transfer_candidate_group_count": len(integration.get("自有账户互转组") or []),
        "review_item_count": _frame_len(dataset.get("review_items")),
        "qc_status": str(qc.get("status") or ""),
        "qc_failed_rule_count": failed_rules,
        "warning_count": len(warnings),
        "warnings": warnings,
    }


def _business_summary(
    integration: Mapping[str, Any],
    balance: Mapping[str, Any],
    tagging: Mapping[str, Any],
) -> dict[str, Any]:
    overview = integration.get("客户整合概览") or {}
    decision = integration.get("最终判断") or {}
    virtual = balance.get("组合虚拟账户") or {}
    continuity = balance.get("组合连续性校验") or {}
    tag_overview = tagging.get("标签梳理概览") or {}
    reversal = (tag_overview.get("交易关系汇总") or {}).get("银行冲正") or {}
    return {
        "integration": {
            "quality_score": overview.get("整体质量评分"),
            "can_enter_tag_analysis": bool(decision.get("是否可进入标签分析")),
            "blocking_issues": list(decision.get("阻断问题") or []),
            "non_blocking_warnings": list(decision.get("非阻断预警") or []),
            "review_item_count": len(integration.get("人工复核事项") or []),
        },
        "balance": {
            "calculation_basis": virtual.get("口径", ""),
            "opening_total_balance": virtual.get("期初合计余额"),
            "daily_continuity_exception_count": int(continuity.get("异常日数") or 0),
            "daily_continuity_conclusion": continuity.get("结论", ""),
        },
        "tagging": {
            "rule_matched_count": int(tag_overview.get("规则命中数量") or 0),
            "fallback_tag_count": int(tag_overview.get("兜底其他类数量") or 0),
            "matched_field_distribution": dict(tag_overview.get("命中字段分布") or {}),
            "level_1_distribution": dict(tagging.get("一级标签分布") or {}),
            "bank_reversal": {
                "matched_group_count": int(reversal.get("配对组数") or 0),
                "original_transaction_count": int(reversal.get("被冲正原始交易数") or 0),
                "reversal_transaction_count": int(reversal.get("冲正记录数") or 0),
                "implicit_reversal_count": int(reversal.get("隐式冲正数") or 0),
                "pending_review_count": int(reversal.get("待复核冲正数") or 0),
            },
        },
    }


def build_standardization_result(
    execution: PipelineExecutionResult,
    rules_version: str,
) -> StandardizationResult:
    """构造可供 SDK 直接返回或入库的标准化结果。"""
    if not isinstance(execution, PipelineExecutionResult):
        raise TypeError("execution 必须是 PipelineExecutionResult")
    run_result = dict(execution.run_result or {})
    qc = dict(execution.qc or {})
    integration = dict(execution.integration_report or {})
    balance = dict(execution.balance_report or {})
    tagging = dict(execution.tag_report or {})
    dataset = dict(execution.dataset or {})
    file_results = _file_results(execution.file_results, qc)
    stages = _stages(execution.stages)
    return StandardizationResult(
        run_id=execution.run_id,
        status=execution.status,
        next_action=str(run_result.get("next_action") or NextAction.REPORT_ERROR),
        message=str(run_result.get("message") or execution.error or ""),
        client={
            "client_name": execution.client_name,
            "client_no": None,
            "client_org_name": None,
            "client_org_no": None,
        },
        rule_snapshot={"version": rules_version},
        summary=_summary(run_result, file_results, qc, integration, balance, tagging, dataset),
        file_results=file_results,
        stages=stages,
        qc_client=_qc_contract(qc.get("customer") or {}, rules_key="customer_rules"),
        business_summary=_business_summary(integration, balance, tagging),
        dataset=dataset,
        deliverable=dict(execution.deliverable or {}),
    )
