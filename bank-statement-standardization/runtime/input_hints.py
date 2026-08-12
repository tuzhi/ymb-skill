"""兼容客户目录中的文件级输入提示，并转换为 Runtime 内存参数。"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import yaml


HINTS_FILENAME = "_file_hints.yaml"
ALLOWED_HINT_KEYS = frozenset({"open_password", "note"})


class _UniqueKeyLoader(yaml.SafeLoader):
    """拒绝 YAML 重复键，避免密码提示被静默覆盖。"""


def _construct_unique_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(f"{HINTS_FILENAME} 存在重复键：{key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _relative_file_path(value) -> str:
    text = str(value or "").replace("\\", "/").strip()
    path = PurePosixPath(text)
    normalized = path.as_posix()
    if (
        normalized in {"", ".", ".."}
        or path.is_absolute()
        or ".." in path.parts
    ):
        raise ValueError(f"{HINTS_FILENAME} 必须使用客户目录内相对文件路径：{value}")
    return normalized


def consume_file_password_hints(input_dir) -> dict[str, str]:
    """读取兼容 YAML 为内存密码映射，并从 Run 输入快照中移除该文件。

    `_file_hints.yaml` 只是 Skill/CLI 的历史输入适配格式，不进入 Stage 1，
    也不会持久化到 Pipeline 结果。SDK 正式契约仍是
    ``InputFile.open_password``。
    """
    root = Path(input_dir)
    hints_path = root / HINTS_FILENAME
    if not hints_path.is_file():
        return {}

    try:
        try:
            payload = yaml.load(
                hints_path.read_text(encoding="utf-8"),
                Loader=_UniqueKeyLoader,
            ) or {}
        except yaml.YAMLError as exc:
            raise ValueError(f"{HINTS_FILENAME} 解析失败：{exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"{HINTS_FILENAME} 顶层必须是映射")
        unknown_top_level = sorted(set(payload) - {"file_info"})
        if unknown_top_level:
            raise ValueError(
                f"{HINTS_FILENAME} 包含不支持的顶层字段："
                + "、".join(unknown_top_level)
            )
        file_info = payload.get("file_info") or {}
        if not isinstance(file_info, dict):
            raise ValueError(f"{HINTS_FILENAME} 的 file_info 必须是映射")

        passwords = {}
        for raw_path, raw_hints in file_info.items():
            relative = _relative_file_path(raw_path)
            if not isinstance(raw_hints, dict):
                raise ValueError(f"{HINTS_FILENAME} 的 {relative} 必须是映射")
            unknown = sorted(set(raw_hints) - ALLOWED_HINT_KEYS)
            if unknown:
                raise ValueError(
                    f"{HINTS_FILENAME} 的 {relative} 包含不支持字段："
                    + "、".join(unknown)
                )
            target = (root / relative).resolve()
            if root.resolve() not in target.parents or not target.is_file():
                raise ValueError(f"{HINTS_FILENAME} 对应文件不存在：{relative}")
            password = str(raw_hints.get("open_password") or "")
            if password:
                passwords[relative] = password
        return passwords
    finally:
        # 密码不能留在 Run 快照、错误包或后续目录扫描中。
        hints_path.unlink(missing_ok=True)
