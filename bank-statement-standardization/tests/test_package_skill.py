import importlib.util
import json
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
                requirements = zf.read(
                    "bank-statement-standardization/requirements.txt"
                ).decode("utf-8")

        self.assertIn(
            "bank-statement-standardization/packages/ymb_standardization_core/pyproject.toml",
            names,
        )
        self.assertIn(
            "bank-statement-standardization/packages/ymb_standardization_core/"
            "ymb_standardization_core/core.py",
            names,
        )
        self.assertIn(
            "bank-statement-standardization/packages/ymb_standardization_core/"
            "ymb_standardization_core/config/routing/routing_rules.yaml",
            names,
        )
        self.assertIn(
            "bank-statement-standardization/services/statement_service.py",
            names,
        )
        self.assertIn("PyYAML>=6,<7", requirements)
        self.assertIn(
            "bank-statement-standardization/harness/coordinator.py",
            names,
        )
        self.assertIn(
            "bank-statement-standardization/harness/protocols/v1/"
            "fallback-result.template.json",
            names,
        )
        self.assertIn(
            "bank-statement-standardization/harness/protocols/v1/"
            "audit-result.template.json",
            names,
        )
        self.assertIn(
            "bank-statement-standardization/harness/protocols/v1/"
            "retry-decision.template.json",
            names,
        )
        self.assertIn(
            "bank-statement-standardization/packages/ymb_standardization_core/"
            "ymb_standardization_core/readers/routing/evidence.py",
            names,
        )
        self.assertIn(
            "bank-statement-standardization/scripts/skill_entry.py",
            names,
        )
        self.assertIn(
            "bank-statement-standardization/agents/openai.yaml",
            names,
        )
        self.assertIn("bank-statement-standardization/roles/fallback.md", names)
        self.assertIn("bank-statement-standardization/roles/audit.md", names)

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
        self.assertTrue(package_skill._is_included(Path("scripts/skill_entry.py")))
        self.assertFalse((SKILL_ROOT / "scripts" / "standardize.py").exists())
        self.assertTrue(package_skill._is_included(Path("runtime/standardize.py")))
        self.assertTrue(package_skill._is_included(Path("runtime/integrate.py")))
        self.assertTrue(package_skill._is_included(Path("runtime/qc.py")))
        self.assertTrue(package_skill._is_included(Path("services/statement_service.py")))
        self.assertTrue(package_skill._is_included(Path("harness/coordinator.py")))
        self.assertTrue(package_skill._is_included(
            Path("harness/protocols/v1/fallback-result.template.json")
        ))
        self.assertTrue(package_skill._is_included(Path("roles/fallback.md")))
        self.assertTrue(package_skill._is_included(Path("roles/audit.md")))
        self.assertTrue(package_skill._is_included(Path("agents/openai.yaml")))
        self.assertFalse(package_skill._is_included(Path("tools/qa/run_full_test.py")))
        self.assertFalse(package_skill._is_included(Path("AGENTS.md")))
        self.assertFalse(package_skill._is_included(Path("references/prompt-1-字段映射.md")))
        self.assertFalse(package_skill._is_included(Path("README.md")))
        self.assertFalse(package_skill._is_included(Path("版本说明.md")))
        self.assertFalse(package_skill._is_included(Path("测试验证报告.md")))

    def test_packages_one_versioned_harness_skill(self):
        package_skill = self.load_package_module()
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "harness-skill-hashes.json").write_text("stale", encoding="utf-8")
            stale_names = (
                "bank-statement-standardization.zip",
                "bank-statement-fallback.zip",
                "bank-statement-audit.zip",
                "bank-statement-standardization_v1.4.1.zip",
                "bank-statement-fallback_v1.4.1.zip",
                "bank-statement-audit_v1.4.1.zip",
                "bank-statement-fallback_v1.4.2.zip",
                "bank-statement-audit_v1.4.2.zip",
            )
            for name in stale_names:
                (Path(tmp) / name).write_text("stale", encoding="utf-8")
            archive = package_skill.package_harness_skill(SKILL_ROOT.parent, tmp)
            self.assertEqual(archive.name, "bank-statement-standardization_v1.4.4.zip")
            with zipfile.ZipFile(archive) as zf:
                names = set(zf.namelist())
                skill = zf.read("bank-statement-standardization/SKILL.md").decode("utf-8")
                manifest = json.loads(zf.read(
                    "bank-statement-standardization/assets/manifest.template.json"
                ))
            self.assertIn("bank-statement-standardization/SKILL.md", names)
            self.assertIn("!`", skill)
            self.assertIn("$ARGUMENTS", skill)
            self.assertIn("allowed-tools: Bash, Agent", skill)
            self.assertIn('subagent_type="general-purpose"', skill)
            self.assertIn("不得使用 `fork`", skill)
            self.assertIn("不得设置 `resume`", skill)
            self.assertIn("`subAgent.sessionId`", skill)
            self.assertIn("元数据缺失时停止", skill)
            self.assertEqual(manifest["skill"]["version"], "1.4.4")
            self.assertIn("bank-statement-standardization/roles/fallback.md", names)
            self.assertIn("bank-statement-standardization/roles/audit.md", names)
            self.assertFalse(any(name.startswith("bank-statement-fallback/") for name in names))
            self.assertFalse((Path(tmp) / "harness-skill-hashes.json").exists())
            for name in stale_names:
                self.assertFalse((Path(tmp) / name).exists())


if __name__ == "__main__":
    unittest.main()
