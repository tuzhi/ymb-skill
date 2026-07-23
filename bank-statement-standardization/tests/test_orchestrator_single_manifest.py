import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_PATH = SKILL_ROOT / "scripts" / "orchestrator.py"
spec = importlib.util.spec_from_file_location("orchestrator_single_manifest", ORCHESTRATOR_PATH)
orchestrator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orchestrator)


def runner_args(run_root, folder, *, client, client_arg_provided=False, parent_run_id=""):
    return SimpleNamespace(
        run_root=str(run_root),
        folder=str(folder),
        client=client,
        client_arg_provided=client_arg_provided,
        error_bundle_mode="full",
        parent_run_id=parent_run_id,
        rerun_reason="ai_fallback_after_stage_failure" if parent_run_id else "",
        require_model="",
        account_type=None,
    )


class OrchestratorSingleManifestTest(unittest.TestCase):
    def test_new_run_writes_only_manifest_with_run_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "斑马商业对公流水"
            source.mkdir()
            (source / "流水.csv").write_text("raw", encoding="utf-8")

            runner = orchestrator.Runner(runner_args(root / "runs", source, client="斑马商业"))

            manifest_path = Path(runner.run_dir) / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["client"], "斑马商业")
            self.assertEqual(manifest["parent_run_id"], "")
            self.assertEqual(manifest["rerun_reason"], "")
            self.assertFalse((Path(runner.run_dir) / "run_manifest.json").exists())
            for stage_id, stage in manifest.items():
                if not stage_id.startswith("stage_"):
                    continue
                self.assertNotIn("ai_fallback_dir", stage)
                self.assertNotIn("started_at", stage)
                self.assertNotIn("duration_seconds", stage)
                self.assertNotIn("script", stage)
                self.assertNotIn("validator", stage)
                if stage_id != "stage_1_standardize":
                    self.assertNotIn("ai_fallback_refs", stage)
                    self.assertNotIn("ai_fallback_used", stage)
                    self.assertNotIn("ai_fallback_artifacts", stage)

    def test_parent_context_reads_client_and_error_from_single_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp) / "runs"
            parent = run_root / "parent-run"
            fallback = parent / "fallback" / "stage_1_standardize"
            fallback.mkdir(parents=True)
            (fallback / "fallback_request.json").write_text(
                json.dumps({"error": "CSV 无交易数据"}, ensure_ascii=False),
                encoding="utf-8",
            )
            (parent / "manifest.json").write_text(
                json.dumps(
                    {
                        "skill": {"name": "bank-statement-standardization", "version": "1.2.12"},
                        "client": "斑马商业",
                        "parent_run_id": "",
                        "rerun_reason": "",
                        "stage_1_standardize": {
                            "status": "ERROR",
                            "ai_fallback_used": True,
                            "ai_fallback_artifacts": ["fallback_request.json", "mapping_patch.yaml"],
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            context = orchestrator.load_parent_run_context(str(run_root), "parent-run")

            self.assertEqual(context["parent_client"], "斑马商业")
            self.assertEqual(context["parent_status"], "error")
            self.assertEqual(context["parent_error"], "CSV 无交易数据")
            self.assertEqual(context["inherited_fallbacks"][0]["parent_fallback_dir"], str(fallback))

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

            runner = orchestrator.Runner(
                runner_args(run_root, source, client="错误包重跑目录", parent_run_id="parent-run")
            )

            self.assertEqual(runner.args.client, "斑马商业")
            self.assertEqual(runner.manifest["client"], "斑马商业")

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
                orchestrator.Runner(
                    runner_args(
                        run_root,
                        source,
                        client="其他客户",
                        client_arg_provided=True,
                        parent_run_id="parent-run",
                    )
                )

    def test_stage_1_failure_records_ai_fallback_in_single_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input"
            source.mkdir()
            (source / "流水.csv").write_text("raw", encoding="utf-8")

            runner = orchestrator.Runner(
                runner_args(root / "runs-stage-1", source, client="斑马商业")
            )
            stage_id = "stage_1_standardize"
            runner.handle_stage_failure(stage_id, runner.manifest[stage_id], RuntimeError("阶段一测试失败"))

            manifest = json.loads((Path(runner.run_dir) / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest[stage_id]["status"], "ERROR")
            self.assertTrue(manifest[stage_id]["ai_fallback_used"])
            self.assertEqual(manifest[stage_id]["ai_fallback_artifacts"], ["fallback_request.json"])
            request_path = Path(runner.fallback_dir(stage_id)) / "fallback_request.json"
            request = json.loads(request_path.read_text(encoding="utf-8"))
            self.assertEqual(request["error"], "阶段一测试失败")
            self.assertEqual(request["client"], "斑马商业")
            self.assertNotIn("script", request)
            self.assertNotIn("validator", request)

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
                runner = orchestrator.Runner(
                    runner_args(root / f"runs-{stage_id}", source, client="斑马商业")
                )
                runner.handle_stage_failure(stage_id, runner.manifest[stage_id], RuntimeError(f"{stage_id}-测试失败"))

                manifest = json.loads((Path(runner.run_dir) / "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(manifest[stage_id]["status"], "ERROR")
                self.assertNotIn("ai_fallback_refs", manifest[stage_id])
                self.assertNotIn("ai_fallback_used", manifest[stage_id])
                self.assertNotIn("ai_fallback_artifacts", manifest[stage_id])
                self.assertFalse(Path(runner.fallback_dir(stage_id)).exists())
                events = Path(runner.event_path).read_text(encoding="utf-8")
                self.assertIn('"code": "STAGE_ERROR"', events)
                self.assertNotIn('"code": "AI_FALLBACK_REQUIRED"', events)

    def test_ai_fallback_fix_can_create_child_run_and_pass_same_validator(self):
        """锁定失败请求、确定性修复、关联 run 和原 validator 的兼容链条。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_root = root / "runs"
            source = root / "斑马商业对公流水"
            source.mkdir()
            (source / "流水.csv").write_text("raw", encoding="utf-8")

            parent = orchestrator.Runner(runner_args(run_root, source, client="斑马商业"))
            parent.handle_stage_failure(
                "stage_1_standardize",
                parent.manifest["stage_1_standardize"],
                RuntimeError("字段映射失败"),
            )
            fallback_dir = Path(parent.fallback_dir("stage_1_standardize"))
            (fallback_dir / "mapping_patch.yaml").write_text("field_mapping: {}\n", encoding="utf-8")
            parent.mark_stage_ai_fallback_used(
                "stage_1_standardize",
                ["fallback_request.json", "mapping_patch.yaml"],
            )
            # AI 兜底的确定性输入提示由下一次 run 的输入快照接收。
            (source / "_file_hints.yaml").write_text("files: {}\n", encoding="utf-8")

            parent_context = orchestrator.load_parent_run_context(str(run_root), parent.run_id)
            self.assertEqual(parent_context["parent_error"], "字段映射失败")
            self.assertEqual(
                parent_context["inherited_fallbacks"][0]["parent_fallback_artifacts"],
                ["fallback_request.json", "mapping_patch.yaml"],
            )

            child = orchestrator.Runner(
                runner_args(run_root, source, client="斑马商业", parent_run_id=parent.run_id)
            )
            self.assertTrue((Path(child.input_dir) / "_file_hints.yaml").exists())
            for stage_id, spec in child.manifest.items():
                if stage_id.startswith("stage_") and stage_id != "stage_1_standardize":
                    spec["status"] = "DONE"
            child.write_manifest()

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
                        "yaml_match_status": "unmatched",
                    }
                })
                return {"standardized_files": 1}

            child.execute_stage_script = lambda stage_id: fixed_stage_1()
            child.run_manifest_stages()

            self.assertEqual(child.manifest["parent_run_id"], parent.run_id)
            self.assertEqual(child.manifest["rerun_reason"], "ai_fallback_after_stage_failure")
            self.assertEqual(child.manifest["stage_1_standardize"]["status"], "DONE")
            self.assertEqual(child.stage_validation_results["stage_1_standardize"]["standardized_rows"], 1)

    def test_downstream_stage_does_not_call_stage_validator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input"
            source.mkdir()
            (source / "流水.csv").write_text("raw", encoding="utf-8")
            runner = orchestrator.Runner(runner_args(root / "runs", source, client="斑马商业"))
            for stage_id, stage in runner.manifest.items():
                if stage_id.startswith("stage_"):
                    stage["status"] = "DONE"
            runner.manifest["stage_2_integrate"]["status"] = ""
            runner.write_manifest()
            runner.execute_stage_script = Mock(return_value={"integrated_rows": 1})
            runner.validate_stage = Mock(side_effect=AssertionError("下游不应调用 stage validator"))

            runner.run_manifest_stages()

            self.assertEqual(runner.manifest["stage_2_integrate"]["status"], "DONE")
            runner.validate_stage.assert_not_called()
            receipts = [path.name for path in Path(runner.receipt_dir).glob("*.json")]
            self.assertFalse(any("stage_2_integrate__validator" in name for name in receipts))

    def test_execute_reuses_final_delivery_validation_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = orchestrator.Runner.__new__(orchestrator.Runner)
            runner.run_dir = tmp
            runner.final_validation_result = {
                "deliverable": str(Path(tmp) / "客户_已清洗_待分析.xlsx"),
                "deliverable_rows": 1,
                "sheets": ["整合打标流水"],
            }
            runner.warning_events = []
            runner.preflight = Mock()
            runner.run_manifest_stages = Mock()
            runner.receipt = Mock()
            runner.emit = Mock()

            with patch.object(orchestrator.V, "validate_final") as validate_final:
                status = runner.execute()

            self.assertEqual(status, 0)
            validate_final.assert_not_called()
            details = runner.receipt.call_args.args[2]
            self.assertEqual(details, runner.final_validation_result)

    def test_stage_4_runs_final_delivery_validation_inside_program(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "work"
            out = root / "artifacts"
            work.mkdir()
            out.mkdir()
            (work / "客户__整合流水.csv").write_text("交易唯一编号\nTX-1\n", encoding="utf-8")
            (work / "客户__整合报告.json").write_text("{}", encoding="utf-8")
            (work / "客户__打标流水.csv").write_text("交易唯一编号\nTX-1\n", encoding="utf-8")
            (work / "客户__标签报告.json").write_text("{}", encoding="utf-8")

            runner = orchestrator.Runner.__new__(orchestrator.Runner)
            runner.args = SimpleNamespace(client="客户")
            runner.out_dir = str(out)
            runner.manifest = {"skipped_inputs": []}
            runner.final_validation_result = None
            runner.work_dir = lambda: str(work)
            final = {
                "deliverable": str(out / "客户_已清洗_待分析.xlsx"),
                "deliverable_rows": 1,
                "sheets": ["整合打标流水"],
            }

            with (
                patch.object(orchestrator.P, "finalize_deliverable", return_value=final["deliverable"]),
                patch.object(orchestrator.V, "validate_final", return_value=final) as validate_final,
            ):
                result = runner.stage_4_package()

            validate_final.assert_called_once_with(str(out), "客户", tagged_rows=1)
            self.assertEqual(result, final)
            self.assertEqual(runner.final_validation_result, final)

    def test_stage_2_checks_report_and_csv_row_count_inside_program(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            integrated = work / "客户__整合流水.csv"
            integrated.write_text("交易唯一编号\nTX-1\n", encoding="utf-8")
            report_path = work / "客户__整合报告.json"
            report_path.write_text("{}", encoding="utf-8")
            report = {"客户整合概览": {"整合交易数": 2, "整合账户数": 1}}

            runner = orchestrator.Runner.__new__(orchestrator.Runner)
            runner.args = SimpleNamespace(client="客户")
            runner.work_dir = lambda: str(work)
            runner.load_stage_1_routes = lambda: {}

            with patch.object(
                orchestrator.I,
                "integrate_context",
                return_value=(str(integrated), str(report_path), report),
            ):
                with self.assertRaisesRegex(RuntimeError, "阶段二整合交易数不一致"):
                    runner.stage_2_integrate()

    def test_stage_3_checks_input_output_row_count_inside_program(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            integrated = work / "客户__整合流水.csv"
            integrated.write_text("交易唯一编号\nTX-1\nTX-2\n", encoding="utf-8")
            tagged = work / "客户__打标流水.csv"
            tagged.write_text("交易唯一编号\nTX-1\n", encoding="utf-8")
            report_path = work / "客户__标签报告.json"
            report_path.write_text("{}", encoding="utf-8")
            report = {"标签梳理概览": {"交易总数": 2, "规则命中率": 1.0}}

            runner = orchestrator.Runner.__new__(orchestrator.Runner)
            runner.skill_dir = str(SKILL_ROOT)
            runner.work_dir = lambda: str(work)

            with patch.object(
                orchestrator.T,
                "tag",
                return_value=(str(tagged), str(report_path), report),
            ):
                with self.assertRaisesRegex(RuntimeError, "阶段三打标前后交易数不一致"):
                    runner.stage_3_tag()


if __name__ == "__main__":
    unittest.main()
