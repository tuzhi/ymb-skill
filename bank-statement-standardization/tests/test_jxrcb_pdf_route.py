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

PARSER_SPEC = importlib.util.spec_from_file_location(
    "jxrcb_pdf_text",
    CORE_PACKAGE / "ymb_standardization_core" / "parsers" / "jxrcb_pdf_text.py")
jxrcb_pdf_text = importlib.util.module_from_spec(PARSER_SPEC)
PARSER_SPEC.loader.exec_module(jxrcb_pdf_text)

ROUTER_SPEC = importlib.util.spec_from_file_location(
    "router",
    CORE_PACKAGE / "ymb_standardization_core" / "parsers" / "router.py")
router = importlib.util.module_from_spec(ROUTER_SPEC)
ROUTER_SPEC.loader.exec_module(router)


class JiangxiRuralCommercialPdfRouteTests(unittest.TestCase):
    def test_abc_and_jxrcb_routes_are_separate(self):
        abc = router.route_pdf("中国农业银行账户活期交易明细清单", 0, 1)
        jxrcb = router.route_pdf(
            "江西·农商银行 户 名 张华峰 账 号 6226822011500474554 起止日期 "
            "2025-01-01 1.00 2.00 2025-01-02 1.00 3.00 2025-01-03 1.00 4.00 "
            "2025-01-04 1.00 5.00 2025-01-05 1.00 6.00",
            0, 1)

        self.assertEqual(abc["parser"], "abc_text_pdf")
        self.assertEqual(jxrcb["parser"], "jiangxi_rural_commercial_pdf_text")

    def test_jxrcb_requires_bank_heading(self):
        text = (
            "户 名 张三 账 号 6226822011500474554 起止日期 "
            "2025-01-01 1.00 2.00 2025-01-02 1.00 3.00 2025-01-03 1.00 4.00 "
            "2025-01-04 1.00 5.00 2025-01-05 1.00 6.00"
        )

        result = router.route_pdf(text, 0, 1)

        self.assertEqual(result["parser"], "generic_pdf_text_unmatched")

    def test_watermarked_text_line_is_parsed(self):
        # 江西农商 PDF 文本层会把水印字插入数字 token，解析器必须先清理再定位日期/金额。
        line = "2025-行11-22 -11行5.00 65行2.50 微信行支付 扫二维行码付款 100行0107301 行 行"

        row = jxrcb_pdf_text.parse_transaction_line(line)

        self.assertEqual(row, [
            "2025-11-22",
            "-115.00",
            "652.50",
            "微信支付",
            "扫二维码付款",
            "1000107301",
        ])

    def test_local_jxrcb_pdf_uses_text_parser_without_ocr(self):
        pdfs = list((ROOT / "testdata" / "6").glob("江西·农商银行*.pdf"))
        if not pdfs:
            self.skipTest("本地未提供江西农商 PDF 样本")

        with tempfile.TemporaryDirectory() as tmp:
            csv_path, json_path, report = standardize.standardize(str(pdfs[0]), out_dir=tmp)
            with open(json_path, encoding="utf-8") as f:
                mapping = json.load(f)
            with open(csv_path, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))

        image = mapping["文件画像"]
        self.assertEqual(image["parser"], "jiangxi_rural_commercial_pdf_text")
        self.assertFalse(image["ocr_supported"])
        self.assertFalse(image["ocr_used"])
        self.assertEqual(image["本方名称"], "张华峰")
        self.assertEqual(image["本方账户"], "6226822011500474554")
        self.assertGreater(len(rows), 0)
        self.assertTrue(all(r["交易时间"] for r in rows))
        self.assertTrue(all(r["来源文件名"] for r in rows))


if __name__ == "__main__":
    unittest.main()
