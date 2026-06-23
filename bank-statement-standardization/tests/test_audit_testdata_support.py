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
        self.assertEqual(first_row[4], "zhejiang_qyrcb_pdf_text")
        self.assertEqual(first_row[5], "身份:2；结构:15")
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
        self.assertEqual(module.normalize_bank_name("", "", ""), "未识别")


if __name__ == "__main__":
    unittest.main()
