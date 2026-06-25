import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("validate_stage", ROOT / "scripts" / "validate_stage.py")
validate_stage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validate_stage)


class ValidateStageTests(unittest.TestCase):
    def test_stage_1_fails_when_any_input_was_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            columns = sorted(validate_stage.STD_REQUIRED)
            row = {column: "1" for column in columns}
            row.update({
                "交易唯一编号": "a.pdf-1",
                "来源文件名": "a.pdf",
                "来源行号": "1",
            })
            with (work / "a__standardized.csv").open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=columns)
                writer.writeheader()
                writer.writerow(row)
            (work / "a__mapping.json").write_text(json.dumps({"ok": True}), encoding="utf-8")

            with self.assertRaises(validate_stage.ValidationError):
                validate_stage.validate_standardize(
                    str(work),
                    skipped_inputs=[{"name": "b.pdf", "reason": "未识别到结构化流水表格"}],
                )


if __name__ == "__main__":
    unittest.main()
