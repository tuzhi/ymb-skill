import csv
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
STANDARDIZE_PATH = REPO_ROOT / "bank-statement-standardization" / "runtime" / "standardize.py"
spec = importlib.util.spec_from_file_location("standardize", STANDARDIZE_PATH)
standardize = importlib.util.module_from_spec(spec)
spec.loader.exec_module(standardize)


class AlreadyStandardizedInputTest(unittest.TestCase):
    def test_standardize_accepts_already_standardized_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "client__standardized.csv"
            out_dir = tmp_path / "out"
            rows = [
                {
                    "交易唯一编号": "TX-a",
                    "交易时间": "2026-01-02 09:00:00",
                    "本方名称": "测试客户",
                    "本方账户": "6222000000000001",
                    "开户行": "中国工商银行",
                    "账户类型": "个人",
                    "对手名称": "收款方",
                    "对手账户": "10001",
                    "收入金额": "100.00",
                    "支出金额": "",
                    "交易金额": "100.00",
                    "账户余额": "1100.00",
                    "银行备注": "转账",
                    "账户方附言": "",
                    "交易渠道": "网银",
                    "来源文件名": "raw-a.csv",
                    "来源行号": "2",
                },
                {
                    "交易唯一编号": "TX-b",
                    "交易时间": "2026-01-03 10:00:00",
                    "本方名称": "测试客户",
                    "本方账户": "6222000000000001",
                    "开户行": "中国工商银行",
                    "账户类型": "个人",
                    "对手名称": "付款方",
                    "对手账户": "10002",
                    "收入金额": "",
                    "支出金额": "30.00",
                    "交易金额": "-30.00",
                    "账户余额": "1070.00",
                    "银行备注": "消费",
                    "账户方附言": "",
                    "交易渠道": "POS",
                    "来源文件名": "raw-b.csv",
                    "来源行号": "3",
                },
                {
                    "交易唯一编号": "TX-c",
                    "交易时间": "2026-01-04 11:00:00",
                    "本方名称": "测试客户",
                    "本方账户": "6222000000000001",
                    "开户行": "中国工商银行",
                    "账户类型": "个人",
                    "对手名称": "退款方",
                    "对手账户": "10003",
                    "收入金额": "",
                    "支出金额": "",
                    "账户余额": "1090.00",
                    "银行备注": "退款",
                    "账户方附言": "",
                    "交易渠道": "网银",
                    "交易金额": "20.00",
                    "来源文件名": "raw-c.csv",
                    "来源行号": "4",
                },
            ]
            with src.open("w", encoding="utf-8-sig", newline="") as f:
                fieldnames = list(dict.fromkeys(k for row in rows for k in row.keys()))
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)

            csv_path, json_path, report = standardize.standardize(str(src), out_dir=str(out_dir))

            self.assertEqual(Path(csv_path).name, "client__standardized.csv")
            self.assertEqual(Path(json_path).name, "client__mapping.json")
            self.assertEqual(report["文件画像"]["命中模板"], "文件名已标准化输入")
            self.assertEqual(report["标准化统计"]["交易笔数"], 3)
            self.assertEqual(report["标准化统计"]["金额结构"], "已标准化")

            with open(csv_path, encoding="utf-8-sig", newline="") as f:
                out_rows = list(csv.DictReader(f))
            self.assertEqual(out_rows[0]["来源文件名"], "raw-a.csv")
            self.assertEqual(out_rows[0]["来源行号"], "2")
            self.assertEqual(out_rows[0]["交易金额"], "100.00")
            self.assertEqual(out_rows[1]["交易金额"], "-30.00")
            self.assertEqual(out_rows[2]["收入金额"], "")
            self.assertEqual(out_rows[2]["支出金额"], "")
            self.assertEqual(out_rows[0]["交易唯一编号"], "TX-a")

            with open(json_path, encoding="utf-8") as f:
                saved_report = json.load(f)
            self.assertEqual(saved_report["表头识别"]["表头行号"], 0)

    def test_screen_files_keeps_standardized_csv_as_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            standardized = tmp_path / "client__standardized.csv"
            integrated = tmp_path / "client__整合流水.csv"
            standardized.write_text("交易时间,本方名称,本方账户,交易金额\n", encoding="utf-8")
            integrated.write_text("交易时间,本方名称,本方账户,交易金额\n", encoding="utf-8")

            candidates, skipped = standardize.screen_files([str(standardized), str(integrated)])

            self.assertEqual(candidates, [str(standardized)])
            self.assertEqual(skipped, [])

    def test_screen_files_reports_pdf_with_duplicate_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            disguised_pdf = Path(tmp) / "statement.pdf_2"
            disguised_pdf.write_bytes(b"%PDF-1.7\n")

            candidates, skipped = standardize.screen_files([str(disguised_pdf)])

            self.assertEqual(candidates, [])
            self.assertEqual(len(skipped), 1)
            self.assertEqual(skipped[0][0], "statement.pdf_2")
            self.assertIn("伪后缀", skipped[0][1])

    def test_screen_files_reports_excel_with_duplicate_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            converted_excel = Path(tmp) / "statement.xlsx_2"
            converted_excel.write_bytes(b"converted workbook")

            candidates, skipped = standardize.screen_files([str(converted_excel)])

            self.assertEqual(candidates, [])
            self.assertEqual(len(skipped), 1)
            self.assertEqual(skipped[0][0], "statement.xlsx_2")
            self.assertIn("转换文件", skipped[0][1])
            self.assertIn("不作为原始流水接收", skipped[0][1])

    def test_screen_files_rejects_wps_pdf_to_excel_conversion(self):
        with tempfile.TemporaryDirectory() as tmp:
            converted = Path(tmp) / "converted.xlsx"
            custom_properties = """<?xml version="1.0" encoding="UTF-8"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
 xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="2" name="CRO">
    <vt:lpwstr>wqlLaW5nc29mdCBQREYgdG8gV1BTIDEyMA</vt:lpwstr>
  </property>
</Properties>"""
            with zipfile.ZipFile(converted, "w") as archive:
                archive.writestr("docProps/custom.xml", custom_properties)

            candidates, skipped = standardize.screen_files([str(converted)])

            self.assertEqual(candidates, [])
            self.assertEqual(len(skipped), 1)
            self.assertEqual(skipped[0][0], "converted.xlsx")
            self.assertIn("Kingsoft PDF to WPS 120", skipped[0][1])
            self.assertIn("不作为原始流水接收", skipped[0][1])


if __name__ == "__main__":
    unittest.main()
