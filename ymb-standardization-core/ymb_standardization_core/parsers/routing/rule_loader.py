from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

import yaml


@dataclass(frozen=True)
class RouteRule:
    id: str
    parser: str
    file_type: str
    bank: str
    account_type: str
    identity_any: list
    layout_all: list
    metadata_all: dict
    style_all: list
    data_all: list
    date_format_any: list
    has_fingerprint: bool = False

    def base_match_text(self, text, context=None):
        context = context or {}
        identity_hits = [marker for marker in self.identity_any if marker in text]
        if not identity_hits:
            return None

        layout_hits = [marker for marker in self.layout_all if marker in text]
        if len(layout_hits) != len(self.layout_all):
            return None

        return {
            "identity_evidence": identity_hits,
            "layout_evidence": layout_hits,
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

        data_hits = _match_data(self.data_all, context, text)
        if self.data_all and len(data_hits) != len(self.data_all):
            return None

        date_hits = _match_date_formats(self.date_format_any, context)
        if self.date_format_any and not date_hits:
            return None

        return {
            **base_hits,
            "metadata_evidence": metadata_hits,
            "style_evidence": style_hits,
            "data_evidence": data_hits,
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
            "parser": self.parser,
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
            if item not in ({}, [], "", None):
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
        raise ValueError(f"missing id for parser: {item.get('parser')}")
    expected = fingerprint_md5(fingerprint)
    if rule_id != expected:
        raise ValueError(f"fingerprint id mismatch for parser: {item.get('parser')}: {rule_id} != {expected}")
    return rule_id


def load_pdf_route_rules():
    rules = []
    for item in _load_yaml("pdf_rules.yaml"):
        fingerprint = item.get("fingerprint", {})
        rules.append(PdfRouteRule(
            id=_rule_id(item, fingerprint),
            parser=item["parser"],
            file_type=item.get("file_type", "pdf"),
            bank=item["bank"],
            account_type=item.get("account_type", "未知"),
            identity_any=fingerprint.get("identity", {}).get("any", []),
            layout_all=fingerprint.get("layout", {}).get("all", []),
            metadata_all=fingerprint.get("metadata", {}).get("all", {}),
            style_all=fingerprint.get("style", {}).get("all", []),
            data_all=fingerprint.get("data", {}).get("all", []),
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
            parser=item["parser"],
            file_type=item.get("file_type", "excel"),
            bank=item["bank"],
            account_type=item.get("account_type", "未知"),
            identity_any=fingerprint.get("identity", {}).get("any", []),
            layout_all=fingerprint.get("layout", {}).get("all", []),
            metadata_all=fingerprint.get("metadata", {}).get("all", {}),
            style_all=fingerprint.get("style", {}).get("all", []),
            data_all=fingerprint.get("data", {}).get("all", []),
            date_format_any=fingerprint.get("date_format", {}).get("any", []),
            has_fingerprint=bool(fingerprint),
        ))
    return rules
