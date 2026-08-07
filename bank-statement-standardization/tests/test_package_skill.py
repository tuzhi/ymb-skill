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
            "repair-result.template.json",
            names,
        )
        self.assertIn(
            "bank-statement-standardization/packages/ymb_standardization_core/"
            "ymb_standardization_core/readers/routing/evidence.py",
            names,
        )
        self.assertNotIn(
            "bank-statement-standardization/scripts/skill_entry.py",
            names,
        )
        self.assertIn(
            "bank-statement-standardization/agents/openai.yaml",
            names,
        )
        self.assertIn("bank-statement-standardization/roles/repair.md", names)
        self.assertIn("bank-statement-standardization/references/prompt-1-字段映射.md", names)
        self.assertIn("bank-statement-standardization/references/附件A-标准化字段说明.md", names)
        self.assertNotIn("bank-statement-standardization/roles/fallback.md", names)
        self.assertNotIn("bank-statement-standardization/roles/audit.md", names)
        self.assertFalse(any(name.endswith("/.DS_Store") for name in names))

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
        self.assertFalse((SKILL_ROOT / "scripts" / "skill_entry.py").exists())
        self.assertFalse((SKILL_ROOT / "scripts" / "standardize.py").exists())
        self.assertTrue(package_skill._is_included(Path("runtime/standardize.py")))
        self.assertTrue(package_skill._is_included(Path("runtime/integrate.py")))
        self.assertTrue(package_skill._is_included(Path("runtime/qc.py")))
        self.assertTrue(package_skill._is_included(Path("services/statement_service.py")))
        self.assertTrue(package_skill._is_included(Path("harness/coordinator.py")))
        self.assertTrue(package_skill._is_included(
            Path("harness/protocols/v1/repair-result.template.json")
        ))
        self.assertTrue(package_skill._is_included(Path("roles/repair.md")))
        self.assertTrue(package_skill._is_included(Path("agents/openai.yaml")))
        self.assertFalse(package_skill._is_included(Path("tools/qa/run_full_test.py")))
        self.assertFalse(package_skill._is_included(Path("AGENTS.md")))
        self.assertTrue(package_skill._is_included(Path("references/prompt-1-字段映射.md")))
        self.assertTrue(package_skill._is_included(Path("references/附件A-标准化字段说明.md")))
        self.assertFalse(package_skill._is_included(Path("references/prompt-3-交易打标.md")))
        self.assertFalse(package_skill._is_included(Path("README.md")))
        self.assertFalse(package_skill._is_included(Path("版本说明.md")))
        self.assertFalse(package_skill._is_included(Path("测试验证报告.md")))

    def test_packages_two_platform_harness_skills(self):
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
                "bank-statement-standardization_v1.4.7_macos.zip",
                "bank-statement-standardization_v1.4.7_windows.zip",
                "bank-statement-standardization_v1.4.8.zip",
                "bank-statement-standardization_v1.4.8_macos.zip",
                "bank-statement-standardization_v1.4.8_windows.zip",
            )
            for name in stale_names:
                (Path(tmp) / name).write_text("stale", encoding="utf-8")
            archives = package_skill.package_harness_skills(SKILL_ROOT.parent, tmp)
            self.assertEqual(
                [archive.name for archive in archives],
                [
                    "bank-statement-standardization_v1.4.10_macos.zip",
                    "bank-statement-standardization_v1.4.10_windows.zip",
                ],
            )
            packages = {}
            for archive in archives:
                with zipfile.ZipFile(archive) as zf:
                    packages[archive.name] = {
                        "names": set(zf.namelist()),
                        "skill": zf.read(
                            "bank-statement-standardization/SKILL.md"
                        ).decode("utf-8"),
                        "manifest": json.loads(zf.read(
                            "bank-statement-standardization/assets/manifest.template.json"
                        )),
                    }

            macos = packages["bank-statement-standardization_v1.4.10_macos.zip"]
            windows = packages["bank-statement-standardization_v1.4.10_windows.zip"]
            posix_path = "bank-statement-standardization/scripts/run-posix.sh"
            windows_path = "bank-statement-standardization/scripts/run-windows.cmd"
            self.assertIn(posix_path, macos["names"])
            self.assertNotIn(windows_path, macos["names"])
            self.assertIn('!`sh "${CODEBUDDY_SKILL_DIR}/scripts/run-posix.sh"', macos["skill"])
            self.assertIn("${CODEBUDDY_SKILL_DIR}/scripts/run-posix.sh", macos["skill"])
            self.assertIn('--folder "$ARGUMENTS" --run-root "./runs"`', macos["skill"])
            self.assertNotIn("run-windows.cmd", macos["skill"])
            self.assertIn(windows_path, windows["names"])
            self.assertNotIn(posix_path, windows["names"])
            self.assertIn(
                '!`cmd.exe /d /s /c call "${CODEBUDDY_SKILL_DIR}\\scripts\\run-windows.cmd"',
                windows["skill"],
            )
            self.assertIn(
                "${CODEBUDDY_SKILL_DIR}\\scripts\\run-windows.cmd",
                windows["skill"],
            )
            self.assertIn('--folder "$ARGUMENTS" --run-root "./runs"`', windows["skill"])
            self.assertNotIn("run-posix.sh", windows["skill"])
            for packaged in packages.values():
                names = packaged["names"]
                skill = packaged["skill"]
                self.assertIn("bank-statement-standardization/SKILL.md", names)
                self.assertEqual(skill.count("!`"), 1)
                self.assertNotIn("{{PLATFORM_COMMAND}}", skill)
                self.assertNotIn("skill_entry.py", skill)
                self.assertIn("$ARGUMENTS", skill)
                self.assertIn("allowed-tools: Bash", skill)
                self.assertNotIn("allowed-tools: Bash, Agent", skill)
                self.assertIn("独立 Skill 不创建 Agent", skill)
                self.assertNotIn('subagent_type="general-purpose"', skill)
                self.assertEqual(packaged["manifest"]["skill"]["version"], "1.4.10")
                self.assertIn("bank-statement-standardization/roles/repair.md", names)
                self.assertIn("bank-statement-standardization/references/prompt-1-字段映射.md", names)
                self.assertNotIn("bank-statement-standardization/roles/fallback.md", names)
                self.assertNotIn("bank-statement-standardization/roles/audit.md", names)
                self.assertFalse(any(
                    name.startswith("bank-statement-fallback/") for name in names
                ))
            self.assertFalse((Path(tmp) / "harness-skill-hashes.json").exists())
            for name in stale_names:
                self.assertFalse((Path(tmp) / name).exists())

    def test_platform_entry_template_must_have_exactly_one_placeholder(self):
        package_skill = self.load_package_module()

        with self.assertRaisesRegex(ValueError, "必须且只能包含一个"):
            package_skill._render_platform_skill("no placeholder", "macos")
        with self.assertRaisesRegex(ValueError, "必须且只能包含一个"):
            package_skill._render_platform_skill(
                "{{PLATFORM_COMMAND}}\n{{PLATFORM_COMMAND}}",
                "windows",
            )
        with self.assertRaisesRegex(ValueError, "unsupported platform"):
            package_skill._render_platform_skill(
                "{{PLATFORM_COMMAND}}",
                "linux",
            )

    def test_packages_versioned_workbuddy_expert_separately(self):
        package_skill = self.load_package_module()
        with tempfile.TemporaryDirectory() as tmp:
            archive = package_skill.package_workbuddy_expert(SKILL_ROOT.parent, tmp)
            with zipfile.ZipFile(archive) as zf:
                names = set(zf.namelist())
                plugin = json.loads(zf.read(
                    "bank-statement-standardization-expert/.workbuddy-plugin/plugin.json"
                ))
                agent = zf.read(
                    "bank-statement-standardization-expert/agents/"
                    "bank-statement-standardization-expert.md"
                ).decode("utf-8")
                repair_agent = zf.read(
                    "bank-statement-standardization-expert/agents/"
                    "bank-statement-repair.md"
                ).decode("utf-8")
                avatar = zf.read(
                    "bank-statement-standardization-expert/avatars/expert.png"
                )

        self.assertEqual(archive.name, "bank-statement-standardization-expert_v1.0.3.zip")
        self.assertEqual(plugin["agentName"], "bank-statement-standardization-expert")
        self.assertEqual(plugin["agents"], [
            "./agents/bank-statement-standardization-expert.md",
            "./agents/bank-statement-repair.md",
        ])
        self.assertIn("skills: [bank-statement-standardization]", agent)
        self.assertNotIn("\ntools:", agent)
        self.assertNotIn('subagent_type="general-purpose"', agent)
        self.assertIn('subagent_type="bank-statement-repair"', agent)
        self.assertIn("`subAgent.sessionId`", agent)
        self.assertIn("不使用 `fork` 或 `resume`", agent)
        self.assertIn("必须是新 Agent", agent)
        self.assertNotIn("run-posix.sh", agent)
        self.assertNotIn("run-windows.cmd", agent)
        self.assertNotIn("\ntools:", repair_agent)
        self.assertIn("displayName:", repair_agent)
        self.assertIn("profession:", repair_agent)
        self.assertIn("maxTurns: 18", repair_agent)
        self.assertIn("role=repair", repair_agent)
        self.assertEqual(plugin["avatar"], "avatars/expert.png")
        self.assertEqual(len(plugin["tags"]), 3)
        self.assertEqual(len(plugin["quickPrompts"]), 3)
        self.assertLessEqual(len(plugin["displayDescription"]["zh"]), 50)
        self.assertGreaterEqual(len(plugin["displayDescription"]["zh"]), 40)
        self.assertTrue(avatar.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(int.from_bytes(avatar[16:20], "big"), 512)
        self.assertEqual(int.from_bytes(avatar[20:24], "big"), 512)
        self.assertLess(len(avatar), 500 * 1024)
        self.assertNotIn(
            "bank-statement-standardization-expert/.codebuddy-plugin/plugin.json",
            names,
        )
        self.assertEqual(len(names), 4)


if __name__ == "__main__":
    unittest.main()
