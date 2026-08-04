import importlib.util
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
ENTRY_SCRIPT = SKILL_ROOT / "scripts" / "skill_entry.py"


class SkillEntryTest(unittest.TestCase):
    def load_module(self):
        spec = importlib.util.spec_from_file_location("skill_entry", ENTRY_SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def captured_result(self, module, argv):
        output = StringIO()
        with redirect_stdout(output):
            status = module.main(argv)
        return status, json.loads(output.getvalue())

    def test_missing_input_requests_user_without_scanning(self):
        module = self.load_module()
        for value in ("", "$ARGUMENTS"):
            with self.subTest(value=value):
                status, result = self.captured_result(module, ["--input", value])
                self.assertEqual(status, 0)
                self.assertEqual(result["next_action"], "REQUEST_USER")
                self.assertEqual(result["reason_code"], "INPUT_SOURCE_INVALID")

    def test_invalid_input_requests_user(self):
        module = self.load_module()
        status, result = self.captured_result(
            module,
            ["--input", "/path/that/does/not/exist"],
        )
        self.assertEqual(status, 0)
        self.assertEqual(result["next_action"], "REQUEST_USER")

    def test_valid_directory_returns_fast_execution_plan(self):
        module = self.load_module()
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input"
            input_path.mkdir()
            run_root = Path(tmp) / "runs"
            status, result = self.captured_result(
                module,
                ["--input", str(input_path), "--run-root", str(run_root)],
            )

            self.assertEqual(status, 0)
            self.assertEqual(result["status"], "READY")
            self.assertEqual(result["next_action"], "EXECUTE_PIPELINE")
            self.assertEqual(result["action"]["timeout_ms"], 600_000)
            command = result["action"]["argv"]
            self.assertEqual(command[0], module.sys.executable)
            self.assertEqual(command[2], "run")
            self.assertEqual(command[3:5], ["--folder", str(input_path.resolve())])
            self.assertEqual(command[5:7], ["--run-root", str(run_root.resolve())])
            self.assertEqual(command[7:9], ["--run-id", result["run_id"]])
            self.assertEqual(command[9], "--execution-plan-key")
            self.assertFalse((run_root / result["run_id"]).exists())

    def test_repeated_plan_reuses_run_id_until_pipeline_releases_it(self):
        module = self.load_module()
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input"
            input_path.mkdir()
            run_root = Path(tmp) / "runs"
            argv = ["--input", str(input_path), "--run-root", str(run_root)]

            _, first = self.captured_result(module, argv)
            _, second = self.captured_result(module, argv)

            self.assertEqual(first["run_id"], second["run_id"])
            self.assertEqual(first["action"]["command"], second["action"]["command"])


if __name__ == "__main__":
    unittest.main()
