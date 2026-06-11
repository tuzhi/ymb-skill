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
            (root / "scripts" / "__pycache__").mkdir()
            (root / "dist").mkdir()
            (root / ".claude").mkdir()
            (root / "runs").mkdir()
            (root / "tests").mkdir()
            (root / "testdata").mkdir()
            (root / "__pycache__").mkdir()
            (root / "SKILL.md").write_text("# Demo\n", encoding="utf-8")
            (root / "scripts" / "standardize.py").write_text("print('ok')\n", encoding="utf-8")
            (root / "scripts" / "package_skill.py").write_text("print('pack')\n", encoding="utf-8")
            (root / "dist" / "old.zip").write_text("old\n", encoding="utf-8")
            (root / ".claude" / "settings.json").write_text("{}\n", encoding="utf-8")
            (root / "runs" / "run.log").write_text("log\n", encoding="utf-8")
            (root / "tests" / "test_demo.py").write_text("pass\n", encoding="utf-8")
            (root / "testdata" / "sample.csv").write_text("a,b\n", encoding="utf-8")
            (root / "__pycache__" / "demo.pyc").write_bytes(b"cache")
            (root / "scripts" / "__pycache__" / "standardize.pyc").write_bytes(b"cache")

            archive = package_skill.package_skill(root)

            self.assertEqual(archive, root / "dist" / "demo-skill.zip")
            with zipfile.ZipFile(archive) as zf:
                names = set(zf.namelist())

            self.assertIn("demo-skill/SKILL.md", names)
            self.assertIn("demo-skill/scripts/standardize.py", names)
            self.assertNotIn("demo-skill/scripts/package_skill.py", names)
            self.assertFalse(any(name.startswith("demo-skill/dist/") for name in names))
            self.assertFalse(any(name.startswith("demo-skill/.claude/") for name in names))
            self.assertFalse(any(name.startswith("demo-skill/runs/") for name in names))
            self.assertFalse(any(name.startswith("demo-skill/tests/") for name in names))
            self.assertFalse(any(name.startswith("demo-skill/testdata/") for name in names))
            self.assertFalse(any(name.startswith("demo-skill/__pycache__/") for name in names))
            self.assertFalse(any("/__pycache__/" in name for name in names))

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
