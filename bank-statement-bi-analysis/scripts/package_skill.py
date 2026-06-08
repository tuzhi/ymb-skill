#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Package this skill as a .zip archive.

The archive is written to <skill_dir>/dist and contains the skill directory as
the top-level folder. The dist directory and this packager script are excluded.
"""
import argparse
import os
import zipfile
from pathlib import Path


PACKAGER_RELATIVE = Path("scripts") / "package_skill.py"


def _is_excluded(relative_path):
    parts = relative_path.parts
    if not parts:
        return True
    if any(part in {"dist", "testdata", "runs", ".claude", "tests", "__pycache__"} for part in parts):
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

    return archive


def main():
    default_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Package this skill into dist/*.zip")
    parser.add_argument("skill_dir", nargs="?", default=str(default_root),
                        help="skill directory to package; defaults to this skill directory")
    parser.add_argument("--output", help="optional output .zip path")
    args = parser.parse_args()

    archive = package_skill(args.skill_dir, output=args.output)
    print(archive)


if __name__ == "__main__":
    main()
