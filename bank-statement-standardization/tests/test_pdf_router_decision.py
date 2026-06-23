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

        parser_names = [rule.parser for rule in rules]
        for parser in [
            "abc_text_pdf",
            "jiangxi_rural_commercial_pdf_text",
            "kasikorn_pdf_text",
            "zhejiang_qyrcb_pdf_text",
            "icbc_account_detail_pdf",
        ]:
            self.assertIn(parser, parser_names)
        self.assertEqual(rules[0].bank, "中国农业银行")
        self.assertEqual(rules[0].file_type, "pdf")
        self.assertEqual(rules[0].version, "1.0")

        by_parser = {rule.parser: rule for rule in rules}
        self.assertEqual(by_parser["abc_text_pdf"].account_type, "个人")
        for marker in ["交易日期", "交易时间", "交易摘要", "交易金额", "本次余额", "对手信息", "日志号", "交易渠道", "交易附言"]:
            self.assertIn(marker, by_parser["abc_text_pdf"].layout_all)
        self.assertEqual(by_parser["icbc_account_detail_pdf"].account_type, "对公")
        self.assertEqual(by_parser["icbc_account_detail_table_pdf"].account_type, "对公")

    def test_identity_only_kasikorn_markers_do_not_create_ambiguous_route(self):
        text = (
            "中国农业银行账户活期交易明细清单 "
            "交易日期 交易时间 交易摘要 交易金额 本次余额 对手信息 日志号 交易渠道 交易附言 "
            "K PLUS K BIZ AccountMR. Account Number "
            "01-01-26 10:00 Transfer 100.00 "
            "02-01-26 10:00 Transfer 100.00 "
            "03-01-26 10:00 Transfer 100.00 "
            "04-01-26 10:00 Transfer 100.00 "
            "05-01-26 10:00 Transfer 100.00"
        )

        result = router.route_pdf(text, 0, 1)

        self.assertEqual(result["parser"], "abc_text_pdf")
        self.assertEqual(result["decision"], "matched")

    def test_abc_text_pdf_requires_transaction_header_layout(self):
        result = router.route_pdf("中国农业银行账户活期交易明细清单", 0, 1)

        self.assertEqual(result["parser"], "generic_pdf_text_unmatched")

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

    def test_table_pdf_routes_to_specialized_icbc_parser(self):
        text = (
            "中国工商银行账户明细清单 账号：1502000209100022223 币种：人民币 "
            "交易时间 本方账号 对方户名 对方账号 对方账户开户行 凭证号 "
            "借/贷 借方发生额 贷方发生额 摘要 用途 余额"
        )

        result = router.route_pdf(text, 1, 1)

        self.assertEqual(result["parser"], "icbc_account_detail_pdf")
        self.assertEqual(result["decision"], "matched")
        self.assertEqual(result["bank"], "中国工商银行")
        self.assertEqual(result["file_type"], "pdf")
        self.assertEqual(result["account_type"], "对公")

    def test_icbc_debit_history_electronic_pdf_route(self):
        text = (
            "中国工商银行借记账户历史明细（电子版） 卡号 6215581502001090422 户名：秦国有 "
            "交易日期 账号 储种 序号 币种 钞汇 地区 收入/支出金额 余额 渠道"
        )
        context = {
            "metadata": {"Creator": "PDFium", "Producer": "PDFium"},
            "date_patterns": ["yyyy-mm-dd"],
            "styles": [],
            "lines": text.splitlines(),
        }

        result = router.route_pdf(text, 1, 1, context=context)

        self.assertEqual(result["parser"], "icbc_debit_history_electronic_pdf")
        self.assertEqual(result["decision"], "matched")
        self.assertEqual(result["account_type"], "个人")

    def test_abc_corporate_account_detail_pdf_route(self):
        text = (
            "账户明细 账号：14-016301040004172 户名：新建区西山罗广兴旺家电店 币种：人民币 起止日期 "
            "交易时间 收入金额 支出金额 账户余额 交易行名 对方城市 对方账号 对方户名 交易用途 会计日期"
        )
        context = {
            "metadata": {"Producer": "OpenPDF 1.3.32"},
            "date_patterns": [],
            "styles": [],
            "lines": text.splitlines(),
        }

        result = router.route_pdf(text, 1, 1, context=context)

        self.assertEqual(result["parser"], "abc_corporate_account_detail_pdf")
        self.assertEqual(result["decision"], "matched")
        self.assertEqual(result["bank"], "中国农业银行")

    def test_icbc_corporate_account_statement_pdf_route(self):
        text = (
            "中国工商银行账户明细清单 账号：1512201409000016548 本方账号户名 本方账号开户行 时间范围 "
            "对方账号 交易时间 借贷标志 对方单位 用途 摘要 附言 回单个性化信息 转出金额 转入金额 余额"
        )
        context = {
            "metadata": {"Producer": "iText 2.1.7 by 1T3XT"},
            "date_patterns": ["yyyy-mm-dd hh:mm:ss"],
            "styles": [],
            "lines": text.splitlines(),
        }

        result = router.route_pdf(text, 1, 1, context=context)

        self.assertEqual(result["parser"], "icbc_corporate_account_statement_pdf")
        self.assertEqual(result["decision"], "matched")
        self.assertEqual(result["account_type"], "对公")


if __name__ == "__main__":
    unittest.main()
