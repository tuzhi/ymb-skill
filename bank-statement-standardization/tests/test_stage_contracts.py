import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_ROOT = REPO_ROOT / "ymb-standardization-core" / "src"
SKILL_ROOT = REPO_ROOT / "bank-statement-standardization"
for path in (str(CORE_ROOT), str(SKILL_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from ymb_standardization_core.contracts import RouteDecision, StandardizationContext
from ymb_standardization_core import core
from runtime.contracts import yaml_route_summary
from runtime.models import (
    IntegrationContext,
    StageResult,
)
from runtime import integrate
from runtime import portfolio_balance


class StageContractsTest(unittest.TestCase):
    def test_portfolio_analysis_reuses_canonical_frame_and_removes_temporary_columns(self):
        frame = pd.DataFrame([{
            "交易唯一编号": "TX-1",
            "交易时间": pd.Timestamp("2026-01-01 10:00:00"),
            "本方账户": "A-1",
            "收入金额": 100.0,
            "支出金额": 0.0,
            "账户余额": 100.0,
            "来源行号": "2026年1-3月!390",
        }])
        original_id = id(frame)

        daily, _report = portfolio_balance.analyze(frame)

        self.assertEqual(id(frame), original_id)
        self.assertEqual(len(daily), 1)
        self.assertFalse(any(str(column).startswith("__") for column in frame.columns))
        self.assertEqual(frame.at[0, "来源行号"], "2026年1-3月!390")
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(frame["交易时间"].dtype))
        self.assertTrue(pd.api.types.is_numeric_dtype(frame["收入金额"].dtype))

    def test_route_decision_preserves_dict_and_json_contract(self):
        raw = {
            "fingerprint_id": "excel-demo",
            "reader_id": "openpyxl_grid",
            "decision": "matched",
            "bank": "江西农商银行",
            "account_type": "对公",
            "extract_mapping": [{"field": "本方账户"}],
            "identity_evidence": ["抬头"],
        }
        decision = RouteDecision.from_mapping(raw)

        self.assertEqual(dict(decision), raw)
        self.assertEqual(json.loads(json.dumps(decision, ensure_ascii=False)), raw)
        self.assertEqual(decision.fingerprint_id, "excel-demo")
        self.assertEqual(decision.transform_ids, ("extract_mapping",))

    def test_standardization_context_delegates_to_legacy_compatible_entry(self):
        context = StandardizationContext(
            path="流水.xlsx",
            out_dir="output",
            account_type="对公",
            overrides={"账号": "本方账户"},
        )
        expected = ("flow.csv", "mapping.json", {"标准化统计": {}})
        with patch.object(core, "standardize", return_value=expected) as standardize:
            self.assertEqual(core.standardize_file(context), expected)

        standardize.assert_called_once_with(
            "流水.xlsx",
            out_dir="output",
            bank=None,
            account_type="对公",
            header_row=None,
            overrides={"账号": "本方账户"},
            write_mapping=True,
        )

    def test_yaml_route_summary_contains_only_stage_contract_fields(self):
        summary = yaml_route_summary({"文件画像": {
            "decision": "matched",
            "fingerprint_id": "md5:abc",
            "series_family": "family-v1",
            "router_bank": "招商银行",
            "inferred_bank": "内部弱推断银行",
            "reader_id": "pdfplumber_table",
        }})
        self.assertEqual(summary, {
            "fingerprint_id": "md5:abc",
            "series_family": "family-v1",
            "router_bank": "招商银行",
            "reader_id": "pdfplumber_table",
            "account_type": "未知",
            "yaml_match_status": "matched",
        })

    def test_integration_context_delegates_to_existing_business_function(self):
        context = IntegrationContext.create(
            "客户",
            ["work"],
            out_dir="output",
            self_accounts=["6217"],
        )
        expected = ("flow.csv", "report.json", {"客户整合概览": {}})
        with patch.object(integrate, "integrate", return_value=expected) as legacy:
            self.assertEqual(integrate.integrate_context(context), expected)

        legacy.assert_called_once_with(
            "客户", ["work"], out_dir="output", self_accounts=["6217"], file_routes={}
        )

    def test_stage_result_remains_receipt_serializable(self):
        result = StageResult("stage_2_integrate", {"integrated_rows": 10})
        self.assertEqual(result.stage_id, "stage_2_integrate")
        self.assertEqual(json.loads(json.dumps(result)), {"integrated_rows": 10})

if __name__ == "__main__":
    unittest.main()
