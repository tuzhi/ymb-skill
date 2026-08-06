#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把平台 Skill 和可选 WorkBuddy 专家打包成 .zip 归档。

归档写入 <skill_dir>/dist，并以 skill 目录作为 zip 顶层目录。
源码测试、运行产物、原始流水、独立工具、dist 目录和本打包脚本自身会被排除。
"""
import argparse
import json
import os
import tomllib
import zipfile
from pathlib import Path


PACKAGER_RELATIVE = Path("tools") / "release" / "package_skill.py"
CORE_PACKAGE_SOURCE_RELATIVE = Path("ymb-standardization-core")
CORE_PACKAGE_ARCHIVE_RELATIVE = Path("packages") / "ymb_standardization_core"
INCLUDED_TOP_LEVEL_FILES = {
    "SKILL.md",
    "requirements.txt",
}
INCLUDED_TOP_LEVEL_DIRS = {
    "agents",
    "assets",
    "harness",
    "roles",
    "runtime",
    "scripts",
    "services",
}
INCLUDED_REFERENCE_FILES = {
    Path("references") / "prompt-1-字段映射.md",
    Path("references") / "附件A-标准化字段说明.md",
}
HARNESS_SKILL_NAME = "bank-statement-standardization"
EXPERT_NAME = "bank-statement-standardization-expert"
EXPERT_SOURCE_RELATIVE = Path("workbuddy-experts") / EXPERT_NAME
SUPPORTED_PLATFORMS = ("macos", "windows")
PLATFORM_LAUNCHERS = {
    "macos": Path("scripts") / "run-posix.sh",
    "windows": Path("scripts") / "run-windows.cmd",
}
PLATFORM_COMMAND_PLACEHOLDER = "{{PLATFORM_COMMAND}}"
PLATFORM_COMMANDS = {
    "macos": (
        'sh "${CODEBUDDY_SKILL_DIR}/scripts/run-posix.sh" '
        '"${CODEBUDDY_SKILL_DIR}/scripts/orchestrator.py" run'
    ),
    "windows": (
        'cmd.exe /d /s /c call '
        '"${CODEBUDDY_SKILL_DIR}\\scripts\\run-windows.cmd" '
        '"${CODEBUDDY_SKILL_DIR}\\scripts\\orchestrator.py" run'
    ),
}


def workspace_version(repo_root):
    pyproject = Path(repo_root).resolve() / "pyproject.toml"
    if not pyproject.is_file():
        raise FileNotFoundError(f"workspace pyproject.toml not found: {pyproject}")
    with pyproject.open("rb") as stream:
        version = str(tomllib.load(stream).get("project", {}).get("version") or "").strip()
    if not version:
        raise ValueError("pyproject.toml 缺少 project.version")
    return version


def _is_excluded(relative_path):
    parts = relative_path.parts
    if not parts:
        return True
    if any(
        part in {
            ".DS_Store",
            "dist",
            "testdata",
            "testoutput",
            "runs",
            "原始流水数据",
            "tools",
            ".claude",
            "tests",
            "__pycache__",
            "build",
        }
        for part in parts
    ):
        return True
    if any(part.endswith(".egg-info") for part in parts):
        return True
    if relative_path == PACKAGER_RELATIVE:
        return True
    return False


def _is_included(relative_path):
    """发布包只允许进入明确声明的运行时文件和目录。"""
    parts = relative_path.parts
    if not parts or _is_excluded(relative_path):
        return False
    if len(parts) == 1 and parts[0] in INCLUDED_TOP_LEVEL_FILES:
        return True
    if relative_path in INCLUDED_REFERENCE_FILES:
        return True
    if relative_path == Path("references"):
        return True
    return parts[0] in INCLUDED_TOP_LEVEL_DIRS


def _render_platform_skill(content, platform):
    """把单一 SKILL.md 的启动命令变量渲染为指定平台命令。"""
    if platform not in SUPPORTED_PLATFORMS:
        raise ValueError(f"unsupported platform: {platform}")
    if content.count(PLATFORM_COMMAND_PLACEHOLDER) != 1:
        raise ValueError("SKILL.md 必须且只能包含一个平台入口变量")
    rendered = content.replace(
        PLATFORM_COMMAND_PLACEHOLDER,
        PLATFORM_COMMANDS[platform],
    )
    if PLATFORM_COMMAND_PLACEHOLDER in rendered:
        raise ValueError("SKILL.md 平台入口变量渲染失败")
    return rendered.rstrip() + "\n"


def _include_platform_file(relative_path, platform):
    if not platform:
        return True
    selected_launcher = PLATFORM_LAUNCHERS[platform]
    platform_launchers = set(PLATFORM_LAUNCHERS.values())
    return relative_path not in platform_launchers or relative_path == selected_launcher


def package_skill(skill_dir, output=None, platform=None):
    root = Path(skill_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"skill directory not found: {root}")
    if not (root / "SKILL.md").is_file():
        raise FileNotFoundError(f"SKILL.md not found in skill directory: {root}")
    if platform is not None and platform not in SUPPORTED_PLATFORMS:
        raise ValueError(f"unsupported platform: {platform}")

    if output:
        archive = Path(output).resolve()
    else:
        dist_dir = root / "dist"
        dist_dir.mkdir(exist_ok=True)
        archive = dist_dir / f"{root.name}.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)

    top = root.name
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for current, dirs, files in os.walk(root):
            current_path = Path(current)
            rel_dir = current_path.relative_to(root)
            dirs[:] = sorted(d for d in dirs if _is_included(rel_dir / d))
            for filename in sorted(files):
                rel_file = rel_dir / filename
                if not _is_included(rel_file):
                    continue
                if not _include_platform_file(rel_file, platform):
                    continue
                source_path = current_path / filename
                archive_path = Path(top) / rel_file
                if platform and rel_file == Path("SKILL.md"):
                    content = source_path.read_text(encoding="utf-8")
                    zf.writestr(str(archive_path), _render_platform_skill(content, platform))
                else:
                    zf.write(source_path, archive_path)
        repo_root = root.parent
        core_project = repo_root / CORE_PACKAGE_SOURCE_RELATIVE
        core_source = core_project / "src"
        if root.name == "bank-statement-standardization" and core_source.is_dir():
            pyproject = core_project / "pyproject.toml"
            if pyproject.is_file():
                zf.write(
                    pyproject,
                    Path(top) / CORE_PACKAGE_ARCHIVE_RELATIVE / "pyproject.toml",
                )
            for current, dirs, files in os.walk(core_source):
                current_path = Path(current)
                rel_dir = current_path.relative_to(core_source)
                dirs[:] = sorted(
                    d for d in dirs
                    if d not in {"__pycache__", "build", "dist", ".DS_Store"}
                    and not d.endswith(".egg-info")
                )
                for filename in sorted(files):
                    if filename == ".DS_Store" or filename.endswith((".pyc", ".pyo")):
                        continue
                    rel_file = rel_dir / filename
                    archive_path = Path(top) / CORE_PACKAGE_ARCHIVE_RELATIVE / rel_file
                    zf.write(current_path / filename, archive_path)

    return archive


def package_harness_skills(repo_root, output_dir):
    repo_root = Path(repo_root).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "harness-skill-hashes.json").unlink(missing_ok=True)
    version = workspace_version(repo_root)
    obsolete_names = (
        f"{HARNESS_SKILL_NAME}.zip",
        "bank-statement-fallback.zip",
        "bank-statement-audit.zip",
    )
    for name in obsolete_names:
        (output_dir / name).unlink(missing_ok=True)
    for pattern in (
        f"{HARNESS_SKILL_NAME}_v*.zip",
        "bank-statement-fallback_v*.zip",
        "bank-statement-audit_v*.zip",
    ):
        for obsolete in output_dir.glob(pattern):
            obsolete.unlink()
    return tuple(
        package_skill(
            repo_root / HARNESS_SKILL_NAME,
            output=output_dir / f"{HARNESS_SKILL_NAME}_v{version}_{platform}.zip",
            platform=platform,
        )
        for platform in SUPPORTED_PLATFORMS
    )


def package_workbuddy_expert(repo_root, output_dir):
    repo_root = Path(repo_root).resolve()
    output_dir = Path(output_dir).resolve()
    source = repo_root / EXPERT_SOURCE_RELATIVE
    plugin_path = source / ".workbuddy-plugin" / "plugin.json"
    if not plugin_path.is_file():
        raise FileNotFoundError(f"expert plugin.json not found: {plugin_path}")
    plugin = json.loads(plugin_path.read_text(encoding="utf-8"))
    version = str(plugin.get("version") or "").strip()
    if not version:
        raise ValueError("expert plugin.json 缺少 version")
    output_dir.mkdir(parents=True, exist_ok=True)
    for obsolete in output_dir.glob(f"{EXPERT_NAME}_v*.zip"):
        obsolete.unlink()
    archive = output_dir / f"{EXPERT_NAME}_v{version}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for current, dirs, files in os.walk(source):
            current_path = Path(current)
            rel_dir = current_path.relative_to(source)
            dirs[:] = sorted(d for d in dirs if d not in {"__pycache__"})
            for filename in sorted(files):
                if filename in {".DS_Store"} or filename.endswith((".pyc", ".pyo")):
                    continue
                rel_file = rel_dir / filename
                zf.write(current_path / filename, Path(source.name) / rel_file)
    return archive


def main():
    default_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="把 Codex/WorkBuddy skill 打包到 dist/*.zip")
    parser.add_argument("skill_dir", nargs="?", default=str(default_root),
                        help="skill directory to package; defaults to this skill directory")
    parser.add_argument("--output", help="optional output .zip path")
    args = parser.parse_args()

    selected_root = Path(args.skill_dir).resolve()
    if selected_root == default_root and not args.output:
        archives = package_harness_skills(default_root.parent, default_root / "dist")
        archives += (package_workbuddy_expert(default_root.parent, default_root / "dist"),)
    else:
        archives = (package_skill(args.skill_dir, output=args.output),)
    for archive in archives:
        print(archive)


if __name__ == "__main__":
    main()
