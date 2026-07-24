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
from ymb_standardization_core.readers import router  # noqa: E402


SPEC = importlib.util.spec_from_file_location(
    "standardize",
    ROOT / "runtime" / "standardize.py",
)
standardize = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(standardize)


SAMPLE_DIR = DATA_ROOT / "testdata2" / "戴子凯"
SAMPLE_NAMES = (
    "hq_9550889900008084945_1125142514_756540_2.pdf",
    "hq_9550889900008084945_1125492549_339896_4.pdf",
    "hq_9550889900008084945_1126222622_441436_2.pdf",
    "hq_9550889900008084945_1126462646_439759_2.pdf",
    "hq_9550889900008084945_1127132713_025393_3.pdf",
    "hq_9550889900008084945_1127282728_184395_2.pdf",
)
FINGERPRINT_ID = "md5:fd0c28e51e5a27c063d443534cc70c81"
SERIES_FAMILY = "cgb_corporate_current_account_statement_openpdf"


class CgbCorporatePdfRouteTests(unittest.TestCase):
    def test_router_requires_stable_title_metadata_and_columns(self):
        text = (
            "广发银行活期对公对账单 "
            "行 所 号 币 别 账 号 户 名 "
            "交易日期 交易类型 票据号码 本期支出 本期收入 交易对手信息 余额"
        )
        context = {"metadata": {"Producer": "OpenPDF 1.3.30"}}

        route = router.route_pdf(text, 0, 2, context=context)

        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["fingerprint_id"], FINGERPRINT_ID)
        self.assertEqual(route["reader_id"], "pdfplumber_table")
        self.assertEqual(route["bank"], "广发银行")
        self.assertEqual(route["account_type"], "对公")
        self.assertEqual(route["series_family"], SERIES_FAMILY)
        self.assertEqual(route["drop_rows"], [{"any_values": ["上期余额"]}])

    def test_all_local_volumes_route_to_one_family(self):
        samples = [SAMPLE_DIR / name for name in SAMPLE_NAMES]
        if not all(path.exists() for path in samples):
            self.skipTest("本地未提供完整的广发对公分卷 PDF 样本")

        for path in samples:
            with self.subTest(filename=path.name):
                result = input_router.read_rows(str(path))
                route = result.route_info
                self.assertEqual(route["decision"], "matched")
                self.assertEqual(route["fingerprint_id"], FINGERPRINT_ID)
                self.assertEqual(route["series_family"], SERIES_FAMILY)
                self.assertFalse(any("上期余额" in row for row in result.rows[1:]))

    def test_first_volume_extracts_owner_account_and_transactions(self):
        pdf = SAMPLE_DIR / SAMPLE_NAMES[0]
        if not pdf.exists():
            self.skipTest("本地未提供广发对公 PDF 样本")

        with tempfile.TemporaryDirectory() as tmp:
            csv_path, json_path, _report = standardize.standardize(str(pdf), out_dir=tmp)
            with open(json_path, encoding="utf-8") as f:
                mapping = json.load(f)
            with open(csv_path, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))

        image = mapping["文件画像"]
        self.assertEqual(image["decision"], "matched")
        self.assertEqual(image["fingerprint_id"], FINGERPRINT_ID)
        self.assertEqual(image["本方名称"], "南昌傲苏贸易有限公司")
        self.assertEqual(image["本方账户"], "9550889900008084945")
        self.assertEqual(image["账户类型"], "对公")
        self.assertEqual(image["确认银行"], "广发银行")
        self.assertEqual(len(rows), 43)
        self.assertTrue(all(row["交易时间"] for row in rows))
        self.assertEqual({row["本方名称"] for row in rows}, {"南昌傲苏贸易有限公司"})
        self.assertEqual({row["本方账户"] for row in rows}, {"9550889900008084945"})


if __name__ == "__main__":
    unittest.main()
