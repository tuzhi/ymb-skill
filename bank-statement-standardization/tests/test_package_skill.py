import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SCRIPT = SKILL_ROOT / "scripts" / "package_skill.py"


class PackageSkillTest(unittest.TestCase):
    def test_package_excludes_dist_and_packager(self):
        spec = importlib.util.spec_from_file_location("package_skill", PACKAGE_SCRIPT)
        package_skill = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(package_skill)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "demo-skill"
            (root / "scripts").mkdir(parents=True)
            (root / "dist").mkdir()
            (root / "SKILL.md").write_text("# Demo\n", encoding="utf-8")
            (root / "scripts" / "standardize.py").write_text("print('ok')\n", encoding="utf-8")
            (root / "scripts" / "package_skill.py").write_text("print('pack')\n", encoding="utf-8")
            (root / "dist" / "old.zip").write_text("old\n", encoding="utf-8")

            archive = package_skill.package_skill(root)

            self.assertEqual(archive, root / "dist" / "demo-skill.zip")
            with zipfile.ZipFile(archive) as zf:
                names = set(zf.namelist())

            self.assertIn("demo-skill/SKILL.md", names)
            self.assertIn("demo-skill/scripts/standardize.py", names)
            self.assertNotIn("demo-skill/scripts/package_skill.py", names)
            self.assertFalse(any(name.startswith("demo-skill/dist/") for name in names))


if __name__ == "__main__":
    unittest.main()
