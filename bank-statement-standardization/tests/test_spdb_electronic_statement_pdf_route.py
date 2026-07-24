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

from _paths import DATA_ROOT  # noqa: E402
from ymb_standardization_core.readers import input_router  # noqa: E402
from ymb_standardization_core.readers import router  # noqa: E402


SPEC = importlib.util.spec_from_file_location(
    "standardize",
    ROOT / "runtime" / "standardize.py",
)
standardize = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(standardize)


SAMPLE = DATA_ROOT / "testdata2" / "戴子凯" / "傲苏对账单.pdf"
FINGERPRINT_ID = "md5:18cd1e83f703a76a2c83a791160a8eb9"


class SpdbElectronicStatementPdfRouteTests(unittest.TestCase):
    def test_router_requires_stable_title_metadata_and_columns(self):
        text = (
            "上海浦东发展银行电子对账单 客户名称 账户名称 账号 "
            "交易日期 交易流水号 发生额 借方 贷方 账户余额 "
            "交易对手信息 对手机构 对手名称 摘要代码 备注 浦发企业版App"
        )
        context = {
            "metadata": {
                "Creator": (
                    "JasperReports Library version "
                    "6.12.2-75c5e90a222ab406e416cbf590a5397028a52de3"
                ),
                "Producer": "iText 2.1.7 by 1T3XT",
            }
        }

        route = router.route_pdf(text, 0, 2, context=context)

        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["fingerprint_id"], FINGERPRINT_ID)
        self.assertEqual(route["reader_id"], "pdfplumber_table")
        self.assertEqual(route["bank"], "上海浦东发展银行")
        self.assertEqual(route["account_type"], "对公")
        self.assertEqual(route["series_family"], "")
        self.assertEqual(route["header_merge"]["rows"], 2)
        self.assertEqual(len(route["drop_rows"]), 1)
        self.assertEqual(set(route["drop_rows"][0]), {"any_values"})
        self.assertEqual(len(route["drop_rows"][0]["any_values"]), 1)
        self.assertTrue(
            route["drop_rows"][0]["any_values"][0].startswith("提示 Remarks:")
        )

    def test_local_statement_extracts_owner_account_and_six_transactions(self):
        if not SAMPLE.exists():
            self.skipTest("本地未提供浦发电子对账单 PDF 样本")

        result = input_router.read_rows(str(SAMPLE))
        route = result.route_info
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["fingerprint_id"], FINGERPRINT_ID)
        self.assertEqual(route["series_family"], "")
        self.assertNotIn("提示 Remarks", " ".join(str(cell) for row in result.rows for cell in row))

        with tempfile.TemporaryDirectory() as tmp:
            csv_path, json_path, _report = standardize.standardize(str(SAMPLE), out_dir=tmp)
            with open(json_path, encoding="utf-8") as f:
                mapping = json.load(f)
            with open(csv_path, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))

        image = mapping["文件画像"]
        self.assertEqual(image["本方名称"], "南昌傲苏贸易有限公司")
        self.assertEqual(image["本方账户"], "64050078801900001528")
        self.assertEqual(image["确认银行"], "上海浦东发展银行")
        self.assertEqual(image["账户类型"], "对公")
        self.assertEqual(len(rows), 6)
        self.assertTrue(all(row["交易时间"] for row in rows))
        self.assertEqual(sum(bool(row["收入金额"]) for row in rows), 1)
        self.assertEqual(sum(bool(row["支出金额"]) for row in rows), 5)
        self.assertEqual(
            sum(float(row["收入金额"] or 0) for row in rows),
            2476437.30,
        )
        self.assertEqual(
            sum(float(row["支出金额"] or 0) for row in rows),
            2474429.50,
        )
        self.assertEqual(
            {row["本方名称"] for row in rows},
            {"南昌傲苏贸易有限公司"},
        )
        self.assertEqual(
            {row["本方账户"] for row in rows},
            {"64050078801900001528"},
        )


if __name__ == "__main__":
    unittest.main()
