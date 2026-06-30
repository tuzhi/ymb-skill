import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
CORE_PACKAGE = REPO_ROOT / "ymb-standardization-core"
if str(CORE_PACKAGE) not in sys.path:
    sys.path.insert(0, str(CORE_PACKAGE))

from ymb_standardization_core import core  # noqa: E402
from ymb_standardization_core.parsers.routing.rule_loader import ExcelRouteRule  # noqa: E402
from ymb_standardization_core.parsers.routing.rule_loader import fingerprint_md5  # noqa: E402


def load_input_router():
    spec = importlib.util.spec_from_file_location(
        "input_router",
        CORE_PACKAGE / "ymb_standardization_core" / "parsers" / "input_router.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.configure_readers(core.read_rows_excel, core.read_rows_csv, core.NotABankStatement)
    return module


class InputRouterTests(unittest.TestCase):
    def test_csv_input_is_not_supported_as_raw_statement(self):
        module = load_input_router()
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "sample.csv"
            csv_path.write_text("交易日期,收入金额,账户余额\n2026-01-01,1.00,2.00\n", encoding="utf-8")

            with self.assertRaises(core.NotABankStatement) as cm:
                module.read_rows(str(csv_path))

        self.assertIn("CSV/TXT/TSV 当前不作为原始流水支持格式", str(cm.exception))

    def test_excel_input_uses_specialized_excel_route(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "丰城市利华金属制品有限公司" / "2025.5.1-2025.5.31农行.xlsx"
        if not excel.exists():
            self.skipTest("本地未提供 Excel 样本")

        result = module.read_rows(str(excel))

        self.assertEqual(result.kind, "excel")
        self.assertEqual(result.route_info["parser"], "abc_account_detail_excel")
        self.assertEqual(result.route_info["decision"], "matched")
        self.assertEqual(result.route_info["file_type"], "excel")
        self.assertEqual(result.route_info["bank"], "中国农业银行")
        self.assertEqual(result.route_info["account_type"], "对公")

    def test_legacy_abc_xls_uses_specialized_excel_route(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "丰城市利华金属制品有限公司" / "2025.1.1-2025.1.31农行.xls"
        if not excel.exists():
            self.skipTest("本地未提供农行旧版 XLS 样本")

        result = module.read_rows(str(excel))

        self.assertEqual(result.route_info["parser"], "abc_legacy_account_detail_excel")
        self.assertEqual(result.route_info["decision"], "matched")
        self.assertEqual(result.route_info["bank"], "中国农业银行")
        self.assertTrue(result.route_info["style_evidence"])

    def test_historydetail_debit_credit_excel_route_does_not_infer_bank(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "万建平" / "historydetail375.xlsx"

        result = module.read_rows(str(excel))

        self.assertEqual(result.route_info["parser"], "historydetail_debit_credit_excel")
        self.assertEqual(result.route_info["decision"], "matched")
        self.assertEqual(result.route_info["bank"], "未识别")
        self.assertEqual(result.route_info["account_type"], "对公")

    def test_historydetail_transfer_amount_excel_route_does_not_infer_bank(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "共青极兔" / "2025年10月.xlsx"

        result = module.read_rows(str(excel))

        self.assertEqual(result.route_info["parser"], "historydetail_transfer_amount_excel")
        self.assertEqual(result.route_info["decision"], "matched")
        self.assertEqual(result.route_info["bank"], "未识别")
        self.assertEqual(result.route_info["account_type"], "对公")

    def test_boc_hisxls_bilingual_corporate_excel_route(self):
        module = load_input_router()
        excel = (
            ROOT
            / "testdata"
            / "吉安超创电子PCB"
            / "HISXLS-20250101-20250630-0842744197688651565.xls"
        )

        result = module.read_rows(str(excel))

        self.assertEqual(result.route_info["parser"], "boc_hisxls_bilingual_corporate_excel")
        self.assertEqual(result.route_info["decision"], "matched")
        self.assertEqual(result.route_info["bank"], "中国银行")
        self.assertEqual(result.route_info["account_type"], "对公")

    def test_account_transaction_detail_export_excel_does_not_infer_bank(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "奥特联盈" / "2022.08.01-2023.08.01.xls"

        result = module.read_rows(str(excel))

        self.assertEqual(result.route_info["parser"], "account_transaction_detail_export_excel")
        self.assertEqual(result.route_info["decision"], "matched")
        self.assertEqual(result.route_info["bank"], "未识别")
        self.assertEqual(result.route_info["account_type"], "对公")

    def test_account_header_debit_credit_excel_does_not_infer_bank_from_filename(self):
        module = load_input_router()
        excel = (
            ROOT
            / "testdata"
            / "广州沛瑾家具"
            / "广州沛瑾家具有限公司_中国工商银行_TF_1.xlsx"
        )

        result = module.read_rows(str(excel))

        self.assertEqual(result.route_info["parser"], "account_header_debit_credit_excel")
        self.assertEqual(result.route_info["decision"], "matched")
        self.assertEqual(result.route_info["bank"], "未识别")
        self.assertEqual(result.route_info["account_type"], "对公")

    def test_ccb_flat_xls_has_single_specialized_route(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "张运贞" / "25年1-5月.xls"
        if not excel.exists():
            self.skipTest("本地未提供建行扁平对公 XLS 样本")

        result = module.read_rows(str(excel))

        self.assertEqual(result.route_info["parser"], "ccb_corporate_flat_excel")
        self.assertEqual(result.route_info["decision"], "matched")
        self.assertEqual(result.route_info["bank"], "中国建设银行")

    def test_unknown_excel_structure_uses_generic_fallback_route(self):
        module = load_input_router()

        def fake_excel_reader(_path):
            return "Sheet1", [["not", "a", "known", "statement"], ["1", "2", "3", "4"]]

        module.configure_readers(fake_excel_reader, core.read_rows_csv, core.NotABankStatement)

        result = module.read_rows("unknown.xlsx")

        self.assertEqual(result.kind, "excel")
        self.assertEqual(result.route_info["parser"], "generic_excel")
        self.assertEqual(result.route_info["decision"], "unmatched")
        self.assertEqual(result.route_info["file_type"], "excel")

    def test_excel_route_config_uses_fingerprint_for_identity_and_layout(self):
        rules_path = CORE_PACKAGE / "ymb_standardization_core" / "parsers" / "routing" / "excel_rules.yaml"
        items = yaml.safe_load(rules_path.read_text(encoding="utf-8"))

        for item in items:
            self.assertNotIn("identity", item)
            self.assertNotIn("layout", item)
            fingerprint = item.get("fingerprint") or {}
            self.assertIn("id", item)
            self.assertNotIn("version", item)
            self.assertEqual(item["id"], fingerprint_md5(fingerprint))
            self.assertIn("identity", fingerprint)
            self.assertIn("layout", fingerprint)

    def test_excel_route_without_yaml_fingerprint_falls_back_to_generic(self):
        module = load_input_router()
        original = module.load_excel_route_rules
        try:
            module.load_excel_route_rules = lambda: [
                ExcelRouteRule(
                    id="md5:test",
                    parser="unfingerprinted_excel",
                    file_type="excel",
                    bank="测试银行",
                    account_type="未知",
                    identity_any=["测试银行"],
                    layout_all=["交易时间", "账户余额"],
                    metadata_all={},
                    style_all=[],
                    data_all=[],
                    date_format_any=[],
                )
            ]

            route = module.route_excel([["测试银行", "交易时间", "账户余额"]], "Sheet1", context={})

            self.assertEqual(route["parser"], "generic_excel")
            self.assertEqual(route["decision"], "unmatched")
            self.assertIn("candidate_fingerprints", route)
            self.assertEqual(route["candidate_fingerprints"][0]["parser"], "unfingerprinted_excel")
            self.assertEqual(route["candidate_fingerprints"][0]["reason"], "missing_yaml_fingerprint")
        finally:
            module.load_excel_route_rules = original

    def test_headerless_excel_transfer_detail_route(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "杨德嘎" / "20260611105021.xlsx"
        if not excel.exists():
            self.skipTest("本地未提供无抬头 Excel 样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertEqual(route["parser"], "headerless_excel")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "未识别")
        self.assertTrue(route["metadata_evidence"])
        self.assertTrue(route["style_evidence"])

    def test_account_query_result_routes_to_nanchang_rural_commercial_bank(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "秦国有" / "20260604 (2).xls"
        if not excel.exists():
            self.skipTest("本地未提供南昌农商账户明细查询样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertEqual(route["parser"], "nanchang_rural_commercial_account_query_excel")
        self.assertEqual(route["bank"], "南昌农村商业银行股份有限公司")
        self.assertTrue(route["style_evidence"])
        self.assertTrue(route["data_evidence"])

    def test_account_query_result_routes_to_jiangxi_lushan_rural_commercial_bank(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "袁军" / "1-3.xls"
        if not excel.exists():
            self.skipTest("本地未提供江西庐山农商账户明细查询样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertEqual(route["parser"], "jiangxi_lushan_rural_commercial_account_query_excel")
        self.assertEqual(route["bank"], "江西庐山农村商业银行")
        self.assertTrue(route["style_evidence"])
        self.assertTrue(route["data_evidence"])

    def test_pdf_input_keeps_existing_pdf_router(self):
        module = load_input_router()
        pdf = ROOT / "testdata" / "李先根" / "GRZD-9A202606081958362818-20250608-20260607-X_unsign_sign_18831.pdf"
        if not pdf.exists():
            self.skipTest("本地未提供李先根 GRZD 浙江庆元农商 PDF 样本")

        result = module.read_rows(str(pdf))

        self.assertEqual(result.kind, "pdf")
        self.assertEqual(result.route_info["parser"], "zhejiang_qyrcb_pdf_text")
        self.assertGreater(len(result.rows), 200)

    def test_cmb_transaction_pdf_route_matches_local_sample(self):
        module = load_input_router()
        pdf = (
            ROOT
            / "testdata"
            / "宁聚&付亮亮&徐美琴"
            / "付亮亮招商银行交易流水(申请时间2026年03月10日17时56分58秒).pdf"
        )

        result = module.read_rows(str(pdf))

        self.assertEqual(result.kind, "pdf")
        self.assertEqual(result.route_info["parser"], "cmb_transaction_pdf")
        self.assertEqual(result.route_info["decision"], "matched")
        self.assertEqual(result.route_info["bank"], "招商银行")
        self.assertEqual(result.route_info["account_type"], "个人")
        self.assertEqual(len(result.rows), 0)

    def test_corporate_account_statement_pdf_route_does_not_infer_bank(self):
        module = load_input_router()
        pdf = ROOT / "testdata" / "宁聚&付亮亮&徐美琴" / "宁聚招商银行基本户1245.pdf"

        result = module.read_rows(str(pdf))

        self.assertEqual(result.kind, "pdf")
        self.assertEqual(result.route_info["parser"], "corporate_account_statement_pdf_text")
        self.assertEqual(result.route_info["decision"], "matched")
        self.assertEqual(result.route_info["bank"], "未识别")
        self.assertEqual(result.route_info["account_type"], "对公")
        self.assertEqual(len(result.rows), 0)

    def test_corporate_account_statement_excel_route_matches_openpyxl_sample(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "宁聚&付亮亮&徐美琴" / "宁聚招商银行基本户1245.xlsx"

        result = module.read_rows(str(excel))

        self.assertEqual(result.kind, "excel")
        self.assertEqual(result.route_info["parser"], "corporate_account_statement_excel")
        self.assertEqual(result.route_info["decision"], "matched")
        self.assertEqual(result.route_info["bank"], "招商银行")
        self.assertEqual(result.route_info["account_type"], "对公")

    def test_card_detail_download_excel_route_does_not_infer_bank(self):
        module = load_input_router()
        excel = (
            ROOT
            / "testdata"
            / "广州沛瑾家具"
            / "广州沛瑾家具有限公司@李果红_中国工商银行_TF_1.xlsx"
        )

        result = module.read_rows(str(excel))

        self.assertEqual(result.kind, "excel")
        self.assertEqual(result.route_info["parser"], "card_detail_download_excel")
        self.assertEqual(result.route_info["decision"], "matched")
        self.assertEqual(result.route_info["bank"], "未识别")
        self.assertEqual(result.route_info["account_type"], "个人")

    def test_jiujiang_bank_transaction_detail_excel_route(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "广源流水" / "九江银行交易明细1.xlsx"

        result = module.read_rows(str(excel))

        self.assertEqual(result.kind, "excel")
        self.assertEqual(result.route_info["parser"], "jiujiang_bank_transaction_detail_excel")
        self.assertEqual(result.route_info["decision"], "matched")
        self.assertEqual(result.route_info["bank"], "九江银行")
        self.assertEqual(result.route_info["account_type"], "对公")


if __name__ == "__main__":
    unittest.main()
