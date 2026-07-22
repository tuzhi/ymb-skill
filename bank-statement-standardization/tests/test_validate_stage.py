import csv
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("validate_stage", ROOT / "scripts" / "validate_stage.py")
validate_stage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validate_stage)


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
                    "inferred_bank": "",
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


if __name__ == "__main__":
    unittest.main()
