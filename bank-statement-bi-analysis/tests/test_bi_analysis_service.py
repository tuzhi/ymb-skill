from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import sys
import pandas as pd


BI_ROOT = Path(__file__).resolve().parents[1]
if str(BI_ROOT) not in sys.path:
    sys.path.insert(0, str(BI_ROOT))

from bank_statement_bi_analysis import (  # noqa: E402
    AIAnalysisSummaryDTO,
    BiAnalysisRequest,
    BiAnalysisService,
)


def test_execute_analysis_returns_synchronous_dto(tmp_path):
    source = tmp_path / "runs" / "run-1" / "artifacts" / "客户_已清洗_待分析.xlsx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"xlsx")
    months = ["2026-01", "2026-02"]
    frame = pd.DataFrame({
        "内部互转": [False, False, False],
        "二级标签": ["主营业务", "采购", "主营业务"],
        "收入金额": [60.0, 0.0, 40.0],
        "支出金额": [0.0, 50.0, 30.0],
        "月份": ["2026-01", "2026-01", "2026-02"],
    })
    monthly = pd.DataFrame({
        "流入": [60.0, 40.0],
        "流出": [50.0, 30.0],
        "净流入": [10.0, 10.0],
        "月末余额": [110.0, 120.0],
        "月均余额": [105.0, 115.0],
        "月最低余额": [90.0, 100.0],
        "融资流入(修正)": [0.0, 5.0],
        "往来款流入": [0.0, 0.0],
    }, index=months)
    operating = pd.DataFrame({
        "经流入": [60.0, 35.0],
        "经流出": [50.0, 30.0],
    }, index=months)
    counterparties = pd.DataFrame({
        "对手": ["甲公司", "乙公司"],
        "流入": [60.0, 40.0],
        "流出": [10.0, 70.0],
    })
    loan_monthly = pd.DataFrame({
        "借贷流入": [0.0, 5.0],
        "偿还借贷": [2.0, 2.0],
        "净额": [-2.0, 3.0],
    }, index=months)
    engine = SimpleNamespace(
        NEW_LOAN=(0.0, 0.0, 0),
        STRATEGY_VERSION="BANKFLOW-BI-V4.0",
        pick_input=lambda path: path,
        load_v4=lambda path: (frame, None, None),
        prep=lambda value, **_kwargs: value,
        daily_balance=lambda value, balances: None,
        analyze=lambda *args: {
            "q_total": 88,
            "q_grade": "良好",
            "months": months,
            "l12": months,
            "mon": monthly,
            "mon_op": operating,
            "cp": counterparties,
            "closing_cash": 120.0,
            "new_loan": (0.0, 0.0, 0),
            "debts": [],
        },
        analyze_v4=lambda *args: {
            "eff_in": 100.0,
            "eff_out": 80.0,
            "sales": {"mid": 90.0},
            "watch": [["甲"]],
            "n_night": 2,
            "loan_mon": loan_monthly,
            "currencies": ["人民币"],
        },
        spec_augment=lambda value: value,
        compute_spec_metrics=lambda *args, **kwargs: [],
        build_workbook=lambda *args, **kwargs: Path(args[4]).write_bytes(b"report"),
    )
    service = BiAnalysisService(tmp_path)
    with mock.patch.object(service, "_engine", return_value=engine):
        result = service.execute_analysis(BiAnalysisRequest(
            bi_run_id="bi-1",
            statement_run_id="run-1",
            standardized_file_path=str(source),
            client_name="客户甲",
        ))

    assert result.status == "DONE"
    assert result.ai_analysis_summary == AIAnalysisSummaryDTO(
        effective_inflow=100.0,
        effective_outflow=80.0,
        annual_sales_median=90.0,
        attention_counterparty_count=1,
        night_sensitive_expense_count=2,
        data_quality_score=88.0,
        data_quality_grade="良好",
    )
    assert asdict(result)["ai_analysis_summary"] == {
        "effective_inflow": 100.0,
        "effective_outflow": 80.0,
        "annual_sales_median": 90.0,
        "attention_counterparty_count": 1,
        "night_sensitive_expense_count": 2,
        "data_quality_score": 88.0,
        "data_quality_grade": "良好",
    }
    assert Path(result.artifacts["bi_report_path"]).is_file()
    assert Path(result.artifacts["bi_report_path"]).parent == tmp_path.resolve() / "bi_output"
    assert result.chart_data["echarts_version"] == "5"
    assert result.chart_data["schema_version"] == "1.0"
    assert result.chart_data["strategy_version"] == "BANKFLOW-BI-V4.0"
    assert result.chart_data["source_statement_run_id"] == "run-1"
    assert result.chart_data["currency"] == "人民币"
    assert [chart["chart_id"] for chart in result.chart_data["charts"]] == [
        "monthly_cash_flow",
        "monthly_balance",
        "monthly_inflow_composition",
        "monthly_loan_trend",
        "external_income_structure",
        "external_expense_structure",
        "top_inflow_counterparties",
        "top_outflow_counterparties",
        "future_cash_projection",
    ]
    json.dumps(result.chart_data, ensure_ascii=False, allow_nan=False)
    assert result.chart_data["omitted_charts"] == []
    assert result.chart_data["warnings"] == []


