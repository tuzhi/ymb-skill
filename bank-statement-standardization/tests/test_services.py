import io
import hashlib
import json
import sys
import tempfile
import unittest
import pandas as pd
import zipfile
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
from services.models import StandardizationResult  # noqa: E402
from services.result_mapper import build_standardization_result  # noqa: E402
from runtime.models import PipelineExecutionResult  # noqa: E402
from scripts import repair_coordinator as coordinator_cli  # noqa: E402


class StatementServiceTests(unittest.TestCase):
    def test_result_mapper_builds_public_summary_without_service_state(self):
        transactions = pd.DataFrame([{
            "分析收入金额": 120.0,
            "分析支出金额": 20.0,
        }])
        execution = PipelineExecutionResult(
            exit_code=0,
            run_id="run-mapped",
            client_name="客户甲",
            parent_run_id="",
            status="DONE",
            file_results={"files": {}},
            stages={"stage_3_tag": {"name": "打标", "status": "DONE"}},
            qc={"status": "PASS", "customer": {}},
            run_result={"next_action": "DELIVER"},
            integration_report={
                "客户整合概览": {"整合交易数": 1, "整体质量评分": 98},
                "最终判断": {"是否可进入标签分析": True},
            },
            balance_report={
                "组合虚拟账户": {"期初合计余额": 100.0},
                "组合连续性校验": {"异常日数": 0, "结论": "通过"},
            },
            tag_report={
                "标签梳理概览": {
                    "规则命中数量": 1,
                    "兜底其他类数量": 0,
                    "交易关系汇总": {"银行冲正": {"配对组数": 0}},
                },
                "一级标签分布": {"经营类": 1},
            },
            dataset={"transactions": transactions},
        )

        result = build_standardization_result(execution, "rules-v1")

        self.assertEqual(result.summary["net_amount"], 100.0)
        self.assertEqual(result.business_summary["integration"]["quality_score"], 98)
        self.assertEqual(result.business_summary["tagging"]["level_1_distribution"], {"经营类": 1})
        self.assertEqual(result.stages["stage_3"]["status"], "DONE")

    def test_standardization_result_streams_dataframe_directly_to_zip(self):
        transactions = pd.DataFrame([{
            "交易唯一编号": "TX-1",
            "收入金额": 1.0,
            "支出金额": float("nan"),
        }])
        result = StandardizationResult(
            run_id="run-1",
            status="DONE",
            next_action="DELIVER",
            message="完成",
            client={"client_name": "客户甲"},
            rule_snapshot={"version": "sha256-demo"},
            summary={},
            file_results=[],
            stages={},
            qc_client={},
            business_summary={},
            dataset={"transactions": transactions},
            deliverable={},
        )

        self.assertIs(result.dataset["transactions"], transactions)
        self.assertNotIn("dataset", result.to_summary_dict())
        self.assertFalse(hasattr(result, "to_dict"))
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result.zip"
            self.assertEqual(Path(result.write_zip(output)), output.resolve())
            with zipfile.ZipFile(output) as archive:
                payload = json.loads(
                    archive.read("standardization_result.json").decode("utf-8")
                )
        self.assertEqual(
            payload["dataset"]["transactions"][0]["交易唯一编号"],
            "TX-1",
        )
        self.assertIsNone(payload["dataset"]["transactions"][0]["支出金额"])

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
            service = StatementService(Path(tmp) / "runs")
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
                qc={"status": "PASS"},
                run_result={"next_action": "DELIVER"},
            )
            with mock.patch.object(
                service,
                "_execute_pipeline",
                return_value=execution,
            ) as execute:
                result = service.execute_standardization(
                    StandardizationRequest(
                        client_name="客户甲",
                        files=(InputFile("流水.xlsx", str(source)),),
                    ),
                    rules,
                )

            self.assertEqual(result.status, "DONE")
            self.assertEqual(result.rule_snapshot["version"], rules.version)
            self.assertEqual(result.stages["stage_1"]["status"], "DONE")
            self.assertEqual(result.file_results[0]["file_id"], "md5:test")
            self.assertEqual(result.qc_client["status"], "PASS")
            self.assertEqual(result.next_action, "DELIVER")
            self.assertEqual(result.dataset, {})
            self.assertIs(execute.call_args.args[1], rules)

    @staticmethod
    def _finish_run(run_root, run_id):
        result_path = Path(run_root) / run_id / "pipeline_result.json"
        pipeline_result = json.loads(result_path.read_text(encoding="utf-8"))
        for spec in pipeline_result["stages"].values():
            if isinstance(spec, dict):
                spec["status"] = "DONE"
        pipeline_result["status"] = "DONE"
        pipeline_result["next_action"] = "DELIVER"
        result_path.write_text(
            json.dumps(pipeline_result, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_create_runner_snapshots_input_without_async_service_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.xlsx"
            source.write_bytes(b"excel")
            service = StatementService(tmp)

            runner = service._create_runner(
                StandardizationRequest(
                    client_name="客户甲",
                    files=(InputFile("流水.xlsx", str(source)),),
                )
            )

            self.assertEqual(
                [path.name for path in Path(runner.input_dir).iterdir()],
                ["流水.xlsx"],
            )
            pipeline_result = json.loads(
                (Path(tmp) / runner.run_id / "pipeline_result.json").read_text(encoding="utf-8")
            )
            self.assertTrue(pipeline_result["rules_version"].startswith("sha256-"))

    def test_incremental_run_inherits_parent_and_applies_remove_and_add(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_source = Path(tmp) / "source-old.xlsx"
            old_source.write_bytes(b"old")
            new_source = Path(tmp) / "source-new.xlsx"
            new_source.write_bytes(b"new")
            service = StatementService(tmp)
            parent = service._create_runner(
                StandardizationRequest(
                    client_name="客户甲",
                    files=(InputFile("旧.xlsx", str(old_source)),),
                )
            )
            old_id = "md5:" + hashlib.md5(old_source.read_bytes()).hexdigest()
            self._finish_run(tmp, parent.run_id)

            child = service._create_runner(
                StandardizationRequest(
                    client_name=None,
                    files=(InputFile("新.xlsx", str(new_source)),),
                    parent_run_id=parent.run_id,
                    remove_file_ids=(old_id,),
                )
            )

            self.assertEqual(child.pipeline_state["parent_run_id"], parent.run_id)
            self.assertEqual(child.args.client, "客户甲")
            self.assertEqual(
                [path.name for path in Path(child.input_dir).iterdir()],
                ["新.xlsx"],
            )

    def test_password_child_run_keeps_secret_in_memory_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / "parent-run"
            input_dir = parent / "input"
            input_dir.mkdir(parents=True)
            (input_dir / "加密流水.pdf").write_bytes(b"%PDF-1.4\n")
            (parent / "pipeline_result.json").write_text(json.dumps({
                "schema_version": 1,
                "run_id": "parent-run",
                "client_name": "客户甲",
                "status": "ERROR",
                "next_action": "REQUEST_USER",
                "reason_code": "INPUT_PASSWORD_REQUIRED",
                "attempts": {"password": 0, "ai_repair": 0},
                "stages": {"stage_1_standardize": {"status": "ERROR"}},
                "file_results": {"files": {}},
                "qc": {"status": "RUNNING", "files": {}, "customer": {}},
            }, ensure_ascii=False), encoding="utf-8")
            service = StatementService(root)

            request = StandardizationRequest(
                client_name=None,
                files=(),
                parent_run_id="parent-run",
                file_passwords={"加密流水.pdf": "secret"},
            )
            child = service._create_runner(request)

            child_dir = root / child.run_id
            pipeline_result = json.loads(
                (child_dir / "pipeline_result.json").read_text(encoding="utf-8")
            )
            self.assertEqual(child.file_passwords, {"加密流水.pdf": "secret"})
            self.assertEqual(child.args.file_passwords, {})
            self.assertEqual(
                child.input_password(child_dir / "input" / "加密流水.pdf"),
                "secret",
            )
            self.assertFalse((child_dir / "input" / "_file_hints.yaml").exists())
            self.assertEqual(pipeline_result["attempts"]["password"], 1)
            self.assertEqual(pipeline_result["attempts"]["ai_repair"], 0)
            self.assertEqual(pipeline_result["rerun_reason"], "password_retry")
            self.assertNotIn(
                "secret",
                (child_dir / "pipeline_result.json").read_text(encoding="utf-8"),
            )
            self.assertNotIn("secret", repr(request))
            for path in child_dir.rglob("*"):
                if path.is_file() and path.suffix in {".json", ".jsonl", ".txt", ".yaml"}:
                    self.assertNotIn(
                        "secret",
                        path.read_text(encoding="utf-8", errors="replace"),
                    )

    def test_password_retry_cli_reads_secret_from_stdin_not_argv(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "parent-run"
            parent.mkdir()
            service = mock.Mock()
            service._execute_pipeline.return_value = SimpleNamespace(
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
            request = service._execute_pipeline.call_args.args[0]
            self.assertEqual(request.parent_run_id, "parent-run")
            self.assertEqual(request.file_passwords, {"加密流水.pdf": "secret"})

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
                "repair_result_ref": "repair/attempt-01/repair_result.json",
                "repair_result_sha256": "sha256-test",
            }
            service = mock.Mock()
            service._execute_pipeline.return_value = SimpleNamespace(
                run_id="child-run",
                run_result={"run_id": "child-run", "status": "DONE", "next_action": "DELIVER"},
            )
            with (
                mock.patch.object(coordinator_cli, "StatementService", return_value=service),
                mock.patch.object(
                    coordinator_cli,
                    "load_pipeline_result",
                    return_value={
                        "schema_version": 1,
                        "run_id": "child-run",
                        "status": "DONE",
                        "next_action": "DELIVER",
                    },
                ),
            ):
                result = coordinator_cli._start_child_run(coordinator, outcome)
                repeated = coordinator_cli._start_child_run(coordinator, outcome)

            self.assertEqual(result["next_action"], "DELIVER")
            self.assertEqual(repeated["run_id"], result["run_id"])
            self.assertEqual(repeated["next_action"], result["next_action"])
            service._execute_pipeline.assert_called_once()
            request = service._execute_pipeline.call_args.args[0]
            self.assertEqual(request.parent_run_id, "parent-run")

    def test_repair_result_snapshot_creates_isolated_child_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / "parent-run"
            (parent / "input").mkdir(parents=True)
            source = parent / "input" / "流水.csv"
            source.write_text("raw", encoding="utf-8")
            file_id = "md5:" + hashlib.md5(source.read_bytes()).hexdigest()
            (parent / "pipeline_result.json").write_text(json.dumps({
                "schema_version": 1,
                "run_id": "parent-run",
                "client_name": "客户甲",
                "status": "ERROR",
                "next_action": "NEED_REPAIR",
                "reason_code": "READER_FAILED",
                "attempts": {"password": 0, "ai_repair": 0},
                "stages": {"stage_1_standardize": {"status": "ERROR"}},
                "file_results": {"files": {}},
                "qc": {"status": "RUNNING", "files": {}, "customer": {}},
            }, ensure_ascii=False), encoding="utf-8")
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
            service = StatementService(root)
            child = service._create_runner(
                StandardizationRequest(
                    client_name=None,
                    files=(),
                    parent_run_id="parent-run",
                ),
                repair_result_snapshot=snapshot,
                repair_result_sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest(),
            )

            child_result = json.loads(
                (root / child.run_id / "pipeline_result.json").read_text(encoding="utf-8")
            )
            parent_result = json.loads((parent / "pipeline_result.json").read_text(encoding="utf-8"))
            self.assertEqual(child_result["parent_run_id"], "parent-run")
            self.assertEqual(child_result["attempts"]["ai_repair"], 1)
            self.assertEqual(child_result["repair_snapshot"]["scope"], "run_only")
            self.assertTrue((root / child.run_id / "repair" / "standardized" / repaired.name).is_file())
            self.assertEqual(parent_result["stages"]["stage_1_standardize"]["status"], "ERROR")


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
