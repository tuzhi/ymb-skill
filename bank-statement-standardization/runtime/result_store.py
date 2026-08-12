"""流水线整体结果及阶段结果引用的原子持久化。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
import json
import os
import tempfile


PIPELINE_RESULT_FILENAME = "pipeline_result.json"


def atomic_write_json(path: str | os.PathLike[str], value: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(dict(value), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path: str | os.PathLike[str], default: Any = None) -> Any:
    target = Path(path)
    if not target.is_file():
        return default
    with target.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def load_pipeline_result(run_dir: str | os.PathLike[str]) -> dict[str, Any]:
    """读取当前唯一的 Pipeline 整体结果。"""
    root = Path(run_dir)
    current = read_json(root / PIPELINE_RESULT_FILENAME, None)
    if current is None:
        raise FileNotFoundError(f"缺少文件：{root / PIPELINE_RESULT_FILENAME}")
    if not isinstance(current, dict):
        raise ValueError(f"{PIPELINE_RESULT_FILENAME} 必须是 object")
    return current
