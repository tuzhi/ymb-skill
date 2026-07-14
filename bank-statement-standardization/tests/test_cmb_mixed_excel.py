import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
sys.path.insert(0, str(REPO_ROOT / "ymb-standardization-core"))

from ymb_standardization_core.core import standardize  # noqa: E402


class CmbMixedExcelTests(unittest.TestCase):
    def test_compact_first_page_and_grid_rows_are_one_continuous_statement(self):
        source = (
            ROOT / "testdata" / "陈鑫伟"
            / "招商银行交易流水(申请时间2026年06月03日15时00分00秒).xlsx"
        )
        pdf_source = source.with_suffix(".pdf")
        with tempfile.TemporaryDirectory() as tmp:
            csv_path, mapping_path, report = standardize(str(source), out_dir=tmp)
            frame = pd.read_csv(csv_path, dtype=str)
            pdf_csv, pdf_mapping, _pdf_report = standardize(str(pdf_source), out_dir=tmp)
            pdf_frame = pd.read_csv(pdf_csv, dtype=str)

            self.assertEqual(Path(csv_path).name, f"{source.stem}__xlsx__standardized.csv")
            self.assertEqual(Path(pdf_csv).name, f"{source.stem}__pdf__standardized.csv")
            self.assertEqual(Path(mapping_path).name, f"{source.stem}__xlsx__mapping.json")
            self.assertEqual(Path(pdf_mapping).name, f"{source.stem}__pdf__mapping.json")
            self.assertNotEqual(csv_path, pdf_csv)
            self.assertTrue(Path(csv_path).is_file())
            self.assertTrue(Path(pdf_csv).is_file())

        self.assertEqual(report["文件画像"]["reader_id"], "openpyxl_cmb_mixed_grid")
        self.assertEqual(len(frame), 835)
        self.assertEqual(set(frame["本方名称"]), {"陈鑫伟"})
        self.assertEqual(set(frame["本方账户"]), {"6214********5566"})
        self.assertNotIn("Date", set(frame["交易时间"]))
        self.assertEqual(frame.iloc[0]["来源行号"], "12")
        self.assertEqual(frame.iloc[-1]["来源行号"], "848")

        balances = pd.to_numeric(frame["账户余额"])
        amounts = pd.to_numeric(frame["交易金额"])
        self.assertEqual(int(((balances - balances.shift(1) - amounts).abs() > 0.02).sum()), 0)
        self.assertEqual(set(frame["交易唯一编号"]), set(pdf_frame["交易唯一编号"]))


if __name__ == "__main__":
    unittest.main()
