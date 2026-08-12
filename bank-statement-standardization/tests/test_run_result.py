import tempfile
import unittest
from pathlib import Path
import sys


SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from runtime import failure_policy as F
from runtime import result_store as STORE
from runtime.models import run_result as R


class RunResultTests(unittest.TestCase):
    def test_next_actions_have_one_enum_source(self):
        self.assertEqual(set(R.NEXT_ACTIONS), set(R.NextAction))
        self.assertEqual(R.NextAction.NEED_REPAIR.value, "NEED_REPAIR")
        self.assertEqual(R.NextAction.REQUEST_USER.value, "REQUEST_USER")

    def test_execution_plan_is_a_supported_entry_action(self):
        result = R.RunResult("run-1", "READY", R.NextAction.EXECUTE_PIPELINE)
        self.assertEqual(result.next_action, "EXECUTE_PIPELINE")

    def test_password_exception_type_is_classified_even_without_message(self):
        class PDFPasswordIncorrect(Exception):
            pass

        route = F.classify_failure("stage_1_standardize", PDFPasswordIncorrect())
        self.assertEqual(route.reason_code, "INPUT_PASSWORD_REQUIRED")
        self.assertEqual(route.next_action, "REQUEST_USER")

    def test_password_attempts_do_not_become_ai_fallback(self):
        required = F.classify_failure(
            "stage_1_standardize",
            RuntimeError("PDFPasswordIncorrect"),
            password_attempt=0,
        )
        invalid = F.classify_failure(
            "stage_1_standardize",
            RuntimeError("PDFPasswordIncorrect"),
            password_attempt=1,
        )
        exhausted = F.classify_failure(
            "stage_1_standardize",
            RuntimeError("PDFPasswordIncorrect"),
            password_attempt=3,
        )

        self.assertEqual((required.reason_code, required.next_action), (
            R.ReasonCode.INPUT_PASSWORD_REQUIRED, R.NextAction.REQUEST_USER,
        ))
        self.assertEqual((invalid.reason_code, invalid.next_action), (
            R.ReasonCode.INPUT_PASSWORD_INVALID, R.NextAction.REQUEST_USER,
        ))
        self.assertEqual(exhausted.next_action, R.NextAction.REPORT_ERROR)

    def test_structured_route_reason_wins_over_generic_stage_error(self):
        route = F.classify_failure(
            "stage_1_standardize",
            RuntimeError("阶段一存在失败文件"),
            [{"status": "ERROR", "reason_code": R.ReasonCode.ROUTE_AMBIGUOUS}],
        )
        self.assertEqual(route.reason_code, R.ReasonCode.ROUTE_AMBIGUOUS)
        self.assertEqual(route.next_action, R.NextAction.NEED_REPAIR)

    def test_zero_transaction_statement_requests_user_without_ai_repair(self):
        route = F.classify_failure(
            "stage_1_standardize",
            RuntimeError("阶段一存在失败文件"),
            [{
                "status": "BLOCKED",
                "reason_code": R.ReasonCode.ZERO_TRANSACTION_STATEMENT,
            }],
        )
        self.assertEqual(route.reason_code, R.ReasonCode.ZERO_TRANSACTION_STATEMENT)
        self.assertEqual(route.next_action, R.NextAction.REQUEST_USER)

    def test_downstream_failure_never_uses_ai(self):
        route = F.classify_failure("stage_3_tag", RuntimeError("tag failed"))
        self.assertEqual(route.reason_code, R.ReasonCode.DOWNSTREAM_STAGE_FAILURE)
        self.assertEqual(route.next_action, R.NextAction.REPORT_ERROR)

    def test_pipeline_result_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / STORE.PIPELINE_RESULT_FILENAME
            STORE.atomic_write_json(path, {
                "schema_version": 1,
                "run_id": "run-1",
                "status": "DONE",
                "next_action": "DELIVER",
                "reason_code": "",
                "message": "done",
                "deliverables": ["a.xlsx"],
            })
            stored = R.RunResult.from_pipeline_result(STORE.load_pipeline_result(tmp)).to_dict()
            self.assertEqual(stored["next_action"], "DELIVER")
            self.assertEqual(stored["artifact_refs"], ["a.xlsx"])
            self.assertNotIn("action", stored)
            self.assertNotIn("summary", stored)

    def test_run_result_serializes_action_only_when_present(self):
        result = R.RunResult(
            "run-1",
            "ERROR",
            R.NextAction.NEED_REPAIR,
            action={"handler": "repair_coordinator", "operation": "submit"},
        )
        self.assertEqual(result.to_dict()["action"]["operation"], "submit")

    def test_run_result_serializes_delivery_summary_only_when_present(self):
        result = R.RunResult(
            "run-1",
            "DONE",
            R.NextAction.DELIVER,
            summary={
                "input_file_count": 17,
                "processed_file_count": 17,
                "qc_status": "PASS_WITH_WARNINGS",
                "warning_count": 1,
                "warning_summary": ["覆盖不足两年"],
            },
        )
        self.assertEqual(result.to_dict()["summary"]["input_file_count"], 17)
