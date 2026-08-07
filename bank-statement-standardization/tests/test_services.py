import io
import hashlib
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

from services import (  # noqa: E402
    InputFile,
    StandardizationRequest,
    StatementService,
    YamlRuleService,
)
from services.models import RunReference  # noqa: E402
from runtime.models import PipelineExecutionResult  # noqa: E402
from scripts import repair_coordinator as coordinator_cli  # noqa: E402


class StatementServiceTests(unittest.TestCase):
    def test_execute_standardization_uses_supplied_rule_snapshot_and_returns_dto(self):
        production = (
            CORE_PACKAGE
            / "ymb_standardization_core"
            / "config"
            / "routing"
            / "routing_rules.yaml"
        )
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "流水.xlsx"
            source.write_bytes(b"excel")
            rules = YamlRuleService.deserialize(production.read_text(encoding="utf-8"))
            service = StatementService(Path(tmp) / "runs", submit=lambda execute: None)
            execution = PipelineExecutionResult(
                exit_code=0,
                run_id="run-1",
                client_name="客户甲",
                parent_run_id="",
                status="DONE",
                file_results={
                    "files": {
                        "md5:test": {
                            "name": "流水.xlsx",
                            "relative_path": "流水.xlsx",
                            "status": "DONE",
                        },
                    },
                },
                stages={"stage_1_standardize": {"status": "DONE"}},
                stage_summaries={
                    "stage_2_integrate": {"integrated_rows": 10},
                },
                qc={"status": "PASS"},
                artifacts=({"artifact_id": "artifacts/交付物.xlsx"},),
                run_result={"next_action": "DELIVER"},
            )
            active = mock.Mock()
            active.result.return_value = execution
            service._active_runs["run-1"] = active
            with (
                mock.patch.object(
                    service,
                    "_start_run",
                    return_value=RunReference("run-1", ""),
                ) as start,
                mock.patch.object(
                    service,
                    "_get_run",
                    side_effect=AssertionError("正常 Service 路径不应回读 Run 文件"),
                ),
            ):
                result = service.execute_standardization(
                    StandardizationRequest(
                        client_name="客户甲",
                        files=(InputFile("流水.xlsx", str(source)),),
                    ),
                    rules,
                )

            self.assertEqual(result.status, "DONE")
            self.assertEqual(result.rules_version, rules.version)
            self.assertEqual(result.stage_summaries, execution.stage_summaries)
            self.assertEqual(result.file_results[0]["file_id"], "md5:test")
            self.assertEqual(result.qc, {"status": "PASS"})
            self.assertEqual(
                result.artifacts,
                [{"artifact_id": "artifacts/交付物.xlsx"}],
            )
            self.assertIs(start.call_args.kwargs["routing_rules_snapshot"], rules)

    @staticmethod
    def _finish_run(run_root, run_id):
        result_path = Path(run_root) / run_id / "pipeline_result.json"
        pipeline_result = json.loads(result_path.read_text(encoding="utf-8"))
        for spec in pipeline_result["stages"].values():
            if isinstance(spec, dict):
                spec["status"] = "DONE"
        result_path.write_text(
            json.dumps(pipeline_result, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_start_run_accepts_input_file_and_returns_aggregated_detail(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.xlsx"
            source.write_bytes(b"excel")
            submitted = []
            service = StatementService(tmp, submit=lambda execute: submitted.append(execute))

            reference = service._start_run(
                "客户甲",
                [InputFile("流水.xlsx", str(source))],
            )
            receipt_dir = Path(tmp) / reference.run_id / "receipts"
            receipt_dir.mkdir(exist_ok=True)
            (receipt_dir / "invalid.json").write_text("not-json", encoding="utf-8")
            detail = service._get_run(reference.run_id)

            self.assertEqual(reference.status, "RUNNING")
            self.assertEqual(detail.client_name, "客户甲")
            self.assertEqual(detail.status, "RUNNING")
            self.assertEqual([item["name"] for item in detail.files], ["流水.xlsx"])
            self.assertEqual(len(submitted), 1)
            pipeline_result = json.loads(
                (Path(tmp) / reference.run_id / "pipeline_result.json").read_text(encoding="utf-8")
            )
            self.assertTrue(pipeline_result["rules_version"].startswith("sha256-"))
            self.assertTrue(detail.artifacts)

    def test_incremental_run_inherits_parent_and_applies_remove_and_add(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_source = Path(tmp) / "source-old.xlsx"
            old_source.write_bytes(b"old")
            new_source = Path(tmp) / "source-new.xlsx"
            new_source.write_bytes(b"new")
            service = StatementService(tmp, submit=lambda execute: None)
            parent = service._start_run(
                "客户甲",
                [InputFile("旧.xlsx", str(old_source))],
            )
            old_id = service._get_run(parent.run_id).files[0]["file_id"]
            self._finish_run(tmp, parent.run_id)

            child = service._start_run(
                None,
                [InputFile("新.xlsx", str(new_source))],
                parent_run_id=parent.run_id,
                remove_file_ids=[old_id],
            )
            detail = service._get_run(child.run_id)

            self.assertEqual(detail.parent_run_id, parent.run_id)
            self.assertEqual(detail.client_name, "客户甲")
            self.assertEqual([item["name"] for item in detail.files], ["新.xlsx"])

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

            child = service._start_run(
                None,
                [],
                parent_run_id="parent-run",
                file_passwords={"加密流水.pdf": "secret"},
            )

            child_dir = root / child.run_id
            pipeline_result = json.loads(
                (child_dir / "pipeline_result.json").read_text(encoding="utf-8")
            )
            hints = (child_dir / "input" / "_file_hints.yaml").read_text(encoding="utf-8")
            self.assertIn("open_password: secret", hints)
            self.assertEqual(pipeline_result["attempts"]["password"], 1)
            self.assertEqual(pipeline_result["attempts"]["ai_repair"], 0)
            self.assertEqual(pipeline_result["rerun_reason"], "password_retry")
            self.assertNotIn(
                "secret",
                (child_dir / "pipeline_result.json").read_text(encoding="utf-8"),
            )

    def test_password_retry_cli_reads_secret_from_stdin_not_argv(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "parent-run"
            parent.mkdir()
            service = mock.Mock()
            service._start_run.return_value = SimpleNamespace(
                run_id="child-run",
                parent_run_id="parent-run",
            )
            service._get_run.return_value = SimpleNamespace(
                status="DONE",
                run_result={"next_action": "DELIVER"},
            )
            argv = [
                "repair_coordinator.py",
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
            service._start_run.assert_called_once_with(
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
            coordinator.run_dir = run.resolve()
            coordinator.submit.return_value = {
                "contract_version": 1,
                "run_id": "parent-run",
                "status": "REQUEST_USER",
                "message": "需要补充信息",
            }
            argv = [
                "repair_coordinator.py",
                "submit",
                "--run-dir",
                str(run),
                "--request-id",
                "request-1",
                "--session-id",
                "repair-session",
                "--result",
                str(Path(tmp) / "result.json"),
            ]
            (Path(tmp) / "result.json").write_text("{}", encoding="utf-8")
            output = io.StringIO()
            with (
                mock.patch.object(coordinator_cli, "RepairCoordinator", return_value=coordinator),
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(sys, "stdout", output),
            ):
                self.assertEqual(coordinator_cli.main(), 0)

            coordinator.submit.assert_called_once()
            self.assertEqual(json.loads(output.getvalue())["status"], "REQUEST_USER")

    def test_coordinator_submit_rejects_prewrite_inside_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "parent-run"
            run.mkdir()
            result_path = run / "repair" / "attempt-01" / "repair_result.json"
            result_path.parent.mkdir(parents=True)
            result_path.write_text("{}", encoding="utf-8")
            coordinator = mock.Mock()
            coordinator.run_dir = run.resolve()
            argv = [
                "repair_coordinator.py",
                "submit",
                "--run-dir",
                str(run),
                "--request-id",
                "request-1",
                "--session-id",
                "repair-session",
                "--result",
                str(result_path),
            ]
            with (
                mock.patch.object(coordinator_cli, "RepairCoordinator", return_value=coordinator),
                mock.patch.object(sys, "argv", argv),
                self.assertRaisesRegex(ValueError, "Run 目录外"),
            ):
                coordinator_cli.main()
            coordinator.submit.assert_not_called()

    def test_coordinator_auto_starts_child_and_returns_child_run_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "parent-run"
            run.mkdir()
            coordinator = SimpleNamespace(
                run_dir=run,
                run_id="parent-run",
                repair_root=run / "repair" / "attempt-01",
            )
            coordinator.repair_root.mkdir(parents=True)
            snapshot = coordinator.repair_root / "repair_result.json"
            snapshot.write_text('{"status":"REPAIRED","outputs":[]}', encoding="utf-8")
            outcome = {
                "status": "CHILD_RUN_READY",
                "repair_result_ref": "repair/attempt-01/repair_result.json",
                "repair_result_sha256": "sha256-test",
            }
            service = mock.Mock()
            service._start_run.return_value = SimpleNamespace(run_id="child-run")
            service._get_run.return_value = SimpleNamespace(
                run_result={"run_id": "child-run", "status": "DONE", "next_action": "DELIVER"},
            )
            with mock.patch.object(coordinator_cli, "StatementService", return_value=service):
                result = coordinator_cli._advance(coordinator, outcome)
                repeated = coordinator_cli._advance(coordinator, outcome)

            self.assertEqual(result["next_action"], "DELIVER")
            self.assertEqual(repeated, result)
            service._start_run.assert_called_once_with(
                None,
                [],
                parent_run_id="parent-run",
                repair_result_snapshot=snapshot.resolve(),
                repair_result_sha256="sha256-test",
            )

    def test_repair_result_snapshot_creates_isolated_child_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / "parent-run"
            (parent / "input").mkdir(parents=True)
            source = parent / "input" / "流水.csv"
            source.write_text("raw", encoding="utf-8")
            file_id = "md5:" + hashlib.md5(source.read_bytes()).hexdigest()
            (parent / "manifest.json").write_text(json.dumps({
                "client": "客户甲",
                "password_attempt": 0,
                "ai_repair_attempt": 0,
                "stage_1_standardize": {
                    "status": "ERROR",
                },
            }, ensure_ascii=False), encoding="utf-8")
            (parent / "stage_1_results.json").write_text(
                json.dumps({"files": {}}, ensure_ascii=False),
                encoding="utf-8",
            )
            attempt = parent / "repair" / "attempt-01"
            repaired = attempt / "standardized" / "流水__standardized.csv"
            repaired.parent.mkdir(parents=True)
            repaired.write_text(
                "交易唯一编号,交易时间,本方账户,收入金额,支出金额,交易金额,账户余额,来源文件名,来源行号\n"
                "TX-1,2026-01-01,62170001,1,,1,10,流水.csv,1\n",
                encoding="utf-8",
            )
            repaired_sha256 = hashlib.sha256(repaired.read_bytes()).hexdigest()
            snapshot = attempt / "repair_result.json"
            snapshot.write_text(json.dumps({
                "status": "REPAIRED",
                "outputs": [{
                    "file_id": file_id,
                    "source_md5": file_id,
                    "standardized_csv": "standardized/流水__standardized.csv",
                    "row_count": 1,
                    "sha256": repaired_sha256,
                }],
            }), encoding="utf-8")
            service = StatementService(root, submit=lambda execute: None)
            child = service._start_run(
                None,
                [],
                parent_run_id="parent-run",
                repair_result_snapshot=snapshot,
                repair_result_sha256=service._sha256_path(snapshot),
            )

            child_result = json.loads(
                (root / child.run_id / "pipeline_result.json").read_text(encoding="utf-8")
            )
            parent_manifest = json.loads((parent / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(child_result["parent_run_id"], "parent-run")
            self.assertEqual(child_result["attempts"]["ai_repair"], 1)
            self.assertEqual(child_result["repair_snapshot"]["scope"], "run_only")
            self.assertTrue((root / child.run_id / "repair" / "standardized" / repaired.name).is_file())
            self.assertEqual(parent_manifest["stage_1_standardize"]["status"], "ERROR")


class YamlRuleServiceTests(unittest.TestCase):
    def test_yaml_codec_round_trips_original_content(self):
        production = (
            CORE_PACKAGE
            / "ymb_standardization_core"
            / "config"
            / "routing"
            / "routing_rules.yaml"
        )
        content = production.read_text(encoding="utf-8")
        snapshot = YamlRuleService.deserialize(content)
        self.assertEqual(YamlRuleService.serialize(snapshot), content)
        self.assertTrue(snapshot.version.startswith("sha256-"))

if __name__ == "__main__":
    unittest.main()
