import csv
import importlib.util
import json
import sys
import tempfile
import unittest
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

from _paths import TESTDATA_ROOT  # noqa: E402
SPEC = importlib.util.spec_from_file_location("standardize", ROOT / "scripts" / "standardize.py")
standardize = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(standardize)


class KasikornPdfRouteTests(unittest.TestCase):
    def test_local_kasikorn_pdf_uses_grid_line_table_reader(self):
        pdf = TESTDATA_ROOT / "泰国开泰银行" / "111.pdf"
        if not pdf.exists():
            self.skipTest("本地未提供开泰银行 PDF 样本")

        with tempfile.TemporaryDirectory() as tmp:
            csv_path, json_path, _report = standardize.standardize(str(pdf), out_dir=tmp)
            with open(json_path, encoding="utf-8") as f:
                mapping = json.load(f)
            with open(csv_path, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))

        image = mapping["文件画像"]
        self.assertNotIn("parser", image)
        self.assertEqual(image["fingerprint_id"], "md5:b75cf43e9a35b4ca0c082906f3aa2c7b")
        self.assertEqual(image["reader_id"], "pdfplumber_grid_line_table")
        self.assertFalse(image["ocr_supported"])
        self.assertFalse(image["ocr_used"])
        self.assertEqual(image["本方名称"], "HUAHUA JIANG")
        self.assertEqual(image["本方账户"], "061-8-92723-7")
        self.assertEqual(image["date_order"], "dmy")
        self.assertEqual(image["transaction_time_precision"], "minute")
        self.assertEqual(len(rows), 484)
        self.assertEqual(sum(1 for r in rows if r["收入金额"]), 94)
        self.assertEqual(sum(1 for r in rows if r["支出金额"]), 390)
        self.assertTrue(all(r["交易时间"] for r in rows))
        self.assertEqual(rows[0]["交易时间"], "2025-12-01 08:23")
        self.assertEqual(rows[-1]["交易时间"], "2026-05-30 15:55")
        self.assertEqual(rows[0]["对手账户"], "X1042")
        self.assertEqual(rows[0]["对手名称"], "MR. Thanawat Phim")
        self.assertEqual(sum(bool(r["对手账户"]) for r in rows), 334)
        self.assertEqual(sum(bool(r["对手名称"]) for r in rows), 459)


if __name__ == "__main__":
    unittest.main()
