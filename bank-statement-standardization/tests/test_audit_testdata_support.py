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
    def test_match_original_file_uses_duplicate_artifact_extension_marker(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "testdata"
            client_dir = root / "程旭"
            client_dir.mkdir(parents=True)
            pdf = client_dir / "鼎信网商银行2025.1.1-2025.7.31交易明细.pdf"
            xlsx = client_dir / "鼎信网商银行2025.1.1-2025.7.31交易明细.xlsx"
            pdf.write_bytes(b"%PDF-1.4\n")
            xlsx.write_text("xlsx", encoding="utf-8")
            work = Path(tmp) / "_package_work" / "047_程旭" / "_工作区" / "程旭"
            work.mkdir(parents=True)
            csv_path = work / "鼎信网商银行2025.1.1-2025.7.31交易明细__pdf__standardized.csv"
            csv_path.write_text("交易时间\n", encoding="utf-8-sig")
            files_by_client_and_name = {
                ("程旭", pdf.name): pdf,
                ("程旭", xlsx.name): xlsx,
            }

            matched = module._match_original_file(
                csv_path,
                root,
                files_by_client_and_name,
            )

        self.assertEqual(matched, pdf)

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

    def test_build_outputs_sleeps_between_files_by_default(self):
        module = load_module()
        calls = []

        def fake_iter_statement_files(root):
            return [Path(root) / "a.pdf", Path(root) / "b.pdf"]

        def fake_audit_one_file(path, root, output_work_dir, today):
            return (
                {
                    "测试结果": "PASS",
                    "文件路径": Path(path).name,
                },
                {
                    "file_path": Path(path).name,
                    "status": "PASS",
                },
            )

        original_iter = module.iter_statement_files
        original_audit = module.audit_one_file
        original_sleep = module.time.sleep
        try:
            module.iter_statement_files = fake_iter_statement_files
            module.audit_one_file = fake_audit_one_file
            module.time.sleep = lambda seconds: calls.append(seconds)
            with tempfile.TemporaryDirectory() as tmp:
                module.build_outputs(Path(tmp) / "testdata", Path(tmp) / "out")
        finally:
            module.iter_statement_files = original_iter
            module.audit_one_file = original_audit
            module.time.sleep = original_sleep

        self.assertEqual(calls, [0.5])

    def test_write_excel_contains_requested_columns(self):
        module = load_module()
        rows = [{
            "银行": "浙江庆元农商银行",
            "账户类型(YAML)": "个人",
            "格式": "pdf",
            "版本": "个人账户交易明细",
            "文件路径": "testdata/李先根/GRZD.pdf",
            "router类": "md5:69c7df7286e238aef80ae49938fd397a",
            "reader_id": "pdfplumber_coordinate_table",
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
        self.assertEqual(first_row[header.index("router类")], "md5:69c7df7286e238aef80ae49938fd397a")
        self.assertNotIn("命中parser", header)
        self.assertIn("reader_id", header)
        self.assertEqual(first_row[header.index("reader_id")], "pdfplumber_coordinate_table")
        self.assertEqual(first_row[header.index("YAML指纹")], "身份:2；结构:15")
        self.assertIn("创建时间≈修改时间", header)
        self.assertIn("创建人=修改人", header)
        self.assertEqual(first_row[header.index("创建时间≈修改时间")], "是（差0秒）")
        self.assertEqual(first_row[header.index("创建人=修改人")], "是（创建人:test；修改人:test）")

    def test_bank_name_is_expanded_from_fingerprint_and_template(self):
        module = load_module()

        self.assertEqual(
            module.normalize_bank_name("农村商业银行", "md5:0bdf0854f29ad6928e2fdd0da1d52dc5", "江西·农商银行"),
            "江西农商银行",
        )
        self.assertEqual(
            module.normalize_bank_name("农村商业银行", "md5:69c7df7286e238aef80ae49938fd397a", "浙江庆元农商银行"),
            "浙江庆元农商银行",
        )
        self.assertEqual(
            module.normalize_bank_name("", "md5:09ce033b3aeccb7c4dc7a47eac35e16d", "Kasikorn Bank"),
            "开泰银行（Kasikorn Bank）",
        )
        self.assertEqual(
            module.normalize_bank_name("中国工商银行", "", ""),
            "中国工商银行",
        )
        self.assertEqual(
            module.normalize_bank_name("中国农业银行", "", ""),
            "中国农业银行",
        )
        self.assertEqual(
            module.normalize_bank_name("微信支付", "", ""),
            "微信支付",
        )
        self.assertEqual(module.normalize_bank_name("", "", ""), "未识别")

    def test_bank_name_does_not_trust_file_name_without_fingerprint(self):
        module = load_module()

        self.assertEqual(
            module.support_matrix_bank_name({"开户行识别来源": "文件名", "开户行": "工行"}, "", ""),
            "未识别",
        )

    def test_bank_name_prefers_yaml_bank_for_parser(self):
        module = load_module()
        original = module.ROUTE_RULE_INDEX
        try:
            module.ROUTE_RULE_INDEX = {
                "md5:custom": {"bank": "微信支付"},
            }
            self.assertEqual(
                module.support_matrix_bank_name(
                    {"开户行识别来源": "文件名", "开户行": "不可信文件名银行"},
                    "md5:custom",
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
                "md5:personal": {"bank": "未识别"},
            }
            self.assertEqual(
                module.support_matrix_bank_name(
                    {
                        "开户行识别来源": "card_bin",
                        "开户行": "中国工商银行",
                        "确认银行": "中国工商银行",
                    },
                    "md5:personal",
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
                "md5:custom": {"account_type": "个人"},
            }
            self.assertEqual(module.yaml_account_type("md5:custom"), "个人")
            self.assertEqual(module.yaml_account_type("md5:missing"), "")
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
                "md5:strict": {
                    "id": module.fingerprint_md5(fingerprint),
                    "fingerprint": fingerprint,
                },
            }

            self.assertEqual(
                module.support_matrix_fingerprint_id("md5:strict"),
                module.fingerprint_md5(fingerprint),
            )
        finally:
            module.ROUTE_RULE_INDEX = original

    def test_support_matrix_requires_fingerprint_id_for_known_parser(self):
        module = load_module()
        original = module.ROUTE_RULE_INDEX
        try:
            module.ROUTE_RULE_INDEX = {
                "md5:strict": {
                    "fingerprint": {
                        "identity": {"any": ["中国工商银行账户明细清单"]},
                    },
                },
            }

            with self.assertRaisesRegex(ValueError, "missing id"):
                module.support_matrix_fingerprint_id("md5:strict")
        finally:
            module.ROUTE_RULE_INDEX = original

    def test_support_matrix_rejects_fingerprint_id_mismatch(self):
        module = load_module()
        original = module.ROUTE_RULE_INDEX
        try:
            module.ROUTE_RULE_INDEX = {
                "md5:strict": {
                    "id": "md5:bad",
                    "fingerprint": {
                        "identity": {"any": ["中国工商银行账户明细清单"]},
                    },
                },
            }

            with self.assertRaisesRegex(ValueError, "fingerprint id mismatch"):
                module.support_matrix_fingerprint_id("md5:strict")
        finally:
            module.ROUTE_RULE_INDEX = original

    def test_yaml_fingerprint_summary_reads_nested_identity_and_columns(self):
        module = load_module()
        original = module.ROUTE_RULE_INDEX
        try:
            module.ROUTE_RULE_INDEX = {
                "md5:custom": {
                    "fingerprint": {
                        "identity": {"any": ["测试银行"]},
                        "columns": {"all": {"交易时间": "交易时间", "账户余额": "账户余额"}},
                        "metadata": {"all": {"application": "UnitTest"}},
                    }
                },
            }

            summary = module.yaml_fingerprint_summary("md5:custom")

            self.assertIn("身份:1", summary)
            self.assertIn("列标记:2", summary)
            self.assertIn("元数据:application", summary)
        finally:
            module.ROUTE_RULE_INDEX = original

    def test_supported_files_can_be_loaded_from_support_matrix_by_fingerprint(self):
        module = load_module()
        rows = [
            {
                "银行": "测试银行",
                "账户类型(YAML)": "未知",
                "格式": "xlsx",
                "版本": "1.0",
                "文件路径": "客户A/a.xlsx",
                "router类": "md5:strict",
                "reader_id": "openpyxl_grid",
                "YAML指纹": "元数据:application",
                "测试结果": "PASS",
            },
            {
                "银行": "测试银行",
                "账户类型(YAML)": "未知",
                "格式": "xlsx",
                "版本": "1.0",
                "文件路径": "客户A/b.xlsx",
                "router类": "md5:strict",
                "reader_id": "openpyxl_grid",
                "YAML指纹": "元数据:application",
                "测试结果": "FAIL",
            },
            {
                "银行": "测试银行",
                "账户类型(YAML)": "未知",
                "格式": "pdf",
                "版本": "1.0",
                "文件路径": "客户A/c.pdf",
                "router类": "md5:other",
                "reader_id": "pdfplumber_table",
                "YAML指纹": "数据:1",
                "测试结果": "PASS",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            matrix = Path(tmp) / "support_matrix.xlsx"
            module.write_xlsx(matrix, rows)

            files = module.support_matrix_files_for_fingerprint(matrix, "md5:strict")

        self.assertEqual(files, ["客户A/a.xlsx"])


if __name__ == "__main__":
    unittest.main()
