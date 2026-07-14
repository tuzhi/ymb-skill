"""兼容旧命令的标准化 CLI 入口。

标准化内核已迁到仓库平级子项目 ymb-standardization-core，便于脱敏等其他项目直接复用。
"""

import os
import sys
from collections import Counter
from pathlib import Path


_SKILL_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _candidate in (
    _SKILL_ROOT / "packages" / "ymb_standardization_core",
    _REPO_ROOT / "ymb-standardization-core",
):
    if _candidate.exists() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))
        break

from ymb_standardization_core.core import *  # noqa: F401,F403,E402


def duplicate_source_stems(paths):
    counts = Counter(Path(path).stem for path in paths)
    return {stem for stem, count in counts.items() if count > 1}


def _next_available(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    index = 2
    while os.path.exists(f"{base}_{index}{ext}"):
        index += 1
    return f"{base}_{index}{ext}"


def rename_duplicate_artifacts(source_path, csv_path, json_path, duplicate_stems):
    """同名多格式输入追加扩展名后缀，避免阶段一产物互相覆盖。"""
    source = Path(source_path)
    stem = source.stem
    if stem not in duplicate_stems:
        return csv_path, json_path

    suffix = source.suffix.lower().lstrip(".") or "file"
    csv_target = _next_available(
        os.path.join(os.path.dirname(csv_path), f"{stem}__{suffix}__standardized.csv")
    )
    os.replace(csv_path, csv_target)

    json_target = None
    if json_path and os.path.exists(json_path):
        json_target = _next_available(
            os.path.join(os.path.dirname(json_path), f"{stem}__{suffix}__mapping.json")
        )
        os.replace(json_path, json_target)
    return csv_target, json_target


if __name__ == "__main__":
    main()
