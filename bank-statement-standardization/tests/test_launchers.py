import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
POSIX_LAUNCHER = SKILL_ROOT / "scripts" / "run-posix.sh"
WINDOWS_LAUNCHER = SKILL_ROOT / "scripts" / "run-windows.cmd"


class LauncherTest(unittest.TestCase):
    def test_posix_launcher_uses_explicit_python_and_returns_run_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            env["YMB_WORKBUDDY_PYTHON"] = sys.executable
            completed = subprocess.run(
                [
                    "sh", str(POSIX_LAUNCHER),
                    str(SKILL_ROOT / "scripts" / "orchestrator.py"),
                    "run",
                    "--folder", str(Path(tmp) / "missing"),
                ],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )

        result = json.loads(completed.stdout.splitlines()[-1])
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(result["next_action"], "REQUEST_USER")
        self.assertEqual(result["contract_version"], 1)

    def test_windows_launcher_has_only_bounded_python_candidates(self):
        content = WINDOWS_LAUNCHER.read_text(encoding="utf-8")

        self.assertIn("%YMB_WORKBUDDY_PYTHON%", content)
        self.assertIn("%USERPROFILE%\\.workbuddy\\binaries\\python\\envs\\python.exe", content)
        self.assertIn("envs\\default\\python.exe", content)
        self.assertIn("envs\\default\\Scripts\\python.exe", content)
        self.assertIn('"%YMB_PYTHON_BIN%" %*', content)
        self.assertNotIn("skill_entry.py", content)
        self.assertNotIn("for /r", content.lower())
        self.assertNotIn("dir /s", content.lower())


if __name__ == "__main__":
    unittest.main()
