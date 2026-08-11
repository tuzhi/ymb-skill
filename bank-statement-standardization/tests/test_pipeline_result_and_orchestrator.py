import importlib.util
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd


SKILL_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_PATH = SKILL_ROOT / "scripts" / "orchestrator.py"
spec = importlib.util.spec_from_file_location("orchestrator_pipeline_result", ORCHESTRATOR_PATH)
orchestrator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orchestrator)

from runtime import runner as runner_runtime  # noqa: E402
from runtime import result_store as result_store  # noqa: E402
from runtime.models import PipelineExecutionResult  # noqa: E402


def stored_pipeline(runner):
    return json.loads(Path(runner.pipeline_result_path).read_text(encoding="utf-8"))


def stored_run_result(runner):
    return result_store.run_result_from_pipeline(stored_pipeline(runner))


def runner_args(run_root, folder, *, client, client_arg_provided=False, parent_run_id=""):
    return SimpleNamespace(
        run_root=str(run_root),
        folder=str(folder),
        client=client,
        client_arg_provided=client_arg_provided,
        error_bundle_mode="full",
        parent_run_id=parent_run_id,
        rerun_reason="ai_repair_after_stage_1_failure" if parent_run_id else "",
        account_type=None,
    )


class PipelineResultAndOrchestratorTest(unittest.TestCase):
    def test_cli_uses_runner_memory_result_without_reading_run_result_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input"
            source.mkdir()
            run_id = "20260806T120000+0800-abcdef12"
            execution = PipelineExecutionResult(
                exit_code=0,
                run_id=run_id,
                client_name="测试客户",
                parent_run_id="",
                status="DONE",
                file_results={"files": {}},
                stages={},
                stage_summaries={},
                qc={"status": "PASS"},
                artifacts=(),
                run_result={
                    "contract_version": 1,
                    "run_id": run_id,
                    "status": "DONE",
                    "next_action": "DELIVER",
                    "artifact_refs": [],
                    "context_ref": "pipeline_result.json",
                    "message": "完成",
                    "reason_code": "",
                },
            )
            runner = SimpleNamespace(
                run_id=run_id,
                run_dir=str(root / "runs" / run_id),
                pipeline_result_path=str(root / "runs" / run_id / "pipeline_result.json"),
                execute=Mock(return_value=execution),
            )
            stdout = io.StringIO()
            with (
                patch.object(
                    orchestrator._execution_plan,
                    "load_or_create_execution_plan",
                    return_value=(run_id, "plan-key"),
                ),
                patch.object(
                    orchestrator._execution_plan,
                    "claim_planned_run",
                    return_value=(runner.run_dir, True),
                ),
                patch.object(orchestrator._runner_runtime, "Runner", return_value=runner),
                patch.object(
                    orchestrator._execution_plan,
                    "release_execution_plan",
                ) as release,
                patch.object(orchestrator.sys, "stdout", stdout),
            ):
                exit_code = orchestrator.main([
                    "run",
                    "--folder",
                    str(source),
                    "--run-root",
                    str(root / "runs"),
                ])

            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["next_action"], "DELIVER")
            release.assert_called_once_with(
                str(root / "runs"),
                "plan-key",
                run_id,
            )

    def test_new_run_writes_only_pipeline_result_with_run_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "斑马商业对公流水"
            source.mkdir()
            (source / "流水.csv").write_text("raw", encoding="utf-8")

            runner = runner_runtime.Runner(runner_args(root / "runs", source, client="斑马商业"))

            pipeline_result = stored_pipeline(runner)
            self.assertEqual(pipeline_result["client_name"], "斑马商业")
            self.assertEqual(pipeline_result["parent_run_id"], "")
            self.assertEqual(pipeline_result["rerun_reason"], "")
            self.assertEqual(pipeline_result["attempts"]["password"], 0)
            self.assertEqual(pipeline_result["attempts"]["ai_repair"], 0)
            self.assertEqual(pipeline_result["skill_version"], "1.4.11")
            self.assertEqual(pipeline_result["refs"], {
                "stage_1": "stage_1_results.json",
                "qc": "qc_results.json",
            })
            self.assertEqual(pipeline_result["deliverables"], [])
            for removed in (
                "exit_code",
                "run_result",
                "stage_summaries",
                "artifacts",
                "file_results_ref",
                "qc_ref",
                "token_usage_ref",
            ):
                self.assertNotIn(removed, pipeline_result)
            usage = json.loads(
                (Path(runner.run_dir) / "token_usage.json").read_text(encoding="utf-8")
            )
            self.assertEqual(usage["ai_session_count"], 0)
            self.assertEqual(usage["measurement_status"], "not_started")
            self.assertEqual(
                usage["measurement_scope"],
                "repair_sessions_only",
            )
            self.assertNotIn("skill_contracts", usage)
            self.assertFalse((Path(runner.run_dir) / "manifest.json").exists())
            self.assertFalse((Path(runner.run_dir) / "run_result.json").exists())
            self.assertFalse((Path(runner.run_dir) / "run_manifest.json").exists())
            for stage_id, stage in pipeline_result["stages"].items():
                self.assertNotIn("ai_fallback_dir", stage)
                self.assertNotIn("started_at", stage)
                self.assertIsNone(stage["duration_seconds"])
                self.assertNotIn("script", stage)
                self.assertNotIn("validator", stage)
                self.assertNotIn("name", stage)
                if stage_id != "stage_1_standardize":
                    self.assertNotIn("ai_fallback_refs", stage)
                    self.assertNotIn("ai_fallback_used", stage)
                    self.assertNotIn("ai_fallback_artifacts", stage)

    def test_parent_context_reads_client_from_pipeline_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp) / "runs"
            parent = run_root / "parent-run"
            parent.mkdir(parents=True)
            (parent / "manifest.json").write_text(
                json.dumps(
                    {
                        "skill": {"name": "bank-statement-standardization", "version": "1.2.12"},
                        "client": "斑马商业",
                        "parent_run_id": "",
                        "rerun_reason": "",
                        "stage_1_standardize": {
                            "status": "ERROR",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            context = runner_runtime.load_parent_run_context(str(run_root), "parent-run")

            self.assertEqual(context["parent_client"], "斑马商业")

    def test_child_run_inherits_parent_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_root = root / "runs"
            parent = run_root / "parent-run"
            parent.mkdir(parents=True)
            (parent / "manifest.json").write_text(
                json.dumps(
                    {
                        "client": "斑马商业",
                        "stage_1_standardize": {"status": "ERROR", "ai_fallback_artifacts": []},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            source = root / "错误包重跑目录"
            source.mkdir()
            (source / "流水.csv").write_text("raw", encoding="utf-8")

            runner = runner_runtime.Runner(
                runner_args(run_root, source, client="错误包重跑目录", parent_run_id="parent-run")
            )

            self.assertEqual(runner.args.client, "斑马商业")
            self.assertEqual(runner.pipeline_state["client_name"], "斑马商业")

    def test_child_run_rejects_explicit_client_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_root = root / "runs"
            parent = run_root / "parent-run"
            parent.mkdir(parents=True)
            (parent / "manifest.json").write_text(
                json.dumps(
                    {
                        "client": "斑马商业",
                        "stage_1_standardize": {"status": "ERROR", "ai_fallback_artifacts": []},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            source = root / "input"
            source.mkdir()

            with self.assertRaisesRegex(RuntimeError, "父运行客户名称不一致"):
                runner_runtime.Runner(
                    runner_args(
                        run_root,
                        source,
                        client="其他客户",
                        client_arg_provided=True,
                        parent_run_id="parent-run",
                    )
                )

    def test_stage_1_failure_execution_summary_drives_repair_without_stage_file_readback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input"
            source.mkdir()
            (source / "流水.csv").write_text("raw", encoding="utf-8")
            runner = runner_runtime.Runner(
                runner_args(root / "runs-stage-1-summary", source, client="斑马商业")
            )
            stage_id = "stage_1_standardize"
            snapshot = Path(runner.input_dir) / "流水.csv"
            file_id = "md5:" + runner_runtime.md5(str(snapshot))
            file_results = {"files": {file_id: {
                "name": "流水.csv",
                "relative_path": "流水.csv",
                "status": "ERROR",
                "reason_code": "MAPPING_FAILED",
                "message": "阶段一测试失败",
            }}}

            def fail_stage_1():
                runner.write_stage_1_results(file_results)
                error = RuntimeError("阶段一测试失败")
                runner.handle_stage_failure(stage_id, runner.stages[stage_id], error)
                raise error

            runner.run_pipeline_stages = fail_stage_1
            execution = runner.execute()
            summary = execution.to_summary_dict()

            self.assertEqual(execution.exit_code, 1)
            self.assertEqual(summary["next_action"], "NEED_REPAIR")
            self.assertEqual(summary["stages"][stage_id]["status"], "ERROR")
            self.assertNotIn("ai_fallback_used", summary["stages"][stage_id])
            self.assertEqual(summary["file_results"], file_results)
            self.assertNotIn("action", summary)
            self.assertEqual(summary["context_ref"], "stage_1_results.json")
            self.assertNotIn("dataset", summary)
            self.assertFalse((Path(runner.run_dir) / "fallback").exists())
            json.dumps(summary, ensure_ascii=False)

            Path(runner.stage_1_results_path).unlink()
            public = runner_runtime.public_result(
                summary,
                runner.run_dir,
                stage_results=execution.file_results,
                attempts=runner.pipeline_state.get("attempts") or {},
            )
            self.assertEqual(public["status"], "NEED_REPAIR")
            self.assertEqual(public["role"], "repair")
            self.assertEqual(public["action"]["operation"], "submit")
            self.assertEqual(public["request"]["failed_files"][0]["file_id"], file_id)

    def test_password_failure_requests_user_without_ai_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input"
            source.mkdir()
            (source / "加密流水.pdf").write_bytes(b"%PDF-1.4\n")
            runner = runner_runtime.Runner(
                runner_args(root / "runs", source, client="斑马商业")
            )

            runner.handle_stage_failure(
                "stage_1_standardize",
                runner.stages["stage_1_standardize"],
                RuntimeError("PDFPasswordIncorrect: password required"),
            )

            result = stored_run_result(runner)
            self.assertEqual(result["next_action"], "REQUEST_USER")
            self.assertEqual(result["reason_code"], "INPUT_PASSWORD_REQUIRED")
            self.assertEqual(result["action"]["operation"], "retry-password")
            self.assertEqual(result["action"]["file_refs"], ["加密流水.pdf"])
            self.assertEqual(result["action"]["input_transport"], "stdin")
            self.assertFalse((Path(runner.run_dir) / "repair").exists())

    def test_repair_attempt_limit_routes_to_maintainer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input"
            source.mkdir()
            raw = source / "流水.pdf"
            raw.write_bytes(b"%PDF-1.4\n")
            runner = runner_runtime.Runner(
                runner_args(root / "runs", source, client="斑马商业")
            )
            snapshot = Path(runner.input_dir) / raw.name
            file_id = "md5:" + runner_runtime.md5(str(snapshot))
            runner.pipeline_state["attempts"]["ai_repair"] = (
                runner_runtime.F.MAX_AI_REPAIR_ATTEMPTS
            )
            runner.write_pipeline_result()
            runner.write_stage_1_results({"files": {file_id: {
                "name": raw.name,
                "relative_path": raw.name,
                "status": "ERROR",
                "reason_code": "VALIDATION_FAILED",
                "message": "Repair CSV 验证失败",
            }}})

            runner.handle_stage_failure(
                "stage_1_standardize",
                runner.stages["stage_1_standardize"],
                RuntimeError("Repair CSV 验证失败"),
            )

            result = stored_run_result(runner)
            self.assertEqual(result["next_action"], "MAINTAINER_REQUIRED")
            self.assertIn("次数已达上限", result["message"])

    def test_downstream_failure_does_not_record_ai_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input"
            source.mkdir()
            (source / "流水.csv").write_text("raw", encoding="utf-8")

            for stage_id in (
                "stage_2_integrate",
                "stage_2b_portfolio_balance",
                "stage_3_tag",
                "stage_4_package",
            ):
                runner = runner_runtime.Runner(
                    runner_args(root / f"runs-{stage_id}", source, client="斑马商业")
                )
                runner.handle_stage_failure(
                    stage_id,
                    runner.stages[stage_id],
                    RuntimeError(f"{stage_id}-测试失败"),
                )

                pipeline_result = stored_pipeline(runner)
                stage = pipeline_result["stages"][stage_id]
                self.assertEqual(stage["status"], "ERROR")
                self.assertNotIn("ai_fallback_refs", stage)
                self.assertNotIn("ai_fallback_used", stage)
                self.assertNotIn("ai_fallback_artifacts", stage)
                self.assertFalse((Path(runner.run_dir) / "repair").exists())
                result = result_store.run_result_from_pipeline(pipeline_result)
                self.assertEqual(result["next_action"], "REPORT_ERROR")
                self.assertEqual(result["reason_code"], "DOWNSTREAM_STAGE_FAILURE")
                events = Path(runner.event_path).read_text(encoding="utf-8")
                self.assertIn('"code": "STAGE_ERROR"', events)
                self.assertNotIn('"code": "AI_REPAIR_REQUIRED"', events)

    def test_ai_repair_child_run_passes_same_validator(self):
        """锁定关联 Child Run 和原 validator 的兼容链条。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_root = root / "runs"
            source = root / "斑马商业对公流水"
            source.mkdir()
            (source / "流水.csv").write_text("raw", encoding="utf-8")

            parent = runner_runtime.Runner(runner_args(run_root, source, client="斑马商业"))
            parent.handle_stage_failure(
                "stage_1_standardize",
                parent.stages["stage_1_standardize"],
                RuntimeError("字段映射失败"),
            )
            # Repair 以父 Run 输入快照为基础创建 Child Run。
            (source / "_file_hints.yaml").write_text("files: {}\n", encoding="utf-8")

            parent_context = runner_runtime.load_parent_run_context(str(run_root), parent.run_id)
            self.assertEqual(parent_context["parent_run_id"], parent.run_id)

            child = runner_runtime.Runner(
                runner_args(run_root, source, client="斑马商业", parent_run_id=parent.run_id)
            )
            self.assertTrue((Path(child.input_dir) / "_file_hints.yaml").exists())
            for stage_id, spec in child.stages.items():
                if stage_id.startswith("stage_") and stage_id != "stage_1_standardize":
                    spec["status"] = "DONE"
            child.write_pipeline_result()

            def fixed_stage_1():
                work = Path(child.work_dir())
                work.mkdir(parents=True, exist_ok=True)
                (work / "流水__standardized.csv").write_text(
                    "交易唯一编号,交易时间,本方账户,收入金额,支出金额,交易金额,账户余额,来源文件名,来源行号\n"
                    "TX-1,2026-01-01 00:00:00,62170001,1,,1,10,流水.csv,1\n",
                    encoding="utf-8",
                )
                child.write_stage_1_routes(str(work), {
                    "流水__standardized.csv": {
                        "fingerprint_id": "",
                        "series_family": "",
                        "router_bank": "未识别",
                        "reader_id": "ai_repair",
                        "account_type": "未知",
                        "yaml_match_status": "unmatched",
                    }
                })
                return {"standardized_files": 1}

            child.execute_stage_script = lambda stage_id: fixed_stage_1()
            child.run_pipeline_stages()

            self.assertEqual(child.pipeline_state["parent_run_id"], parent.run_id)
            self.assertEqual(
                child.pipeline_state["rerun_reason"],
                "ai_repair_after_stage_1_failure",
            )
            self.assertEqual(child.stages["stage_1_standardize"]["status"], "DONE")
            self.assertEqual(child.stage_validation_results["stage_1_standardize"]["standardized_rows"], 1)

    def test_child_run_consumes_authorized_repair_csv_for_failed_raw_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_root = root / "runs"
            source = root / "source"
            source.mkdir()
            raw = source / "交通银行流水.pdf"
            raw.write_bytes(b"%PDF-1.4\n")

            parent = runner_runtime.Runner(runner_args(run_root, source, client="测试客户"))
            parent_raw = Path(parent.input_dir) / raw.name
            file_id = "md5:" + runner_runtime.md5(str(parent_raw))
            parent.write_stage_1_results({"files": {file_id: {
                "name": raw.name,
                "relative_path": raw.name,
                "status": "ERROR",
                "reason_code": "ROUTE_UNMATCHED",
                "message": "未唯一命中 YAML",
                "route": {
                    "fingerprint_id": "",
                    "series_family": "",
                    "router_bank": "交通银行",
                    "yaml_match_status": "unmatched",
                },
            }}})
            parent.stages["stage_1_standardize"]["status"] = "ERROR"
            parent.write_pipeline_result()

            attempt = Path(parent.run_dir) / "repair" / "attempt-01"
            repaired = attempt / "standardized" / "交通银行__standardized.csv"
            repaired.parent.mkdir(parents=True)
            repaired.write_text(
                "交易唯一编号,交易时间,本方账户,收入金额,支出金额,交易金额,账户余额,来源文件名,来源行号\n"
                "TX-1,2026-01-01,62170001,100,,100,1100,交通银行流水.pdf,1\n",
                encoding="utf-8",
            )
            result_path = attempt / "repair_result.json"
            result_path.write_text(json.dumps({
                "contract_version": 1,
                "run_id": parent.run_id,
                "attempt": 1,
                "stage_id": "stage_1_standardize",
                "role": "repair",
                "status": "REPAIRED",
                "outputs": [{
                    "file_id": file_id,
                    "source_md5": file_id,
                    "standardized_csv": "standardized/交通银行__standardized.csv",
                    "row_count": 1,
                    "sha256": hashlib.sha256(repaired.read_bytes()).hexdigest(),
                }],
                "message": "",
            }, ensure_ascii=False), encoding="utf-8")

            args = runner_args(
                run_root,
                Path(parent.input_dir),
                client="测试客户",
                parent_run_id=parent.run_id,
            )
            args.ai_repair_attempt_increment = 1
            args.repair_result_snapshot = str(result_path)
            args.repair_result_sha256 = hashlib.sha256(result_path.read_bytes()).hexdigest()
            child = runner_runtime.Runner(args)

            stage_result = child.stage_1_standardize()
            validation = child.validate_stage("stage_1_standardize")
            record = child.load_stage_1_results()["files"][file_id]

            self.assertEqual(stage_result["repaired"], [file_id])
            self.assertEqual(record["status"], "DONE")
            self.assertEqual(record["standardization_source"], "ai_repair")
            self.assertEqual(record["route"]["yaml_match_status"], "unmatched")
            self.assertEqual(validation["standardized_rows"], 1)

    def test_downstream_stage_does_not_call_stage_validator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input"
            source.mkdir()
            (source / "流水.csv").write_text("raw", encoding="utf-8")
            runner = runner_runtime.Runner(runner_args(root / "runs", source, client="斑马商业"))
            for stage_id, stage in runner.stages.items():
                if stage_id.startswith("stage_"):
                    stage["status"] = "DONE"
            runner.stages["stage_2_integrate"]["status"] = ""
            runner.write_pipeline_result()
            integrated_csv = Path(runner.run_dir) / "artifacts" / "整合流水.csv"
            runner.execute_stage_script = Mock(return_value={
                "integrated_csv": str(integrated_csv),
                "integrated_rows": 1,
            })
            runner.validate_stage = Mock(side_effect=AssertionError("下游不应调用 stage validator"))

            with patch.object(
                runner_runtime.time,
                "perf_counter",
                side_effect=[10.0, 12.3456],
            ):
                runner.run_pipeline_stages()

            self.assertEqual(runner.stages["stage_2_integrate"]["status"], "DONE")
            self.assertEqual(
                runner.stages["stage_2_integrate"]["duration_seconds"],
                2.346,
            )
            runner.validate_stage.assert_not_called()
            receipt_paths = list(Path(runner.receipt_dir).glob("*.json"))
            self.assertFalse(any(
                "stage_2_integrate__validator" in path.name
                for path in receipt_paths
            ))
            expected = {
                "integrated_csv": "artifacts/整合流水.csv",
                "integrated_rows": 1,
            }
            self.assertEqual(runner.stage_summaries["stage_2_integrate"], expected)
            receipt = json.loads(receipt_paths[0].read_text(encoding="utf-8"))
            self.assertEqual(receipt["details"]["result"], expected)

    def test_failed_stage_records_duration_seconds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input"
            source.mkdir()
            (source / "流水.csv").write_text("raw", encoding="utf-8")
            runner = runner_runtime.Runner(
                runner_args(root / "runs", source, client="斑马商业")
            )
            for stage_id, stage in runner.stages.items():
                if stage_id.startswith("stage_"):
                    stage["status"] = "DONE"
            runner.stages["stage_2_integrate"]["status"] = ""
            runner.write_pipeline_result()
            runner.execute_stage_script = Mock(
                side_effect=RuntimeError("阶段二测试失败")
            )

            with (
                patch.object(
                    runner_runtime.time,
                    "perf_counter",
                    side_effect=[20.0, 21.2349],
                ),
                self.assertRaisesRegex(RuntimeError, "阶段二测试失败"),
            ):
                runner.run_pipeline_stages()

            stages = stored_pipeline(runner)["stages"]
            self.assertEqual(
                stages["stage_2_integrate"]["status"],
                "ERROR",
            )
            self.assertEqual(
                stages["stage_2_integrate"]["duration_seconds"],
                1.235,
            )

    def test_execute_failure_result_can_be_summarized_and_serialized(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input"
            source.mkdir()
            (source / "流水.csv").write_text("raw", encoding="utf-8")
            runner = runner_runtime.Runner(
                runner_args(root / "runs", source, client="斑马商业")
            )
            runner.stages["stage_1_standardize"]["status"] = "DONE"
            runner.write_pipeline_result()
            runner.execute_stage_script = Mock(
                side_effect=RuntimeError("阶段二测试失败")
            )

            execution = runner.execute()
            summary = execution.to_summary_dict()

            self.assertIsInstance(
                execution,
                PipelineExecutionResult,
            )
            self.assertEqual(execution.exit_code, 1)
            self.assertEqual(execution.status, "ERROR")
            self.assertEqual(execution.error, "阶段二测试失败")
            self.assertEqual(execution.run_result["next_action"], "REPORT_ERROR")
            self.assertEqual(summary["next_action"], "REPORT_ERROR")
            self.assertEqual(summary["stages"]["stage_1_standardize"]["status"], "DONE")
            self.assertEqual(summary["stages"]["stage_2_integrate"]["status"], "ERROR")
            self.assertEqual(summary["error"], "阶段二测试失败")
            self.assertNotIn("dataset", summary)
            self.assertNotIn("integration_report", summary)
            json.dumps(summary, ensure_ascii=False)

    def test_execution_result_uses_runner_memory_for_stage_and_qc_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input"
            source.mkdir()
            (source / "流水.csv").write_text("raw", encoding="utf-8")
            runner = runner_runtime.Runner(
                runner_args(root / "runs", source, client="斑马商业")
            )
            file_results = {
                "files": {
                    "md5:test": {
                        "name": "流水.csv",
                        "relative_path": "流水.csv",
                        "status": "DONE",
                    },
                },
            }
            qc_results = {"status": "PASS", "files": {}, "customer": {}}
            runner.write_stage_1_results(file_results)
            runner.write_qc_results(qc_results)
            input_reference = runner._remember_artifact(
                str(Path(runner.input_dir) / "流水.csv")
            )
            runner.write_run_result(
                status="DONE",
                next_action="DELIVER",
                context_ref="pipeline_result.json",
            )

            with (
                patch.object(
                    runner_runtime,
                    "read_json_if_exists",
                    side_effect=AssertionError("不应回读 Stage 1 文件"),
                ),
                patch.object(
                    runner_runtime.Q,
                    "load_results",
                    side_effect=AssertionError("不应回读 QC 文件"),
                ),
            ):
                execution = runner._pipeline_execution_result(0)

            self.assertIs(execution.file_results, file_results)
            self.assertIs(execution.qc, qc_results)
            self.assertEqual(input_reference, "input/流水.csv")
            artifact_ids = {
                item["artifact_id"]
                for item in execution.artifacts
            }
            self.assertIn("stage_1_results.json", artifact_ids)
            self.assertIn("qc_results.json", artifact_ids)
            self.assertNotIn("pipeline_result.json", artifact_ids)
            self.assertNotIn("input/流水.csv", artifact_ids)

    def test_stage_4_runs_final_delivery_validation_inside_program(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "artifacts"
            out.mkdir()

            runner = runner_runtime.Runner.__new__(runner_runtime.Runner)
            runner.args = SimpleNamespace(client="客户")
            runner.out_dir = str(out)
            runner.pipeline_state = {"skipped_inputs": []}
            runner.final_validation_result = None
            runner.run_dir = str(root)
            runner.tagged_transactions = pd.DataFrame({"交易唯一编号": ["TX-1"]})
            runner.daily_balances = pd.DataFrame({"日期": ["2026-01-01"]})
            runner.integration_report = {}
            runner.tag_report = {}
            runner.balance_report = {}
            final = {
                "deliverable": str(out / "客户_已清洗_待分析.xlsx"),
                "deliverable_rows": 1,
                "sheets": ["整合打标流水"],
            }

            with (
                patch.object(
                    runner_runtime.P,
                    "finalize_deliverable",
                    return_value=(final["deliverable"], {}),
                ),
                patch.object(runner, "run_relative_ref", return_value="artifacts/客户_已清洗_待分析.xlsx"),
                patch.object(runner_runtime, "sha256", return_value="sha256"),
                patch.object(runner_runtime.V, "validate_final", return_value=final) as validate_final,
            ):
                result = runner.stage_4_package()

            validate_final.assert_called_once_with(
                str(out), "客户", tagged_rows=1, require_daily_balance=True)
            self.assertEqual(result, {"status": "DONE", **final})
            self.assertEqual(runner.final_validation_result, {"status": "DONE", **final})

    def test_stage_4_allows_empty_daily_balance_dataframe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "artifacts"
            out.mkdir()

            runner = runner_runtime.Runner.__new__(runner_runtime.Runner)
            runner.args = SimpleNamespace(client="客户")
            runner.out_dir = str(out)
            runner.pipeline_state = {"skipped_inputs": []}
            runner.final_validation_result = None
            runner.run_dir = str(root)
            runner.tagged_transactions = pd.DataFrame({"交易唯一编号": ["TX-1"]})
            runner.daily_balances = pd.DataFrame()
            runner.integration_report = {}
            runner.tag_report = {}
            runner.balance_report = {}
            final = {
                "deliverable": str(out / "客户_已清洗_待分析.xlsx"),
                "deliverable_rows": 1,
                "sheets": ["整合打标流水"],
            }

            with (
                patch.object(
                    runner_runtime.P,
                    "finalize_deliverable",
                    return_value=(str(out / "客户_已清洗_待分析.xlsx"), {}),
                ) as finalize,
                patch.object(runner, "run_relative_ref", return_value="artifacts/客户_已清洗_待分析.xlsx"),
                patch.object(runner_runtime, "sha256", return_value="sha256"),
                patch.object(runner_runtime.V, "validate_final", return_value=final) as validate_final,
            ):
                runner.stage_4_package()

            daily = finalize.call_args.args[2]
            self.assertTrue(daily.empty)
            validate_final.assert_called_once_with(
                str(out), "客户", tagged_rows=1, require_daily_balance=False)

    def test_stage_4_keeps_dataset_when_excel_side_effect_fails(self):
        runner = runner_runtime.Runner.__new__(runner_runtime.Runner)
        runner.args = SimpleNamespace(client="客户")
        runner.out_dir = "/tmp/artifacts"
        runner.pipeline_state = {"skipped_inputs": []}
        runner.tagged_transactions = pd.DataFrame({"交易唯一编号": ["TX-1"]})
        runner.daily_balances = pd.DataFrame()
        runner.integration_report = {}
        runner.tag_report = {}
        runner.balance_report = {}
        runner.load_qc_results = lambda: {"status": "PASS", "files": {}, "customer": {}}
        dataset = {"transactions": runner.tagged_transactions}
        failure = runner_runtime.P.DeliverableWriteError(
            "/tmp/artifacts/客户_已清洗_待分析.xlsx",
            dataset,
            OSError("disk full"),
        )

        with patch.object(runner_runtime.P, "finalize_deliverable", side_effect=failure):
            result = runner.stage_4_package()

        self.assertIs(runner.delivery_dataset, dataset)
        self.assertEqual(runner.deliverable["status"], "ERROR")
        self.assertEqual(result["status"], "ERROR")

    def test_stage_2_checks_report_and_memory_row_count_inside_program(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            report = {"客户整合概览": {"整合交易数": 2, "整合账户数": 1}}

            runner = runner_runtime.Runner.__new__(runner_runtime.Runner)
            runner.args = SimpleNamespace(client="客户")
            runner.work_dir = lambda: str(work)
            runner.load_stage_1_routes = lambda: {}
            runner.standardized_paths_from_results = lambda: [str(work / "input__standardized.csv")]

            with patch.object(
                runner_runtime.I,
                "integrate_context_memory",
                return_value=(pd.DataFrame({"交易唯一编号": ["TX-1"]}), report),
            ):
                with self.assertRaisesRegex(RuntimeError, "阶段二整合交易数不一致"):
                    runner.stage_2_integrate()

    def test_stage_3_checks_input_output_row_count_inside_program(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            report = {"标签梳理概览": {"交易总数": 2, "规则命中率": 1.0}}

            runner = runner_runtime.Runner.__new__(runner_runtime.Runner)
            runner.skill_dir = str(SKILL_ROOT)
            runner.work_dir = lambda: str(work)
            runner.integrated_transactions = pd.DataFrame({"交易唯一编号": ["TX-1", "TX-2"]})

            with patch.object(
                runner_runtime.T,
                "tag_dataframe",
                return_value=(pd.DataFrame({"交易唯一编号": ["TX-1"]}), report),
            ):
                with self.assertRaisesRegex(RuntimeError, "阶段三打标前后交易数不一致"):
                    runner.stage_3_tag()


if __name__ == "__main__":
    unittest.main()
