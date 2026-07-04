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
        self.assertEqual(abc["fingerprint_id"], "md5:ab5d413308d9d27f3aa913d772fa3494")
        self.assertNotIn("parser", jxrcb)
        self.assertEqual(jxrcb["fingerprint_id"], "md5:e833fbf4a2171d66315c5a3bda64711c")

    def test_jxrcb_requires_bank_heading(self):
        text = (
            "户 名 张三 账 号 6226822011500474554 起止日期 "
            "2025-01-01 1.00 2.00 2025-01-02 1.00 3.00 2025-01-03 1.00 4.00 "
            "2025-01-04 1.00 5.00 2025-01-05 1.00 6.00"
        )

        result = router.route_pdf(text, 0, 1)

        self.assertNotIn("parser", result)
        self.assertEqual(result["decision"], "unmatched")
        self.assertEqual(result["parser_id"], "none")

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


if __name__ == "__main__":
    unittest.main()
