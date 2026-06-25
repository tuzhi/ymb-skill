import csv
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
CORE_PACKAGE = REPO_ROOT / "ymb-standardization-core"
if str(CORE_PACKAGE) not in sys.path:
    sys.path.insert(0, str(CORE_PACKAGE))

SPEC = importlib.util.spec_from_file_location("standardize", ROOT / "scripts" / "standardize.py")
standardize = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(standardize)


class WorkBuddyParserIteration20260624Tests(unittest.TestCase):
    def _standardize(self, name):
        sample = ROOT / "testdata" / "陈国付" / name
        if not sample.exists():
            self.skipTest(f"本地未提供样本 {sample}")
        with tempfile.TemporaryDirectory() as tmp:
            csv_path, json_path, _report = standardize.standardize(str(sample), out_dir=tmp)
            with open(csv_path, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
            with open(json_path, encoding="utf-8") as f:
                mapping = json.load(f)
        return rows, mapping

    def test_abc_text_statement_with_spaced_journal_header_is_not_skipped(self):
        rows, mapping = self._standardize("26060214275857136186.pdf")

        self.assertEqual(mapping["文件画像"]["parser"], "abc_text_pdf")
        self.assertGreater(len(rows), 100)
        self.assertEqual(rows[0]["本方名称"], "龚小雪")
        self.assertEqual(rows[0]["本方账户"], "6228482321342799515")

    def test_jiangxi_yumin_bank_text_statement_is_not_skipped(self):
        rows, mapping = self._standardize("APPLY2026060214573700135618149968_trade_history_sign.pdf")

        self.assertEqual(mapping["文件画像"]["parser"], "jiangxi_yumin_bank_pdf")
        self.assertGreater(len(rows), 100)
        self.assertEqual(rows[0]["本方名称"], "陈俊")
        self.assertEqual(rows[0]["本方账户"], "6236433910000367400")

    def test_wechat_pay_proof_other_direction_has_amount_direction(self):
        rows, mapping = self._standardize("微信支付交易明细证明(20250515-20260515)_20260602143541.pdf")

        self.assertEqual(mapping["文件画像"]["parser"], "wechat_pay_proof_pdf")
        expected = {
            "零钱提现": "支出金额",
            "经营账户提现": "支出金额",
            "零钱充值": "收入金额",
            "转入零钱通-来自零钱": "支出金额",
            "零钱通转出-到零钱": "收入金额",
        }
        for memo, amount_field in expected.items():
            hits = [row for row in rows if row["银行备注"] == memo]
            self.assertTrue(hits, memo)
            self.assertFalse([row for row in hits if not row[amount_field].strip()], memo)
            self.assertFalse([row for row in hits if not row["交易金额"].strip()], memo)


if __name__ == "__main__":
    unittest.main()
