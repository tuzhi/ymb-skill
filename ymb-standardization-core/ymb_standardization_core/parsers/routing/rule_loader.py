from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

import yaml


@dataclass(frozen=True)
class RouteRule:
    id: str
    parser_id: str
    file_type: str
    bank: str
    account_type: str
    column_mapping: dict
    identity_any: list
    column_markers: list
    metadata_all: dict
    style_all: list
    date_format_any: list
    has_fingerprint: bool = False

    def base_match_text(self, text, context=None):
        context = context or {}
        identity_hits = [marker for marker in self.identity_any if marker in text]
        if not identity_hits:
            return None

        column_hits = [marker for marker in self.column_markers if marker in text]
        if len(column_hits) != len(self.column_markers):
            return None

        return {
            "identity_evidence": identity_hits,
            "columns_evidence": column_hits,
        }

    def match_text(self, text, context=None):
        context = context or {}
        base_hits = self.base_match_text(text, context=context)
        if not base_hits or not self.has_fingerprint:
            return None

        metadata_hits = _match_metadata(self.metadata_all, context)
        if self.metadata_all and not metadata_hits:
            return None

        style_hits = _match_styles(self.style_all, context)
        if self.style_all and len(style_hits) != len(self.style_all):
            return None

        date_hits = _match_date_formats(self.date_format_any, context)
        if self.date_format_any and not date_hits:
            return None

        return {
            **base_hits,
            "metadata_evidence": metadata_hits,
            "style_evidence": style_hits,
            "date_format_evidence": date_hits,
        }

    def fingerprint_candidate_text(self, text, context=None):
        base_hits = self.base_match_text(text or "", context=context)
        if not base_hits:
            return None
        match = self.match_text(text or "", context=context)
        if match:
            reason = "matched"
        elif not self.has_fingerprint:
            reason = "missing_yaml_fingerprint"
        else:
            reason = "fingerprint_mismatch"
        return {
            "id": self.id,
            "fingerprint_id": self.id,
            "parser_id": self.parser_id,
            "bank": self.bank,
            "file_type": self.file_type,
            "reason": reason,
            **base_hits,
            "suggested_fingerprint": suggest_fingerprint(context or {}, text or ""),
        }


@dataclass(frozen=True)
class PdfRouteRule(RouteRule):
    def match(self, text, context=None):
        return self.match_text(text or "", context=context)

    def fingerprint_candidate(self, text, context=None):
        return self.fingerprint_candidate_text(text or "", context=context)


@dataclass(frozen=True)
class ExcelRouteRule(RouteRule):
    def match(self, rows, context=None):
        text = _rows_text(rows)
        return self.match_text(text, context=context)

    def fingerprint_candidate(self, rows, context=None):
        text = _rows_text(rows)
        return self.fingerprint_candidate_text(text, context=context)


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


def suggest_fingerprint(context, text):
    """为未识别/冲突样本输出可沉淀到 YAML 的候选指纹线索。"""
    metadata = {k: v for k, v in (context.get("metadata") or {}).items() if v not in (None, "")}
    styles = []
    for style in (context.get("styles") or [])[:30]:
        text_value = str(style.get("text") or "").strip()
        if not text_value:
            continue
        item = {"text": text_value}
        for key in ("font", "size", "bold", "row", "col", "top", "number_format"):
            value = style.get(key)
            if value not in (None, ""):
                item[key] = value
        styles.append(item)
        if len(styles) >= 5:
            break
    lines = [str(line).strip()[:200] for line in (context.get("lines") or str(text or "").splitlines()) if str(line).strip()]
    return {
        "metadata": metadata,
        "styles": styles,
        "date_patterns": list(context.get("date_patterns") or []),
        "sample_lines": lines[:5],
    }


