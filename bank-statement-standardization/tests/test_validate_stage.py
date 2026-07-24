import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime import validators as validate_stage


class ValidateStageTests(unittest.TestCase):
    def test_stage_1_reports_non_statement_inputs_as_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            columns = sorted(validate_stage.STD_REQUIRED)
            row = {column: "1" for column in columns}
            row.update({
                "交易唯一编号": "a.pdf-1",
                "交易时间": "2026-01-01 12:00:00",
                "来源文件名": "a.pdf",
                "来源行号": "1",
            })
            with (work / "a__standardized.csv").open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=columns)
                writer.writeheader()
                writer.writerow(row)
            result = validate_stage.validate_standardize(
                str(work),
                skipped_inputs=[{"name": "b.pdf", "reason": "未识别到结构化流水表格"}],
                file_routes={"a__standardized.csv": {
                    "fingerprint_id": "",
                    "series_family": "",
                    "router_bank": "未识别",
                    "yaml_match_status": "unmatched",
                }},
            )

        self.assertEqual(result["standardized_files"], 1)
        self.assertEqual(result["skipped_inputs"], 1)

    def test_stage_1_rejects_embedded_english_header_as_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            columns = sorted(validate_stage.STD_REQUIRED)
            row = {column: "1" for column in columns}
            row.update({
                "交易唯一编号": "a.xlsx-30",
                "交易时间": "Date",
                "来源文件名": "a.xlsx",
                "来源行号": "30",
            })
            with (work / "a__standardized.csv").open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=columns)
                writer.writeheader()
                writer.writerow(row)
            with self.assertRaisesRegex(validate_stage.ValidationError, "Date"):
                validate_stage.validate_standardize(str(work))

    def test_stage_1_rejects_manifest_route_without_matching_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            columns = sorted(validate_stage.STD_REQUIRED)
            row = {column: "1" for column in columns}
            row.update({"交易时间": "2026-01-01", "来源文件名": "a.pdf", "来源行号": "1"})
            with (work / "a__standardized.csv").open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=columns)
                writer.writeheader()
                writer.writerow(row)
            with self.assertRaisesRegex(validate_stage.ValidationError, "文件路由与标准化 CSV 不一致"):
                validate_stage.validate_standardize(str(work), file_routes={})

    def test_portfolio_allows_missing_daily_csv_when_report_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "客户__余额校验.json").write_text(
                json.dumps({"数据范围": {"账户数": 0}}, ensure_ascii=False),
                encoding="utf-8",
            )

            result = validate_stage.validate_portfolio(str(work))

        self.assertEqual(result["portfolio_days"], 0)

    def test_portfolio_requires_daily_csv_when_report_has_balance_accounts(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "客户__余额校验.json").write_text(
                json.dumps({"数据范围": {"账户数": 1}}, ensure_ascii=False),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(validate_stage.ValidationError, "缺少组合日余额 CSV"):
                validate_stage.validate_portfolio(str(work))

    def test_final_allows_workbook_without_optional_daily_balance_sheet(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            path = out / "客户_已清洗_待分析.xlsx"
            columns = sorted(validate_stage.TAG_REQUIRED)
            row = {column: "1" for column in columns}
            row.update({
                "交易唯一编号": "TX-1",
                "来源文件名": "a.pdf",
                "来源行号": "1",
            })
            with pd.ExcelWriter(path, engine="openpyxl") as writer:
                pd.DataFrame([["说明"]], columns=["内容"]).to_excel(
                    writer, sheet_name="封面与说明", index=False)
                pd.DataFrame([row], columns=columns).to_excel(
                    writer, sheet_name="整合打标流水", index=False)
                for sheet in ("账户清单", "余额校验", "标签汇总", "人工复核事项"):
                    pd.DataFrame([[""]], columns=["内容"]).to_excel(
                        writer, sheet_name=sheet, index=False)

            result = validate_stage.validate_final(str(out), "客户", tagged_rows=1)

        self.assertNotIn("组合日余额(虚拟账户)", result["sheets"])


if __name__ == "__main__":
    unittest.main()
