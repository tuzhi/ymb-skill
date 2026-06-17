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
CORE_PACKAGE = REPO_ROOT / "packages" / "ymb_standardization_core"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(CORE_PACKAGE) not in sys.path:
    sys.path.insert(0, str(CORE_PACKAGE))

SPEC = importlib.util.spec_from_file_location("standardize", ROOT / "scripts" / "standardize.py")
standardize = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(standardize)


class KasikornPdfRouteTests(unittest.TestCase):
    def test_local_kasikorn_pdf_uses_text_parser(self):
        pdf = ROOT / "testdata" / "泰国开泰银行" / "111.pdf"
        if not pdf.exists():
            self.skipTest("本地未提供开泰银行 PDF 样本")

        with tempfile.TemporaryDirectory() as tmp:
            csv_path, json_path, _report = standardize.standardize(str(pdf), out_dir=tmp)
            with open(json_path, encoding="utf-8") as f:
                mapping = json.load(f)
            with open(csv_path, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))

        image = mapping["文件画像"]
        self.assertEqual(image["parser"], "kasikorn_pdf_text")
        self.assertFalse(image["ocr_supported"])
        self.assertFalse(image["ocr_used"])
        self.assertEqual(image["本方名称"], "HUAHUA JIANG")
        self.assertEqual(image["本方账户"], "061-8-92723-7")
        self.assertEqual(len(rows), 484)
        self.assertEqual(sum(1 for r in rows if r["收入金额"]), 94)
        self.assertEqual(sum(1 for r in rows if r["支出金额"]), 390)
        self.assertTrue(all(r["交易时间"] for r in rows))


if __name__ == "__main__":
    unittest.main()