def _load_yaml(name):
    with (Path(__file__).resolve().parent / name).open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def _normalize_fingerprint(value):
    if isinstance(value, dict):
        normalized = {}
        for key in sorted(value):
            item = _normalize_fingerprint(value[key])
            if item not in ({}, [], ""):
                normalized[str(key).strip()] = item
        return normalized
    if isinstance(value, list):
        normalized = []
        for item in value:
            normalized_item = _normalize_fingerprint(item)
            if normalized_item not in ({}, [], "", None):
                normalized.append(normalized_item)
        return normalized
    if isinstance(value, str):
        return value.strip()
    return value


def fingerprint_md5(fingerprint):
    """返回 fingerprint 节点规范化后的 md5 id。"""
    canonical = _normalize_fingerprint(fingerprint or {})
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "md5:" + hashlib.md5(payload.encode("utf-8")).hexdigest()


def _rule_id(item, fingerprint):
    rule_id = str(item.get("id") or "").strip()
    if not rule_id:
        raise ValueError(f"missing id for route rule: bank={item.get('bank')}")
    expected = fingerprint_md5(fingerprint)
    if rule_id != expected:
        raise ValueError(f"fingerprint id mismatch for route rule: {rule_id} != {expected}")
    return rule_id


def _parser_id(item, default_file_type):
    parser_id = str(item.get("parser_id") or "").strip()
    if parser_id:
        return parser_id
    file_type = item.get("file_type", default_file_type)
    if file_type == "excel":
        return "excel_grid"
    if file_type == "pdf":
        return "pdf_table"
    return "none"


def _columns_all(fingerprint):
    columns = (fingerprint or {}).get("columns") or {}
    all_columns = columns.get("all") or {}
    if not isinstance(all_columns, dict):
        raise ValueError("fingerprint.columns.all must be a dict")
    return all_columns


def _column_markers(fingerprint):
    return [str(key).strip() for key in _columns_all(fingerprint).keys() if str(key).strip()]


def _column_mapping(fingerprint):
    mapping = {}
    for key, value in _columns_all(fingerprint).items():
        source = str(key).strip()
        target = "" if value is None else str(value).strip()
        if source and target:
            mapping[source] = target
    if not isinstance(mapping, dict):
        raise ValueError("column_mapping must be a dict")
    return {str(key).strip(): str(value).strip() for key, value in mapping.items() if str(key).strip() and str(value).strip()}


def load_pdf_route_rules():
    rules = []
    for item in _load_yaml("pdf_rules.yaml"):
        fingerprint = item.get("fingerprint", {})
        rules.append(PdfRouteRule(
            id=_rule_id(item, fingerprint),
            parser_id=_parser_id(item, "pdf"),
            file_type=item.get("file_type", "pdf"),
            bank=item["bank"],
            account_type=item.get("account_type", "未知"),
            column_mapping=_column_mapping(fingerprint),
            identity_any=fingerprint.get("identity", {}).get("any", []),
            column_markers=_column_markers(fingerprint),
            metadata_all=fingerprint.get("metadata", {}).get("all", {}),
            style_all=fingerprint.get("style", {}).get("all", []),
            date_format_any=fingerprint.get("date_format", {}).get("any", []),
            has_fingerprint=bool(fingerprint),
        ))
    return rules


def load_excel_route_rules():
    rules = []
    for item in _load_yaml("excel_rules.yaml"):
        fingerprint = item.get("fingerprint", {})
        rules.append(ExcelRouteRule(
            id=_rule_id(item, fingerprint),
            parser_id=_parser_id(item, "excel"),
            file_type=item.get("file_type", "excel"),
            bank=item["bank"],
            account_type=item.get("account_type", "未知"),
            column_mapping=_column_mapping(fingerprint),
            identity_any=fingerprint.get("identity", {}).get("any", []),
            column_markers=_column_markers(fingerprint),
            metadata_all=fingerprint.get("metadata", {}).get("all", {}),
            style_all=fingerprint.get("style", {}).get("all", []),
            date_format_any=fingerprint.get("date_format", {}).get("any", []),
            has_fingerprint=bool(fingerprint),
        ))
    return rules
