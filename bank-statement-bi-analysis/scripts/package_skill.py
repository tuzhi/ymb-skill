#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Package this skill for distribution.

Writes two deliverables to <skill_dir>/dist:
  1. <name>/       — clean project folder (drop into any skills directory)
  2. <name>.skill  — single-file archive (zip) of the same content
The dist directory, caches, .DS_Store and this packager script are excluded.
"""
import argparse
import os
import shutil
import zipfile
from pathlib import Path


PACKAGER_RELATIVE = Path("scripts") / "package_skill.py"


def _is_excluded(relative_path):
    parts = relative_path.parts
    if not parts:
        return True
    if any(part in {"dist", "testdata", "runs", ".claude", "tests", "__pycache__"} for part in parts):
        return True
    if parts[-1] in {".DS_Store"} or parts[-1].endswith((".skill", ".pyc")):
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
    archive = Path(output).resolve() if output else dist_dir / f"{root.name}.skill"
    archive.parent.mkdir(parents=True, exist_ok=True)

    included = []
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        rel_dir = current_path.relative_to(root)
        dirs[:] = sorted(d for d in dirs if not _is_excluded(rel_dir / d))
        for filename in sorted(files):
            rel_file = rel_dir / filename
            if not _is_excluded(rel_file):
                included.append(rel_file)

    top = root.name
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel_file in included:
            zf.write(root / rel_file, Path(top) / rel_file)

    folder = archive.parent / top
    if folder.exists():
        shutil.rmtree(folder)
    for rel_file in included:
        dst = folder / rel_file
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel_file, dst)

    return archive, folder


def main():
    default_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Package this skill into dist/<name>.skill + dist/<name>/")
    parser.add_argument("skill_dir", nargs="?", default=str(default_root),
                        help="skill directory to package; defaults to this skill directory")
    parser.add_argument("--output", help="optional output .skill path")
    args = parser.parse_args()

    archive, folder = package_skill(args.skill_dir, output=args.output)
    print(archive)
    print(folder)


if __name__ == "__main__":
    main()
