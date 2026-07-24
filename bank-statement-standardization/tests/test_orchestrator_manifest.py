import importlib.util
import csv
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


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
            runner.run_dir = str(root)
            runner.manifest = {
                "skipped_inputs": [],
                "client": "测试客户",
                "stage_1_standardize": {"route_artifact": ""},
            }
            runner.write_manifest = lambda: None

            result = runner.stage_1_standardize()

            self.assertEqual(result["processed_files"], 1)
            self.assertEqual(len(runner.manifest["skipped_inputs"]), 1)
            skipped = runner.manifest["skipped_inputs"][0]
            self.assertEqual(skipped["name"], "转换流水.xlsx")
            self.assertIn("Kingsoft PDF to WPS 120", skipped["reason"])

    def test_stage_one_blocks_when_identified_export_lacks_required_optional_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            statement = source / "招商银行交易流水.pdf"
            statement.write_bytes(b"%PDF-1.4\n")

            runner = orchestrator.Runner.__new__(orchestrator.Runner)
            runner.args = SimpleNamespace(folder=str(source), client="测试客户", account_type=None)
            runner.out_dir = str(root / "output")
            runner.run_dir = str(root)
            runner.manifest = {
                "skipped_inputs": [],
                "client": "测试客户",
                "stage_1_standardize": {"route_artifact": ""},
            }
            runner.write_manifest = lambda: None

            original = orchestrator.S.standardize_file
            try:
                orchestrator.S.standardize_file = lambda _context: (_ for _ in ()).throw(
                    orchestrator.S.SourceFormatQualityError(
                        "已识别为招商银行个人流水，但原始导出缺少必需可选列：对手信息。"
                        "请重新导出招商银行交易流水，并勾选“对手信息”"
                    )
                )
                with self.assertRaisesRegex(RuntimeError, "阶段一 QC 未通过.*对手信息"):
                    runner.stage_1_standardize()
            finally:
                orchestrator.S.standardize_file = original

            self.assertEqual(runner.manifest["skipped_inputs"], [])

    def test_stage_one_recursively_reads_nested_customer_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            nested = source / "新余分公司"
            nested.mkdir(parents=True)
            statement = nested / "流水.pdf"
            statement.write_bytes(b"%PDF-1.4\n")

            runner = orchestrator.Runner.__new__(orchestrator.Runner)
            runner.args = SimpleNamespace(folder=str(source), client="测试客户", account_type=None)
            runner.out_dir = str(root / "output")
            runner.run_dir = str(root)
            runner.manifest = {
                "skipped_inputs": [],
                "client": "测试客户",
                "stage_1_standardize": {"route_artifact": ""},
            }
            runner.write_manifest = lambda: None

            def standardize(context):
                work = Path(context.out_dir)
                output = work / "流水__pdf__standardized.csv"
                self._write_standardized_csv(output, statement.name)
                return str(output), "", {
                    "标准化统计": {"交易笔数": 1},
                    "路由信息": {},
                }

            with patch.object(orchestrator.S, "standardize_file", side_effect=standardize):
                result = runner.stage_1_standardize()

            self.assertEqual(result["processed_files"], 1)
            self.assertEqual(Path(result["standardized"][0]["input"]), statement)

    def test_stage_one_keeps_zero_row_output_for_validator_to_reject(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            empty_source = source / "空流水.pdf"
            valid_source = source / "有效流水.pdf"
            empty_source.write_bytes(b"%PDF-1.4\n")
            valid_source.write_bytes(b"%PDF-1.4\n")

            runner = orchestrator.Runner.__new__(orchestrator.Runner)
            runner.args = SimpleNamespace(folder=str(source), client="测试客户", account_type=None)
            runner.out_dir = str(root / "output")
            runner.run_dir = str(root)
            runner.manifest = {
                "skipped_inputs": [],
                "client": "测试客户",
                "stage_1_standardize": {"route_artifact": ""},
            }
            runner.write_manifest = lambda: None

            def standardize(context):
                work = Path(context.out_dir)
                stem = Path(context.path).stem
                output = work / f"{stem}__pdf__standardized.csv"
                if Path(context.path) == empty_source:
                    output.write_text(",".join(sorted(orchestrator.V.STD_REQUIRED)) + "\n", encoding="utf-8")
                    rows = 0
                else:
                    self._write_standardized_csv(output, valid_source.name)
                    rows = 1
                return str(output), "", {
                    "标准化统计": {"交易笔数": rows},
                    "路由信息": {},
                }

            with patch.object(orchestrator.S, "standardize_file", side_effect=standardize):
                result = runner.stage_1_standardize()

            self.assertEqual(result["processed_files"], 2)
            self.assertTrue((Path(runner.work_dir()) / "空流水__pdf__standardized.csv").exists())
            self.assertEqual(runner.manifest["skipped_inputs"], [])
            with self.assertRaisesRegex(orchestrator.V.ValidationError, "CSV 无交易数据"):
                orchestrator.V.validate_standardize(
                    runner.work_dir(),
                    file_routes=runner.load_stage_1_routes(),
                )

    def test_ccb_personal_coordinate_pdf_reads_all_transactions(self):
        source = (
            Path("/Users/tuzhi/Developer/ymb-skill-data/testdata")
            / "程旭" / "程旭建行2025.1-3月.pdf"
        )
        continuation = source.with_name("程旭建行2025.1-3月（2）.pdf")
        if not source.exists():
            self.skipTest("本地未提供建行个人横向 PDF 样本")

        file_kind, _preamble, rows, route = orchestrator.S.read_rows(str(source))

        self.assertEqual(file_kind, "pdf")
        self.assertEqual(route["reader_id"], "pdfplumber_coordinate_table")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "中国建设银行")
        self.assertEqual(rows[0][0], "序号")
        self.assertEqual(len(rows), 571)
        self.assertEqual(rows[1][0], "1")
        self.assertEqual(rows[-1][0], "570")
        self.assertEqual(rows[-1][4], "20250326")
        self.assertEqual(rows[-1][5], "-40,157.00")
        self.assertNotIn("生成时间", "".join(rows[-1]))

        _kind, _preamble, continuation_rows, continuation_route = (
            orchestrator.S.read_rows(str(continuation))
        )
        self.assertEqual(continuation_route["fingerprint_id"], route["fingerprint_id"])
        self.assertEqual(len(continuation_rows), 34)
        self.assertEqual(continuation_rows[1][0], "571")
        self.assertEqual(continuation_rows[-1][0], "603")
        self.assertEqual(continuation_rows[-1][4], "20250331")
        self.assertNotIn("生成时间", "".join(continuation_rows[-1]))

        with tempfile.TemporaryDirectory() as tmp:
            csv_path, _mapping_path, report = orchestrator.S.standardize_file(
                orchestrator.S.StandardizationContext(
                    path=str(source),
                    out_dir=tmp,
                    write_mapping=False,
                )
            )
            with Path(csv_path).open(encoding="utf-8-sig", newline="") as f:
                standardized = list(csv.DictReader(f))

        self.assertEqual(report["标准化统计"]["交易笔数"], 570)
        self.assertEqual(len(standardized), 570)
        self.assertEqual(standardized[0]["本方名称"], "程旭")
        self.assertEqual(standardized[0]["本方账户"], "6236682020001828281")
        self.assertEqual(standardized[0]["交易时间"], "2025-01-01")
        self.assertEqual(standardized[-1]["交易时间"], "2025-03-26")
        self.assertEqual(standardized[-1]["交易金额"], "-40157.0")

    def test_ccb_personal_portrait_pdf_keeps_native_table_reader(self):
        source = (
            Path("/Users/tuzhi/Developer/ymb-skill-data/testdata")
            / "涂志" / "hqmx_20260604142056.pdf"
        )
        if not source.exists():
            self.skipTest("本地未提供建行个人竖向 PDF 样本")

        file_kind, _preamble, rows, route = orchestrator.S.read_rows(str(source))

        self.assertEqual(file_kind, "pdf")
        self.assertEqual(route["reader_id"], "pdfplumber_table")
        self.assertEqual(route["fingerprint_id"], "md5:6c51495092e9abac017b130c6e41991d")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(len(rows), 760)
        self.assertEqual(rows[1][0], "1")
        self.assertEqual(rows[-1][0], "759")

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

    def test_runner_uses_extracted_zip_snapshot_as_input(self):
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
            )
            runner = orchestrator.Runner(args)

            self.assertEqual(Path(runner.args.folder), Path(runner.run_dir) / "input")
            self.assertEqual(
                (Path(runner.args.folder) / "流水.csv").read_text(encoding="utf-8"),
                "raw",
            )

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
            runner.run_dir = str(tmp_path)
            runner.manifest = {
                "skipped_inputs": [],
                "client": "tokenized_batch_bundle",
                "stage_1_standardize": {"route_artifact": ""},
            }
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
            self.assertFalse((work / "001_raw-a__mapping.json").exists())
            self.assertFalse((work / "002_raw-b__mapping.json").exists())
            self.assertNotIn("file_routes", runner.manifest["stage_1_standardize"])
            route_artifact = runner.manifest["stage_1_standardize"]["route_artifact"]
            routes = json.loads((tmp_path / route_artifact).read_text(encoding="utf-8"))
            self.assertEqual(set(routes), {"001_raw-a__standardized.csv", "002_raw-b__standardized.csv"})
            self.assertTrue(all(route["yaml_match_status"] == "unmatched" for route in routes.values()))
            validation = orchestrator.V.validate_standardize(str(work), file_routes=routes)
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
            runner.manifest_path = str(runtime)

            runner.copy_stage_manifest()

            data = json.loads(runtime.read_text(encoding="utf-8"))
            stage = data["stage_1_standardize"]
            self.assertFalse(stage["ai_fallback_used"])
            self.assertEqual(stage["ai_fallback_artifacts"], [])
            self.assertNotIn("ai_fallback_dir", stage)
            self.assertNotIn("started_at", stage)
            self.assertNotIn("duration_seconds", stage)
            self.assertNotIn("script", stage)
            self.assertNotIn("validator", stage)
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
            self.assertNotIn("script", inherited)
            self.assertNotIn("validator", inherited)

    def test_load_parent_run_context_rejects_missing_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "parent run 不存在"):
                orchestrator.load_parent_run_context(str(Path(tmp) / "runs"), "missing-run")

if __name__ == "__main__":
    unittest.main()
