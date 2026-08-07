from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import sys


BI_ROOT = Path(__file__).resolve().parents[1]
if str(BI_ROOT) not in sys.path:
    sys.path.insert(0, str(BI_ROOT))

from bank_statement_bi_analysis import (  # noqa: E402
    BiAnalysisRequest,
    BiAnalysisService,
)


def test_execute_analysis_returns_synchronous_dto(tmp_path):
    source = tmp_path / "客户_已清洗_待分析.xlsx"
    source.write_bytes(b"xlsx")
    frame = object()
    engine = SimpleNamespace(
        NEW_LOAN=(0.0, 0.0, 0),
        pick_input=lambda path: path,
        load_v4=lambda path: (frame, None, None),
        prep=lambda value: value,
        daily_balance=lambda value, balances: None,
        analyze=lambda *args: {"q_total": 88, "q_grade": "良好"},
        analyze_v4=lambda *args: {
            "eff_in": 100.0,
            "eff_out": 80.0,
            "sales": {"mid": 90.0},
            "watch": [["甲"]],
            "n_night": 2,
        },
        spec_augment=lambda value: value,
        compute_spec_metrics=lambda *args, **kwargs: [],
        build_workbook=lambda *args: Path(args[4]).write_bytes(b"report"),
    )
    service = BiAnalysisService()
    with mock.patch.object(service, "_engine", return_value=engine):
        result = service.execute_analysis(BiAnalysisRequest(
            bi_run_id="bi-1",
            statement_run_id="run-1",
            standardized_file_path=str(source),
            client_name="客户甲",
            output_dir=str(tmp_path),
        ))

    assert result.status == "DONE"
    assert result.ai_analysis_summary["有效流入"] == 100.0
    assert Path(result.artifacts["bi_report_path"]).is_file()
    assert result.chart_data == {"echarts_version": "5", "charts": []}


def test_execute_analysis_returns_structured_error(tmp_path):
    result = BiAnalysisService().execute_analysis(BiAnalysisRequest(
        bi_run_id="bi-1",
        statement_run_id="run-1",
        standardized_file_path=str(tmp_path / "missing.xlsx"),
        client_name="客户甲",
    ))

    assert result.status == "ERROR"
    assert result.error is not None
    assert result.error.code == "BI_ANALYSIS_FAILED"
