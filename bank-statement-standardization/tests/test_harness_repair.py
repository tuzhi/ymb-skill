import hashlib
import csv
import json
import tempfile
import unittest
from pathlib import Path
import sys


SKILL_ROOT = Path(__file__).resolve().parents[1]
CORE_PACKAGE = SKILL_ROOT.parent / "ymb-standardization-core" / "src"
for path in (SKILL_ROOT, CORE_PACKAGE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from harness.contracts import CHILD_RUN_READY, MAINTAINER_REQUIRED, NEED_REPAIR, REQUEST_USER
from harness.coordinator import RepairCoordinator
from harness.protocols import normalize_protocol, protocol_path


class HarnessRepairTests(unittest.TestCase):
    def _run(self, root: Path, *, attempt: int = 0) -> Path:
        run = root / f"run-{attempt + 1}"
        source = run / "input" / "流水.pdf"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"%PDF-1.4\n")
        file_id = "md5:" + hashlib.md5(source.read_bytes()).hexdigest()
        (run / "manifest.json").write_text(json.dumps({
            "ai_repair_attempt": attempt,
            "stage_1_standardize": {"status": "ERROR"},
        }), encoding="utf-8")
        (run / "stage_1_results.json").write_text(json.dumps({
            "files": {
                file_id: {
                    "name": source.name,
                    "relative_path": source.name,
                    "status": "ERROR",
                    "reason_code": "ROUTE_UNMATCHED",
                    "message": "未唯一命中 YAML",
                }
            }
        }), encoding="utf-8")
        (run / "run_result.json").write_text(json.dumps({
            "contract_version": 1,
            "run_id": run.name,
            "status": "ERROR",
            "next_action": "NEED_REPAIR",
            "reason_code": "ROUTE_UNMATCHED",
            "artifact_refs": ["stage_1_results.json"],
            "context_ref": "stage_1_results.json",
        }), encoding="utf-8")
        return run

    @staticmethod
    def _write_repair_csv(path: Path, source_name="流水.pdf") -> tuple[int, str]:
        path.parent.mkdir(parents=True, exist_ok=True)
        columns = [
            "交易唯一编号", "交易时间", "本方账户", "收入金额", "支出金额",
            "交易金额", "账户余额", "来源文件名", "来源行号",
        ]
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerow({
                "交易唯一编号": "TX-1",
                "交易时间": "2026-01-01",
                "本方账户": "62170001",
                "收入金额": "1",
                "支出金额": "",
                "交易金额": "1",
                "账户余额": "10",
                "来源文件名": source_name,
                "来源行号": "1",
            })
        return 1, hashlib.sha256(path.read_bytes()).hexdigest()

    @classmethod
    def _payload(cls, request, status="REPAIRED", message=""):
        outputs = []
        if status == "REPAIRED":
            failed = request["failed_files"][0]
            relative = "standardized/流水__standardized.csv"
            rows, checksum = cls._write_repair_csv(Path(request["repair_dir"]) / relative)
            outputs = [{
                "file_id": failed["file_id"],
                "source_md5": failed["source_md5"],
                "standardized_csv": relative,
                "row_count": rows,
                "sha256": checksum,
            }]
        return {
            "contract_version": 1,
            "run_id": request["run_id"],
            "attempt": request["attempt"],
            "stage_id": "stage_1_standardize",
            "role": "repair",
            "status": status,
            "outputs": outputs,
            "message": message,
        }

    def test_request_points_to_stage_results_raw_file_and_repair_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run(Path(tmp))
            result = RepairCoordinator(run).decision()
            request = result["request"]

            self.assertEqual(result["status"], NEED_REPAIR)
            self.assertEqual(request["input_refs"], ["stage_1_results.json", "input/流水.pdf"])
            self.assertTrue(request["role_prompt_ref"].endswith("/roles/repair.md"))
            self.assertEqual(request["output_contract_ref"], protocol_path("repair-result").as_posix())
            self.assertEqual(request["repair_dir"], (run / "repair" / "attempt-01").resolve().as_posix())
            self.assertEqual(request["failed_files"][0]["file_id"], request["failed_files"][0]["source_md5"])
            self.assertTrue((run / "repair" / "attempt-01" / "repair_request.json").is_file())
            self.assertNotIn("routing_rules_ref", request)
            self.assertFalse((run / "fallback").exists())
            self.assertFalse((run / "evidence_bundle.json").exists())

    def test_failed_file_must_have_resolvable_relative_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run(Path(tmp))
            data = json.loads((run / "stage_1_results.json").read_text(encoding="utf-8"))
            next(iter(data["files"].values())).pop("relative_path")
            (run / "stage_1_results.json").write_text(json.dumps(data), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "relative_path"):
                RepairCoordinator(run).decision()

    def test_repaired_result_uses_fixed_attempt_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run(Path(tmp))
            coordinator = RepairCoordinator(run)
            request = coordinator.decision()["request"]
            repair_dir = Path(request["repair_dir"])

            outcome = coordinator.submit(
                request_id=request["request_id"],
                session_id="repair-session-1",
                payload=self._payload(request),
                usage={"input_tokens": 100, "output_tokens": 20, "cached_input_tokens": 80},
            )

            self.assertEqual(outcome["status"], CHILD_RUN_READY)
            self.assertEqual(outcome["repair_result_ref"], "repair/attempt-01/repair_result.json")
            self.assertTrue(outcome["repair_result_sha256"])
            receipt = json.loads((repair_dir / "session-receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["session_id"], "repair-session-1")
            self.assertEqual(receipt["measurement_status"], "available")
            self.assertEqual(receipt["usage"]["cached_input_tokens"], 80)
            usage = json.loads((run / "token_usage.json").read_text(encoding="utf-8"))
            self.assertEqual(usage["measurement_scope"], "repair_sessions_only")
            self.assertEqual(usage["measurement_status"], "available")
            self.assertEqual(usage["ai_session_count"], 1)
            self.assertEqual(usage["input_tokens"], 100)
            self.assertEqual(usage["output_tokens"], 20)
            self.assertEqual(usage["cached_input_tokens"], 80)

    def test_partial_usage_is_not_treated_as_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run(Path(tmp))
            coordinator = RepairCoordinator(run)
            request = coordinator.decision()["request"]

            coordinator.submit(
                request_id=request["request_id"],
                session_id="repair-session-with-partial-usage",
                payload=self._payload(request),
                usage={"input_tokens": 100, "output_tokens": 20},
            )

            usage = json.loads((run / "token_usage.json").read_text(encoding="utf-8"))
            self.assertEqual(usage["measurement_status"], "partial")
            self.assertEqual(usage["input_tokens"], 100)
            self.assertEqual(usage["output_tokens"], 20)
            self.assertIsNone(usage["cached_input_tokens"])

    def test_missing_usage_is_recorded_as_unavailable_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run(Path(tmp))
            coordinator = RepairCoordinator(run)
            request = coordinator.decision()["request"]

            coordinator.submit(
                request_id=request["request_id"],
                session_id="repair-session-without-usage",
                payload=self._payload(request),
            )

            receipt = json.loads(
                (Path(request["repair_dir"]) / "session-receipt.json").read_text(encoding="utf-8")
            )
            self.assertEqual(receipt["measurement_status"], "unavailable")
            self.assertTrue(all(value is None for value in receipt["usage"].values()))
            usage = json.loads((run / "token_usage.json").read_text(encoding="utf-8"))
            self.assertEqual(usage["measurement_status"], "unavailable")
            self.assertIsNone(usage["input_tokens"])
            self.assertIsNone(usage["output_tokens"])
            self.assertIsNone(usage["cached_input_tokens"])

    def test_repaired_result_requires_all_failed_csv_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run(Path(tmp))
            coordinator = RepairCoordinator(run)
            request = coordinator.decision()["request"]
            payload = self._payload(request)
            payload["outputs"] = []
            with self.assertRaisesRegex(ValueError, "标准化 CSV"):
                coordinator.submit(
                    request_id=request["request_id"],
                    session_id="repair-session-1",
                    payload=payload,
                )

    def test_request_user_and_maintainer_stop_without_child_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            for status, expected in (("REQUEST_USER", REQUEST_USER), ("MAINTAINER_REQUIRED", MAINTAINER_REQUIRED)):
                run = self._run(Path(tmp) / status)
                coordinator = RepairCoordinator(run)
                request = coordinator.decision()["request"]
                outcome = coordinator.submit(
                    request_id=request["request_id"],
                    session_id=f"session-{status}",
                    payload=self._payload(request, status=status, message="需要处理"),
                )
                self.assertEqual(outcome["status"], expected)
                self.assertEqual(outcome["message"], "需要处理")

    def test_request_id_ignores_absolute_run_path(self):
        with tempfile.TemporaryDirectory() as first_tmp, tempfile.TemporaryDirectory() as second_tmp:
            first = RepairCoordinator(self._run(Path(first_tmp))).decision()["request"]
            second = RepairCoordinator(self._run(Path(second_tmp))).decision()["request"]
            self.assertNotEqual(first["run_dir"], second["run_dir"])
            self.assertEqual(first["request_id"], second["request_id"])

    def test_attempt_limit_is_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run(Path(tmp), attempt=2)
            with self.assertRaisesRegex(RuntimeError, "次数已达上限"):
                RepairCoordinator(run)

    def test_protocol_rejects_unknown_fields(self):
        with self.assertRaisesRegex(ValueError, "模板外字段"):
            normalize_protocol("repair-result", {"unexpected": True})


if __name__ == "__main__":
    unittest.main()
