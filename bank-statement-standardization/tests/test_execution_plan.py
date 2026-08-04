import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_SCRIPT = SKILL_ROOT / "scripts" / "orchestrator.py"


class ExecutionPlanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "execution_plan_orchestrator",
            ORCHESTRATOR_SCRIPT,
        )
        cls.orchestrator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.orchestrator)

    def test_planned_run_is_claimed_only_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp) / "runs"
            run_id = "20260804T155026+0800-3ab67cad"

            first_dir, first_claimed = self.orchestrator.claim_planned_run(
                str(run_root), run_id
            )
            second_dir, second_claimed = self.orchestrator.claim_planned_run(
                str(run_root), run_id
            )

            self.assertTrue(first_claimed)
            self.assertFalse(second_claimed)
            self.assertEqual(first_dir, second_dir)

    def test_duplicate_execution_attaches_to_existing_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp) / "runs"
            run_id = "20260804T155026+0800-3ab67cad"
            run_dir, _ = self.orchestrator.claim_planned_run(str(run_root), run_id)
            expected = {
                "run_id": run_id,
                "status": "DONE",
                "next_action": "DELIVER",
            }
            (Path(run_dir) / "run_result.json").write_text(
                json.dumps(expected),
                encoding="utf-8",
            )

            actual = self.orchestrator.wait_for_run_result(run_dir, 0.1)

            self.assertEqual(actual, expected)

    def test_release_only_removes_matching_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp) / "runs"
            plan_dir = run_root / ".harness-plans"
            plan_dir.mkdir(parents=True)
            plan_key = "a" * 64
            plan_path = plan_dir / f"{plan_key}.json"
            plan_path.write_text(
                json.dumps({"run_id": "20260804T155026+0800-3ab67cad"}),
                encoding="utf-8",
            )

            self.orchestrator.release_execution_plan(
                str(run_root), plan_key, "different-run"
            )
            self.assertTrue(plan_path.exists())

            self.orchestrator.release_execution_plan(
                str(run_root), plan_key, "20260804T155026+0800-3ab67cad"
            )
            self.assertFalse(plan_path.exists())


if __name__ == "__main__":
    unittest.main()
