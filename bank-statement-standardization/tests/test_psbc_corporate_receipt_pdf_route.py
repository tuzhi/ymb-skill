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


SAMPLE = DATA_ROOT / "testdata2" / "龙江" / "邮政银行近一年流水.pdf"
FINGERPRINT_ID = "md5:9e9d9f84bdf430cc19a4e1badb7ebd6b"


class PsbcCorporateReceiptPdfRouteTests(unittest.TestCase):
    def test_local_statement_matches_table_reader_and_all_transactions(self):
        if not SAMPLE.exists():
            self.skipTest("本地未提供邮储对公账户交易明细专用回单 PDF 样本")

        result = input_router.read_rows(str(SAMPLE))
        route = result.route_info

        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["fingerprint_id"], FINGERPRINT_ID)
        self.assertEqual(route["reader_id"], "pdfplumber_table")
        self.assertEqual(route["bank"], "中国邮政储蓄银行")
        self.assertEqual(route["account_type"], "对公")
        self.assertEqual(len(result.rows), 704)
        self.assertEqual(result.rows[4][0], "序号")
        self.assertEqual(result.rows[5][0], "1")
        self.assertEqual(result.rows[-1][0], "699")

    def test_standardized_rows_pass_totals_and_balance_chain(self):
        if not SAMPLE.exists():
            self.skipTest("本地未提供邮储对公账户交易明细专用回单 PDF 样本")

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
        self.assertEqual(image["reader_id"], "pdfplumber_table")
        self.assertEqual(image["确认银行"], "中国邮政储蓄银行")
        self.assertEqual(image["账户类型"], "对公")
        self.assertEqual(len(rows), 699)
        self.assertEqual(
            sum(Decimal(row["收入金额"] or "0") for row in rows),
            Decimal("41892030.38"),
        )
        self.assertEqual(
            sum(Decimal(row["支出金额"] or "0") for row in rows),
            Decimal("42391517.31"),
        )

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


if __name__ == "__main__":
    unittest.main()
