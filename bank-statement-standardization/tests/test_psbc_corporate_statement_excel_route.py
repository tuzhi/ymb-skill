import csv
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
CORE_PACKAGE = REPO_ROOT / "ymb-standardization-core" / "src"
QA_DIR = ROOT / "tools" / "qa"
if str(QA_DIR) not in sys.path:
    sys.path.insert(0, str(QA_DIR))
if str(CORE_PACKAGE) not in sys.path:
    sys.path.insert(0, str(CORE_PACKAGE))

from _paths import DATA_ROOT  # noqa: E402
from ymb_standardization_core import core  # noqa: E402
from ymb_standardization_core.readers import input_router  # noqa: E402


SAMPLE = DATA_ROOT / "testdata2" / "胡鹏" / "明细查询_20251215-20260815_共98笔.xls"
FINGERPRINT_ID = "md5:38f9c3bbe99ac6ce186b1767d6feff2b"


class PsbcCorporateStatementExcelRouteTests(unittest.TestCase):
    def test_statement_matches_excel_reader(self):
        if not SAMPLE.exists():
            self.skipTest("本地未提供邮储账户交易明细 XLS 样本")

        result = input_router.read_rows(str(SAMPLE))
        route = result.route_info

        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["fingerprint_id"], FINGERPRINT_ID)
        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["bank"], "中国邮政储蓄银行")
        self.assertEqual(route["account_type"], "未知")
        self.assertEqual(route["source_order"], "ascending")
        self.assertEqual(len(result.rows), 104)

    def test_standardized_rows_reconcile_balance_chain(self):
        if not SAMPLE.exists():
            self.skipTest("本地未提供邮储账户交易明细 XLS 样本")

        with tempfile.TemporaryDirectory() as tmp:
            csv_path, _json_path, report = core.standardize(str(SAMPLE), out_dir=tmp)
            with open(csv_path, encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))

        image = report["文件画像"]
        self.assertEqual(image["fingerprint_id"], FINGERPRINT_ID)
        self.assertEqual(image["确认银行"], "中国邮政储蓄银行")
        self.assertEqual(image["本方名称"], "上饶晶云光学有限公司")
        self.assertEqual(image["本方账户"], "936113013000836305")
        self.assertEqual(image["账户类型"], "拟对公")
        self.assertEqual(len(rows), 98)
        self.assertEqual(
            sum(Decimal(row["收入金额"] or "0") for row in rows),
            Decimal("7494671.04"),
        )
        self.assertEqual(
            sum(Decimal(row["支出金额"] or "0") for row in rows),
            Decimal("7494304.74"),
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
