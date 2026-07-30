import csv
import importlib.util
import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
SCRIPTS = ROOT / "scripts"
CORE_PACKAGE = REPO_ROOT / "ymb-standardization-core" / "src"
QA_DIR = ROOT / "tools" / "qa"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(QA_DIR) not in sys.path:
    sys.path.insert(0, str(QA_DIR))
if str(CORE_PACKAGE) not in sys.path:
    sys.path.insert(0, str(CORE_PACKAGE))

from _paths import DATA_ROOT  # noqa: E402
from ymb_standardization_core.readers import input_router  # noqa: E402


SPEC = importlib.util.spec_from_file_location(
    "standardize",
    ROOT / "runtime" / "standardize.py",
)
standardize = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(standardize)


SAMPLE = (
    DATA_ROOT
    / "testdata2"
    / "陈建兵"
    / "b93c0a8e17124b55b3c4c2997e8043df.pdf"
)
FINGERPRINT_ID = "md5:6ad5ced0dfc4ac24cc57fec75b908069"


class CiticPersonalPdfRouteTests(unittest.TestCase):
    def test_local_statement_uses_coordinate_reader_without_losing_page_tail_rows(self):
        if not SAMPLE.exists():
            self.skipTest("本地未提供中信银行个人账户交易明细 PDF 样本")

        result = input_router.read_rows(str(SAMPLE))
        route = result.route_info

        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["fingerprint_id"], FINGERPRINT_ID)
        self.assertEqual(route["reader_id"], "pdfplumber_coordinate_table")
        self.assertEqual(route["bank"], "中信银行")
        self.assertEqual(route["account_type"], "个人")
        self.assertEqual(
            route["series_family"],
            "citic_personal_transaction_details_openpdf",
        )
        self.assertIn("户名：陈建兵", result.preamble)
        self.assertIn("账号：6217735701508878", result.preamble)
        self.assertEqual(len(result.rows) - 1, 197)
        self.assertEqual(result.rows[1][0], "20250603")
        self.assertEqual(result.rows[-1][0], "20260530")
        self.assertEqual(result.rows[-1][2], "200.00")
        self.assertEqual(result.rows[-1][3], "871.05")

    def test_standardized_rows_have_complete_balance_chain(self):
        if not SAMPLE.exists():
            self.skipTest("本地未提供中信银行个人账户交易明细 PDF 样本")

        with tempfile.TemporaryDirectory() as tmp:
            csv_path, json_path, _report = standardize.standardize(
                str(SAMPLE),
                out_dir=tmp,
            )
            with open(json_path, encoding="utf-8") as stream:
                mapping = json.load(stream)
            with open(csv_path, encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))

        image = mapping["文件画像"]
        self.assertEqual(image["fingerprint_id"], FINGERPRINT_ID)
        self.assertEqual(image["reader_id"], "pdfplumber_coordinate_table")
        self.assertEqual(image["确认银行"], "中信银行")
        self.assertEqual(image["账户类型"], "个人")
        self.assertEqual(image["本方名称"], "陈建兵")
        self.assertEqual(image["本方账户"], "6217735701508878")
        self.assertEqual(len(rows), 197)

        balance_breaks = 0
        for previous, current in zip(rows, rows[1:]):
            expected = (
                Decimal(previous["账户余额"])
                + Decimal(current["收入金额"] or "0")
                - Decimal(current["支出金额"] or "0")
            )
            if expected != Decimal(current["账户余额"]):
                balance_breaks += 1

        self.assertEqual(balance_breaks, 0)
        self.assertEqual(Decimal(rows[-1]["账户余额"]), Decimal("871.05"))


if __name__ == "__main__":
    unittest.main()
