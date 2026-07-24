"""声明式行变换注册表。"""

from collections.abc import Callable, Mapping
from typing import Any

from ymb_standardization_core.readers.base import RawRows


RowTransform = Callable[[RawRows, Any], RawRows]


class RowTransformRegistry:
    """按稳定 transform_id 顺序执行 ``rows -> rows`` 变换。"""

    def __init__(self) -> None:
        self._transforms: dict[str, RowTransform] = {}

    def register(self, transform_id: str, transform: RowTransform) -> None:
        normalized_id = str(transform_id or "").strip()
        if not normalized_id:
            raise ValueError("transform_id must be non-empty")
        if normalized_id in self._transforms:
            raise ValueError(f"duplicate transform_id: {normalized_id}")
        self._transforms[normalized_id] = transform

    def apply(self, rows: RawRows, options: Mapping[str, Any]) -> RawRows:
        transformed = rows
        for transform_id, transform in self._transforms.items():
            config = options.get(transform_id)
            if config:
                transformed = transform(transformed, config)
        return transformed

    def ids(self) -> tuple[str, ...]:
        return tuple(self._transforms)
