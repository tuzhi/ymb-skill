import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from runtime import runner as runner_runtime  # noqa: E402
from runtime import result_store as result_store  # noqa: E402


def runner_args(run_root, folder, *, client="测试客户", parent_run_id=""):
    return SimpleNamespace(
        run_root=str(run_root),
        folder=str(folder),
        client=client,
        client_arg_provided=True,
        error_bundle_mode="safe",
        parent_run_id=parent_run_id,
        rerun_reason="incremental_submission" if parent_run_id else "",
        account_type=None,
        file_sleep_seconds=0,
    )


class StageOneResultsAndQCTest(unittest.TestCase):
    def write_standardized(self, path, source_name, transaction_time="2026-01-01"):
        columns = sorted(runner_runtime.V.STD_REQUIRED)
        row = {column: "1" for column in columns}
        row["交易唯一编号"] = f"TX-{source_name}"
        row["交易时间"] = transaction_time
        row["来源文件名"] = source_name
        row["来源行号"] = "2"
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            writer.writerow(row)

    def standardize_success(self, context):
        source_name = Path(context.path).name
        output = Path(context.out_dir) / f"{Path(source_name).stem}__pdf__standardized.csv"
        self.write_standardized(output, source_name)
        return str(output), "", {
            "标准化统计": {"交易笔数": 1},
            "文件画像": {
                "fingerprint_id": "pdf.test",
                "series_family": "test",
                "router_bank": "测试银行",
                "decision": "matched",
            },
        }

    def test_single_file_error_does_not_discard_other_done_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input"
            source.mkdir()
            (source / "正常.pdf").write_bytes(b"good")
            (source / "失败.pdf").write_bytes(b"bad")
            runner = runner_runtime.Runner(runner_args(root / "runs", source))

            def standardize(context):
                if Path(context.path).name == "失败.pdf":
                    raise RuntimeError("模拟 Parser 失败")
                return self.standardize_success(context)

            with patch.object(runner_runtime.S, "standardize_file", side_effect=standardize):
                with self.assertRaisesRegex(RuntimeError, "模拟 Parser 失败"):
                    runner.stage_1_standardize()

            results = runner.load_stage_1_results()["files"]
            by_name = {record["name"]: record for record in results.values()}
            self.assertEqual(by_name["正常.pdf"]["status"], "DONE")
            self.assertEqual(by_name["正常.pdf"]["recognized_type"], "测试银行")
            self.assertEqual(by_name["正常.pdf"]["record_count"], 1)
            self.assertEqual(by_name["失败.pdf"]["status"], "ERROR")
            self.assertTrue(
                (Path(runner.run_dir) / by_name["正常.pdf"]["output"]).is_file()
            )
            decision_receipt = next(
                Path(runner.receipt_dir).glob("*-stage_1_files.json")
            )
            receipt = json.loads(decision_receipt.read_text(encoding="utf-8"))
            self.assertEqual(receipt["status"], "partial")
            self.assertEqual(len(receipt["details"]["added"]), 2)

            runner.handle_stage_failure(
                "stage_1_standardize",
                runner.stages["stage_1_standardize"],
                RuntimeError("阶段一存在失败文件"),
            )
            run_result = result_store.run_result_from_pipeline(json.loads(
                Path(runner.pipeline_result_path).read_text(encoding="utf-8")
            ))
            self.assertEqual(run_result["next_action"], "NEED_REPAIR")
            public = runner_runtime.public_result(run_result, runner.run_dir)
            self.assertEqual(
                public["request"]["input_refs"],
                ["input/失败.pdf"],
            )

    def test_child_run_reuses_same_md5_same_name_done_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input"
            source.mkdir()
            (source / "流水.pdf").write_bytes(b"same-content")
            run_root = root / "runs"

            parent = runner_runtime.Runner(runner_args(run_root, source))
            with patch.object(
                runner_runtime.S,
                "standardize_file",
                side_effect=self.standardize_success,
            ):
                parent.stage_1_standardize()

            child = runner_runtime.Runner(
                runner_args(run_root, source, parent_run_id=parent.run_id)
            )
            with patch.object(
                runner_runtime.S,
                "standardize_file",
                side_effect=AssertionError("复用文件不应重新标准化"),
            ):
                result = child.stage_1_standardize()

            self.assertEqual(len(result["reused"]), 1)
            self.assertEqual(result["rerun"], [])
            record = next(iter(child.load_stage_1_results()["files"].values()))
            self.assertEqual(record["status"], "DONE")
            self.assertEqual(record["recognized_type"], "测试银行")
            self.assertEqual(record["record_count"], 1)
            self.assertNotIn("route_artifact", child.stages["stage_1_standardize"])

    def test_child_run_does_not_reuse_legacy_unmatched_raw_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input"
            source.mkdir()
            (source / "流水.pdf").write_bytes(b"same-content")
            run_root = root / "runs"

            parent = runner_runtime.Runner(runner_args(run_root, source))
            with patch.object(
                runner_runtime.S,
                "standardize_file",
                side_effect=self.standardize_success,
            ):
                parent.stage_1_standardize()
            parent_results = parent.load_stage_1_results()
            parent_record = next(iter(parent_results["files"].values()))
            parent_record["route"] = {
                "fingerprint_id": "",
                "series_family": "",
                "router_bank": "未识别",
                "yaml_match_status": "unmatched",
            }
            parent.write_stage_1_results(parent_results)

            child = runner_runtime.Runner(
                runner_args(run_root, source, parent_run_id=parent.run_id)
            )
            with patch.object(
                runner_runtime.S,
                "standardize_file",
                side_effect=self.standardize_success,
            ) as standardize:
                result = child.stage_1_standardize()

            self.assertEqual(standardize.call_count, 1)
            self.assertEqual(result["reused"], [])
            self.assertEqual(len(result["rerun"]), 1)
            decision = next(iter(result["decisions"].values()))
            self.assertEqual(decision["reason"], "parent_route_invalid")

    def test_child_run_reuses_three_done_and_reruns_two_failed_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input"
            source.mkdir()
            for name in ("A.pdf", "B.pdf", "C.pdf", "D.pdf", "E.pdf"):
                (source / name).write_bytes(name.encode("utf-8"))
            run_root = root / "runs"
            failed_names = {"D.pdf", "E.pdf"}

            parent = runner_runtime.Runner(runner_args(run_root, source))

            def parent_standardize(context):
                if Path(context.path).name in failed_names:
                    raise RuntimeError("模拟 Parser 失败")
                return self.standardize_success(context)

            with patch.object(
                runner_runtime.S,
                "standardize_file",
                side_effect=parent_standardize,
            ):
                with self.assertRaisesRegex(RuntimeError, "存在失败文件"):
                    parent.run_pipeline_stages()

            parent_records = parent.load_stage_1_results()["files"]
            self.assertEqual(
                sorted(record["status"] for record in parent_records.values()),
                ["DONE", "DONE", "DONE", "ERROR", "ERROR"],
            )
            self.assertEqual(
                parent.stages["stage_1_standardize"]["status"],
                "ERROR",
            )

            child = runner_runtime.Runner(
                runner_args(run_root, source, parent_run_id=parent.run_id)
            )
            with patch.object(
                runner_runtime.S,
                "standardize_file",
                side_effect=self.standardize_success,
            ) as standardize:
                result = child.stage_1_standardize()

            rerun_names = {
                Path(call.args[0].path).name
                for call in standardize.call_args_list
            }
            self.assertEqual(standardize.call_count, 2)
            self.assertEqual(rerun_names, failed_names)
            self.assertEqual(len(result["reused"]), 3)
            self.assertEqual(len(result["rerun"]), 2)
            self.assertEqual(result["added"], [])
            self.assertEqual(
                {record["status"] for record in child.load_stage_1_results()["files"].values()},
                {"DONE"},
            )

    def test_same_md5_different_name_is_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent_source = root / "parent-input"
            child_source = root / "child-input"
            parent_source.mkdir()
            child_source.mkdir()
            (parent_source / "A.pdf").write_bytes(b"same-content")
            (child_source / "B.pdf").write_bytes(b"same-content")
            run_root = root / "runs"

            parent = runner_runtime.Runner(runner_args(run_root, parent_source))
            with patch.object(
                runner_runtime.S,
                "standardize_file",
                side_effect=self.standardize_success,
            ):
                parent.stage_1_standardize()

            child = runner_runtime.Runner(
                runner_args(run_root, child_source, parent_run_id=parent.run_id)
            )
            with patch.object(
                runner_runtime.S,
                "standardize_file",
                side_effect=self.standardize_success,
            ) as standardize:
                result = child.stage_1_standardize()

            self.assertEqual(standardize.call_count, 1)
            self.assertEqual(result["reused"], [])
            self.assertEqual(len(result["rerun"]), 1)
            decision = next(iter(result["decisions"].values()))
            self.assertEqual(decision["reason"], "same_md5_different_name")

    def test_parent_abc_to_bcd_reports_reused_added_and_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent_source = root / "parent-input"
            child_source = root / "child-input"
            parent_source.mkdir()
            child_source.mkdir()
            for name, content in (("A.pdf", b"A"), ("B.pdf", b"B"), ("C.pdf", b"C")):
                (parent_source / name).write_bytes(content)
            for name, content in (("B.pdf", b"B"), ("C.pdf", b"C"), ("D.pdf", b"D")):
                (child_source / name).write_bytes(content)
            run_root = root / "runs"

            parent = runner_runtime.Runner(runner_args(run_root, parent_source))
            with patch.object(
                runner_runtime.S,
                "standardize_file",
                side_effect=self.standardize_success,
            ):
                parent.stage_1_standardize()

            child = runner_runtime.Runner(
                runner_args(run_root, child_source, parent_run_id=parent.run_id)
            )
            with patch.object(
                runner_runtime.S,
                "standardize_file",
                side_effect=self.standardize_success,
            ) as standardize:
                result = child.stage_1_standardize()

            self.assertEqual(standardize.call_count, 1)
            self.assertEqual(len(result["reused"]), 2)
            self.assertEqual(len(result["added"]), 1)
            self.assertEqual(result["rerun"], [])
            self.assertEqual(result["removed"], [f"md5:{runner_runtime.md5(parent_source / 'A.pdf')}"])

    def test_same_run_duplicate_content_is_standardized_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input"
            source.mkdir()
            (source / "A.pdf").write_bytes(b"same-content")
            (source / "B.pdf").write_bytes(b"same-content")
            runner = runner_runtime.Runner(runner_args(root / "runs", source))

            with patch.object(
                runner_runtime.S,
                "standardize_file",
                side_effect=self.standardize_success,
            ) as standardize:
                result = runner.stage_1_standardize()

            self.assertEqual(standardize.call_count, 1)
            self.assertEqual(result["processed_files"], 1)
            self.assertEqual(len(runner.load_stage_1_results()["files"]), 1)
            self.assertEqual(len(runner.pipeline_state["skipped_inputs"]), 1)
            self.assertIn(
                "内容 MD5 相同",
                runner.pipeline_state["skipped_inputs"][0]["reason"],
            )

    def test_qc_results_separate_file_contains_file_and_customer_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input"
            source.mkdir()
            (source / "流水.pdf").write_bytes(b"content")
            runner = runner_runtime.Runner(runner_args(root / "runs", source))

            with patch.object(
                runner_runtime.S,
                "standardize_file",
                side_effect=self.standardize_success,
            ):
                runner.stage_1_standardize()

            qc = json.loads(Path(runner.qc_results_file()).read_text(encoding="utf-8"))
            file_rules = next(iter(qc["files"].values()))
            self.assertIn("file.openable", file_rules)
            self.assertIn("file.source_format_quality", file_rules)
            self.assertIn("customer.coverage_two_years", qc["customer"])
            self.assertEqual(qc["status"], "RUNNING")
            self.assertNotIn("qc", runner.pipeline_state)

            self.assertEqual(
                runner_runtime.Q.update_status(qc, final=True),
                "PASS_WITH_WARNINGS",
            )

    def test_declared_standardized_input_is_blocked_by_customer_hard_qc(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input"
            source.mkdir()
            runner = runner_runtime.Runner(runner_args(root / "runs", source))

            def declared_stage_1(work):
                output = Path(work) / "流水__standardized.csv"
                self.write_standardized(output, "流水.csv")
                runner.write_stage_1_results({
                    "files": {
                        "md5:test": {
                            "name": "流水__standardized.csv",
                            "status": "DONE",
                            "output": str(output.relative_to(runner.run_dir)),
                            "route": {
                                "fingerprint_id": "",
                                "series_family": "",
                                "router_bank": "未识别",
                                "yaml_match_status": "unmatched",
                            },
                        }
                    }
                })
                return runner_runtime.StageResult(
                    "stage_1_standardize",
                    {"mode": "manifest_declared_standardized_input"},
                )

            with (
                patch.object(
                    runner,
                    "stage_1_from_declared_standardized_manifest",
                    side_effect=declared_stage_1,
                ),
                patch.object(runner, "run_customer_qc", return_value=True),
            ):
                with self.assertRaisesRegex(RuntimeError, "客户级 HARD QC 未通过"):
                    runner.stage_1_standardize()

    def test_qc_executor_runs_remaining_rule_after_exception(self):
        results = runner_runtime.Q.empty_results()
        registry = {
            "file.first": {
                "scope": runner_runtime.Q.FILE,
                "checkpoint": runner_runtime.Q.BEFORE_STAGE_1,
                "level": runner_runtime.Q.SOFT,
                "handler": lambda _context: (_ for _ in ()).throw(RuntimeError("boom")),
            },
            "file.second": {
                "scope": runner_runtime.Q.FILE,
                "checkpoint": runner_runtime.Q.BEFORE_STAGE_1,
                "level": runner_runtime.Q.SOFT,
                "handler": lambda _context: {"passed": True, "message": ""},
            },
        }

        bucket = runner_runtime.Q.execute_checkpoint(
            results,
            runner_runtime.Q.FILE,
            runner_runtime.Q.BEFORE_STAGE_1,
            {},
            file_id="md5:test",
            registry=registry,
        )

        self.assertFalse(bucket["file.first"]["passed"])
        self.assertTrue(bucket["file.second"]["passed"])


if __name__ == "__main__":
    unittest.main()
