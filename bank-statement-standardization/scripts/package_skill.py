#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把当前 skill 打包成 .zip 归档。

归档写入 <skill_dir>/dist，并以 skill 目录作为 zip 顶层目录。
dist 目录和本打包脚本自身会被排除。
"""
import argparse
import os
import zipfile
from pathlib import Path


PACKAGER_RELATIVE = Path("scripts") / "package_skill.py"
CORE_PACKAGE_RELATIVE = Path("packages") / "ymb_standardization_core"


def _is_excluded(relative_path):
    parts = relative_path.parts
    if not parts:
        return True
    if any(part in {"dist", "testdata", "runs", ".claude", "tests", "__pycache__", "build"} for part in parts):
        return True
    if any(part.endswith(".egg-info") for part in parts):
        return True
    if relative_path == PACKAGER_RELATIVE:
        return True
    return False


def package_skill(skill_dir, output=None):
    root = Path(skill_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"skill directory not found: {root}")
    if not (root / "SKILL.md").is_file():
        raise FileNotFoundError(f"SKILL.md not found in skill directory: {root}")

    dist_dir = root / "dist"
    dist_dir.mkdir(exist_ok=True)
    archive = Path(output).resolve() if output else dist_dir / f"{root.name}.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)

    top = root.name
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for current, dirs, files in os.walk(root):
            current_path = Path(current)
            rel_dir = current_path.relative_to(root)
            dirs[:] = sorted(d for d in dirs if not _is_excluded(rel_dir / d))
            for filename in sorted(files):
                rel_file = rel_dir / filename
                if _is_excluded(rel_file):
                    continue
                zf.write(current_path / filename, Path(top) / rel_file)
        repo_root = root.parent
        core_package = repo_root / CORE_PACKAGE_RELATIVE
        if core_package.is_dir():
            for current, dirs, files in os.walk(core_package):
                current_path = Path(current)
                rel_dir = current_path.relative_to(core_package)
                dirs[:] = sorted(
                    d for d in dirs
                    if d not in {"__pycache__", "build"} and not d.endswith(".egg-info")
                )
                for filename in sorted(files):
                    if filename.endswith((".pyc", ".pyo")):
                        continue
                    rel_file = rel_dir / filename
                    archive_path = Path(top) / CORE_PACKAGE_RELATIVE / rel_file
                    zf.write(current_path / filename, archive_path)

    return archive


def main():
    default_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="把 Codex/WorkBuddy skill 打包到 dist/*.zip")
    parser.add_argument("skill_dir", nargs="?", default=str(default_root),
                        help="skill directory to package; defaults to this skill directory")
    parser.add_argument("--output", help="optional output .zip path")
    args = parser.parse_args()

    archive = package_skill(args.skill_dir, output=args.output)
    print(archive)


if __name__ == "__main__":
    main()
