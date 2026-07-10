from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re

import yaml


@dataclass(frozen=True)
class RouteRule:
    id: str
    reader_id: str
    file_type: str
    bank: str
    account_type: str
    column_mapping: dict
    identity_any: list
    column_markers: list
    metadata_all: dict
    style_all: list
    date_format_any: list
    column_transforms: dict = field(default_factory=dict)
    row_anchor: dict = field(default_factory=dict)
    word_filters: dict = field(default_factory=dict)
    direction_from_column: dict = field(default_factory=dict)
    drop_rows: list = field(default_factory=list)
    split_amount_balance: dict = field(default_factory=dict)
    amount_columns: list = field(default_factory=list)
    extract_patterns: list = field(default_factory=list)
    preamble_mapping: dict = field(default_factory=dict)
    preamble_extractors: list = field(default_factory=list)
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
            "reader_id": self.reader_id,
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


def _reader_id(item, default_file_type):
    reader_id = str(item.get("reader_id") or "").strip()
    if reader_id:
        return reader_id
    file_type = item.get("file_type", default_file_type)
    if file_type == "pdf":
        return "pdfplumber_table"
    if file_type == "excel":
        return "openpyxl_grid"
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


def _preamble_mapping(fingerprint):
    mapping = (fingerprint or {}).get("preamble_mapping") or {}
    if not isinstance(mapping, dict):
        raise ValueError("fingerprint.preamble_mapping must be a dict")
    return {
        str(key).strip(): str(value).strip()
        for key, value in mapping.items()
        if str(key).strip() and str(value).strip()
    }


def _preamble_extractors(fingerprint):
    extractors = (fingerprint or {}).get("preamble_extractors") or []
    if not isinstance(extractors, list):
        raise ValueError("fingerprint.preamble_extractors must be a list")
    normalized = []
    for extractor in extractors:
        if not isinstance(extractor, dict):
            raise ValueError("fingerprint.preamble_extractors items must be dicts")
        field_name = str(extractor.get("field") or "").strip()
        pattern = str(extractor.get("pattern") or "").strip()
        template = str(extractor.get("template") or "").strip()
        if not field_name or not pattern:
            raise ValueError("preamble extractor requires field and pattern")
        item = {"field": field_name, "pattern": pattern}
        if template:
            item["template"] = template
        normalized.append(item)
    return normalized


def _reader_options(item):
    options = (item or {}).get("reader_options") or {}
    if not isinstance(options, dict):
        raise ValueError("reader_options must be a dict")
    return options


def _column_transforms(item, fingerprint):
    options = _reader_options(item)
    transforms = options.get("column_transforms")
    if transforms is None:
        transforms = (fingerprint or {}).get("column_transforms") or {}
    if not isinstance(transforms, dict):
        raise ValueError("reader_options.column_transforms must be a dict")
    normalized = {}
    for column, options in transforms.items():
        source = str(column).strip()
        if not source:
            continue
        if options is None:
            continue
        if not isinstance(options, dict):
            raise ValueError("reader_options.column_transforms options must be dicts")
        item = {}
        newline = str(options.get("newline") or "").strip()
        if newline:
            if newline not in {"space", "cjk_join", "remove_all"}:
                raise ValueError(f"unsupported newline transform: {newline}")
            item["newline"] = newline
        if item:
            normalized[source] = item
    return normalized


def _row_anchor(item, fingerprint):
    options = _reader_options(item)
    row_transforms = options.get("row_transforms")
    if row_transforms is None:
        row_transforms = (fingerprint or {}).get("row_anchor") or {}
    if not isinstance(row_transforms, dict):
        raise ValueError("reader_options.row_transforms must be a dict")

    anchor = {}
    column = row_transforms.get("anchor_column", row_transforms.get("column"))
    if column:
        anchor["column"] = str(column).strip()
    pattern = row_transforms.get("anchor_pattern", row_transforms.get("pattern"))
    if pattern:
        anchor["pattern"] = str(pattern).strip()
    values = row_transforms.get("anchor_values", row_transforms.get("values"))
    if values:
        anchor["values"] = [str(value).strip() for value in values if str(value).strip()]
    continuation = row_transforms.get("continuation")
    if continuation:
        continuation = str(continuation).strip()
        if continuation not in {"until_next_anchor"}:
            raise ValueError(f"unsupported row continuation: {continuation}")
        anchor["continuation"] = continuation
    return anchor


def _word_filters(item, fingerprint):
    options = _reader_options(item)
    filters = options.get("word_filters")
    if filters is None:
        filters = (fingerprint or {}).get("word_filters") or {}
    if not isinstance(filters, dict):
        raise ValueError("reader_options.word_filters must be a dict")
    return filters


