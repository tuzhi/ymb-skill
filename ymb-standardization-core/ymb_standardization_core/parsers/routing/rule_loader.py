from dataclasses import dataclass
from pathlib import Path
import re

import yaml


@dataclass(frozen=True)
class RouteRule:
    parser: str
    file_type: str
    bank: str
    version: str
    account_type: str
    identity_any: list
    layout_all: list
    metadata_all: dict
    style_all: list
    data_all: list
    date_format_any: list

    def match_text(self, text, context=None):
        context = context or {}
        identity_hits = [marker for marker in self.identity_any if marker in text]
        if not identity_hits:
            return None

        layout_hits = [marker for marker in self.layout_all if marker in text]
        if len(layout_hits) != len(self.layout_all):
            return None

        metadata_hits = _match_metadata(self.metadata_all, context)
        if self.metadata_all and not metadata_hits:
            return None

        style_hits = _match_styles(self.style_all, context)
        if self.style_all and len(style_hits) != len(self.style_all):
            return None

        data_hits = _match_data(self.data_all, context, text)
        if self.data_all and len(data_hits) != len(self.data_all):
            return None

        date_hits = _match_date_formats(self.date_format_any, context)
        if self.date_format_any and not date_hits:
            return None

        return {
            "identity_evidence": identity_hits,
            "layout_evidence": layout_hits,
            "metadata_evidence": metadata_hits,
            "style_evidence": style_hits,
            "data_evidence": data_hits,
            "date_format_evidence": date_hits,
        }


@dataclass(frozen=True)
class PdfRouteRule(RouteRule):
    def match(self, text, context=None):
        return self.match_text(text or "", context=context)


@dataclass(frozen=True)
class ExcelRouteRule(RouteRule):
    def match(self, rows, context=None):
        text = _rows_text(rows)
        return self.match_text(text, context=context)


def _rows_text(rows):
    parts = []
    for row in rows[:300]:
        for value in row:
            if value is None:
                continue
            text = str(value).strip()
            if text:
                parts.append(text)
    return "\n".join(parts)


def _match_metadata(expected, context):
    if not expected:
        return {}
    actual = context.get("metadata") or {}
    hits = {}
    for key, value in expected.items():
        actual_value = str(actual.get(key, "") or "")
        expected_value = str(value or "")
        if actual_value != expected_value:
            return {}
        hits[key] = actual_value
    return hits


def _match_styles(style_rules, context):
    if not style_rules:
        return []
    styles = context.get("styles") or []
    hits = []
    for rule in style_rules:
        hit = _match_one_style(rule, styles)
        if not hit:
            return hits
        hits.append(hit)
    return hits


def _match_one_style(rule, styles):
    expected_text = str(rule.get("text", "")).strip()
    expected_font = str(rule.get("font", "")).strip()
    size_min = rule.get("size_min")
    size_max = rule.get("size_max")
    row_max = rule.get("row_max")
    col_max = rule.get("col_max")
    top_max = rule.get("top_max")
    centered = bool(rule.get("centered", False))
    center_tolerance = float(rule.get("center_tolerance", 0.12) or 0.12)

    for style in styles:
        text = str(style.get("text") or "").strip()
        if expected_text and text != expected_text:
            continue
        font = str(style.get("font") or "")
        if expected_font and expected_font not in font:
            continue
        size = _float_or_none(style.get("size"))
        if size_min is not None and (size is None or size < float(size_min)):
            continue
        if size_max is not None and (size is None or size > float(size_max)):
            continue
        if "bold" in rule and bool(style.get("bold")) != bool(rule.get("bold")):
            continue
        if row_max is not None and int(style.get("row") or 10 ** 6) > int(row_max):
            continue
        if col_max is not None and int(style.get("col") or 10 ** 6) > int(col_max):
            continue
        if top_max is not None and float(style.get("top") or 10 ** 6) > float(top_max):
            continue
        if centered and style.get("page_width"):
            center = (float(style.get("x0") or 0) + float(style.get("x1") or 0)) / 2
            if abs(center - float(style["page_width"]) / 2) / float(style["page_width"]) > center_tolerance:
                continue
        return {k: style.get(k) for k in ("text", "font", "size", "bold", "row", "col", "number_format", "top")}
    return None


def _match_data(data_rules, context, text):
    if not data_rules:
        return []
    lines = context.get("lines") or str(text or "").splitlines()
    hits = []
    for rule in data_rules:
        same_row_all = [str(x) for x in rule.get("same_row_all", [])]
        same_row_none = [str(x) for x in rule.get("same_row_none", [])]
        min_hits = int(rule.get("min_hits", 1) or 1)
        matched_lines = []
        for line in lines:
            if all(term in line for term in same_row_all) and not any(term in line for term in same_row_none):
                matched_lines.append(line[:200])
        if len(matched_lines) < min_hits:
            return hits
        hits.append({
            "same_row_all": same_row_all,
            "same_row_none": same_row_none,
            "matched_lines": matched_lines[:5],
        })
    return hits


def _match_date_formats(patterns, context):
    if not patterns:
        return []
    found = set(context.get("date_patterns") or [])
    hits = [pattern for pattern in patterns if pattern in found]
    return hits


def _float_or_none(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _load_yaml(name):
    with (Path(__file__).resolve().parent / name).open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def load_pdf_route_rules():
    rules = []
    for item in _load_yaml("pdf_rules.yaml"):
        fingerprint = item.get("fingerprint", {})
        rules.append(PdfRouteRule(
            parser=item["parser"],
            file_type=item.get("file_type", "pdf"),
            bank=item["bank"],
            version=str(item["version"]),
            account_type=item.get("account_type", "未知"),
            identity_any=item.get("identity", {}).get("any", []),
            layout_all=item.get("layout", {}).get("all", []),
            metadata_all=fingerprint.get("metadata", {}).get("all", {}),
            style_all=fingerprint.get("style", {}).get("all", []),
            data_all=fingerprint.get("data", {}).get("all", []),
            date_format_any=fingerprint.get("date_format", {}).get("any", []),
        ))
    return rules


def load_excel_route_rules():
    rules = []
    for item in _load_yaml("excel_rules.yaml"):
        fingerprint = item.get("fingerprint", {})
        rules.append(ExcelRouteRule(
            parser=item["parser"],
            file_type=item.get("file_type", "excel"),
            bank=item["bank"],
            version=str(item["version"]),
            account_type=item.get("account_type", "未知"),
            identity_any=item.get("identity", {}).get("any", []),
            layout_all=item.get("layout", {}).get("all", []),
            metadata_all=fingerprint.get("metadata", {}).get("all", {}),
            style_all=fingerprint.get("style", {}).get("all", []),
            data_all=fingerprint.get("data", {}).get("all", []),
            date_format_any=fingerprint.get("date_format", {}).get("any", []),
        ))
    return rules
