import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
CORE_PACKAGE = REPO_ROOT / "packages" / "ymb_standardization_core"
if str(CORE_PACKAGE) not in sys.path:
    sys.path.insert(0, str(CORE_PACKAGE))

ROUTER_SPEC = importlib.util.spec_from_file_location(
    "router",
    CORE_PACKAGE / "ymb_standardization_core" / "parsers" / "router.py")
router = importlib.util.module_from_spec(ROUTER_SPEC)
ROUTER_SPEC.loader.exec_module(router)

from ymb_standardization_core.parsers.routing.rule_loader import load_pdf_route_rules  # noqa: E402


class PdfRouterDecisionTests(unittest.TestCase):
    def test_pdf_specialized_routes_are_loaded_from_config(self):
        rules = load_pdf_route_rules()

        self.assertEqual(
            [rule.parser for rule in rules],
            [
                "abc_text_pdf",
                "jiangxi_rural_commercial_pdf_text",
                "kasikorn_pdf_text",
                "zhejiang_qyrcb_pdf_text",
            ],
        )
        self.assertEqual(rules[0].bank, "中国农业银行")
        self.assertEqual(rules[0].file_type, "pdf")
        self.assertEqual(rules[0].version, "1.0")

    def test_ambiguous_when_two_specialized_pdf_routes_match(self):
        text = (
            "中国农业银行账户活期交易明细清单 "
            "K PLUS K BIZ AccountMR. Account Number "
            "01-01-26 10:00 Transfer 100.00 "
            "02-01-26 10:00 Transfer 100.00 "
            "03-01-26 10:00 Transfer 100.00 "
            "04-01-26 10:00 Transfer 100.00 "
            "05-01-26 10:00 Transfer 100.00"
        )

        result = router.route_pdf(text, 0, 1)

        self.assertEqual(result["parser"], "ambiguous_router_match")
        self.assertEqual(result["decision"], "ambiguous")
        self.assertEqual(
            [candidate["parser"] for candidate in result["candidates"]],
            ["abc_text_pdf", "kasikorn_pdf_text"],
        )

    def test_specialized_pdf_route_exposes_identity_and_layout_evidence(self):
        text = (
            "江西·农商银行 户 名 张华峰 账 号 6226822011500474554 起止日期 "
            "2025-01-01 1.00 2.00 2025-01-02 1.00 3.00 2025-01-03 1.00 4.00 "
            "2025-01-04 1.00 5.00 2025-01-05 1.00 6.00"
        )

        result = router.route_pdf(text, 0, 1)

        self.assertEqual(result["parser"], "jiangxi_rural_commercial_pdf_text")
        self.assertEqual(result["decision"], "matched")
        self.assertEqual(result["bank"], "江西农商银行")
        self.assertEqual(result["file_type"], "pdf")
        self.assertEqual(result["version"], "1.0")
        self.assertIn("江西·农商银行", result["identity_evidence"])
        self.assertIn("户 名", result["layout_evidence"])

    def test_transaction_regex_evidence_is_not_required_for_specialized_route(self):
        text = (
            "江西·农商银行 户 名 张华峰 账 号 6226822011500474554 起止日期"
        )

        result = router.route_pdf(text, 0, 1)

        self.assertEqual(result["parser"], "jiangxi_rural_commercial_pdf_text")
        self.assertEqual(result["decision"], "matched")
        self.assertNotIn("route_evidence", result)


if __name__ == "__main__":
    unittest.main()
