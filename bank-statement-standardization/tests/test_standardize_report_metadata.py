import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
STANDARDIZE_PATH = REPO_ROOT / "bank-statement-standardization" / "scripts" / "standardize.py"
spec = importlib.util.spec_from_file_location("standardize", STANDARDIZE_PATH)
standardize = importlib.util.module_from_spec(spec)
spec.loader.exec_module(standardize)


class StandardizeReportMetadataTest(unittest.TestCase):
    def test_report_account_metadata_uses_data_columns_when_header_sniff_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "statement.csv"
            out_dir = tmp_path / "out"
            rows = [
                {
                    "客户账号": "36050183115000001593",
                    "账户名称": "江西省鹏达石业有限公司",
                    "交易时间": "2026-01-02 09:00:00",
                    "借方发生额(支取)": "",
                    "贷方发生额(收入)": "100.00",
                    "余额": "1100.00",
                    "对方户名": "付款方",
                    "对方账号": "10001",
                    "摘要": "收款",
                },
                {
                    "客户账号": "36050183115000001593",
                    "账户名称": "江西省鹏达石业有限公司",
                    "交易时间": "2026-01-03 10:00:00",
                    "借方发生额(支取)": "30.00",
                    "贷方发生额(收入)": "",
                    "余额": "1070.00",
                    "对方户名": "收款方",
                    "对方账号": "10002",
                    "摘要": "付款",
                },
            ]
            with src.open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)

            csv_path, json_path, report = standardize.standardize(str(src), out_dir=str(out_dir))

            with open(csv_path, encoding="utf-8-sig", newline="") as f:
                out_rows = list(csv.DictReader(f))
            self.assertEqual(out_rows[0]["本方名称"], "江西省鹏达石业有限公司")
            self.assertEqual(out_rows[0]["本方账户"], "36050183115000001593")

            self.assertEqual(report["文件画像"]["本方名称"], "江西省鹏达石业有限公司")
            self.assertEqual(report["文件画像"]["本方账户"], "36050183115000001593")
            self.assertNotIn("_账号未识别", json.dumps(report, ensure_ascii=False))

            with open(json_path, encoding="utf-8") as f:
                saved_report = json.load(f)
            self.assertEqual(saved_report["文件画像"]["本方名称"], "江西省鹏达石业有限公司")
            self.assertEqual(saved_report["文件画像"]["本方账户"], "36050183115000001593")


if __name__ == "__main__":
    unittest.main()
