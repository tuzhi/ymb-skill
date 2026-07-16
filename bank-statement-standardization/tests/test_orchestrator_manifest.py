import importlib.util
import csv
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace


SKILL_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_PATH = SKILL_ROOT / "scripts" / "orchestrator.py"
spec = importlib.util.spec_from_file_location("orchestrator", ORCHESTRATOR_PATH)
orchestrator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orchestrator)


class OrchestratorManifestTest(unittest.TestCase):
    def _write_standardized_csv(self, path, source_name):
        columns = list(orchestrator.V.STD_REQUIRED)
        row = {column: "1" for column in columns}
        for column in columns:
            if "来源文件" in column:
                row[column] = source_name
            if "来源行号" in column:
                row[column] = "2"
            if "交易唯一编号" in column:
                row[column] = f"{source_name}-2"
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            writer.writerow(row)

    def test_skill_metadata_reads_name_and_version_from_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "skill"
            template_dir = skill / "assets"
            template_dir.mkdir(parents=True)
            (template_dir / "manifest.template.json").write_text(
                json.dumps(
                    {
                        "skill": {
                            "name": "bank-statement-standardization",
                            "version": "1.2.6",
                        },
                        "stage_1_standardize": {},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            metadata = orchestrator.load_skill_metadata(str(skill))

            self.assertEqual(metadata["name"], "bank-statement-standardization")
            self.assertEqual(metadata["version"], "1.2.6")

    def test_stage_one_records_wps_pdf_to_excel_as_skipped_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            converted = source / "转换流水.xlsx"
            custom_properties = """<?xml version="1.0" encoding="UTF-8"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
 xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="2" name="CRO">
    <vt:lpwstr>wqlLaW5nc29mdCBQREYgdG8gV1BTIDEyMA</vt:lpwstr>
  </property>
</Properties>"""
            with zipfile.ZipFile(converted, "w") as archive:
                archive.writestr("docProps/custom.xml", custom_properties)
            self._write_standardized_csv(source / "有效流水__standardized.csv", "有效流水.xlsx")

            runner = orchestrator.Runner.__new__(orchestrator.Runner)
            runner.args = SimpleNamespace(folder=str(source), client="测试客户", account_type=None)
            runner.out_dir = str(root / "output")
            runner.manifest = {"skipped_inputs": [], "client": "测试客户"}
            runner.write_manifest = lambda: None

            result = runner.stage_1_standardize()

            self.assertEqual(result["processed_files"], 1)
            self.assertEqual(len(runner.manifest["skipped_inputs"]), 1)
            skipped = runner.manifest["skipped_inputs"][0]
            self.assertEqual(skipped["name"], "转换流水.xlsx")
            self.assertIn("Kingsoft PDF to WPS 120", skipped["reason"])

    def test_inventory_excludes_token_vault_secret_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary = root / "summary"
            summary.mkdir()
            (summary / "tokenized_batch_bundle_token_vault.json").write_text("secret", encoding="utf-8")
            (summary / "token_vault_manifest.json").write_text("secret", encoding="utf-8")
            (summary / "tokenized_batch_bundle_token_vault_ref.json").write_text("ref", encoding="utf-8")
            (summary / "manifest.json").write_text("{}", encoding="utf-8")

            rows = orchestrator.inventory(str(root))
            paths = {row["path"].replace("\\", "/") for row in rows}

            self.assertNotIn("summary/tokenized_batch_bundle_token_vault.json", paths)
            self.assertNotIn("summary/token_vault_manifest.json", paths)
            self.assertIn("summary/tokenized_batch_bundle_token_vault_ref.json", paths)
            self.assertIn("summary/manifest.json", paths)

    def test_snapshot_input_folder_copies_inputs_under_run_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            nested = source / "nested"
            summary = source / "summary"
            nested.mkdir(parents=True)
            summary.mkdir()
            (source / "流水.xlsx").write_text("raw", encoding="utf-8")
            (nested / "补充.csv").write_text("nested", encoding="utf-8")
            (summary / "tokenized_batch_bundle_token_vault.json").write_text("secret", encoding="utf-8")
            (summary / "tokenized_batch_bundle_token_vault_ref.json").write_text("ref", encoding="utf-8")

            target = orchestrator.snapshot_input_folder(str(source), str(root / "run"))

            self.assertEqual(Path(target), root / "run" / "input")
            self.assertEqual((root / "run" / "input" / "流水.xlsx").read_text(encoding="utf-8"), "raw")
            self.assertEqual((root / "run" / "input" / "nested" / "补充.csv").read_text(encoding="utf-8"), "nested")
            self.assertFalse((root / "run" / "input" / "summary" / "tokenized_batch_bundle_token_vault.json").exists())
            self.assertTrue((root / "run" / "input" / "summary" / "tokenized_batch_bundle_token_vault_ref.json").exists())

    def test_snapshot_input_folder_skips_run_dir_when_nested_in_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            run_dir = source / "runs" / "run-001"
            run_dir.mkdir(parents=True)
            (source / "流水.csv").write_text("raw", encoding="utf-8")
            (run_dir / "traceback.txt").write_text("diagnostic", encoding="utf-8")

            target = Path(orchestrator.snapshot_input_folder(str(source), str(run_dir)))

            self.assertTrue((target / "流水.csv").is_file())
            self.assertFalse((target / "runs" / "run-001" / "traceback.txt").exists())

    def test_prepare_input_snapshot_extracts_zip_under_run_input_and_records_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zip_path = root / "张运贞.zip"
            mojibake_name = "张运贞/25年1-5月.xls".encode("gbk").decode("cp437")
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("张运贞/", "")
                zf.writestr(mojibake_name, "raw-xls")
                zf.writestr("张运贞/嵌套/补充.csv", "nested")

            target, details = orchestrator.prepare_input_snapshot(str(zip_path), str(root / "run"))

            self.assertEqual(Path(target), root / "run" / "input")
            self.assertEqual((Path(target) / "25年1-5月.xls").read_text(encoding="utf-8"), "raw-xls")
            self.assertEqual((Path(target) / "嵌套" / "补充.csv").read_text(encoding="utf-8"), "nested")
            self.assertEqual(details["input_kind"], "zip")
            self.assertEqual(details["common_root_stripped"], "张运贞")
            output_paths = {row["output_path"].replace("\\", "/") for row in details["extracted_files"]}
            self.assertEqual(output_paths, {"25年1-5月.xls", "嵌套/补充.csv"})
            decoded_names = {row["decoded_name"].replace("\\", "/") for row in details["extracted_files"]}
            self.assertIn("张运贞/25年1-5月.xls", decoded_names)

    def test_prepare_input_snapshot_rejects_zip_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zip_path = root / "bad.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("../escape.csv", "bad")

            with self.assertRaisesRegex(RuntimeError, "非法 zip 路径"):
                orchestrator.prepare_input_snapshot(str(zip_path), str(root / "run"))

            self.assertFalse((root / "escape.csv").exists())

    def test_preflight_receipt_records_zip_snapshot_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zip_path = root / "客户.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("客户/流水.csv", "raw")

            args = SimpleNamespace(
                run_root=str(root / "runs"),
                folder=str(zip_path),
                client="客户",
                client_arg_provided=False,
                client_explicit=False,
                error_bundle_mode="full",
                parent_run_id="",
                rerun_reason="",
                require_model="",
            )
            runner = orchestrator.Runner(args)

            runner.preflight()

            receipt_path = Path(runner.receipt_dir) / "01-preflight.json"
            data = json.loads(receipt_path.read_text(encoding="utf-8"))
            snapshot = data["details"]["input_snapshot"]
            self.assertEqual(snapshot["input_kind"], "zip")
            self.assertEqual(snapshot["common_root_stripped"], "客户")
            self.assertEqual(snapshot["extracted_files"][0]["output_path"], "流水.csv")

    def test_error_bundle_excludes_token_vault_secret_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "input"
            summary = input_dir / "summary"
            summary.mkdir(parents=True)
            (summary / "tokenized_batch_bundle_token_vault.json").write_text("secret", encoding="utf-8")
            (summary / "token_vault_manifest.json").write_text("secret", encoding="utf-8")
            (summary / "tokenized_batch_bundle_token_vault_ref.json").write_text("ref", encoding="utf-8")

            runner = orchestrator.Runner.__new__(orchestrator.Runner)
            runner.run_id = "test-run"
            runner.run_dir = str(root / "run")
            runner.args = SimpleNamespace(folder=str(input_dir), error_bundle_mode="full")
            Path(runner.run_dir).mkdir()
            (Path(runner.run_dir) / "events.jsonl").write_text("", encoding="utf-8")

            bundle = runner.bundle("ERROR")

            with zipfile.ZipFile(bundle) as zf:
                names = {name.replace("\\", "/") for name in zf.namelist()}
            self.assertNotIn("raw_inputs/summary/tokenized_batch_bundle_token_vault.json", names)
            self.assertNotIn("raw_inputs/summary/token_vault_manifest.json", names)
            self.assertIn("raw_inputs/summary/tokenized_batch_bundle_token_vault_ref.json", names)

    def test_first_pending_stage_skips_skill_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "manifest.json"
            runtime.write_text(
                json.dumps(
                    {
                        "skill": {
                            "name": "bank-statement-standardization",
                            "version": "1.2.6",
                        },
                        "stage_1_standardize": {"status": ""},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            runner = orchestrator.Runner.__new__(orchestrator.Runner)
            runner.stage_manifest_path = str(runtime)
            runner.manifest_path = str(runtime)
            runner.manifest = json.loads(runtime.read_text(encoding="utf-8"))

            stage_id, spec = runner.first_pending_stage()

            self.assertEqual(stage_id, "stage_1_standardize")
            self.assertEqual(spec, {"status": ""})

    def test_stage_1_keeps_fixed_client_and_does_not_accept_upstream_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bundle = tmp_path / "tokenized_batch_bundle"
            summary = bundle / "summary"
            summary.mkdir(parents=True)
            first = bundle / "001_raw-a__standardized.csv"
            second = bundle / "002_raw-b__standardized.csv"
            self._write_standardized_csv(first, "raw-a.csv")
            self._write_standardized_csv(second, "raw-b.csv")
            (summary / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": "bank-statement-standardization.manifest/v1",
                        "producer": "token_vault_service",
                        "archive_name": "真实客户名称",
                        "archive_id": "tv_20260612_fa8d03d0",
                        "client_alias": "陈某001",
                        "stage_1_standardize": {
                            "status": "DONE",
                            "outputs": [
                                "../001_raw-a__standardized.csv",
                                "../002_raw-b__standardized.csv",
                            ],
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            runner = orchestrator.Runner.__new__(orchestrator.Runner)
            runner.args = SimpleNamespace(
                folder=str(bundle),
                client="tokenized_batch_bundle",
                account_type=None,
                client_arg_provided=False,
                client_explicit=False,
            )
            runner.out_dir = str(tmp_path / "artifacts")
            runner.manifest = {"skipped_inputs": [], "client": "tokenized_batch_bundle"}
            runner.write_manifest = lambda: None
            runner.emit = lambda *args, **kwargs: None

            result = runner.stage_1_standardize()
            work = Path(runner.work_dir())

            self.assertEqual(result["mode"], "manifest_declared_standardized_input")
            self.assertEqual(result["processed_files"], 2)
            self.assertEqual(result["upstream_manifest"]["schema_version"], "bank-statement-standardization.manifest/v1")
            self.assertEqual(runner.args.client, "tokenized_batch_bundle")
            self.assertEqual(runner.manifest["client"], "tokenized_batch_bundle")
            self.assertNotIn("archive_name", result["upstream_manifest"])
            self.assertEqual(result["upstream_manifest"]["archive_id"], "tv_20260612_fa8d03d0")
            self.assertTrue(result["upstream_manifest"]["archive_name_present"])
            self.assertTrue((work / "001_raw-a__standardized.csv").is_file())
            self.assertTrue((work / "002_raw-b__standardized.csv").is_file())
            self.assertTrue((work / "001_raw-a__mapping.json").is_file())
            self.assertTrue((work / "002_raw-b__mapping.json").is_file())
            validation = orchestrator.V.validate_standardize(str(work))
            self.assertEqual(validation["standardized_files"], 2)

    def test_copy_stage_manifest_resets_runtime_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            template = tmp_path / "manifest.json"
            runtime = tmp_path / "run" / "manifest.json"
            runtime.parent.mkdir()
            template.write_text(
                json.dumps(
                    {
                        "stage_1_standardize": {
                            "name": "stage 1",
                            "script": "scripts/standardize.py",
                            "ai_fallback_refs": [],
                            "ai_fallback_info": "Prompt 1A 用于加密 PDF/Excel 无法打开时，向用户索要密码并写入 _file_hints.yaml 后重跑阶段一。",
                            "validator": "scripts/validate_stage.py::validate_standardize",
                            "ai_fallback_used": True,
                            "ai_fallback_dir": "C:/Users/28307/WorkBuddy/runs/old-run/fallback/stage_1_standardize",
                            "ai_fallback_artifacts": ["old_patch.py"],
                            "started_at": "2026-06-08T18:38:26.495086+08:00",
                            "duration_seconds": 45.504,
                            "status": "DONE",
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            runner = orchestrator.Runner.__new__(orchestrator.Runner)
            runner.template_manifest_path = str(template)
            runner.stage_manifest_path = str(runtime)
            runner.manifest_path = str(runtime)

            runner.copy_stage_manifest()

            data = json.loads(runtime.read_text(encoding="utf-8"))
            stage = data["stage_1_standardize"]
            self.assertFalse(stage["ai_fallback_used"])
            self.assertEqual(stage["ai_fallback_artifacts"], [])
            self.assertNotIn("ai_fallback_dir", stage)
            self.assertNotIn("started_at", stage)
            self.assertNotIn("duration_seconds", stage)
            self.assertEqual(stage["status"], "")
            self.assertEqual(stage["ai_fallback_info"], "Prompt 1A 用于加密 PDF/Excel 无法打开时，向用户索要密码并写入 _file_hints.yaml 后重跑阶段一。")

    def test_load_parent_run_context_collects_fallbacks(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp) / "runs"
            parent = run_root / "parent-run"
            fallback = parent / "fallback" / "stage_1_standardize"
            fallback.mkdir(parents=True)
            (fallback / "fallback_request.json").write_text("{}", encoding="utf-8")
            (parent / "manifest.json").write_text(
                json.dumps(
                    {
                        "stage_1_standardize": {
                            "name": "stage 1",
                            "script": "scripts/standardize.py",
                            "validator": "scripts/validate_stage.py::validate_standardize",
                            "ai_fallback_used": True,
                            "ai_fallback_dir": str(fallback),
                            "ai_fallback_artifacts": ["fallback_request.json", "patch_header_nan_fix.py"],
                            "status": "ERROR",
                        },
                        "stage_2_integrate": {
                            "ai_fallback_used": False,
                            "ai_fallback_dir": "",
                            "ai_fallback_artifacts": [],
                            "status": "",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (parent / "run_manifest.json").write_text(
                json.dumps({"run_id": "parent-run", "status": "error", "error": "CSV 无交易数据"}, ensure_ascii=False),
                encoding="utf-8",
            )

            context = orchestrator.load_parent_run_context(str(run_root), "parent-run")

            self.assertEqual(context["parent_run_id"], "parent-run")
            self.assertEqual(context["parent_status"], "error")
            self.assertEqual(context["parent_error"], "CSV 无交易数据")
            self.assertEqual(len(context["inherited_fallbacks"]), 1)
            inherited = context["inherited_fallbacks"][0]
            self.assertEqual(inherited["stage"], "stage_1_standardize")
            self.assertEqual(inherited["parent_status"], "ERROR")
            self.assertEqual(inherited["parent_fallback_artifacts"], ["fallback_request.json", "patch_header_nan_fix.py"])

    def test_load_parent_run_context_rejects_missing_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "parent run 不存在"):
                orchestrator.load_parent_run_context(str(Path(tmp) / "runs"), "missing-run")

    def test_collect_skill_source_snapshot_hashes_source_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "skill"
            (skill / "scripts").mkdir(parents=True)
            (skill / "assets").mkdir()
            (skill / "dist").mkdir()
            (skill / "testdata").mkdir()
            (skill / "build").mkdir()
            (skill / "demo.egg-info").mkdir()
            (skill / "SKILL.md").write_text("# skill\n", encoding="utf-8")
            (skill / "assets" / "manifest.template.json").write_text("{}\n", encoding="utf-8")
            (skill / "scripts" / "standardize.py").write_text("print('ok')\n", encoding="utf-8")
            (skill / "dist" / "bundle.zip").write_text("ignore\n", encoding="utf-8")
            (skill / "testdata" / "raw.csv").write_text("ignore\n", encoding="utf-8")
            (skill / "build" / "generated.py").write_text("ignore\n", encoding="utf-8")
            (skill / "demo.egg-info" / "SOURCES.txt").write_text("ignore\n", encoding="utf-8")

            snapshot = orchestrator.collect_skill_source_snapshot(str(skill))

            self.assertIn("git_commit", snapshot)
            self.assertIn("dirty", snapshot)
            self.assertIn("modified_files", snapshot)
            self.assertIn("file_sha256", snapshot)
            self.assertIn("SKILL.md", snapshot["file_sha256"])
            self.assertIn("assets/manifest.template.json", snapshot["file_sha256"])
            self.assertIn("scripts/standardize.py", snapshot["file_sha256"])
            self.assertNotIn("dist/bundle.zip", snapshot["file_sha256"])
            self.assertNotIn("testdata/raw.csv", snapshot["file_sha256"])
            self.assertNotIn("build/generated.py", snapshot["file_sha256"])
            self.assertNotIn("demo.egg-info/SOURCES.txt", snapshot["file_sha256"])


if __name__ == "__main__":
    unittest.main()
