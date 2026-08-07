"""流水线 JSON 契约的原子持久化。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
import json
import os
import tempfile

from .models import RunResult


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


def write_run_result(path: str | os.PathLike[str], result: RunResult) -> RunResult:
    atomic_write_json(path, result.to_dict())
    return result
