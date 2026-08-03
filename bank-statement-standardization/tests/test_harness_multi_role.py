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

from harness.contracts import MAINTAINER_REQUIRED, NEED_AUDIT, NEED_FALLBACK, REQUEST_USER
from harness.coordinator import FallbackCoordinator
from harness.policy_gate import _merge_rule, evaluate_routing_draft
from harness.protocols import load_protocol, normalize_protocol, protocol_path, render_protocol
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
            self.assertEqual(
                first["task"]["output_contract_ref"],
                protocol_path("fallback-result").as_posix(),
            )
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
            self.assertEqual(
                second["task"]["output_contract_ref"],
                protocol_path("audit-result").as_posix(),
            )
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

    def test_protocol_templates_are_copy_safe_and_reject_unknown_fields(self):
        first = load_protocol("audit-result")
        first["status"] = "CHANGED"
        self.assertEqual(load_protocol("audit-result")["status"], "")

        rendered = render_protocol("child-run-request", {
            "parent_run_id": "parent-1",
            "authorized_by": {"audit": "audit.json"},
        })
        self.assertEqual(rendered["parent_run_id"], "parent-1")
        self.assertEqual(rendered["authorized_by"]["audit"], "audit.json")
        self.assertEqual(rendered["authorized_by"]["policy_gate"], "")

        with self.assertRaisesRegex(ValueError, "模板外字段"):
            normalize_protocol("audit-result", {"unexpected": True})
        with self.assertRaisesRegex(ValueError, "模板外字段"):
            normalize_protocol("fallback-result", {
                "repair_payload": {"rule": {"source_patch": "forbidden"}},
            })

    def test_submitted_role_result_is_normalized_to_its_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run(Path(tmp))
            coordinator = FallbackCoordinator(run)
            first = coordinator.next()
            coordinator.submit(
                "fallback",
                session_id="fallback-session",
                payload={
                    "contract_version": 1,
                    "run_id": run.name,
                    "stage_id": "stage_1_standardize",
                    "role": "fallback",
                    "status": "REQUEST_USER",
                    "classification": "UNKNOWN",
                    "affected_file_ids": [],
                    "user_request": "请提供原始文件",
                },
            )

            output = json.loads(
                (run / first["task"]["output_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(set(output), set(load_protocol("fallback-result")))
            self.assertEqual(output["user_request"], "请提供原始文件")
            self.assertEqual(output["limitations"], [])

    def test_submit_recovers_output_written_before_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run(Path(tmp))
            coordinator = FallbackCoordinator(run)
            first = coordinator.next()
            payload = self._fallback_payload(first["task"])
            output_path = run / first["task"]["output_path"]
            output_path.write_text(
                json.dumps(normalize_protocol("fallback-result", payload)),
                encoding="utf-8",
            )

            receipt = coordinator.submit(
                "fallback",
                session_id="fallback-session",
                payload=payload,
                usage={"input_tokens": 10, "output_tokens": 2},
            )
            replayed = coordinator.submit(
                "fallback",
                session_id="fallback-session",
                payload=payload,
                usage={"input_tokens": 999, "output_tokens": 999},
            )

            self.assertEqual(replayed, receipt)
            usage = json.loads((run / "token_usage.json").read_text(encoding="utf-8"))
            self.assertEqual(usage["ai_session_count"], 1)
            self.assertEqual(usage["input_tokens"], 10)
            self.assertEqual(usage["output_tokens"], 2)

    def test_submit_recovers_rejection_written_before_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run(Path(tmp))
            coordinator = FallbackCoordinator(run)
            first = coordinator.next()
            payload = self._fallback_payload(first["task"])
            payload["repair_payload"]["rule"]["fingerprint"] = {
                "title_pattern": "不受支持字段",
            }

            with patch.object(
                coordinator,
                "_write_receipt",
                side_effect=RuntimeError("模拟 receipt 写入前中断"),
            ):
                with self.assertRaisesRegex(RuntimeError, "模拟 receipt"):
                    coordinator.submit(
                        "fallback",
                        session_id="fallback-session",
                        payload=payload,
                    )

            receipt = coordinator.submit(
                "fallback",
                session_id="fallback-session",
                payload=payload,
            )
            self.assertEqual(receipt["status"], "REJECTED")
            usage = json.loads((run / "token_usage.json").read_text(encoding="utf-8"))
            self.assertEqual(usage["ai_session_count"], 1)

    def test_legacy_accepted_receipt_can_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run(Path(tmp))
            coordinator = FallbackCoordinator(run)
            first = coordinator.next()
            coordinator.submit(
                "fallback",
                session_id="fallback-session",
                payload=self._fallback_payload(first["task"]),
            )
            receipt_path = coordinator.receipt_root / "fallback.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt.pop("status")
            receipt.pop("rejection_ref")
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

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
                (attempt / "policy_gate.json").write_text(
                    json.dumps(gate), encoding="utf-8"
                )
                return gate

            with patch("harness.coordinator.evaluate_routing_draft", side_effect=accepted_gate):
                resumed = coordinator.next()
            self.assertEqual(resumed["status"], NEED_AUDIT)

    def test_audit_acceptance_must_cover_exact_fallback_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run(Path(tmp))
            coordinator = FallbackCoordinator(run)
            first = coordinator.next()
            coordinator.submit(
                "fallback",
                session_id="fallback-session",
                payload=self._fallback_payload(first["task"]),
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
                (attempt / "policy_gate.json").write_text(
                    json.dumps(gate), encoding="utf-8"
                )
                return gate

            with patch("harness.coordinator.evaluate_routing_draft", side_effect=accepted_gate):
                audit_task = coordinator.next()
            coordinator.submit(
                "audit",
                session_id="audit-session",
                payload={
                    "contract_version": 1,
                    "run_id": run.name,
                    "stage_id": "stage_1_standardize",
                    "role": "audit",
                    "status": "ACCEPTED",
                    "affected_file_ids": [],
                    "reason": "证据充分",
                },
            )

            outcome = coordinator.next()
            self.assertEqual(outcome["status"], MAINTAINER_REQUIRED)
            self.assertIn("修复范围不一致", outcome["message"])
            self.assertFalse(
                (run / audit_task["task"]["output_path"]).parent.joinpath(
                    "child_run_request.json"
                ).exists()
            )

    def test_gate_rejection_creates_attempt_two_without_deleting_attempt_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run(Path(tmp))
            coordinator = FallbackCoordinator(run)
            first = coordinator.next()
            coordinator.submit(
                "fallback",
                session_id="fallback-session-1",
                payload=self._fallback_payload(first["task"]),
            )
            attempt_one = coordinator.attempt_root
            first_result = (attempt_one / "fallback_result.json").read_bytes()

            def rejected_gate(**kwargs):
                attempt = Path(kwargs["attempt_root"])
                gate = render_protocol("policy-gate", {
                    "run_id": run.name,
                    "stage_id": "stage_1_standardize",
                    "status": "REJECTED",
                    "checks": [{
                        "name": "syntax_and_target",
                        "passed": False,
                        "detail": "fingerprint DSL 无效",
                    }],
                })
                (attempt / "policy_gate.json").write_text(
                    json.dumps(gate), encoding="utf-8"
                )
                return gate

            with patch("harness.coordinator.evaluate_routing_draft", side_effect=rejected_gate):
                second = coordinator.next()

            self.assertEqual(second["status"], NEED_FALLBACK)
            self.assertEqual(second["attempt"], 2)
            self.assertEqual(second["task"]["attempt"], 2)
            self.assertTrue((attempt_one / "fallback_task.json").is_file())
            self.assertEqual((attempt_one / "fallback_result.json").read_bytes(), first_result)
            self.assertTrue((attempt_one / "policy_gate.json").is_file())
            decision = run / "fallback" / "stage_1_standardize" / "retry-decisions" / "attempt-01.json"
            self.assertTrue(decision.is_file())
            self.assertIn(
                "fallback/stage_1_standardize/attempt-01/policy_gate.json",
                second["task"]["input_refs"],
            )

            resumed = FallbackCoordinator(run)
            self.assertEqual(resumed.attempt, 2)
            with self.assertRaisesRegex(RuntimeError, "不同的新会话"):
                resumed.submit(
                    "fallback",
                    session_id="fallback-session-1",
                    payload=self._fallback_payload(second["task"]),
                )
            resumed.submit(
                "fallback",
                session_id="fallback-session-2",
                payload=self._fallback_payload(second["task"]),
            )
            with patch("harness.coordinator.evaluate_routing_draft", side_effect=rejected_gate):
                stopped = resumed.next()
            self.assertEqual(stopped["status"], MAINTAINER_REQUIRED)
            self.assertEqual(stopped["attempt"], 2)
            self.assertFalse(
                (run / "fallback" / "stage_1_standardize" / "attempt-03").exists()
            )

    def test_invalid_fallback_contract_consumes_attempt_and_keeps_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run(Path(tmp))
            coordinator = FallbackCoordinator(run)
            first = coordinator.next()
            payload = self._fallback_payload(first["task"])
            payload["repair_payload"]["rule"]["fingerprint"] = {
                "title_pattern": "不受支持字段",
            }
            receipt = coordinator.submit(
                "fallback",
                session_id="fallback-session-1",
                payload=payload,
            )
            self.assertEqual(receipt["status"], "REJECTED")
            self.assertFalse((coordinator.attempt_root / "fallback_result.json").exists())
            rejection = coordinator.attempt_root / "fallback_rejection.json"
            self.assertTrue(rejection.is_file())

            second = coordinator.next()
            self.assertEqual(second["status"], NEED_FALLBACK)
            self.assertEqual(second["attempt"], 2)
            self.assertIn(
                "fallback/stage_1_standardize/attempt-01/fallback_rejection.json",
                second["task"]["input_refs"],
            )

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

    def test_rule_merge_rejects_unsupported_account_type_and_fingerprint_dsl(self):
        base = {
            "operation": "append",
            "rule": {
                "file_type": "excel",
                "bank": "测试银行",
                "account_type": "企业账户",
                "fingerprint": {"identity": {"any": ["测试银行"]}},
                "reader_id": "openpyxl_grid",
            },
        }
        with self.assertRaisesRegex(ValueError, "account_type"):
            _merge_rule("[]\n", base)

        base["rule"]["account_type"] = "对公"
        base["rule"]["fingerprint"] = {"style": {"any": [{"bold": True}]}}
        with self.assertRaisesRegex(ValueError, "不支持字段"):
            _merge_rule("[]\n", base)

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

    def test_policy_gate_reads_real_excel_in_fresh_process_state(self):
        from openpyxl import Workbook

        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp).resolve() / "run-excel"
            input_dir = run / "input"
            input_dir.mkdir(parents=True)
            source = input_dir / "测试流水.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "企业流水"
            sheet.append(["Harness独立进程银行流水"])
            sheet.append(["交易日期", "收入金额", "账户余额"])
            sheet.append(["2026-01-01", 100, 1100])
            workbook.save(source)

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
            fingerprint = {
                "identity": {"any": ["Harness独立进程银行流水"]},
                "columns": {
                    "all": {
                        "交易日期": "交易日期",
                        "收入金额": "收入金额",
                        "账户余额": "账户余额",
                    }
                },
            }
            fallback = {
                "run_id": run.name,
                "affected_file_ids": [file_id],
                "repair_type": "ROUTING_RULE_DRAFT",
                "repair_payload": {
                    "operation": "append",
                    "rule": {
                        "file_type": "excel",
                        "bank": "未识别",
                        "account_type": "未知",
                        "fingerprint": fingerprint,
                        "reader_id": "openpyxl_grid",
                    },
                },
            }

            gate = evaluate_routing_draft(
                run_dir=run,
                attempt_root=run / "fallback" / "stage_1_standardize" / "attempt-01",
                fallback_request=request,
                fallback_result=fallback,
            )

            self.assertEqual(gate["status"], "ACCEPTED", gate)
            self.assertEqual(gate["routing_test"]["tested_file_count"], 1)
            self.assertEqual(
                gate["routing_test"]["affected_files"][0]["reader_id"],
                "openpyxl_grid",
            )


if __name__ == "__main__":
    unittest.main()
