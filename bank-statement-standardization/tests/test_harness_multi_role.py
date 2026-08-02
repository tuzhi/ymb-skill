import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys


SKILL_ROOT = Path(__file__).resolve().parents[1]
CORE_PACKAGE = SKILL_ROOT.parent / "ymb-standardization-core" / "src"
for path in (SKILL_ROOT, CORE_PACKAGE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from harness.contracts import NEED_AUDIT, NEED_FALLBACK, REQUEST_USER
from harness.coordinator import FallbackCoordinator
from harness.policy_gate import _merge_rule, evaluate_routing_draft
from ymb_standardization_core.readers.routing.rule_loader import fingerprint_md5, routing_rules_path


class HarnessMultiRoleTests(unittest.TestCase):
    def _run(self, root: Path) -> Path:
        run = root / "run-1"
        fallback = run / "fallback" / "stage_1_standardize"
        fallback.mkdir(parents=True)
        evidence = {
            "contract_version": 1,
            "run_id": run.name,
            "reason_code": "ROUTE_UNMATCHED",
            "failed_files": [{"file_id": "md5:file-1"}],
        }
        (fallback / "evidence_bundle.json").write_text(json.dumps(evidence), encoding="utf-8")
        request = {
            "contract_version": "bank-statement-standardization.fallback-request/v2",
            "run_id": run.name,
            "stage_id": "stage_1_standardize",
            "reason_code": "ROUTE_UNMATCHED",
            "attempt": 1,
            "max_attempts": 2,
            "next_action": "AI_FALLBACK",
            "evidence_ref": "fallback/stage_1_standardize/evidence_bundle.json",
            "files": [{"file_id": "md5:file-1", "name": "流水.pdf"}],
        }
        (fallback / "fallback_request.json").write_text(json.dumps(request), encoding="utf-8")
        (run / "token_usage.json").write_text(json.dumps({
            "contract_version": 1,
            "run_id": run.name,
            "measurement_scope": "fallback_and_audit_sessions_only",
            "ai_session_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_input_tokens": 0,
            "sessions": [],
        }), encoding="utf-8")
        return run

    @staticmethod
    def _fallback_payload(task):
        return {
            "contract_version": 1,
            "run_id": task["run_id"],
            "stage_id": "stage_1_standardize",
            "role": "fallback",
            "status": "REPAIR_PROPOSED",
            "classification": "ROUTE_UNMATCHED",
            "affected_file_ids": ["md5:file-1"],
            "repair_type": "ROUTING_RULE_DRAFT",
            "repair_payload": {
                "operation": "append",
                "rule": {
                    "file_type": "pdf",
                    "bank": "测试银行",
                    "account_type": "个人",
                    "fingerprint": {"identity": {"any": ["测试银行"]}},
                    "reader_id": "pdfplumber_table",
                },
            },
        }

    def test_fallback_and_audit_require_distinct_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run(Path(tmp))
            coordinator = FallbackCoordinator(run)
            first = coordinator.next()
            self.assertEqual(first["status"], NEED_FALLBACK)
            self.assertTrue(first["task"]["role_prompt_ref"].endswith("/roles/fallback.md"))
            self.assertNotIn("skill", first["task"])
            coordinator.submit(
                "fallback",
                session_id="fallback-session",
                payload=self._fallback_payload(first["task"]),
                usage={"input_tokens": 100, "output_tokens": 20, "cached_input_tokens": 10},
            )

            def accepted_gate(**kwargs):
                attempt = Path(kwargs["attempt_root"])
                snapshot = attempt / "repair" / "routing_rules.yaml"
                snapshot.parent.mkdir(parents=True)
                snapshot.write_text("[]\n", encoding="utf-8")
                gate = {
                    "status": "ACCEPTED",
                    "snapshot_ref": snapshot.resolve().relative_to(run.resolve()).as_posix(),
                    "snapshot_sha256": "sha256-test",
                }
                (attempt / "policy_gate.json").write_text(json.dumps(gate), encoding="utf-8")
                return gate

            with patch("harness.coordinator.evaluate_routing_draft", side_effect=accepted_gate):
                second = coordinator.next()
            self.assertEqual(second["status"], NEED_AUDIT)
            self.assertTrue(second["task"]["role_prompt_ref"].endswith("/roles/audit.md"))
            self.assertIn(
                "fallback/stage_1_standardize/evidence_bundle.json",
                second["task"]["input_refs"],
            )
            self.assertNotIn(
                "fallback/stage_1_standardize/attempt-01/repair/routing_rules.yaml",
                second["task"]["input_refs"],
            )
            audit = {
                "contract_version": 1,
                "run_id": run.name,
                "stage_id": "stage_1_standardize",
                "role": "audit",
                "status": "ACCEPTED",
                "affected_file_ids": ["md5:file-1"],
                "reason": "证据充分",
            }
            with self.assertRaisesRegex(RuntimeError, "不同的新会话"):
                coordinator.submit(
                    "audit",
                    session_id="fallback-session",
                    payload=audit,
                )
            coordinator.submit(
                "audit",
                session_id="audit-session",
                payload=audit,
                usage={"input_tokens": 40, "output_tokens": 10},
            )
            final = coordinator.next()
            self.assertEqual(final["status"], "CHILD_RUN_READY")
            usage = json.loads((run / "token_usage.json").read_text(encoding="utf-8"))
            self.assertEqual(usage["ai_session_count"], 2)
            self.assertEqual(usage["input_tokens"], 140)

    def test_request_user_does_not_create_audit_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run(Path(tmp))
            coordinator = FallbackCoordinator(run)
            first = coordinator.next()
            payload = {
                "contract_version": 1,
                "run_id": run.name,
                "stage_id": "stage_1_standardize",
                "role": "fallback",
                "status": "REQUEST_USER",
                "classification": "UNKNOWN",
                "affected_file_ids": [],
                "user_request": "请提供原始文件",
            }
            coordinator.submit("fallback", session_id="fallback-session", payload=payload)
            result = coordinator.next()
            self.assertEqual(result["status"], REQUEST_USER)
            self.assertFalse((coordinator.attempt_root / "audit_task.json").exists())

    def test_rule_merge_rejects_duplicate_append(self):
        fingerprint = {"identity": {"any": ["A"]}}
        rule_id = fingerprint_md5(fingerprint)
        production = f"- id: {rule_id}\n  file_type: pdf\n  bank: A\n  account_type: 个人\n  fingerprint:\n    identity:\n      any: [A]\n  reader_id: pdfplumber_table\n"
        payload = {
            "operation": "append",
            "rule": {
                "file_type": "pdf",
                "bank": "A",
                "account_type": "个人",
                "fingerprint": fingerprint,
                "reader_id": "pdfplumber_table",
            },
        }
        with self.assertRaisesRegex(ValueError, "不允许覆盖"):
            _merge_rule(production, payload)

    def test_policy_gate_tests_run_scoped_snapshot_without_publishing(self):
        production = Path(routing_rules_path()).resolve()
        production_before = production.read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp).resolve() / "run-1"
            input_dir = run / "input"
            input_dir.mkdir(parents=True)
            source = input_dir / "流水.pdf"
            source.write_bytes(b"%PDF-1.4\n")
            digest = hashlib.md5(source.read_bytes()).hexdigest()
            file_id = f"md5:{digest}"
            (run / "stage_1_results.json").write_text(json.dumps({
                "files": {
                    file_id: {
                        "name": source.name,
                        "status": "ERROR",
                        "reason_code": "ROUTE_UNMATCHED",
                    }
                }
            }), encoding="utf-8")
            request = {"files": [{"file_id": file_id, "name": source.name}]}
            fingerprint = {"identity": {"any": ["测试银行"]}}
            rule_id = fingerprint_md5(fingerprint)
            fallback = {
                "run_id": run.name,
                "affected_file_ids": [file_id],
                "repair_type": "ROUTING_RULE_DRAFT",
                "repair_payload": {
                    "operation": "append",
                    "rule": {
                        "file_type": "pdf",
                        "bank": "测试银行",
                        "account_type": "个人",
                        "fingerprint": fingerprint,
                        "reader_id": "pdfplumber_table",
                    },
                },
            }
            routed = SimpleNamespace(route_info={
                "decision": "matched",
                "fingerprint_id": rule_id,
                "reader_id": "pdfplumber_table",
            })
            with patch("services.yaml_rule_service.read_rows", return_value=routed):
                gate = evaluate_routing_draft(
                    run_dir=run,
                    attempt_root=run / "fallback" / "stage_1_standardize" / "attempt-01",
                    fallback_request=request,
                    fallback_result=fallback,
                )

            self.assertEqual(gate["status"], "ACCEPTED", gate)
            self.assertEqual(gate["routing_test"]["tested_file_count"], 1)
            self.assertEqual(len(gate["routing_test"]["affected_files"]), 1)
            snapshot = run / gate["snapshot_ref"]
            self.assertTrue(snapshot.is_file())
            self.assertIn(rule_id, snapshot.read_text(encoding="utf-8"))
        self.assertEqual(production.read_bytes(), production_before)


if __name__ == "__main__":
    unittest.main()
