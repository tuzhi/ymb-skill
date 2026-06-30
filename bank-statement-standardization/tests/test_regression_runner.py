import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("run_regression", ROOT / "scripts" / "run_regression.py")
run_regression = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_regression)


class RegressionRunnerTests(unittest.TestCase):
    def test_load_suite_reads_cases_from_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "regression_cases.yaml"
            config.write_text(
                """
suites:
  p0_smoke:
    - id: sample_case
      file: 客户/样本.xlsx
      reason: 覆盖示例
      tags: [xlsx, 对公]
""",
                encoding="utf-8",
            )

            cases = run_regression.load_suite(config, "p0_smoke")

        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["id"], "sample_case")
        self.assertEqual(cases[0]["file"], "客户/样本.xlsx")

    def test_compare_baseline_reports_changed_metric(self):
        actual = {
            "case_id": "sample_case",
            "status": "PASS",
            "metrics": {
                "parser": "new_parser",
                "standardized_rows": 10,
            },
        }
        expected = {
            "case_id": "sample_case",
            "status": "PASS",
            "metrics": {
                "parser": "old_parser",
                "standardized_rows": 10,
            },
        }

        diffs = run_regression.compare_case(actual, expected)

        self.assertEqual(
            diffs,
            [{"metric": "parser", "expected": "old_parser", "actual": "new_parser"}],
        )

    def test_write_baseline_keeps_suite_and_case_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "baseline.json"
            cases = [{"case_id": "a", "status": "PASS", "metrics": {"standardized_rows": 1}}]

            run_regression.write_baseline(path, "p0_smoke", cases)

            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(payload["suite"], "p0_smoke")
        self.assertEqual(payload["case_count"], 1)
        self.assertEqual(payload["cases"], cases)

    def test_metrics_include_route_info(self):
        df = pd.DataFrame([
            {
                "交易唯一编号": "u1",
                "本方账户": "123",
                "本方名称": "客户A",
                "开户行": "测试银行",
                "账户类型": "对公",
                "收入金额": "10",
                "支出金额": "",
                "交易金额": "10",
                "账户余额": "10",
            }
        ])
        route_info = {
            "parser": "sample_parser",
            "decision": "matched",
            "bank": "测试银行",
            "account_type": "对公",
        }

        metrics = run_regression._metrics(df, route_info)

        self.assertEqual(metrics["parser"], "sample_parser")
        self.assertEqual(metrics["decision"], "matched")
        self.assertEqual(metrics["bank"], "测试银行")
        self.assertEqual(metrics["account_type"], "对公")


if __name__ == "__main__":
    unittest.main()
