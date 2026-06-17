import csv
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
CORE_PACKAGE = REPO_ROOT / "packages" / "ymb_standardization_core"
if str(CORE_PACKAGE) not in sys.path:
    sys.path.insert(0, str(CORE_PACKAGE))

from ymb_standardization_core import core  # noqa: E402


def load_input_router():
    spec = importlib.util.spec_from_file_location(
        "input_router",
        CORE_PACKAGE / "ymb_standardization_core" / "parsers" / "input_router.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.configure_readers(core.read_rows_excel, core.read_rows_csv, core.NotABankStatement)
    return module


class InputRouterTests(unittest.TestCase):
    def test_csv_input_returns_uniform_read_result(self):
        module = load_input_router()
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "sample.csv"
            with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["# 户名:张三 账号:1234567890"])
                writer.writerow(["交易日期", "收入金额", "账户余额"])
                writer.writerow(["2026-01-01", "1.00", "2.00"])

            result = module.read_rows(str(csv_path))

        self.assertEqual(result.kind, "csv")
        self.assertIn("户名:张三", result.preamble)
        self.assertEqual(result.rows[0], ["交易日期", "收入金额", "账户余额"])
        self.assertEqual(result.route_info["parser"], "generic_csv")

    def test_pdf_input_keeps_existing_pdf_router(self):
        module = load_input_router()
        pdf = ROOT / "testdata" / "李先根" / "GRZD-9A202606081958362818-20250608-20260607-X_unsign_sign_18831.pdf"
        if not pdf.exists():
            self.skipTest("本地未提供李先根 GRZD 浙江庆元农商 PDF 样本")

        result = module.read_rows(str(pdf))

        self.assertEqual(result.kind, "pdf")
        self.assertEqual(result.route_info["parser"], "zhejiang_qyrcb_pdf_text")
        self.assertGreater(len(result.rows), 200)


if __name__ == "__main__":
    unittest.main()
