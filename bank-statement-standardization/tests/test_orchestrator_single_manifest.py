import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


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

    def test_every_stage_can_record_ai_fallback_in_single_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input"
            source.mkdir()
            (source / "流水.csv").write_text("raw", encoding="utf-8")

            for stage_id in (
                "stage_1_standardize",
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
                self.assertTrue(manifest[stage_id]["ai_fallback_used"])
                self.assertEqual(manifest[stage_id]["ai_fallback_artifacts"], ["fallback_request.json"])
                request_path = Path(runner.fallback_dir(stage_id)) / "fallback_request.json"
                request = json.loads(request_path.read_text(encoding="utf-8"))
                self.assertEqual(request["error"], f"{stage_id}-测试失败")
                self.assertEqual(request["client"], "斑马商业")


if __name__ == "__main__":
    unittest.main()
