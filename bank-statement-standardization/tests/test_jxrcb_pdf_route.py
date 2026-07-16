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
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(CORE_PACKAGE) not in sys.path:
    sys.path.insert(0, str(CORE_PACKAGE))
SPEC = importlib.util.spec_from_file_location("standardize", ROOT / "scripts" / "standardize.py")
standardize = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(standardize)

ROUTER_SPEC = importlib.util.spec_from_file_location(
    "router",
    CORE_PACKAGE / "ymb_standardization_core" / "readers" / "router.py")
router = importlib.util.module_from_spec(ROUTER_SPEC)
ROUTER_SPEC.loader.exec_module(router)


class JiangxiRuralCommercialPdfRouteTests(unittest.TestCase):
    def test_matched_jxrcb_fingerprint_is_authoritative_for_standardized_bank(self):
        path = ROOT / "testdata" / "艾晓林" / "江西·农商银行(2026年05月20日11时29分50秒)-2.pdf"

        with tempfile.TemporaryDirectory() as tmp:
            csv_path, _json_path, report = standardize.standardize(str(path), out_dir=tmp)
            with open(csv_path, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))

        self.assertEqual(report["文件画像"]["decision"], "matched")
        self.assertEqual(report["文件画像"]["开户行识别来源"], "router")
        self.assertEqual(report["文件画像"]["确认银行"], "江西农商银行")
        self.assertEqual({row["开户行"] for row in rows}, {"江西农商银行"})

    def test_abc_and_jxrcb_routes_are_separate(self):
        abc = router.route_pdf(
            "中国农业银行账户活期交易明细清单 "
            "交易日期 交易时间 交易摘要 交易金额 本次余额 对手信息 日 志 号 交易渠道 交易附言",
            0, 1)
        jxrcb = router.route_pdf(
            "江西·农商银行交易流水 江西·农商银行 户 名 张华峰 账 号 6226822011500474554 起止日期 "
            "记账日期 交易金额(元) 交易后余额(元) 交易摘要 对方户名 对方账号 "
            "2025-01-01 1.00 2.00 2025-01-02 1.00 3.00 2025-01-03 1.00 4.00 "
            "2025-01-04 1.00 5.00 2025-01-05 1.00 6.00",
            0, 1,
            context={"lines": [], "date_patterns": ["yyyy-mm-dd"]})

        self.assertNotIn("parser", abc)
        self.assertEqual(abc["fingerprint_id"], "md5:f1f82a652560900aea7c11139b852abf")
        self.assertNotIn("parser", jxrcb)
        self.assertEqual(jxrcb["fingerprint_id"], "md5:58cf5d000d5fe58b0247e61150522ba9")

    def test_jxrcb_requires_bank_heading(self):
        text = (
            "户 名 张三 账 号 6226822011500474554 起止日期 "
            "2025-01-01 1.00 2.00 2025-01-02 1.00 3.00 2025-01-03 1.00 4.00 "
            "2025-01-04 1.00 5.00 2025-01-05 1.00 6.00"
        )

        result = router.route_pdf(text, 0, 1)

        self.assertNotIn("parser", result)
        self.assertEqual(result["decision"], "unmatched")
        self.assertEqual(result["reader_id"], "none")

    def test_watermarked_pdf_uses_coordinate_reader(self):
        path = ROOT / "testdata" / "艾晓林" / "江西·农商银行(2026年05月20日11时29分50秒)-2.pdf"

        preamble, rows, route_info = router.read_pdf_rows(str(path))

        self.assertEqual(route_info["reader_id"], "pdfplumber_coordinate_table")
        self.assertIn("户名: 艾晓林", preamble)
        self.assertIn("账号: 6226822010201107935", preamble)
        row = next(item for item in rows[1:] if item[0] == "2025-06-12" and item[1] == "-41,100.00")
        self.assertEqual(row[3], "跨行转出-南昌巨鲸农牧发展有限公司")
        self.assertEqual(row[4], "南昌巨鲸农牧发展有限公司")
        self.assertEqual(row[5], "36050182035200000593")


if __name__ == "__main__":
    unittest.main()
