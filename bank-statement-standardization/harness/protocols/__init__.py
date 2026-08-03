"""版本化 Harness 协议模板加载与填充。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping
import json


PROTOCOL_VERSION = "v1"
PROTOCOL_ROOT = Path(__file__).resolve().parent / PROTOCOL_VERSION


def protocol_path(name: str) -> Path:
    if not name or any(char not in "abcdefghijklmnopqrstuvwxyz-" for char in name):
        raise ValueError(f"协议名称无效：{name}")
    path = (PROTOCOL_ROOT / f"{name}.template.json").resolve()
    if path.parent != PROTOCOL_ROOT.resolve() or not path.is_file():
        raise FileNotFoundError(f"缺少 Harness 协议模板：{path}")
    return path


def load_protocol(name: str) -> dict[str, Any]:
    value = json.loads(protocol_path(name).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Harness 协议模板必须是 JSON object：{name}")
    return deepcopy(value)


def _merge_declared(template: dict[str, Any], values: Mapping[str, Any], name: str) -> dict[str, Any]:
    unknown = sorted(set(values) - set(template))
    if unknown:
        raise ValueError(f"{name} 包含模板外字段：{unknown}")
    output = deepcopy(template)
    for key, value in values.items():
        current = output[key]
        if isinstance(current, dict) and isinstance(value, Mapping) and current:
            output[key] = _merge_declared(current, value, f"{name}.{key}")
        else:
            output[key] = deepcopy(value)
    return output


def render_protocol(name: str, values: Mapping[str, Any]) -> dict[str, Any]:
    return _merge_declared(load_protocol(name), values, name)


def normalize_protocol(name: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError(f"{name} 输出必须是 JSON object")
    return render_protocol(name, payload)
