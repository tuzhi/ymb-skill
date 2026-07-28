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

            selected_id = service._file_md5(run_input / "主账户.xlsx")
            with mock.patch.object(yaml_service_module, "read_rows", side_effect=route_for):
                selected = service.test_draft("run-directory", [selected_id])
            self.assertTrue(selected.passed)
            self.assertEqual(selected.summary["total"], 1)
            self.assertEqual(
                [item["relative_path"] for item in selected.files],
                ["主账户.xlsx"],
            )


if __name__ == "__main__":
    unittest.main()
