import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_ROOT = REPO_ROOT / "ymb-standardization-core"
SKILL_ROOT = REPO_ROOT / "bank-statement-standardization"
SCRIPTS = SKILL_ROOT / "scripts"
for path in (str(CORE_ROOT), str(SKILL_ROOT), str(SCRIPTS)):
    if path not in sys.path:
        sys.path.insert(0, path)

from ymb_standardization_core.contracts import RouteDecision, StandardizationContext
from ymb_standardization_core import core
from runtime.contracts import IntegrationContext, StageResult, yaml_route_summary


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


from runtime import deliverable as package_deliverable
from runtime import integrate

orchestrator = load_module("orchestrator_contract_test", SCRIPTS / "orchestrator.py")


class StageContractsTest(unittest.TestCase):
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

    def test_orchestrator_uses_public_package_boundary(self):
        self.assertTrue(hasattr(package_deliverable, "finalize_deliverable"))
        self.assertFalse(hasattr(package_deliverable, "_finalize"))
        self.assertFalse(hasattr(orchestrator.Runner, "run_pipeline"))
        self.assertFalse(hasattr(orchestrator.Runner, "validate"))


if __name__ == "__main__":
    unittest.main()
