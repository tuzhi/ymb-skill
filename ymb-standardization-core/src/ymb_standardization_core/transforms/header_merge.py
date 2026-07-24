"""多层表头合并变换。"""

from collections.abc import Mapping
from typing import Any

from ymb_standardization_core.readers.base import RawRows


def merge_configured_header(
        rows: RawRows,
        route_info: Mapping[str, Any]) -> tuple[RawRows, dict[str, Any]]:
    """按 fingerprint 显式配置合并多层表头，不改变原始数据行号。"""
    config = route_info.get("header_merge") or {}
    if not rows or not config:
        return rows, dict(route_info)
    row_count = int(config.get("rows") or 0)
    route_columns = {
        str(column or "").strip()
        for column in (route_info.get("column_mapping") or {})
        if str(column or "").strip()
    }
    if row_count < 2 or not route_columns:
        return rows, dict(route_info)
    header_index = max(
        range(min(30, len(rows))),
        key=lambda index: sum(
            1
            for value in rows[index]
            if any(
                marker in str(value or "").strip()
                for marker in route_columns
            )
        ),
    )
    if header_index + row_count > len(rows):
        return rows, dict(route_info)

    width = max(len(rows[header_index + offset]) for offset in range(row_count))
    separator = str(config.get("separator") or "")
    merged = []
    parent = ""
    for column_index in range(width):
        top = str(
            rows[header_index][column_index]
            if column_index < len(rows[header_index]) else ""
        ).strip()
        if top:
            parent = top
        parts = []
        for offset in range(1, row_count):
            row = rows[header_index + offset]
            value = str(row[column_index] if column_index < len(row) else "").strip()
            if value:
                parts.append(value)
        if parts:
            merged.append(separator.join([value for value in (top or parent, *parts) if value]))
        else:
            merged.append(top)

    output = [list(row) for row in rows]
    output[header_index] = merged
    for offset in range(1, row_count):
        output[header_index + offset] = [None] * width
    updated_route = dict(route_info)
    updated_mapping = dict(updated_route.get("column_mapping") or {})
    updated_mapping.update(config.get("columns") or {})
    updated_route["column_mapping"] = updated_mapping
    return output, updated_route
