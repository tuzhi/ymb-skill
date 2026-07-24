from dataclasses import dataclass, field
from functools import lru_cache
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
    optional_columns: dict = field(default_factory=dict)
    series_family: str = ""
    text_table_layout: str = ""
    source_order: str = ""
    date_order: str = ""
    multi_sheet_same_layout: bool = False
    dedupe_chars: bool = False
    header_merge: dict = field(default_factory=dict)
    repeated_header: dict = field(default_factory=dict)
    row_anchor: dict = field(default_factory=dict)
    word_filters: dict = field(default_factory=dict)
    direction_from_column: dict = field(default_factory=dict)
    drop_rows: list = field(default_factory=list)
    split_amount_balance: dict = field(default_factory=dict)
    amount_columns: list = field(default_factory=list)
    extract_patterns: list = field(default_factory=list)
    preamble_mapping: dict = field(default_factory=dict)
    preamble_extractors: list = field(default_factory=list)
    conditional_mapping: list = field(default_factory=list)
    extract_mapping: list = field(default_factory=list)
    require_monetary_value: bool = False
    has_fingerprint: bool = False

    def base_match_text(self, text, context=None):
        context = context or {}
        identity_hits = [marker for marker in self.identity_any if marker in text]
        if not identity_hits:
            return None

        required_hits = [marker for marker in self.column_markers if marker in text]
        if len(required_hits) != len(self.column_markers):
            return None

        optional_hits = [marker for marker in self.optional_columns if marker in text]
        missing_required_columns = [
            marker
            for marker, config in self.optional_columns.items()
            if config.get("qc") == "required" and marker not in optional_hits
        ]
        missing_hints = [
            self.optional_columns[marker].get("missing_hint")
            or f"请重新导出流水，并勾选“{marker}”"
            for marker in missing_required_columns
        ]

        return {
            "identity_evidence": identity_hits,
            "columns_evidence": required_hits + optional_hits,
            "required_columns_evidence": required_hits,
            "optional_columns_evidence": optional_hits,
            "missing_required_columns": missing_required_columns,
            "missing_hints": missing_hints,
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
            "decision": (
                "matched_incomplete"
                if base_hits["missing_required_columns"]
                else "matched"
            ),
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
            reason = match["decision"]
        elif not self.has_fingerprint:
            reason = "missing_yaml_fingerprint"
        else:
            reason = "fingerprint_mismatch"
        return {
            "id": self.id,
            "fingerprint_id": self.id,
            "reader_id": self.reader_id,
            "series_family": self.series_family,
            "source_order": self.source_order,
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


def _yaml_version(name):
    """返回可用于进程内缓存失效的 YAML 文件版本。"""
    path = Path(__file__).resolve().parents[2] / "config" / "routing" / name
    stat = path.stat()
    return str(path), stat.st_mtime_ns, stat.st_size


@lru_cache(maxsize=8)
def _load_yaml_versioned(path_text, _mtime_ns, _size):
    """相同版本的 YAML 只解析一次；mtime 或大小变化后自动加载新版本。"""
    with Path(path_text).open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def _load_yaml(name):
    return _load_yaml_versioned(*_yaml_version(name))


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


def _columns_required(fingerprint):
    columns = (fingerprint or {}).get("columns") or {}
    has_all = "all" in columns
    has_required = "required" in columns
    if has_all and has_required:
        raise ValueError("fingerprint.columns cannot contain both all and required")
    required = columns.get("required") if has_required else columns.get("all")
    required = required or {}
    if not isinstance(required, dict):
        raise ValueError("fingerprint.columns.required/all must be a dict")
    return required


def _optional_columns(fingerprint):
    columns = (fingerprint or {}).get("columns") or {}
    optional = columns.get("optional") or {}
    if not isinstance(optional, dict):
        raise ValueError("fingerprint.columns.optional must be a dict")
    normalized = {}
    for source, raw_config in optional.items():
        source = str(source).strip()
        if not source:
            continue
        if raw_config is None:
            config = {}
        elif isinstance(raw_config, str):
            config = {"field": raw_config}
        elif isinstance(raw_config, dict):
            config = dict(raw_config)
        else:
            raise ValueError(f"fingerprint.columns.optional.{source} must be a dict or string")
        qc = str(config.get("qc") or "optional").strip()
        if qc not in {"optional", "required"}:
            raise ValueError(
                f"fingerprint.columns.optional.{source}.qc must be optional or required"
            )
        normalized[source] = {
            "field": None if config.get("field") is None else str(config.get("field")).strip(),
            "qc": qc,
            "missing_hint": str(config.get("missing_hint") or "").strip(),
        }
    return normalized


def _column_markers(fingerprint):
    return [str(key).strip() for key in _columns_required(fingerprint).keys() if str(key).strip()]


def _column_mapping(fingerprint):
    mapping = {}
    for key, value in _columns_required(fingerprint).items():
        source = str(key).strip()
        if not source:
            continue
        mapping[source] = None if value is None else str(value).strip()
    for source, config in _optional_columns(fingerprint).items():
        mapping[source] = config.get("field")
    if not isinstance(mapping, dict):
        raise ValueError("column_mapping must be a dict")
    return mapping


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


def _conditional_mapping(item):
    rules = _reader_options(item).get("conditional_mapping") or []
    if not isinstance(rules, list):
        raise ValueError("reader_options.conditional_mapping must be a list")
    normalized = []
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("reader_options.conditional_mapping items must be dicts")
        condition = rule.get("if") or {}
        mapping = rule.get("map") or {}
        if not isinstance(condition, dict) or len(condition) != 1:
            raise ValueError("conditional_mapping.if must contain exactly one comparison")
        if not isinstance(mapping, dict) or not mapping:
            raise ValueError("conditional_mapping.map must be a non-empty dict")
        source, target = next(iter(condition.items()))
        normalized.append({
            "if": {str(source).strip(): str(target).strip()},
            "map": {str(raw).strip(): str(field).strip() for raw, field in mapping.items()},
        })
    return normalized


def _extract_mapping(item):
    rules = _reader_options(item).get("extract_mapping") or []
    if not isinstance(rules, list):
        raise ValueError("reader_options.extract_mapping must be a list")
    normalized = []
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("reader_options.extract_mapping items must be dicts")
        source = str(rule.get("source") or "").strip()
        field = str(rule.get("field") or "").strip()
        pattern = str(rule.get("pattern") or "").strip()
        replacement = str(rule.get("replacement", r"\1"))
        if not source or not field or not pattern:
            raise ValueError("extract_mapping requires source, field and pattern")
        try:
            compiled = re.compile(pattern)
            compiled.sub(replacement, "")
        except re.error as exc:
            raise ValueError(f"invalid extract_mapping pattern or replacement: {pattern}") from exc
        normalized.append({
            "source": source,
            "field": field,
            "pattern": pattern,
            "replacement": replacement,
        })
    return normalized


def _reader_options(item):
    options = (item or {}).get("reader_options") or {}
    if not isinstance(options, dict):
        raise ValueError("reader_options must be a dict")
    return options


def _require_monetary_value(item):
    return bool(_reader_options(item).get("require_monetary_value", False))


def _text_table_layout(item):
    layout = str(_reader_options(item).get("text_table_layout") or "").strip()
    if layout not in {"", "currency", "cmbc_personal"}:
        raise ValueError(f"unsupported reader_options.text_table_layout: {layout}")
    return layout


def _source_order(item):
    source_order = str(_reader_options(item).get("source_order") or "").strip()
    if source_order not in {"", "ascending", "descending"}:
        raise ValueError(f"unsupported reader_options.source_order: {source_order}")
    return source_order


def _date_order(item):
    date_order = str(_reader_options(item).get("date_order") or "").strip().lower()
    if date_order not in {"", "dmy", "mdy", "ymd"}:
        raise ValueError(f"unsupported reader_options.date_order: {date_order}")
    return date_order


def _multi_sheet_same_layout(item):
    return bool(_reader_options(item).get("multi_sheet_same_layout", False))


def _dedupe_chars(item):
    return bool(_reader_options(item).get("dedupe_chars", False))


def _header_merge(item):
    config = _reader_options(item).get("header_merge") or {}
    if not config:
        return {}
    if not isinstance(config, dict):
        raise ValueError("reader_options.header_merge must be a dict")
    rows = int(config.get("rows") or 0)
    if rows < 2:
        raise ValueError("reader_options.header_merge.rows must be at least 2")
    columns = config.get("columns") or {}
    if not isinstance(columns, dict) or not columns:
        raise ValueError("reader_options.header_merge.columns must be a non-empty dict")
    return {
        "rows": rows,
        "separator": str(config.get("separator") or "").strip(),
        "columns": {
            str(source).strip(): str(target).strip()
            for source, target in columns.items()
            if str(source).strip() and str(target).strip()
        },
    }


def _repeated_header(item):
    config = _reader_options(item).get("repeated_header") or {}
    if not config:
        return {}
    if not isinstance(config, dict):
        raise ValueError("reader_options.repeated_header must be a dict")
    end_markers = config.get("end_markers") or []
    if not isinstance(end_markers, list):
        raise ValueError("reader_options.repeated_header.end_markers must be a list")
    end_markers = [
        str(marker).strip()
        for marker in end_markers
        if str(marker).strip()
    ]
    if not end_markers:
        raise ValueError("reader_options.repeated_header.end_markers must be non-empty")
    return {"end_markers": end_markers}


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
        if continuation not in {
            "until_next_anchor",
            "until_next_anchor_across_pages",
        }:
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
        any_values = [
            str(value).strip()
            for value in (rule.get("any_values") or [])
            if str(value).strip()
        ]
        if column and values:
            normalized.append({"column": column, "values": values})
        elif any_values:
            normalized.append({"any_values": any_values})
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


@lru_cache(maxsize=8)
def _load_pdf_route_rules_versioned(_path_text, _mtime_ns, _size):
    rules = []
    for item in _load_yaml("pdf_rules.yaml"):
        fingerprint = item.get("fingerprint", {})
        rules.append(PdfRouteRule(
            id=_rule_id(item, fingerprint),
            reader_id=_reader_id(item, "pdf"),
            file_type=item.get("file_type", "pdf"),
            bank=item["bank"],
            account_type=item.get("account_type", "未知"),
            series_family=str(item.get("series_family") or "").strip(),
            text_table_layout=_text_table_layout(item),
            source_order=_source_order(item),
            date_order=_date_order(item),
            multi_sheet_same_layout=_multi_sheet_same_layout(item),
            dedupe_chars=_dedupe_chars(item),
            header_merge=_header_merge(item),
            repeated_header=_repeated_header(item),
            column_mapping=_column_mapping(fingerprint),
            identity_any=fingerprint.get("identity", {}).get("any", []),
            column_markers=_column_markers(fingerprint),
            optional_columns=_optional_columns(fingerprint),
            metadata_all=fingerprint.get("metadata", {}).get("all", {}),
            style_all=fingerprint.get("style", {}).get("all", []),
            date_format_any=fingerprint.get("date_format", {}).get("any", []),
            row_anchor=_row_anchor(item, fingerprint),
            word_filters=_word_filters(item, fingerprint),
            direction_from_column=_direction_from_column(item),
            drop_rows=_drop_rows(item),
            split_amount_balance=_split_amount_balance(item),
            amount_columns=_amount_columns(item),
            extract_patterns=_extract_patterns(item),
            preamble_mapping=_preamble_mapping(fingerprint),
            preamble_extractors=_preamble_extractors(fingerprint),
            conditional_mapping=_conditional_mapping(item),
            extract_mapping=_extract_mapping(item),
            require_monetary_value=_require_monetary_value(item),
            has_fingerprint=bool(fingerprint),
        ))
    return tuple(rules)


def load_pdf_route_rules():
    return _load_pdf_route_rules_versioned(*_yaml_version("pdf_rules.yaml"))


@lru_cache(maxsize=8)
def _load_excel_route_rules_versioned(_path_text, _mtime_ns, _size):
    rules = []
    for item in _load_yaml("excel_rules.yaml"):
        fingerprint = item.get("fingerprint", {})
        rules.append(ExcelRouteRule(
            id=_rule_id(item, fingerprint),
            reader_id=_reader_id(item, "excel"),
            file_type=item.get("file_type", "excel"),
            bank=item["bank"],
            account_type=item.get("account_type", "未知"),
            series_family=str(item.get("series_family") or "").strip(),
            source_order=_source_order(item),
            date_order=_date_order(item),
            multi_sheet_same_layout=_multi_sheet_same_layout(item),
            header_merge=_header_merge(item),
            column_mapping=_column_mapping(fingerprint),
            identity_any=fingerprint.get("identity", {}).get("any", []),
            column_markers=_column_markers(fingerprint),
            optional_columns=_optional_columns(fingerprint),
            metadata_all=fingerprint.get("metadata", {}).get("all", {}),
            style_all=fingerprint.get("style", {}).get("all", []),
            date_format_any=fingerprint.get("date_format", {}).get("any", []),
            row_anchor=_row_anchor(item, fingerprint),
            word_filters=_word_filters(item, fingerprint),
            direction_from_column=_direction_from_column(item),
            drop_rows=_drop_rows(item),
            split_amount_balance=_split_amount_balance(item),
            amount_columns=_amount_columns(item),
            extract_patterns=_extract_patterns(item),
            preamble_mapping=_preamble_mapping(fingerprint),
            preamble_extractors=_preamble_extractors(fingerprint),
            conditional_mapping=_conditional_mapping(item),
            extract_mapping=_extract_mapping(item),
            require_monetary_value=_require_monetary_value(item),
            has_fingerprint=bool(fingerprint),
        ))
    return tuple(rules)


def load_excel_route_rules():
    return _load_excel_route_rules_versioned(*_yaml_version("excel_rules.yaml"))


def clear_route_rule_cache():
    """清理 Router 规则缓存，供测试和同进程内配置热更新显式调用。"""
    _load_yaml_versioned.cache_clear()
    _load_pdf_route_rules_versioned.cache_clear()
    _load_excel_route_rules_versioned.cache_clear()
