import csv
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "qa" / "run_full_test.py"


def load_module():
    spec = importlib.util.spec_from_file_location("run_full_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FullTestOutputTests(unittest.TestCase):
    def test_default_output_dir_is_testoutput_timestamp_sibling_of_testdata(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            testdata = Path(tmp) / "testdata"
            testdata.mkdir()

            run_dir = module.create_run_dir(testdata, run_id="20260630203045")

        self.assertEqual(run_dir, Path(tmp) / "testoutput" / "20260630203045")

    def test_iter_client_dirs_skips_generated_and_internal_directories(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in [
                ".git",
                "_support_matrix_work",
                "_分发_已清洗_待分析_20260630T185846",
                "已清洗_待分析02",
                "客户A",
                "客户B",
            ]:
                (root / name).mkdir()

            clients = [path.name for path in module.iter_client_dirs(root)]

        self.assertEqual(clients, ["客户A", "客户B"])

    def test_main_packages_deliverables_before_support_matrix(self):
        module = load_module()
        calls = []

        def fake_create_run_dir(testdata_root, run_id=None, output_root=None):
            calls.append(("create_run_dir", Path(testdata_root), run_id, output_root))
            return Path("testoutput/20260630203045")

        def fake_run_support_matrix_from_package_work(testdata_root, run_dir, package_work_root):
            calls.append((
                "run_support_matrix_from_package_work",
                Path(testdata_root),
                Path(run_dir),
                Path(package_work_root),
            ))
            return Path(run_dir) / "support_matrix.xlsx", Path(run_dir) / "baseline_summary.json"

        def fake_run_package_deliverables(testdata_root, run_dir):
            calls.append(("run_package_deliverables", Path(testdata_root), Path(run_dir)))
            return Path(run_dir) / "_summary.csv", [{"status": "PASS"}]

        original_create = module.create_run_dir
        original_support = module.run_support_matrix_from_package_work
        original_package = module.run_package_deliverables
        try:
            module.create_run_dir = fake_create_run_dir
            module.run_support_matrix_from_package_work = fake_run_support_matrix_from_package_work
            module.run_package_deliverables = fake_run_package_deliverables
            module.main(["--testdata-root", "testdata", "--run-id", "20260630203045"])
        finally:
            module.create_run_dir = original_create
            module.run_support_matrix_from_package_work = original_support
            module.run_package_deliverables = original_package

        self.assertEqual(calls, [
            ("create_run_dir", Path("testdata"), "20260630203045", None),
            ("run_package_deliverables", Path("testdata"), Path("testoutput/20260630203045")),
            (
                "run_support_matrix_from_package_work",
                Path("testdata"),
                Path("testoutput/20260630203045"),
                Path("testoutput/20260630203045/_package_work"),
            ),
        ])

    def test_skip_package_argument_is_not_supported(self):
        module = load_module()

        def fail_if_called(*args, **kwargs):
            raise AssertionError("full test should not run when CLI parsing fails")

        original_support = module.run_support_matrix_from_package_work
        original_package = module.run_package_deliverables
        try:
            module.run_support_matrix_from_package_work = fail_if_called
            module.run_package_deliverables = fail_if_called
            with self.assertRaises(SystemExit):
                module.main(["--skip-package"])
        finally:
            module.run_support_matrix_from_package_work = original_support
            module.run_package_deliverables = original_package

    def test_write_summary_csv_lives_in_run_dir(self):
        module = load_module()
        rows = [
            {"client": "客户A", "status": "PASS", "file_count": 1, "files": "客户A_已清洗_待分析.xlsx", "log": "001_客户A.log", "returncode": ""},
            {"client": "客户B", "status": "FAIL", "file_count": 0, "files": "", "log": "002_客户B.log", "returncode": 1},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            summary = module.write_summary_csv(Path(tmp), rows)
            with summary.open(encoding="utf-8-sig", newline="") as f:
                loaded = list(csv.DictReader(f))

        self.assertEqual(summary.name, "_summary.csv")
        self.assertEqual(loaded[0]["client"], "客户A")
        self.assertEqual(loaded[1]["status"], "FAIL")

    def test_product_deliverables_are_copied_to_run_dir(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work_dir = root / "work"
            run_dir = root / "testoutput" / "20260630203045"
            artifacts = work_dir / "runs" / "run-1" / "artifacts"
            artifacts.mkdir(parents=True)
            run_dir.mkdir(parents=True)
            deliverable = artifacts / "客户A_已清洗_待分析.xlsx"
            deliverable.write_text("xlsx", encoding="utf-8")

            copied = module._copy_deliverables(work_dir, run_dir, 1)

            self.assertEqual(copied, ["客户A_已清洗_待分析.xlsx"])
            self.assertTrue((run_dir / "客户A_已清洗_待分析.xlsx").exists())
            self.assertFalse((run_dir.parent / "客户A_已清洗_待分析.xlsx").exists())

    def test_package_client_passes_file_sleep_to_orchestrator(self):
        module = load_module()
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 1, stdout="stopped")

        original_run = module.subprocess.run
        try:
            module.subprocess.run = fake_run
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                client = root / "testdata" / "客户A"
                client.mkdir(parents=True)
                module.package_one_client(
                    1,
                    client,
                    root / "testoutput",
                    root / "work",
                    file_sleep_seconds=2,
                )
        finally:
            module.subprocess.run = original_run

        self.assertIn("--file-sleep-seconds", calls[0])
        self.assertEqual(calls[0][calls[0].index("--file-sleep-seconds") + 1], "2.0")


if __name__ == "__main__":
    unittest.main()
