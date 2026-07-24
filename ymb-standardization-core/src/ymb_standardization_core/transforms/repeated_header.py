"""分页重复表头边界识别。"""

from collections.abc import Iterable, Mapping
from typing import Any


def repeated_header_bottom(
        words: Iterable[Mapping[str, Any]],
        header_top: float,
        first_anchor_top: float | None,
        config: Mapping[str, Any] | None) -> float | None:
    """返回当前页重复表头的实际底边。

    ``end_markers`` 必须来自稳定模板表头。搜索范围被限制在已识别列头与
    首个交易 anchor 之间，避免把交易内容误当作表头。
    """
    end_markers = {
        str(marker).strip()
        for marker in (config or {}).get("end_markers", [])
        if str(marker).strip()
    }
    if not end_markers or first_anchor_top is None:
        return None
    bottoms = [
        float(word.get("bottom", word.get("top", 0)))
        for word in words
        if header_top <= float(word.get("top", 0)) < first_anchor_top
        and str(word.get("text") or "").strip() in end_markers
    ]
    return max(bottoms) if bottoms else None