def _direction_from_column(item):
    config = _reader_options(item).get("direction_from_column") or {}
    if not config:
        return {}
    if not isinstance(config, dict):
        raise ValueError("reader_options.direction_from_column must be a dict")
    normalized = {}
    source = str(config.get("source") or "").strip()
    if source:
        normalized["source"] = source
    normalized["target"] = str(config.get("target") or "收支方向").strip()
    for key in ("income_prefixes", "expense_prefixes"):
        values = config.get(key) or []
        if not isinstance(values, list):
            raise ValueError(f"reader_options.direction_from_column.{key} must be a list")
        normalized[key] = [str(value).strip() for value in values if str(value).strip()]
    return normalized if normalized.get("source") else {}


def _drop_rows(item):
    rules = _reader_options(item).get("drop_rows") or []
    if not rules:
        return []
    if not isinstance(rules, list):
        raise ValueError("reader_options.drop_rows must be a list")
    normalized = []
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("reader_options.drop_rows items must be dicts")
        column = str(rule.get("column") or "").strip()
        values = [str(value).strip() for value in (rule.get("values") or []) if str(value).strip()]
        if column and values:
            normalized.append({"column": column, "values": values})
    return normalized


def _split_amount_balance(item):
    config = _reader_options(item).get("split_amount_balance") or {}
    if not config:
        return {}
    if not isinstance(config, dict):
        raise ValueError("reader_options.split_amount_balance must be a dict")
    source = str(config.get("source") or "").strip()
    amount = str(config.get("amount") or "").strip()
    if not source or not amount:
        raise ValueError("reader_options.split_amount_balance requires source and amount")
    return {"source": source, "amount": amount}


def _amount_columns(item):
    columns = _reader_options(item).get("amount_columns") or []
    if not columns:
        return []
    if not isinstance(columns, list):
        raise ValueError("reader_options.amount_columns must be a list")
    return [str(column).strip() for column in columns if str(column).strip()]


def _extract_patterns(item):
    patterns = _reader_options(item).get("extract_patterns") or []
    if not patterns:
        return []
    if not isinstance(patterns, list):
        raise ValueError("reader_options.extract_patterns must be a list")
    normalized = []
    for pattern in patterns:
        if not isinstance(pattern, dict):
            raise ValueError("reader_options.extract_patterns items must be dicts")
        column = str(pattern.get("column") or "").strip()
        regex = str(pattern.get("pattern") or "").strip()
        if column and regex:
            normalized.append({"column": column, "pattern": regex})
    return normalized


def load_pdf_route_rules():
    rules = []
    for item in _load_yaml("pdf_rules.yaml"):
        fingerprint = item.get("fingerprint", {})
        rules.append(PdfRouteRule(
            id=_rule_id(item, fingerprint),
            reader_id=_reader_id(item, "pdf"),
            file_type=item.get("file_type", "pdf"),
            bank=item["bank"],
            account_type=item.get("account_type", "未知"),
            column_mapping=_column_mapping(fingerprint),
            identity_any=fingerprint.get("identity", {}).get("any", []),
            column_markers=_column_markers(fingerprint),
            metadata_all=fingerprint.get("metadata", {}).get("all", {}),
            style_all=fingerprint.get("style", {}).get("all", []),
            date_format_any=fingerprint.get("date_format", {}).get("any", []),
            column_transforms=_column_transforms(item, fingerprint),
            row_anchor=_row_anchor(item, fingerprint),
            word_filters=_word_filters(item, fingerprint),
            direction_from_column=_direction_from_column(item),
            drop_rows=_drop_rows(item),
            split_amount_balance=_split_amount_balance(item),
            amount_columns=_amount_columns(item),
            extract_patterns=_extract_patterns(item),
            preamble_mapping=_preamble_mapping(fingerprint),
            preamble_extractors=_preamble_extractors(fingerprint),
            has_fingerprint=bool(fingerprint),
        ))
    return rules


def load_excel_route_rules():
    rules = []
    for item in _load_yaml("excel_rules.yaml"):
        fingerprint = item.get("fingerprint", {})
        rules.append(ExcelRouteRule(
            id=_rule_id(item, fingerprint),
            reader_id=_reader_id(item, "excel"),
            file_type=item.get("file_type", "excel"),
            bank=item["bank"],
            account_type=item.get("account_type", "未知"),
            column_mapping=_column_mapping(fingerprint),
            identity_any=fingerprint.get("identity", {}).get("any", []),
            column_markers=_column_markers(fingerprint),
            metadata_all=fingerprint.get("metadata", {}).get("all", {}),
            style_all=fingerprint.get("style", {}).get("all", []),
            date_format_any=fingerprint.get("date_format", {}).get("any", []),
            column_transforms=_column_transforms(item, fingerprint),
            row_anchor=_row_anchor(item, fingerprint),
            word_filters=_word_filters(item, fingerprint),
            direction_from_column=_direction_from_column(item),
            drop_rows=_drop_rows(item),
            split_amount_balance=_split_amount_balance(item),
            amount_columns=_amount_columns(item),
            extract_patterns=_extract_patterns(item),
            preamble_mapping=_preamble_mapping(fingerprint),
            preamble_extractors=_preamble_extractors(fingerprint),
            has_fingerprint=bool(fingerprint),
        ))
    return rules
