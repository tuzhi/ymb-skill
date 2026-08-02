import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SKILL_ROOT.parent
CORE_PACKAGE = REPO_ROOT / "ymb-standardization-core" / "src"
for path in (SKILL_ROOT, CORE_PACKAGE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from services import StatementService, YamlRuleService  # noqa: E402
from services import yaml_rule_service as yaml_service_module  # noqa: E402
from scripts import fallback_coordinator as coordinator_cli  # noqa: E402
from ymb_standardization_core.contracts import RouteDecision  # noqa: E402
from ymb_standardization_core.readers.routing.rule_loader import (  # noqa: E402
    routing_rules_version,
)


class _Upload:
    def __init__(self, filename, content):
        self.filename = filename
        self.file = io.BytesIO(content)


class StatementServiceTests(unittest.TestCase):
    @staticmethod
    def _finish_run(run_root, run_id):
        manifest_path = Path(run_root) / run_id / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for stage_id, spec in manifest.items():
            if stage_id.startswith("stage_") and isinstance(spec, dict):
                spec["status"] = "DONE"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_start_run_accepts_upload_stream_and_returns_aggregated_detail(self):
        with tempfile.TemporaryDirectory() as tmp:
            submitted = []
            service = StatementService(tmp, submit=lambda execute: submitted.append(execute))

            reference = service.start_run("客户甲", [_Upload("流水.xlsx", b"excel")])
            detail = service.get_run(reference.run_id)

            self.assertEqual(reference.status, "RUNNING")
            self.assertEqual(detail.client_name, "客户甲")
            self.assertEqual(detail.status, "RUNNING")
            self.assertEqual([item["name"] for item in detail.files], ["流水.xlsx"])
            self.assertEqual(len(submitted), 1)
            manifest = json.loads(
                (Path(tmp) / reference.run_id / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertTrue(manifest["routing_rules_version"].startswith("sha256-"))
            self.assertTrue(detail.artifacts)
            artifact = service.get_artifact(reference.run_id, "stage_1_results.json")
            self.assertEqual(json.loads(artifact.read()), {"files": {}})

    def test_incremental_run_inherits_parent_and_applies_remove_and_add(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = StatementService(tmp, submit=lambda execute: None)
            parent = service.start_run("客户甲", [_Upload("旧.xlsx", b"old")])
            old_id = service.get_run(parent.run_id).files[0]["file_id"]
            self._finish_run(tmp, parent.run_id)

            child = service.start_run(
                None,
                [_Upload("新.xlsx", b"new")],
                parent_run_id=parent.run_id,
                remove_file_ids=[old_id],
            )
            detail = service.get_run(child.run_id)

            self.assertEqual(detail.parent_run_id, parent.run_id)
            self.assertEqual(detail.client_name, "客户甲")
            self.assertEqual([item["name"] for item in detail.files], ["新.xlsx"])

    def test_delete_rejects_running_and_referenced_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = StatementService(tmp, submit=lambda execute: None)
            parent = service.start_run("客户甲", [_Upload("旧.xlsx", b"old")])
            with self.assertRaisesRegex(RuntimeError, "RUNNING"):
                service.delete_run(parent.run_id)

            self._finish_run(tmp, parent.run_id)
            child = service.start_run(None, [], parent_run_id=parent.run_id)
            self._finish_run(tmp, child.run_id)

            with self.assertRaisesRegex(RuntimeError, "子运行引用"):
                service.delete_run(parent.run_id)
            service.delete_run(child.run_id)
            service.delete_run(parent.run_id)

    def test_password_child_run_writes_hints_without_consuming_ai_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / "parent-run"
            input_dir = parent / "input"
            input_dir.mkdir(parents=True)
            (input_dir / "加密流水.pdf").write_bytes(b"%PDF-1.4\n")
            (parent / "manifest.json").write_text(json.dumps({
                "client": "客户甲",
                "password_attempt": 0,
                "ai_repair_attempt": 0,
                "stage_1_standardize": {
                    "status": "ERROR",
                    "ai_fallback_used": False,
                    "ai_fallback_artifacts": [],
                },
            }, ensure_ascii=False), encoding="utf-8")
            (parent / "stage_1_results.json").write_text(
                json.dumps({"files": {}}, ensure_ascii=False),
                encoding="utf-8",
            )
            service = StatementService(root, submit=lambda execute: None)

            child = service.start_run(
                None,
                [],
                parent_run_id="parent-run",
                file_passwords={"加密流水.pdf": "secret"},
            )

            child_dir = root / child.run_id
            manifest = json.loads((child_dir / "manifest.json").read_text(encoding="utf-8"))
            hints = (child_dir / "input" / "_file_hints.yaml").read_text(encoding="utf-8")
            self.assertIn("open_password: secret", hints)
            self.assertEqual(manifest["password_attempt"], 1)
            self.assertEqual(manifest["ai_repair_attempt"], 0)
            self.assertEqual(manifest["rerun_reason"], "password_retry")
            self.assertNotIn("secret", (child_dir / "manifest.json").read_text(encoding="utf-8"))

    def test_password_retry_cli_reads_secret_from_stdin_not_argv(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "parent-run"
            parent.mkdir()
            service = mock.Mock()
            service.start_run.return_value = SimpleNamespace(
                run_id="child-run",
                parent_run_id="parent-run",
            )
            service.get_run.return_value = SimpleNamespace(
                status="DONE",
                run_result={"next_action": "DELIVER"},
            )
            argv = [
                "fallback_coordinator.py",
                "retry-password",
                "--run-dir",
                str(parent),
                "--file",
                "加密流水.pdf",
                "--password-stdin",
            ]
            output = io.StringIO()
            with (
                mock.patch.object(coordinator_cli, "StatementService", return_value=service),
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(sys, "stdin", io.StringIO("secret\n")),
                mock.patch.object(sys, "stdout", output),
            ):
                self.assertEqual(coordinator_cli.main(), 0)

            self.assertNotIn("secret", argv)
            self.assertEqual(json.loads(output.getvalue())["next_action"], "DELIVER")
            service.start_run.assert_called_once_with(
                None,
                [],
                parent_run_id="parent-run",
                file_passwords={"加密流水.pdf": "secret"},
            )

    def test_coordinator_submit_advances_without_returning_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "parent-run"
            run.mkdir()
            coordinator = mock.Mock()
            coordinator.next.return_value = {
                "contract_version": 1,
                "run_id": "parent-run",
                "status": "NEED_AUDIT",
            }
            argv = [
                "fallback_coordinator.py",
                "submit",
                "--run-dir",
                str(run),
                "--role",
                "fallback",
                "--session-id",
                "fallback-session",
                "--result",
                str(run / "result.json"),
            ]
            (run / "result.json").write_text("{}", encoding="utf-8")
            output = io.StringIO()
            with (
                mock.patch.object(coordinator_cli, "FallbackCoordinator", return_value=coordinator),
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(sys, "stdout", output),
            ):
                self.assertEqual(coordinator_cli.main(), 0)

            coordinator.submit.assert_called_once()
            coordinator.next.assert_called_once_with()
            self.assertEqual(json.loads(output.getvalue())["status"], "NEED_AUDIT")

    def test_coordinator_auto_starts_child_and_returns_child_run_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "parent-run"
            run.mkdir()
            coordinator = SimpleNamespace(
                run_dir=run,
                run_id="parent-run",
                attempt_root=run / "fallback" / "stage_1_standardize" / "attempt-01",
                next=lambda: {
                    "status": "CHILD_RUN_READY",
                    "child_run_request": "fallback/stage_1_standardize/attempt-01/child_run_request.json",
                },
            )
            coordinator.attempt_root.mkdir(parents=True)
            service = mock.Mock()
            service.start_child_run_from_request.return_value = SimpleNamespace(run_id="child-run")
            service.get_run.return_value = SimpleNamespace(
                run_result={"run_id": "child-run", "status": "DONE", "next_action": "DELIVER"},
            )
            with mock.patch.object(coordinator_cli, "StatementService", return_value=service):
                result = coordinator_cli._advance(coordinator)
                repeated = coordinator_cli._advance(coordinator)

            self.assertEqual(result["next_action"], "DELIVER")
            self.assertEqual(repeated, result)
            service.start_child_run_from_request.assert_called_once_with(
                "parent-run",
                "fallback/stage_1_standardize/attempt-01/child_run_request.json",
            )

    def test_authorized_routing_snapshot_creates_isolated_child_run(self):
        production = (
            CORE_PACKAGE
            / "ymb_standardization_core"
            / "config"
            / "routing"
            / "routing_rules.yaml"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / "parent-run"
            (parent / "input").mkdir(parents=True)
            (parent / "input" / "流水.csv").write_text("raw", encoding="utf-8")
            (parent / "manifest.json").write_text(json.dumps({
                "client": "客户甲",
                "password_attempt": 0,
                "ai_repair_attempt": 0,
                "stage_1_standardize": {
                    "status": "ERROR",
                    "ai_fallback_used": True,
                    "ai_fallback_artifacts": ["fallback_request.json"],
                },
            }, ensure_ascii=False), encoding="utf-8")
            (parent / "stage_1_results.json").write_text(
                json.dumps({"files": {}}, ensure_ascii=False),
                encoding="utf-8",
            )
            attempt = parent / "fallback" / "stage_1_standardize" / "attempt-01"
            snapshot = attempt / "repair" / "routing_rules.yaml"
            snapshot.parent.mkdir(parents=True)
            snapshot.write_text(production.read_text(encoding="utf-8"), encoding="utf-8")
            gate = attempt / "policy_gate.json"
            gate.write_text(json.dumps({
                "status": "ACCEPTED",
            }), encoding="utf-8")
            audit = attempt / "audit_result.json"
            audit.write_text(json.dumps({
                "status": "ACCEPTED",
            }), encoding="utf-8")
            receipts = attempt / "session-receipts"
            receipts.mkdir()
            service = StatementService(root, submit=lambda execute: None)
            (receipts / "audit.json").write_text(json.dumps({
                "output_sha256": service._sha256_path(audit),
            }), encoding="utf-8")
            request = attempt / "child_run_request.json"
            request.write_text(json.dumps({
                "parent_run_id": "parent-run",
                "routing_rules_snapshot": snapshot.relative_to(parent).as_posix(),
                "routing_rules_sha256": service._sha256_path(snapshot),
                "authorized_by": {
                    "policy_gate": gate.relative_to(parent).as_posix(),
                    "audit": audit.relative_to(parent).as_posix(),
                },
            }), encoding="utf-8")

            child = service.start_child_run_from_request(
                "parent-run",
                request.relative_to(parent).as_posix(),
            )

            child_manifest = json.loads(
                (root / child.run_id / "manifest.json").read_text(encoding="utf-8")
            )
            parent_manifest = json.loads((parent / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(child_manifest["parent_run_id"], "parent-run")
            self.assertEqual(child_manifest["ai_repair_attempt"], 1)
            self.assertEqual(child_manifest["routing_rules_snapshot"]["scope"], "run_only")
            self.assertEqual(parent_manifest["stage_1_standardize"]["status"], "ERROR")


class YamlRuleServiceTests(unittest.TestCase):
    def test_draft_must_pass_isolated_file_test_before_publish(self):
        production = (
            CORE_PACKAGE
            / "ymb_standardization_core"
            / "config"
            / "routing"
            / "routing_rules.yaml"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            production_copy = root / "routing_rules.yaml"
            production_copy.write_text(production.read_text(encoding="utf-8"), encoding="utf-8")
            run_input = root / "runs" / "run-1" / "input"
            run_input.mkdir(parents=True)
            (run_input / "sample.xlsx").write_bytes(b"fake")
            service = YamlRuleService(
                run_root=root / "runs",
                storage_root=root / "rule-store",
                production_rules_path=production_copy,
            )
            initial_version = routing_rules_version(
                production_copy.read_text(encoding="utf-8")
            )

            draft = service.create_draft()
            with self.assertRaisesRegex(RuntimeError, "已经存在草稿"):
                service.create_draft()
            service.save_draft("not: [valid")
            invalid = service.test_draft("run-1")
            self.assertFalse(invalid.passed)
            with self.assertRaisesRegex(RuntimeError, "必须先通过"):
                service.publish_draft()

            content = "# draft version\n" + production_copy.read_text(encoding="utf-8")
            service.save_draft(content)
            matched = SimpleNamespace(route_info=RouteDecision({
                "decision": "matched",
                "fingerprint_id": "md5:test",
                "reader_id": "openpyxl_grid",
            }))
            with mock.patch.object(yaml_service_module, "read_rows", return_value=matched):
                result = service.test_draft("run-1")

            self.assertTrue(result.passed)
            self.assertEqual(result.source_run_id, "run-1")
            self.assertTrue(result.test_id.startswith("rule-test-"))
            self.assertEqual(result.draft_version, routing_rules_version(content))
            self.assertEqual(
                result.summary,
                {
                    "total": 1,
                    "supported": 1,
                    "matched": 1,
                    "unmatched": 0,
                    "ambiguous": 0,
                    "incomplete": 0,
                    "errors": 0,
                    "skipped": 0,
                    "failed": 0,
                },
            )
            test_result_path = (
                root / "rule-store" / "tests" / result.test_id / "result.json"
            )
            persisted = json.loads(test_result_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["source_run_id"], "run-1")
            self.assertEqual(persisted["files"][0]["relative_path"], "sample.xlsx")

            service.draft_path.write_text(content + "\n# untested change\n", encoding="utf-8")
            self.assertFalse(service._draft().tested)
            with self.assertRaisesRegex(RuntimeError, "已通过测试的版本不一致"):
                service.publish_draft()
            service.save_draft(content)
            with mock.patch.object(yaml_service_module, "read_rows", return_value=matched):
                service.test_draft("run-1")
            published = service.publish_draft()
            self.assertEqual(service.download_rules().read(), content.encode("utf-8"))
            self.assertEqual(service.download_rules(published.version).read(), content.encode("utf-8"))
            self.assertFalse((root / "rule-store" / "drafts" / "routing_rules.yaml").exists())
            service.create_draft()

            rolled_back = service.rollback(initial_version)
            self.assertNotEqual(rolled_back.version, published.version)
            self.assertIn(
                f"rolled_back_from: {initial_version}",
                service.download_rules().read().decode("utf-8"),
            )

    def test_draft_tests_entire_run_snapshot_and_keeps_results_outside_run(self):
        production = (
            CORE_PACKAGE
            / "ymb_standardization_core"
            / "config"
            / "routing"
            / "routing_rules.yaml"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            production_copy = root / "routing_rules.yaml"
            content = production.read_text(encoding="utf-8")
            production_copy.write_text(content, encoding="utf-8")
            run_input = root / "runs" / "run-directory" / "input"
            nested = run_input / "银行A"
            nested.mkdir(parents=True)
            (run_input / "主账户.xlsx").write_bytes(b"excel")
            (nested / "补充流水.pdf").write_bytes(b"pdf")
            (nested / "说明.txt").write_text("仅用于说明", encoding="utf-8")
            service = YamlRuleService(
                run_root=root / "runs",
                storage_root=root / "rule-store",
                production_rules_path=production_copy,
            )
            service.create_draft()

            def route_for(path, route_rules):
                decision = (
                    "matched_incomplete"
                    if str(path).endswith("补充流水.pdf")
                    else "matched"
                )
                return SimpleNamespace(route_info=RouteDecision({
                    "decision": decision,
                    "fingerprint_id": "md5:test",
                    "reader_id": (
                        "pdfplumber_coordinate_table"
                        if str(path).endswith(".pdf")
                        else "openpyxl_grid"
                    ),
                }))

            with mock.patch.object(yaml_service_module, "read_rows", side_effect=route_for):
                result = service.test_draft("run-directory")

            self.assertFalse(result.passed)
            self.assertEqual(
                result.summary,
                {
                    "total": 3,
                    "supported": 2,
                    "matched": 1,
                    "unmatched": 0,
                    "ambiguous": 0,
                    "incomplete": 1,
                    "errors": 0,
                    "skipped": 1,
                    "failed": 1,
                },
            )
            self.assertEqual(
                {item["relative_path"] for item in result.files},
                {"主账户.xlsx", "银行A/补充流水.pdf", "银行A/说明.txt"},
            )
            persisted = (
                root
                / "rule-store"
                / "tests"
                / result.test_id
                / "result.json"
            )
            self.assertTrue(persisted.is_file())
            self.assertFalse(
                any((root / "runs" / "run-directory").rglob("result.json"))
            )
            with self.assertRaisesRegex(RuntimeError, "必须先通过"):
                service.publish_draft()

            with self.assertRaises(TypeError):
                service.test_draft("run-directory", ["md5:not-supported"])


if __name__ == "__main__":
    unittest.main()
