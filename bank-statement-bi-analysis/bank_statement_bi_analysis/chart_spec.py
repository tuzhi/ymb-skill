"""从 BI 分析结果构造 Excel/ECharts 共用的图表事实模型。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Any

from .models import ChartBundle, ChartSpec, OmittedChart, SeriesSpec


def build_chart_bundle(
    analysis: Mapping[str, Any],
    detail: Mapping[str, Any],
    frame: Any,
    *,
    strategy_version: str,
) -> ChartBundle:
    """只计算一次图表数据，供 Excel 与 ECharts 两个渲染器共用。"""
    charts: list[ChartSpec] = []
    omitted: list[OmittedChart] = []
    warnings: list[str] = []
    months = tuple(str(value) for value in analysis.get("months", []) or [])
    monthly = analysis.get("mon")
    operating = analysis.get("mon_op")
    loans = detail.get("loan_mon")

    _add_cartesian(
        charts,
        omitted,
        "monthly_cash_flow",
        "月度流入/流出与净流入",
        months,
        (
            _series("流入金额", "bar", _column(monthly, months, "流入"), "inflow"),
            _series("流出金额", "bar", _column(monthly, months, "流出"), "outflow"),
            _series("净流入", "line", _column(monthly, months, "净流入"), "net"),
        ),
    )
    _add_cartesian(
        charts,
        omitted,
        "monthly_balance",
        "余额波动（月末/月均/月最低）",
        months,
        (
            _series("月末余额", "line", _column(monthly, months, "月末余额")),
            _series("月均余额", "line", _column(monthly, months, "月均余额")),
            _series("月最低余额", "line", _column(monthly, months, "月最低余额")),
        ),
    )
    _add_cartesian(
        charts,
        omitted,
        "monthly_inflow_composition",
        "经营 vs 融资 vs 往来款月度流入",
        months,
        (
            _series(
                "经营流入(剔往来)",
                "line",
                _column(operating, months, "经流入"),
                "operating",
            ),
            _series(
                "融资流入(修正)",
                "line",
                _column(monthly, months, "融资流入(修正)"),
                "financing",
            ),
            _series(
                "往来款流入",
                "line",
                _column(monthly, months, "往来款流入"),
                "related_party",
            ),
        ),
    )
    _add_cartesian(
        charts,
        omitted,
        "monthly_loan_trend",
        "借贷趋势（借贷流入/偿还借贷/净额）",
        months,
        (
            _series("借贷流入", "bar", _column(loans, months, "借贷流入"), "inflow"),
            _series("偿还借贷", "bar", _column(loans, months, "偿还借贷"), "outflow"),
            _series("借贷净额", "line", _column(loans, months, "净额"), "net"),
        ),
    )

    external = _external_transactions(frame)
    _add_pie(
        charts,
        omitted,
        "external_income_structure",
        "收入用途结构（对外）",
        _category_amounts(external, "收入金额"),
        "inflow",
    )
    _add_pie(
        charts,
        omitted,
        "external_expense_structure",
        "支出用途结构（对外）",
        _category_amounts(external, "支出金额"),
        "outflow",
    )

    counterparties = analysis.get("cp")
    _add_horizontal_bar(
        charts,
        omitted,
        "top_inflow_counterparties",
        "十大流入对手（客户/下游）",
        _top_counterparties(counterparties, "流入"),
        "inflow",
    )
    _add_horizontal_bar(
        charts,
        omitted,
        "top_outflow_counterparties",
        "十大流出对手（供应商/上游）",
        _top_counterparties(counterparties, "流出"),
        "outflow",
    )

    projection = _cash_projection(analysis, frame)
    if projection is None:
        omitted.append(OmittedChart(
            chart_id="future_cash_projection",
            reason="缺少月份、经营收支或现金余额数据",
        ))
    else:
        future_months, scenarios, assumptions = projection
        charts.append(ChartSpec(
            chart_id="future_cash_projection",
            title="未来12个月期末现金推演（三情景）",
            kind="cartesian",
            categories=tuple(future_months),
            series=tuple(
                _series(name, "line", values, "forecast")
                for name, values in scenarios
            ),
            metadata={"assumptions": assumptions, "snapshot": True},
        ))

    currencies = tuple(str(value) for value in detail.get("currencies", []) or [])
    if len(currencies) > 1:
        currency = "MULTI"
        warnings.append(
            "检测到多币种且未提供统一折算口径；金额图为原报告聚合口径，不能直接用于跨币种比较。"
        )
    else:
        currency = currencies[0] if currencies else "人民币"

    return ChartBundle(
        schema_version="1.0",
        strategy_version=strategy_version,
        currency=currency,
        charts=tuple(charts),
        omitted_charts=tuple(omitted),
        warnings=tuple(warnings),
    )


def _number(value: Any) -> float | None:
    result = _raw_number(value)
    return round(result, 2) if result is not None else None


def _raw_number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _column(table: Any, categories: Sequence[str], name: str) -> tuple[float | None, ...]:
    if table is None or not hasattr(table, "loc"):
        return ()
    result = []
    for category in categories:
        try:
            result.append(_number(table.loc[category, name]))
        except (KeyError, TypeError, IndexError):
            result.append(None)
    return tuple(result)


def _raw_column(table: Any, categories: Sequence[str], name: str) -> tuple[float | None, ...]:
    if table is None or not hasattr(table, "loc"):
        return ()
    result = []
    for category in categories:
        try:
            result.append(_raw_number(table.loc[category, name]))
        except (KeyError, TypeError, IndexError):
            result.append(None)
    return tuple(result)


def _series(
    name: str,
    chart_type: str,
    values: Sequence[float | None],
    role: str = "neutral",
) -> SeriesSpec:
    return SeriesSpec(name=name, chart_type=chart_type, values=tuple(values), role=role)


def _has_values(series: Sequence[SeriesSpec]) -> bool:
    return any(any(value is not None for value in item.values) for item in series)


def _add_cartesian(
    charts: list[ChartSpec],
    omitted: list[OmittedChart],
    chart_id: str,
    title: str,
    categories: Sequence[str],
    series: Sequence[SeriesSpec],
) -> None:
    usable = tuple(item for item in series if item.values)
    if not categories or not usable or not _has_values(usable):
        omitted.append(OmittedChart(chart_id=chart_id, reason="缺少可用月度序列"))
        return
    charts.append(ChartSpec(
        chart_id=chart_id,
        title=title,
        kind="cartesian",
        categories=tuple(categories),
        series=usable,
    ))


def _external_transactions(frame: Any) -> Any:
    required = {"内部互转", "二级标签", "收入金额", "支出金额"}
    if frame is None or not hasattr(frame, "columns"):
        return None
    if not required.issubset({str(value) for value in frame.columns}):
        return None
    mask = frame["内部互转"].fillna(False).astype(bool)
    return frame.loc[~mask]


def _category_amounts(frame: Any, amount_column: str) -> tuple[tuple[str, float], ...]:
    if frame is None or len(frame) == 0:
        return ()
    labels = frame["二级标签"].fillna("").astype(str).str.strip().replace("", "未标注")
    amounts = frame[amount_column].map(_number)
    valid = amounts.fillna(0) > 0
    grouped = amounts[valid].groupby(labels[valid]).sum().sort_values(ascending=False).head(8)
    return tuple((str(name), round(float(value), 2)) for name, value in grouped.items())


def _add_pie(
    charts: list[ChartSpec],
    omitted: list[OmittedChart],
    chart_id: str,
    title: str,
    values: Sequence[tuple[str, float]],
    role: str,
) -> None:
    if not values:
        omitted.append(OmittedChart(chart_id=chart_id, reason="缺少可用分类金额"))
        return
    charts.append(ChartSpec(
        chart_id=chart_id,
        title=title,
        kind="pie",
        categories=tuple(name for name, _ in values),
        series=(_series("金额", "pie", tuple(value for _, value in values), role),),
    ))


def _top_counterparties(table: Any, amount_column: str) -> tuple[tuple[str, float], ...]:
    if table is None or not hasattr(table, "sort_values"):
        return ()
    try:
        rows = table[table[amount_column] > 0].sort_values(
            amount_column, ascending=False
        ).head(10)
    except (KeyError, TypeError):
        return ()
    values = []
    for _, row in rows.iterrows():
        amount = _number(row.get(amount_column))
        if amount is not None:
            values.append((str(row.get("对手") or "未标注对手"), amount))
    return tuple(values)


def _add_horizontal_bar(
    charts: list[ChartSpec],
    omitted: list[OmittedChart],
    chart_id: str,
    title: str,
    values: Sequence[tuple[str, float]],
    role: str,
) -> None:
    if not values:
        omitted.append(OmittedChart(chart_id=chart_id, reason="缺少可用对手方金额"))
        return
    ordered = tuple(reversed(values))
    charts.append(ChartSpec(
        chart_id=chart_id,
        title=title,
        kind="horizontal_bar",
        categories=tuple(name for name, _ in ordered),
        series=(_series("金额", "bar", tuple(value for _, value in ordered), role),),
    ))


def _cash_projection(
    analysis: Mapping[str, Any], frame: Any
) -> tuple[
    list[str],
    list[tuple[str, list[float]]],
    dict[str, float | int],
] | None:
    months = [str(value) for value in analysis.get("months", []) or []]
    operating = analysis.get("mon_op")
    monthly = analysis.get("mon")
    if not months or operating is None or monthly is None:
        return None
    base_months = [str(value) for value in analysis.get("l12", months[-12:]) or []]
    inflow = _mean(_raw_column(operating, base_months, "经流入"))
    outflow = _mean(_raw_column(operating, base_months, "经流出"))
    if inflow is None or outflow is None:
        return None

    exchange_in = sum(value or 0 for value in _raw_column(monthly, base_months, "往来款流入"))
    exchange_out = 0.0
    required = {"月份", "二级标签", "支出金额"}
    if frame is not None and hasattr(frame, "columns") and required.issubset(set(frame.columns)):
        labels = frame["二级标签"].fillna("").astype(str)
        selected = labels.str.contains("往来") & frame["月份"].astype(str).isin(base_months)
        exchange_out = sum(_raw_number(value) or 0 for value in frame.loc[selected, "支出金额"])
    exchange_net = (exchange_in - exchange_out) / max(len(base_months), 1)

    configured_loan = analysis.get("new_loan") or (0.0, 0.0, 0)
    principal, annual_rate, term = configured_loan
    principal = _raw_number(principal) or 0.0
    annual_rate = _raw_number(annual_rate) or 0.0
    try:
        term = int(term)
    except (TypeError, ValueError):
        term = 0
    new_payment = float(round(_monthly_payment(principal, annual_rate, term)))
    debt_service = []
    debts = analysis.get("debts", []) or []
    for month_index in range(12):
        total = new_payment
        for debt in debts:
            if not isinstance(debt, Mapping):
                continue
            total += _raw_number(debt.get("pmt")) or 0.0
            if month_index == 5:
                total += _raw_number(debt.get("balloon")) or 0.0
        debt_service.append(total)

    start_cash = _raw_number(analysis.get("closing_cash")) or 0.0
    future_months = _next_months(months[-1], 12)
    if not future_months:
        return None
    scenarios = []
    for name, inflow_factor, outflow_factor in (
        ("基线", 1.0, 1.0),
        ("压力情景1（收入-20%、支出-10%）", 0.8, 0.9),
        ("压力情景2（收入-30%、支出-15%）", 0.7, 0.85),
    ):
        cash = start_cash
        values = []
        for index in range(12):
            cash += inflow * inflow_factor - outflow * outflow_factor + exchange_net
            cash -= debt_service[index]
            if index == 0:
                cash += principal
            values.append(round(float(cash), 2))
        scenarios.append((name, values))
    assumptions: dict[str, float | int] = {
        "opening_cash": round(start_cash, 2),
        "monthly_operating_inflow": round(inflow, 2),
        "monthly_operating_outflow": round(outflow, 2),
        "monthly_related_party_net_inflow": round(exchange_net, 2),
        "new_loan_principal": round(principal, 2),
        "new_loan_annual_rate": annual_rate,
        "new_loan_term_months": term,
    }
    return future_months, scenarios, assumptions


def _mean(values: Sequence[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return sum(present) / len(present) if present else None


def _monthly_payment(principal: float, annual_rate: float, term: int) -> float:
    if principal <= 0 or term <= 0:
        return 0.0
    monthly_rate = annual_rate / 12
    if monthly_rate == 0:
        return principal / term
    return principal * monthly_rate / (1 - (1 + monthly_rate) ** -term)


def _next_months(last_month: str, count: int) -> list[str]:
    try:
        year, month = (int(value) for value in last_month.split("-")[:2])
        if not 1 <= month <= 12:
            return []
    except (TypeError, ValueError):
        return []
    result = []
    for _ in range(count):
        month += 1
        if month == 13:
            year += 1
            month = 1
        result.append(f"{year:04d}-{month:02d}")
    return result
