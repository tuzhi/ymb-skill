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
CORE_PACKAGE = REPO_ROOT / "ymb-standardization-core"
QA_DIR = ROOT / "tools" / "qa"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(QA_DIR) not in sys.path:
    sys.path.insert(0, str(QA_DIR))
if str(CORE_PACKAGE) not in sys.path:
    sys.path.insert(0, str(CORE_PACKAGE))

from _paths import DATA_ROOT  # noqa: E402
from ymb_standardization_core.readers import input_router  # noqa: E402
from ymb_standardization_core.readers import router  # noqa: E402


SPEC = importlib.util.spec_from_file_location(
    "standardize",
    ROOT / "runtime" / "standardize.py",
)
standardize = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(standardize)


SAMPLE = (
    DATA_ROOT
    / "testdata2"
    / "戴子凯"
    / "平安银行个人账户交易明细 JYLS260716045633.pdf"
)
FINGERPRINT_ID = "md5:e073efe882f3e565867cff972fc58e3e"


class PinganPersonalPdfRouteTests(unittest.TestCase):
    def test_router_requires_stable_title_metadata_and_columns(self):
        text = (
            "平安银行个人账户交易明细清单 "
            "Transaction Details List of Personal Account of Pingan Bank "
            "户名 卡号/账号 存款类型 "
            "序号 交易日期 交易金额 余额 交易地点 摘要 备注"
        )
        context = {
            "metadata": {
                "Producer": (
                    "SealSADK® 3.1.4.6; "
                    "modified using SealSADK® 3.1.4.6"
                ),
            }
        }

        route = router.route_pdf(text, 0, 10, context=context)

        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["fingerprint_id"], FINGERPRINT_ID)
        self.assertEqual(route["reader_id"], "pdfplumber_table")
        self.assertEqual(route["bank"], "平安银行")
        self.assertEqual(route["account_type"], "个人")
        self.assertEqual(route["series_family"], "")
        self.assertTrue(route["dedupe_chars"])
        self.assertEqual(
            route["word_filters"]["drop_chars"],
            [{"rotated": True}],
        )

    def test_local_statement_dedupes_owner_account_and_watermark_chars(self):
        if not SAMPLE.exists():
            self.skipTest("本地未提供平安银行个人账户交易明细 PDF 样本")

        result = input_router.read_rows(str(SAMPLE))
        route = result.route_info
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["fingerprint_id"], FINGERPRINT_ID)
        self.assertEqual(route["series_family"], "")
        self.assertIn("户名： 戴子凯", result.preamble)
        self.assertIn("卡号/账号： 6230586801468888888", result.preamble)
        self.assertEqual(len(result.rows) - 1, 466)

        with tempfile.TemporaryDirectory() as tmp:
            csv_path, json_path, _report = standardize.standardize(
                str(SAMPLE),
                out_dir=tmp,
            )
            with open(json_path, encoding="utf-8") as f:
                mapping = json.load(f)
            with open(csv_path, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))

        image = mapping["文件画像"]
        self.assertEqual(image["本方名称"], "戴子凯")
        self.assertEqual(image["本方账户"], "6230586801468888888")
        self.assertEqual(image["确认银行"], "平安银行")
        self.assertEqual(image["账户类型"], "个人")
        self.assertEqual(len(rows), 466)
        self.assertTrue(all(row["交易时间"] for row in rows))
        self.assertTrue(all(row["账户余额"] for row in rows))
        self.assertEqual(
            sum(Decimal(row["收入金额"] or "0") for row in rows),
            Decimal("11137933.05"),
        )
        self.assertEqual(
            sum(Decimal(row["支出金额"] or "0") for row in rows),
            Decimal("11145444.16"),
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
        self.assertEqual(Decimal(rows[-1]["账户余额"]), Decimal("8242.79"))


if __name__ == "__main__":
    unittest.main()
