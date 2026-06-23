import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
CORE_PACKAGE = REPO_ROOT / "ymb-standardization-core"
if str(CORE_PACKAGE) not in sys.path:
    sys.path.insert(0, str(CORE_PACKAGE))

from ymb_standardization_core import core  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
