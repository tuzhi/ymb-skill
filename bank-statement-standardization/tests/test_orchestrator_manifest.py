import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_PATH = SKILL_ROOT / "scripts" / "orchestrator.py"
spec = importlib.util.spec_from_file_location("orchestrator", ORCHESTRATOR_PATH)
orchestrator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orchestrator)


class OrchestratorManifestTest(unittest.TestCase):
    def test_copy_stage_manifest_resets_runtime_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            template = tmp_path / "manifest.json"
            runtime = tmp_path / "run" / "manifest.json"
            runtime.parent.mkdir()
            template.write_text(
                json.dumps(
                    {
                        "stage_1_standardize": {
                            "name": "stage 1",
                            "script": "scripts/standardize.py",
                            "ai_fallback_refs": [],
                            "validator": "scripts/validate_stage.py::validate_standardize",
                            "ai_fallback_used": True,
                            "ai_fallback_dir": "C:/Users/28307/WorkBuddy/runs/old-run/fallback/stage_1_standardize",
                            "ai_fallback_artifacts": ["old_patch.py"],
                            "started_at": "2026-06-08T18:38:26.495086+08:00",
                            "duration_seconds": 45.504,
                            "status": "DONE",
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            runner = orchestrator.Runner.__new__(orchestrator.Runner)
            runner.template_manifest_path = str(template)
            runner.stage_manifest_path = str(runtime)

            runner.copy_stage_manifest()

            data = json.loads(runtime.read_text(encoding="utf-8"))
            stage = data["stage_1_standardize"]
            self.assertFalse(stage["ai_fallback_used"])
            self.assertEqual(stage["ai_fallback_dir"], "")
            self.assertEqual(stage["ai_fallback_artifacts"], [])
            self.assertEqual(stage["started_at"], "")
            self.assertIsNone(stage["duration_seconds"])
            self.assertEqual(stage["status"], "")

    def test_load_parent_run_context_collects_fallbacks(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp) / "runs"
            parent = run_root / "parent-run"
            fallback = parent / "fallback" / "stage_1_standardize"
            fallback.mkdir(parents=True)
            (fallback / "fallback_request.json").write_text("{}", encoding="utf-8")
            (parent / "manifest.json").write_text(
                json.dumps(
                    {
                        "stage_1_standardize": {
                            "name": "stage 1",
                            "script": "scripts/standardize.py",
                            "validator": "scripts/validate_stage.py::validate_standardize",
                            "ai_fallback_used": True,
                            "ai_fallback_dir": str(fallback),
                            "ai_fallback_artifacts": ["fallback_request.json", "patch_header_nan_fix.py"],
                            "status": "ERROR",
                        },
                        "stage_2_integrate": {
                            "ai_fallback_used": False,
                            "ai_fallback_dir": "",
                            "ai_fallback_artifacts": [],
                            "status": "",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (parent / "run_manifest.json").write_text(
                json.dumps({"run_id": "parent-run", "status": "error", "error": "CSV 无交易数据"}, ensure_ascii=False),
                encoding="utf-8",
            )

            context = orchestrator.load_parent_run_context(str(run_root), "parent-run")

            self.assertEqual(context["parent_run_id"], "parent-run")
            self.assertEqual(context["parent_status"], "error")
            self.assertEqual(context["parent_error"], "CSV 无交易数据")
            self.assertEqual(len(context["inherited_fallbacks"]), 1)
            inherited = context["inherited_fallbacks"][0]
            self.assertEqual(inherited["stage"], "stage_1_standardize")
            self.assertEqual(inherited["parent_status"], "ERROR")
            self.assertEqual(inherited["parent_fallback_artifacts"], ["fallback_request.json", "patch_header_nan_fix.py"])

    def test_load_parent_run_context_rejects_missing_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "parent run 不存在"):
                orchestrator.load_parent_run_context(str(Path(tmp) / "runs"), "missing-run")

    def test_collect_skill_source_snapshot_hashes_source_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "skill"
            (skill / "scripts").mkdir(parents=True)
            (skill / "dist").mkdir()
            (skill / "testdata").mkdir()
            (skill / "SKILL.md").write_text("# skill\n", encoding="utf-8")
            (skill / "manifest.json").write_text("{}\n", encoding="utf-8")
            (skill / "scripts" / "standardize.py").write_text("print('ok')\n", encoding="utf-8")
            (skill / "dist" / "bundle.zip").write_text("ignore\n", encoding="utf-8")
            (skill / "testdata" / "raw.csv").write_text("ignore\n", encoding="utf-8")

            snapshot = orchestrator.collect_skill_source_snapshot(str(skill))

            self.assertIn("git_commit", snapshot)
            self.assertIn("dirty", snapshot)
            self.assertIn("modified_files", snapshot)
            self.assertIn("file_sha256", snapshot)
            self.assertIn("SKILL.md", snapshot["file_sha256"])
            self.assertIn("manifest.json", snapshot["file_sha256"])
            self.assertIn("scripts/standardize.py", snapshot["file_sha256"])
            self.assertNotIn("dist/bundle.zip", snapshot["file_sha256"])
            self.assertNotIn("testdata/raw.csv", snapshot["file_sha256"])


if __name__ == "__main__":
    unittest.main()