def test_execute_analysis_returns_structured_error(tmp_path):
    result = BiAnalysisService(tmp_path).execute_analysis(BiAnalysisRequest(
        bi_run_id="bi-1",
        statement_run_id="run-1",
        standardized_file_path=str(tmp_path / "runs" / "missing.xlsx"),
        client_name="客户甲",
    ))

    assert result.status == "ERROR"
    assert result.error is not None
    assert result.error.code == "BI_ANALYSIS_FAILED"
    assert result.ai_analysis_summary == AIAnalysisSummaryDTO()


def test_execute_analysis_accepts_standardization_dataset_without_reading_excel(tmp_path):
    transactions = pd.DataFrame([{"交易唯一编号": "TX-1"}])
    daily = pd.DataFrame([{"日期": "2026-01-01", "合计余额": 1.0}])
    checks = pd.DataFrame([{"账户": "A-1", "余额断点": 0}])
    prep = mock.Mock(side_effect=lambda value, **_kwargs: value)
    engine = SimpleNamespace(
        NEW_LOAN=(0.0, 0.0, 0),
        STRATEGY_VERSION="BANKFLOW-BI-V4.0",
        prep=prep,
        daily_balance=lambda value, balances: balances,
        analyze=lambda *args: {"q_total": 100, "q_grade": "优"},
        analyze_v4=lambda *args: {
            "eff_in": 1.0,
            "eff_out": 0.0,
            "sales": {"mid": 1.0},
            "watch": [],
            "n_night": 0,
        },
        spec_augment=lambda value: value,
        compute_spec_metrics=lambda *args, **kwargs: [],
        build_workbook=lambda *args, **kwargs: Path(args[4]).write_bytes(b"report"),
        load_v4=mock.Mock(side_effect=AssertionError("内存路径不应读取 Excel")),
    )
    service = BiAnalysisService(tmp_path)
    with mock.patch.object(service, "_engine", return_value=engine):
        result = service.execute_analysis(BiAnalysisRequest(
            bi_run_id="bi-memory",
            statement_run_id="run-1",
            standardized_file_path="",
            client_name="客户甲",
            dataset={
                "transactions": transactions,
                "daily_balances": daily,
                "balance_checks": checks,
            },
        ))

    assert result.status == "DONE"
    engine.load_v4.assert_not_called()
    prep.assert_called_once_with(transactions, normalize_types=False)


def test_services_require_absolute_workspace_path():
    try:
        BiAnalysisService("relative/task")
    except ValueError as exc:
        assert "绝对路径" in str(exc)
    else:
        raise AssertionError("relative workspace should be rejected")


def test_service_initializes_singular_input_directory(tmp_path):
    service = BiAnalysisService(tmp_path)

    assert service.input_root == tmp_path.resolve() / "input"
    assert service.input_root.is_dir()


def test_bi_rejects_standardized_file_outside_workspace(tmp_path):
    outside = tmp_path.parent / "outside.xlsx"
    result = BiAnalysisService(tmp_path).execute_analysis(BiAnalysisRequest(
        bi_run_id="bi-1",
        statement_run_id="run-1",
        standardized_file_path=str(outside),
        client_name="客户甲",
    ))

    assert result.status == "ERROR"
    assert result.error is not None
    assert "Workspace runs/" in result.error.message


def test_bi_rejects_client_name_path_escape(tmp_path):
    try:
        BiAnalysisService(tmp_path)._output_path("../escape")
    except ValueError as exc:
        assert "路径字符" in str(exc)
    else:
        raise AssertionError("client name path escape should be rejected")
