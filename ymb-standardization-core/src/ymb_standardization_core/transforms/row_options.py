"""YAML ``reader_options`` 驱动的通用行变换。"""

from collections.abc import Mapping, Sequence
from functools import lru_cache
from typing import Any
import re

from ymb_standardization_core.readers.base import RawRows

from .registry import RowTransformRegistry


def drop_configured_rows(rows: RawRows, rules: Sequence[Mapping[str, Any]]) -> RawRows:
    if not rows or not rules:
        return rows
    headers = [str(header or "").strip() for header in rows[0]]
    output = [rows[0]]
    for row in rows[1:]:
        drop = False
        for rule in rules:
            any_values = {
                str(item).strip()
                for item in rule.get("any_values", [])
                if str(item).strip()
            }
            if any_values and any(
                str(value or "").strip() in any_values for value in row
            ):
                drop = True
                break
            column = str(rule.get("column") or "").strip()
            if column not in headers:
                continue
            index = headers.index(column)
            value = str(row[index] if index < len(row) else "").strip()
            if value in {str(item).strip() for item in rule.get("values", [])}:
                drop = True
                break
        if not drop:
            output.append(row)
    return output if len(output) > 1 else []


def split_amount_balance_column(rows: RawRows, config: Mapping[str, Any]) -> RawRows:
    if not rows or not config:
        return rows
    headers = [str(header or "").strip() for header in rows[0]]
    source = str(config.get("source") or "").strip()
    amount = str(config.get("amount") or "").strip()
    if source not in headers or amount not in headers:
        return rows
    source_index = headers.index(source)
    amount_index = headers.index(amount)
    money_re = re.compile(r"\d[\d,]*\.\d{2}")
    output = [rows[0]]
    for row in rows[1:]:
        cells = list(row)
        if len(cells) < len(headers):
            cells.extend([""] * (len(headers) - len(cells)))
        values = money_re.findall(str(cells[source_index] if source_index < len(cells) else ""))
        if not str(cells[amount_index] if amount_index < len(cells) else "").strip() and len(values) >= 2:
            cells[amount_index] = values[0]
            cells[source_index] = values[-1]
        output.append(cells)
    return output


def normalize_amount_columns(rows: RawRows, columns: Sequence[str]) -> RawRows:
    if not rows or not columns:
        return rows
    headers = [str(header or "").strip() for header in rows[0]]
    indexes = [headers.index(column) for column in columns if column in headers]
    if not indexes:
        return rows
    money_re = re.compile(r"\d[\d,]*\.\d{2}")
    output = [rows[0]]
    for row in rows[1:]:
        cells = list(row)
        if len(cells) < len(headers):
            cells.extend([""] * (len(headers) - len(cells)))
        for index in indexes:
            match = money_re.search(str(cells[index] if index < len(cells) else ""))
            if match:
                cells[index] = match.group(0)
        output.append(cells)
    return output


def extract_column_patterns(
        rows: RawRows,
        patterns: Sequence[Mapping[str, Any]]) -> RawRows:
    if not rows or not patterns:
        return rows
    headers = [str(header or "").strip() for header in rows[0]]
    compiled = []
    for item in patterns:
        column = str(item.get("column") or "").strip()
        pattern = str(item.get("pattern") or "").strip()
        if column in headers and pattern:
            compiled.append((headers.index(column), re.compile(pattern)))
    if not compiled:
        return rows
    output = [rows[0]]
    for row in rows[1:]:
        cells = list(row)
        if len(cells) < len(headers):
            cells.extend([""] * (len(headers) - len(cells)))
        for index, pattern in compiled:
            match = pattern.search(str(cells[index] if index < len(cells) else ""))
            if match:
                cells[index] = match.group(1) if match.groups() else match.group(0)
        output.append(cells)
    return output


def apply_direction_from_column(rows: RawRows, config: Mapping[str, Any]) -> RawRows:
    if not rows or not config:
        return rows
    headers = [str(header or "").strip() for header in rows[0]]
    source = str(config.get("source") or "").strip()
    target = str(config.get("target") or "收支方向").strip()
    if source not in headers or not target:
        return rows
    source_index = headers.index(source)
    if target in headers:
        target_index = headers.index(target)
        output = [headers]
    else:
        target_index = len(headers)
        output = [headers + [target]]
    income_prefixes = [str(value).strip().lower() for value in config.get("income_prefixes", [])]
    expense_prefixes = [str(value).strip().lower() for value in config.get("expense_prefixes", [])]
    for row in rows[1:]:
        cells = list(row)
        if len(cells) < len(headers):
            cells.extend([""] * (len(headers) - len(cells)))
        text = str(cells[source_index] if source_index < len(cells) else "").strip().lower()
        direction = ""
        if any(text == prefix or text.startswith(prefix) for prefix in income_prefixes):
            direction = "收入"
        elif any(text == prefix or text.startswith(prefix) for prefix in expense_prefixes):
            direction = "支出"
        if target_index < len(cells):
            cells[target_index] = direction
        else:
            cells.append(direction)
        output.append(cells)
    return output


@lru_cache(maxsize=1)
def reader_row_transform_registry() -> RowTransformRegistry:
    registry = RowTransformRegistry()
    registry.register("drop_rows", drop_configured_rows)
    registry.register("split_amount_balance", split_amount_balance_column)
    registry.register("amount_columns", normalize_amount_columns)
    registry.register("extract_patterns", extract_column_patterns)
    registry.register("direction_from_column", apply_direction_from_column)
    return registry


def apply_reader_options(rows: RawRows, options: Mapping[str, Any]) -> RawRows:
    return reader_row_transform_registry().apply(rows, options)
