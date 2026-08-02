#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把当前 skill 打包成 .zip 归档。

归档写入 <skill_dir>/dist，并以 skill 目录作为 zip 顶层目录。
源码测试、运行产物、原始流水、独立工具、dist 目录和本打包脚本自身会被排除。
"""
import argparse
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
HARNESS_SKILL_NAME = "bank-statement-standardization"


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
    return parts[0] in INCLUDED_TOP_LEVEL_DIRS


def package_skill(skill_dir, output=None):
    root = Path(skill_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"skill directory not found: {root}")
    if not (root / "SKILL.md").is_file():
        raise FileNotFoundError(f"SKILL.md not found in skill directory: {root}")

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
                zf.write(current_path / filename, Path(top) / rel_file)
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
                    if d not in {"__pycache__", "build", "dist"} and not d.endswith(".egg-info")
                )
                for filename in sorted(files):
                    if filename.endswith((".pyc", ".pyo")):
                        continue
                    rel_file = rel_dir / filename
                    archive_path = Path(top) / CORE_PACKAGE_ARCHIVE_RELATIVE / rel_file
                    zf.write(current_path / filename, archive_path)

    return archive


def package_harness_skill(repo_root, output_dir):
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
    return package_skill(
        repo_root / HARNESS_SKILL_NAME,
        output=output_dir / f"{HARNESS_SKILL_NAME}_v{version}.zip",
    )


def main():
    default_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="把 Codex/WorkBuddy skill 打包到 dist/*.zip")
    parser.add_argument("skill_dir", nargs="?", default=str(default_root),
                        help="skill directory to package; defaults to this skill directory")
    parser.add_argument("--output", help="optional output .zip path")
    args = parser.parse_args()

    selected_root = Path(args.skill_dir).resolve()
    if selected_root == default_root and not args.output:
        archive = package_harness_skill(default_root.parent, default_root / "dist")
    else:
        archive = package_skill(args.skill_dir, output=args.output)
    print(archive)


if __name__ == "__main__":
    main()
