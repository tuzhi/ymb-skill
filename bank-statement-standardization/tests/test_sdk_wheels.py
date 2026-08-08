import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = REPO_ROOT / "sdk" / "build_wheels.py"


def load_builder():
    spec = importlib.util.spec_from_file_location("build_sdk_wheels", BUILD_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SdkWheelTests(unittest.TestCase):
    def test_builds_two_wheels_with_public_api_and_package_resources(self):
        builder = load_builder()
        with tempfile.TemporaryDirectory() as temporary:
            wheels = builder.build_wheels(Path(temporary))
            self.assertEqual(len(wheels), 2)
            names = {wheel.name for wheel in wheels}
            self.assertIn(
                "ymb_statement_standardization_sdk-1.0.0-py3-none-any.whl",
                names,
            )
            self.assertIn(
                "ymb_bank_statement_bi_sdk-1.0.0-py3-none-any.whl",
                names,
            )

            standardization = next(wheel for wheel in wheels if "standardization" in wheel.name)
            bi = next(wheel for wheel in wheels if "_bi_" in wheel.name)
            with zipfile.ZipFile(standardization) as archive:
                files = set(archive.namelist())
                self.assertIn("ymb_statement_standardization/__init__.py", files)
                self.assertIn(
                    "ymb_statement_standardization/assets/tag_rules.csv",
                    files,
                )
                self.assertIn(
                    "ymb_standardization_core/config/routing/routing_rules.yaml",
                    files,
                )
                runner = archive.read(
                    "ymb_statement_standardization/runtime/runner.py"
                ).decode("utf-8")
                self.assertIn(
                    "from ymb_statement_standardization.runtime import deliverable",
                    runner,
                )
            with zipfile.ZipFile(bi) as archive:
                files = set(archive.namelist())
                self.assertIn(
                    "bank_statement_bi_analysis/engine/build_bi_report_v4.py",
                    files,
                )
                service = archive.read(
                    "bank_statement_bi_analysis/service.py"
                ).decode("utf-8")
                self.assertIn("from .engine import build_bi_report_v4", service)

            install_root = Path(temporary) / "installed"
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--no-deps",
                    "--disable-pip-version-check",
                    "--target",
                    str(install_root),
                    *(str(wheel) for wheel in wheels),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(install_root)
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "from importlib.resources import files; "
                        "from ymb_statement_standardization import StatementService, YamlRuleService; "
                        "from bank_statement_bi_analysis import BiAnalysisService; "
                        "assert StatementService.__module__.startswith('ymb_statement_standardization.'); "
                        "assert YamlRuleService.deserialize(files('ymb_standardization_core')"
                        ".joinpath('config/routing/routing_rules.yaml').read_text(encoding='utf-8')); "
                        "assert BiAnalysisService()._engine().__name__.endswith('build_bi_report_v4')"
                    ),
                ],
                cwd=temporary,
                env=environment,
                check=True,
            )


if __name__ == "__main__":
    unittest.main()
