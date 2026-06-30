import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SCRIPT = SKILL_ROOT / "scripts" / "package_skill.py"


class PackageSkillTest(unittest.TestCase):
    def test_package_includes_shared_standardization_core(self):
        spec = importlib.util.spec_from_file_location("package_skill", PACKAGE_SCRIPT)
        package_skill = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(package_skill)

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


if __name__ == "__main__":
    unittest.main()
