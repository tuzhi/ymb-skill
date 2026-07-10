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


class ZhejiangQingyuanRuralCommercialPdfRouteTests(unittest.TestCase):
    def test_zhejiang_qyrcb_requires_qingyuan_heading(self):
        text = (
            "某地农商银行 个人账户交易明细 户名:张三 账号:6228580999004272748 "
            "开户行:某地农商银行营业部 账户种类:卡/折 "
            "2025-06-09 人民币 汇出 -60.00 20808.46 "
            "2025-06-10 人民币 汇出 -61.00 20747.46 "
            "2025-06-11 人民币 汇出 -62.00 20685.46 "
            "2025-06-12 人民币 汇出 -63.00 20622.46 "
            "2025-06-13 人民币 汇出 -64.00 20558.46"
        )

        result = router.route_pdf(text, 0, 1)

        self.assertNotIn("parser", result)
        self.assertEqual(result["decision"], "unmatched")
        self.assertEqual(result["reader_id"], "none")

    def test_local_grzd_pdf_uses_coordinate_table_reader(self):
        pdf = ROOT / "testdata" / "李先根" / "GRZD-9A202606081958362818-20250608-20260607-X_unsign_sign_18831.pdf"
        if not pdf.exists():
            self.skipTest("本地未提供李先根 GRZD 浙江庆元农商 PDF 样本")

        with tempfile.TemporaryDirectory() as tmp:
            csv_path, json_path, _report = standardize.standardize(str(pdf), out_dir=tmp)
            with open(json_path, encoding="utf-8") as f:
                mapping = json.load(f)
            with open(csv_path, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))

        image = mapping["文件画像"]
        self.assertNotIn("parser", image)
        self.assertEqual(image["reader_id"], "pdfplumber_coordinate_table")
        self.assertEqual(image["decision"], "matched")
        self.assertTrue(image["fingerprint_id"].startswith("md5:"))
        self.assertEqual(image["file_type"], "pdf")
        self.assertEqual(image["bank"], "浙江庆元农商银行")
        self.assertIn("庆元农商银行", image["identity_evidence"])
        self.assertIn("个人账户交易明细", image["columns_evidence"])
        self.assertFalse(image["ocr_supported"])
        self.assertFalse(image["ocr_used"])
        self.assertEqual(image["本方名称"], "李先根")
        self.assertEqual(image["本方账户"], "6228580999004272748")
        self.assertEqual(len(rows), 240)
        self.assertEqual(rows[0]["交易时间"][:10], "2025-06-09")
        self.assertEqual(rows[0]["支出金额"], "60.0")
        self.assertEqual(rows[-1]["交易时间"][:10], "2026-05-30")
        self.assertEqual(rows[-1]["支出金额"], "37.81")
        self.assertTrue(all(r["来源文件名"] for r in rows))


if __name__ == "__main__":
    unittest.main()
