import importlib.util
import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_testdata_support.py"


def load_module():
    spec = importlib.util.spec_from_file_location("audit_testdata_support", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AuditTestdataSupportTests(unittest.TestCase):
    def test_iter_statement_files_skips_generated_outputs(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "客户A").mkdir()
            raw_pdf = root / "客户A" / "流水.pdf"
            raw_pdf.write_text("x", encoding="utf-8")
            raw_xlsx = root / "客户A" / "流水.xlsx"
            raw_xlsx.write_text("x", encoding="utf-8")
            generated = root / "客户A" / "客户A_已清洗_待分析.xlsx"
            generated.write_text("x", encoding="utf-8")
            matrix_xlsx = root / "support_matrix.xlsx"
            matrix_xlsx.write_text("x", encoding="utf-8")
            matrix_csv = root / "support_matrix.csv"
            matrix_csv.write_text("x", encoding="utf-8")
            matrix_md = root / "support_matrix.md"
            matrix_md.write_text("x", encoding="utf-8")
            baseline = root / "baseline_summary.json"
            baseline.write_text("x", encoding="utf-8")
            lock_file = root / "客户A" / "~$流水.xlsx"
            lock_file.write_text("x", encoding="utf-8")
            ignored = root / "客户A" / "说明.md"
            ignored.write_text("x", encoding="utf-8")

            files = list(module.iter_statement_files(root))

        self.assertEqual([p.name for p in files], ["流水.pdf", "流水.xlsx"])

    def test_write_excel_contains_requested_columns(self):
        module = load_module()
        rows = [{
            "银行": "浙江庆元农商银行",
            "账户类型(YAML)": "个人",
            "格式": "pdf",
            "版本": "个人账户交易明细",
            "文件路径": "testdata/李先根/GRZD.pdf",
            "router类": "zhejiang_qyrcb_pdf_text",
            "YAML指纹": "身份:2；结构:15",
            "测试类": "test_zhejiang_qyrcb_pdf_route.py",
            "测试日期": "2026-06-17",
            "测试结果": "PASS",
            "创建时间≈修改时间": "是（差0秒）",
            "创建人=修改人": "是（创建人:test；修改人:test）",
        }]

        with tempfile.TemporaryDirectory() as tmp:
            xlsx_path = Path(tmp) / "support_matrix.xlsx"
            module.write_xlsx(xlsx_path, rows)
            workbook = load_workbook(xlsx_path)
            sheet = workbook.active
            header = [cell.value for cell in sheet[1][:len(module.MATRIX_COLUMNS)]]
            first_row = [cell.value for cell in sheet[2][:len(module.MATRIX_COLUMNS)]]

        self.assertEqual(header, module.MATRIX_COLUMNS)
        self.assertIn("YAML指纹", header)
        self.assertEqual(first_row[0], "浙江庆元农商银行")
        self.assertIn("账户类型(YAML)", header)
        self.assertEqual(first_row[header.index("账户类型(YAML)")], "个人")
        self.assertEqual(first_row[header.index("router类")], "zhejiang_qyrcb_pdf_text")
        self.assertEqual(first_row[header.index("YAML指纹")], "身份:2；结构:15")
        self.assertIn("创建时间≈修改时间", header)
        self.assertIn("创建人=修改人", header)
        self.assertEqual(first_row[header.index("创建时间≈修改时间")], "是（差0秒）")
        self.assertEqual(first_row[header.index("创建人=修改人")], "是（创建人:test；修改人:test）")

    def test_bank_name_is_expanded_from_parser_and_template(self):
        module = load_module()

        self.assertEqual(
            module.normalize_bank_name("农村商业银行", "jiangxi_rural_commercial_pdf_text", "江西·农商银行"),
            "江西农商银行",
        )
        self.assertEqual(
            module.normalize_bank_name("农村商业银行", "zhejiang_qyrcb_pdf_text", "浙江庆元农商银行"),
            "浙江庆元农商银行",
        )
        self.assertEqual(
            module.normalize_bank_name("", "kasikorn_pdf_text", "Kasikorn Bank"),
            "开泰银行（Kasikorn Bank）",
        )
        self.assertEqual(
            module.normalize_bank_name("", "icbc_historydetail_excel", ""),
            "中国工商银行",
        )
        self.assertEqual(
            module.normalize_bank_name("", "abc_legacy_account_detail_excel", ""),
            "中国农业银行",
        )
        self.assertEqual(
            module.normalize_bank_name("", "wechat_bill_excel", ""),
            "微信支付",
        )
        self.assertEqual(module.normalize_bank_name("", "", ""), "未识别")

    def test_bank_name_uses_parser_when_file_name_is_not_trusted(self):
        module = load_module()

        self.assertEqual(
            module.support_matrix_bank_name(
                {"开户行识别来源": "文件名", "开户行": "工行"},
                "icbc_historydetail_excel",
                "",
            ),
            "中国工商银行",
        )
        self.assertEqual(
            module.support_matrix_bank_name(
                {"开户行识别来源": "文件名", "开户行": "农行"},
                "abc_account_detail_excel",
                "",
            ),
            "中国农业银行",
        )
        self.assertEqual(
            module.support_matrix_bank_name({"开户行识别来源": "文件名", "开户行": "工行"}, "", ""),
            "未识别",
        )

    def test_bank_name_prefers_yaml_bank_for_parser(self):
        module = load_module()
        original = module.ROUTE_RULE_INDEX
        try:
            module.ROUTE_RULE_INDEX = {
                "custom_parser": {"bank": "微信支付"},
            }
            self.assertEqual(
                module.support_matrix_bank_name(
                    {"开户行识别来源": "文件名", "开户行": "不可信文件名银行"},
                    "custom_parser",
                    "",
                ),
                "微信支付",
            )
        finally:
            module.ROUTE_RULE_INDEX = original

    def test_bank_name_uses_card_bin_report_when_yaml_bank_is_unknown(self):
        module = load_module()
        original = module.ROUTE_RULE_INDEX
        try:
            module.ROUTE_RULE_INDEX = {
                "personal_card_parser": {"bank": "未识别"},
            }
            self.assertEqual(
                module.support_matrix_bank_name(
                    {
                        "开户行识别来源": "card_bin",
                        "开户行": "中国工商银行",
                        "确认银行": "中国工商银行",
                    },
                    "personal_card_parser",
                    "",
                ),
                "中国工商银行",
            )
        finally:
            module.ROUTE_RULE_INDEX = original

    def test_yaml_account_type_uses_router_rule(self):
        module = load_module()
        original = module.ROUTE_RULE_INDEX
        try:
            module.ROUTE_RULE_INDEX = {
                "custom_parser": {"account_type": "个人"},
            }
            self.assertEqual(module.yaml_account_type("custom_parser"), "个人")
            self.assertEqual(module.yaml_account_type("missing_parser"), "")
            self.assertEqual(module.yaml_account_type(""), "")
        finally:
            module.ROUTE_RULE_INDEX = original

    def test_support_matrix_uses_fingerprint_md5_id_in_router_column(self):
        module = load_module()
        original = module.ROUTE_RULE_INDEX
        fingerprint = {
            "identity": {"any": ["中国工商银行账户明细清单"]},
        }
        try:
            module.ROUTE_RULE_INDEX = {
                "generic_pdf_table": {
                    "id": module.fingerprint_md5(fingerprint),
                    "fingerprint": fingerprint,
                },
            }

            self.assertEqual(
                module.support_matrix_fingerprint_id("generic_pdf_table"),
                module.fingerprint_md5(fingerprint),
            )
        finally:
            module.ROUTE_RULE_INDEX = original

    def test_support_matrix_requires_fingerprint_id_for_known_parser(self):
        module = load_module()
        original = module.ROUTE_RULE_INDEX
        try:
            module.ROUTE_RULE_INDEX = {
                "generic_pdf_table": {
                    "fingerprint": {
                        "identity": {"any": ["中国工商银行账户明细清单"]},
                    },
                },
            }

            with self.assertRaisesRegex(ValueError, "missing id"):
                module.support_matrix_fingerprint_id("generic_pdf_table")
        finally:
            module.ROUTE_RULE_INDEX = original

    def test_support_matrix_rejects_fingerprint_id_mismatch(self):
        module = load_module()
        original = module.ROUTE_RULE_INDEX
        try:
            module.ROUTE_RULE_INDEX = {
                "generic_pdf_table": {
                    "id": "md5:bad",
                    "fingerprint": {
                        "identity": {"any": ["中国工商银行账户明细清单"]},
                    },
                },
            }

            with self.assertRaisesRegex(ValueError, "fingerprint id mismatch"):
                module.support_matrix_fingerprint_id("generic_pdf_table")
        finally:
            module.ROUTE_RULE_INDEX = original

    def test_yaml_fingerprint_summary_reads_nested_identity_and_layout(self):
        module = load_module()
        original = module.ROUTE_RULE_INDEX
        try:
            module.ROUTE_RULE_INDEX = {
                "custom_parser": {
                    "fingerprint": {
                        "identity": {"any": ["测试银行"]},
                        "layout": {"all": ["交易时间", "账户余额"]},
                        "metadata": {"all": {"application": "UnitTest"}},
                    }
                },
            }

            summary = module.yaml_fingerprint_summary("custom_parser")

            self.assertIn("身份:1", summary)
            self.assertIn("结构:2", summary)
            self.assertIn("元数据:application", summary)
        finally:
            module.ROUTE_RULE_INDEX = original

    def test_supported_files_can_be_loaded_from_support_matrix_by_parser(self):
        module = load_module()
        rows = [
            {
                "银行": "测试银行",
                "账户类型(YAML)": "未知",
                "格式": "xlsx",
                "版本": "1.0",
                "文件路径": "客户A/a.xlsx",
                "router类": "strict_excel",
                "YAML指纹": "元数据:application",
                "测试结果": "PASS",
            },
            {
                "银行": "测试银行",
                "账户类型(YAML)": "未知",
                "格式": "xlsx",
                "版本": "1.0",
                "文件路径": "客户A/b.xlsx",
                "router类": "strict_excel",
                "YAML指纹": "元数据:application",
                "测试结果": "FAIL",
            },
            {
                "银行": "测试银行",
                "账户类型(YAML)": "未知",
                "格式": "pdf",
                "版本": "1.0",
                "文件路径": "客户A/c.pdf",
                "router类": "other_pdf",
                "YAML指纹": "数据:1",
                "测试结果": "PASS",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            matrix = Path(tmp) / "support_matrix.xlsx"
            module.write_xlsx(matrix, rows)

            files = module.support_matrix_files_for_parser(matrix, "strict_excel")

        self.assertEqual(files, ["客户A/a.xlsx"])


if __name__ == "__main__":
    unittest.main()
