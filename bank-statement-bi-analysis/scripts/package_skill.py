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
SUPPORTED_PLATFORMS = ("macos", "windows")
PLATFORM_LAUNCHERS = {
    "macos": Path("scripts") / "run-posix.sh",
    "windows": Path("scripts") / "run-windows.cmd",
}
PLATFORM_COMMAND_PLACEHOLDER = "{{PLATFORM_COMMAND}}"
PLATFORM_COMMANDS = {
    "macos": (
        'sh "${CODEBUDDY_SKILL_DIR}/scripts/run-posix.sh" '
        '"${CODEBUDDY_SKILL_DIR}/scripts/build_bi_report_v4.py"'
    ),
    "windows": (
        'cmd.exe /d /s /c call '
        '"${CODEBUDDY_SKILL_DIR}\\scripts\\run-windows.cmd" '
        '"${CODEBUDDY_SKILL_DIR}\\scripts\\build_bi_report_v4.py"'
    ),
}


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


def _render_platform_skill(content, platform):
    if platform not in SUPPORTED_PLATFORMS:
        raise ValueError(f"unsupported platform: {platform}")
    if content.count(PLATFORM_COMMAND_PLACEHOLDER) != 1:
        raise ValueError("SKILL.md 必须且只能包含一个平台入口变量")
    return content.replace(
        PLATFORM_COMMAND_PLACEHOLDER,
        PLATFORM_COMMANDS[platform],
    ).rstrip() + "\n"


def _include_platform_file(relative_path, platform):
    if not platform:
        return True
    selected = PLATFORM_LAUNCHERS[platform]
    launchers = set(PLATFORM_LAUNCHERS.values())
    return relative_path not in launchers or relative_path == selected


def package_skill(skill_dir, output=None, platform=None):
    root = Path(skill_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"skill directory not found: {root}")
    if not (root / "SKILL.md").is_file():
        raise FileNotFoundError(f"SKILL.md not found in skill directory: {root}")

    dist_dir = root / "dist"
    dist_dir.mkdir(exist_ok=True)
    if platform is not None and platform not in SUPPORTED_PLATFORMS:
        raise ValueError(f"unsupported platform: {platform}")
    default_name = f"{root.name}_{platform}.zip" if platform else f"{root.name}.skill"
    archive = Path(output).resolve() if output else dist_dir / default_name
    archive.parent.mkdir(parents=True, exist_ok=True)

    included = []
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        rel_dir = current_path.relative_to(root)
        dirs[:] = sorted(d for d in dirs if not _is_excluded(rel_dir / d))
        for filename in sorted(files):
            rel_file = rel_dir / filename
            if not _is_excluded(rel_file) and _include_platform_file(rel_file, platform):
                included.append(rel_file)

    top = root.name
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel_file in included:
            source = root / rel_file
            archive_path = Path(top) / rel_file
            if platform and rel_file == Path("SKILL.md"):
                zf.writestr(
                    str(archive_path),
                    _render_platform_skill(source.read_text(encoding="utf-8"), platform),
                )
            else:
                zf.write(source, archive_path)

    folder = archive.parent / top
    if folder.exists():
        shutil.rmtree(folder)
    for rel_file in included:
        dst = folder / rel_file
        dst.parent.mkdir(parents=True, exist_ok=True)
        if platform and rel_file == Path("SKILL.md"):
            dst.write_text(
                _render_platform_skill((root / rel_file).read_text(encoding="utf-8"), platform),
                encoding="utf-8",
            )
        else:
            shutil.copy2(root / rel_file, dst)

    return archive, folder


def main():
    default_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Package this skill into dist/<name>.skill + dist/<name>/")
    parser.add_argument("skill_dir", nargs="?", default=str(default_root),
                        help="skill directory to package; defaults to this skill directory")
    parser.add_argument("--output", help="optional output .skill path")
    args = parser.parse_args()

    if args.output:
        archive, folder = package_skill(args.skill_dir, output=args.output)
        print(archive)
        print(folder)
        return
    for platform in SUPPORTED_PLATFORMS:
        archive, _folder = package_skill(args.skill_dir, platform=platform)
        print(archive)


if __name__ == "__main__":
    main()
