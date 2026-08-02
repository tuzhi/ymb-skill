import importlib.util
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch


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

    def test_valid_directory_executes_orchestrator_with_current_python(self):
        module = self.load_module()
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input"
            input_path.mkdir()
            run_root = Path(tmp) / "runs"
            with patch.object(module.os, "execv") as execv:
                status = module.main(
                    ["--input", str(input_path), "--run-root", str(run_root)]
                )

        self.assertEqual(status, 1)
        executable, command = execv.call_args.args
        self.assertEqual(executable, module.sys.executable)
        self.assertEqual(command[0], module.sys.executable)
        self.assertEqual(command[2], "run")
        self.assertEqual(command[3:5], ["--folder", str(input_path.resolve())])
        self.assertEqual(command[5:7], ["--run-root", str(run_root.resolve())])


if __name__ == "__main__":
    unittest.main()
