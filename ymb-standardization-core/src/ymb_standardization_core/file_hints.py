"""客户目录文件级 hints 读取。

只处理打开原始文件所需的文件级信息，例如加密文件的打开密码。
交易字段、金额、余额、标签等业务口径不得放入 hints。
"""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Optional

import yaml


HINTS_FILENAME = "_file_hints.yaml"
SENSITIVE_HINT_KEYS = {"open_password"}
ALLOWED_FILE_HINT_KEYS = {"open_password", "note"}


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(f"_file_hints.yaml 存在重复键：{key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


@dataclass(frozen=True)
class FileHints:
    root: Optional[Path] = None
    file_info: Optional[dict] = None

    def for_file(self, path):
        """按相对路径精确匹配单个文件的 hints。"""
        if not self.root or not self.file_info:
            return {}
        try:
            relative = Path(path).resolve().relative_to(self.root.resolve())
        except Exception:
            relative = Path(path)
        key = _normalize_relative_path(relative)
        value = self.file_info.get(key)
        return dict(value or {})

    def audit_for_file(self, path):
        hints = self.for_file(path)
        if not hints:
            return {}
        return {
            "hints_applied": sorted(k for k in hints if k != "note"),
            "hints_source": HINTS_FILENAME,
            "sensitive_values_redacted": any(k in SENSITIVE_HINT_KEYS for k in hints),
        }


EMPTY_FILE_HINTS = FileHints()


def find_hints_root(path):
    """从文件所在目录向上查找最近的 _file_hints.yaml。"""
    current = Path(path).resolve()
    if current.is_file() or current.suffix:
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / HINTS_FILENAME).is_file():
            return candidate
    return None


def load_file_hints(root):
    root = Path(root)
    hints_path = root / HINTS_FILENAME
    if not hints_path.exists():
        return FileHints(root=root, file_info={})
    try:
        payload = yaml.load(hints_path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"_file_hints.yaml 解析失败：{exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("_file_hints.yaml 顶层必须是映射")
    if "file_patterns" in payload:
        raise ValueError("_file_hints.yaml 不支持 file_patterns；请在 file_info 中逐个写明相对文件路径")

    raw_file_info = payload.get("file_info", {})
    if raw_file_info is None:
        raw_file_info = {}
    if not isinstance(raw_file_info, dict):
        raise ValueError("_file_hints.yaml 的 file_info 必须是映射")

    file_info = {}
    for raw_key, raw_hints in raw_file_info.items():
        key = _normalize_relative_path(raw_key)
        if not isinstance(raw_hints, dict):
            raise ValueError(f"_file_hints.yaml 的 {key} 必须是映射")
        unknown = sorted(set(raw_hints) - ALLOWED_FILE_HINT_KEYS)
        if unknown:
            raise ValueError(f"_file_hints.yaml 的 {key} 包含不支持字段：{', '.join(unknown)}")
        file_info[key] = dict(raw_hints)
    return FileHints(root=root, file_info=file_info)


def load_file_hints_for_path(path):
    root = find_hints_root(path)
    if not root:
        return EMPTY_FILE_HINTS
    return load_file_hints(root)


def _normalize_relative_path(path):
    text = str(path).replace("\\", "/").strip()
    normalized = PurePosixPath(text).as_posix()
    if normalized in {"", "."} or normalized.startswith("../") or normalized == "..":
        raise ValueError(f"_file_hints.yaml 文件路径必须是客户目录内相对路径：{path}")
    if PurePosixPath(normalized).is_absolute():
        raise ValueError(f"_file_hints.yaml 文件路径不能是绝对路径：{path}")
    return normalized
