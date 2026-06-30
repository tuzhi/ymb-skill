import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_full_test.py"


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

    def test_run_support_matrix_writes_into_run_dir(self):
        module = load_module()
        calls = []

        def fake_build_outputs(testdata_root, output_dir, write_baseline):
            calls.append((Path(testdata_root), Path(output_dir), write_baseline))
            return Path(output_dir) / "support_matrix.xlsx", Path(output_dir) / "baseline_summary.json"

        original = module.audit.build_outputs
        try:
            module.audit.build_outputs = fake_build_outputs
            support, baseline = module.run_support_matrix(Path("testdata"), Path("testoutput/20260630203045"))
        finally:
            module.audit.build_outputs = original

        self.assertEqual(calls, [(Path("testdata"), Path("testoutput/20260630203045"), True)])
        self.assertEqual(support, Path("testoutput/20260630203045/support_matrix.xlsx"))
        self.assertEqual(baseline, Path("testoutput/20260630203045/baseline_summary.json"))

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
            work_dir.mkdir(parents=True)
            run_dir.mkdir(parents=True)
            deliverable = work_dir / "客户A_已清洗_待分析.xlsx"
            deliverable.write_text("xlsx", encoding="utf-8")

            copied = module._copy_deliverables(work_dir, run_dir, 1)

            self.assertEqual(copied, ["客户A_已清洗_待分析.xlsx"])
            self.assertTrue((run_dir / "客户A_已清洗_待分析.xlsx").exists())
            self.assertFalse((run_dir.parent / "客户A_已清洗_待分析.xlsx").exists())


if __name__ == "__main__":
    unittest.main()
