import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SCRIPT = SKILL_ROOT / "tools" / "release" / "package_skill.py"


class PackageSkillTest(unittest.TestCase):
    def load_package_module(self):
        spec = importlib.util.spec_from_file_location("package_skill", PACKAGE_SCRIPT)
        package_skill = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(package_skill)
        return package_skill

    def test_package_includes_shared_standardization_core(self):
        package_skill = self.load_package_module()

        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "skill.zip"
            package_skill.package_skill(SKILL_ROOT, output=archive)
            with zipfile.ZipFile(archive) as zf:
                names = set(zf.namelist())

        self.assertIn(
            "bank-statement-standardization/packages/ymb_standardization_core/pyproject.toml",
            names,
        )
        self.assertIn(
            "bank-statement-standardization/packages/ymb_standardization_core/"
            "ymb_standardization_core/core.py",
            names,
        )

    def test_package_excludes_runtime_raw_data_and_independent_tools(self):
        package_skill = self.load_package_module()

        for path in (
            ".DS_Store",
            "testdata/sample.xlsx",
            "testoutput/run/final.xlsx",
            "runs/run-id/manifest.json",
            "原始流水数据/source.xlsx",
            "tools/qa/audit_testdata_support.py",
        ):
            with self.subTest(path=path):
                self.assertTrue(package_skill._is_excluded(Path(path)))

    def test_package_uses_runtime_allowlist(self):
        package_skill = self.load_package_module()

        self.assertTrue(package_skill._is_included(Path("scripts/orchestrator.py")))
        self.assertFalse((SKILL_ROOT / "scripts" / "standardize.py").exists())
        self.assertTrue(package_skill._is_included(Path("runtime/standardize.py")))
        self.assertTrue(package_skill._is_included(Path("runtime/integrate.py")))
        self.assertFalse(package_skill._is_included(Path("tools/qa/run_full_test.py")))
        self.assertFalse(package_skill._is_included(Path("AGENTS.md")))


if __name__ == "__main__":
    unittest.main()
