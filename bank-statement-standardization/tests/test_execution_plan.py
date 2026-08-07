import importlib.util
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_SCRIPT = SKILL_ROOT / "scripts" / "orchestrator.py"
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from runtime import execution_plan  # noqa: E402
from runtime.models import PipelineExecutionResult  # noqa: E402
from runtime.runner import protocol_exit_status, resolve_run_root  # noqa: E402


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

            first_dir, first_claimed = execution_plan.claim_planned_run(
                str(run_root), run_id
            )
            second_dir, second_claimed = execution_plan.claim_planned_run(
                str(run_root), run_id
            )

            self.assertTrue(first_claimed)
            self.assertFalse(second_claimed)
            self.assertEqual(first_dir, second_dir)

    def test_default_run_root_uses_working_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            expected = Path(os.path.abspath(os.path.join(tmp, "runs")))

            actual = Path(resolve_run_root(None, cwd=tmp))

            self.assertEqual(actual, expected)
            self.assertNotEqual(actual.parent, SKILL_ROOT)

    def test_same_input_reuses_active_execution_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input"
            input_path.mkdir()
            run_root = Path(tmp) / "runs"

            first = execution_plan.load_or_create_execution_plan(
                str(input_path), str(run_root)
            )
            second = execution_plan.load_or_create_execution_plan(
                str(input_path), str(run_root)
            )

            self.assertEqual(first, second)
            self.assertFalse((run_root / first[0]).exists())

    def test_duplicate_execution_attaches_to_existing_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp) / "runs"
            run_id = "20260804T155026+0800-3ab67cad"
            run_dir, _ = execution_plan.claim_planned_run(str(run_root), run_id)
            expected = {
                "run_id": run_id,
                "status": "DONE",
                "next_action": "DELIVER",
            }
            (Path(run_dir) / "pipeline_result.json").write_text(
                json.dumps({"run_result": expected}),
                encoding="utf-8",
            )

            actual = execution_plan.wait_for_run_result(run_dir, 0.1)

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

            execution_plan.release_execution_plan(
                str(run_root), plan_key, "different-run"
            )
            self.assertTrue(plan_path.exists())

            execution_plan.release_execution_plan(
                str(run_root), plan_key, "20260804T155026+0800-3ab67cad"
            )
            self.assertFalse(plan_path.exists())

    def test_invalid_input_returns_request_user_without_creating_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp) / "runs"
            output = StringIO()
            with redirect_stdout(output):
                status = self.orchestrator.main([
                    "run",
                    "--folder", str(Path(tmp) / "missing"),
                    "--run-root", str(run_root),
                ])

            result = json.loads(output.getvalue())
            self.assertEqual(status, 0)
            self.assertEqual(result["next_action"], "REQUEST_USER")
            self.assertEqual(result["reason_code"], "INPUT_SOURCE_INVALID")
            self.assertFalse(run_root.exists())

    def test_valid_business_run_result_uses_zero_process_status(self):
        result = {
            "contract_version": 1,
            "next_action": "NEED_REPAIR",
        }

        self.assertEqual(protocol_exit_status(result), 0)
        self.assertEqual(protocol_exit_status({}, 3), 3)

    def test_main_calls_runner_directly_and_releases_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input"
            input_path.mkdir()
            run_root = Path(tmp) / "runs"
            final_result = {
                "run_id": "",
                "status": "ERROR",
                "next_action": "NEED_REPAIR",
                "reason_code": "ROUTE_UNMATCHED",
                "artifact_refs": [],
                "context_ref": "stage_1_results.json",
                "message": "需要诊断",
                "contract_version": 1,
            }
            class FakeRunner:
                def __init__(self, args):
                    self.run_id = args.run_id
                    self.pipeline_result_path = str(
                        run_root / self.run_id / "pipeline_result.json"
                    )

                def execute(self):
                    result = dict(final_result, run_id=self.run_id)
                    return PipelineExecutionResult(
                        exit_code=1,
                        run_id=self.run_id,
                        client_name="input",
                        parent_run_id="",
                        status="ERROR",
                        file_results={"files": {}},
                        stages={},
                        stage_summaries={},
                        qc={},
                        artifacts=(),
                        run_result=result,
                        error="需要诊断",
                    )

            output = StringIO()
            public_result = {
                "contract_version": 1,
                "run_id": "run-1",
                "attempt": 1,
                "status": "NEED_REPAIR",
                "role": "repair",
                "request": {},
            }
            with (
                patch.object(self.orchestrator._runner_runtime, "Runner", FakeRunner),
                patch.object(
                    self.orchestrator._runner_runtime,
                    "public_result",
                    return_value=public_result,
                ),
                redirect_stdout(output),
            ):
                status = self.orchestrator.main([
                    "run",
                    "--folder", str(input_path),
                    "--run-root", str(run_root),
                ])

            result = json.loads(output.getvalue())
            self.assertEqual(status, 0)
            self.assertEqual(result["status"], "NEED_REPAIR")
            self.assertFalse(any((run_root / ".harness-plans").glob("*.json")))


if __name__ == "__main__":
    unittest.main()
