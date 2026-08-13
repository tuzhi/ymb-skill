"""PDF 模板路由决策。Reader 实现位于 readers.pdf。"""

from ymb_standardization_core.readers.registry import pdf_reader_registry
from ymb_standardization_core.readers.routing.evidence import build_pdf_routing_evidence
from ymb_standardization_core.readers.routing.rule_loader import load_pdf_route_rules


def _pdf_candidate(id, reader_id, file_type, bank, account_type, series_family, column_mapping,
                   identity_evidence, columns_evidence, route_evidence=None,
                   decision="matched", required_columns_evidence=None,
                   optional_columns_evidence=None, missing_required_columns=None,
                   missing_hints=None, required_reader_headers=None):
    return {
        "id": id,
        "fingerprint_id": id,
        "reader_id": reader_id,
        "decision": decision,
        "file_type": file_type,
        "bank": bank,
        "account_type": account_type,
        "series_family": series_family,
        "column_mapping": column_mapping,
        "identity_evidence": identity_evidence,
        "columns_evidence": columns_evidence,
        "required_columns_evidence": required_columns_evidence or [],
        "optional_columns_evidence": optional_columns_evidence or [],
        "missing_required_columns": missing_required_columns or [],
        "missing_hints": missing_hints or [],
        "required_reader_headers": required_reader_headers or {},
        "word_filters": route_evidence.get("word_filters", {}) if route_evidence else {},
        "drop_chars": route_evidence.get("drop_chars", []) if route_evidence else [],
        "direction_from_column": route_evidence.get("direction_from_column", {}) if route_evidence else {},
        "drop_rows": route_evidence.get("drop_rows", []) if route_evidence else [],
        "split_amount_balance": route_evidence.get("split_amount_balance", {}) if route_evidence else {},
        "amount_columns": route_evidence.get("amount_columns", []) if route_evidence else [],
        "extract_patterns": route_evidence.get("extract_patterns", []) if route_evidence else [],
        "dedupe_chars": route_evidence.get("dedupe_chars", False) if route_evidence else False,
        "header_merge": route_evidence.get("header_merge", {}) if route_evidence else {},
        "repeated_header": route_evidence.get("repeated_header", {}) if route_evidence else {},
        "preamble_mapping": route_evidence.get("preamble_mapping", {}) if route_evidence else {},
        "preamble_extractors": route_evidence.get("preamble_extractors", []) if route_evidence else [],
        "conditional_mapping": route_evidence.get("conditional_mapping", []) if route_evidence else [],
        "extract_mapping": route_evidence.get("extract_mapping", []) if route_evidence else [],
        "text_table": route_evidence.get("text_table", {}) if route_evidence else {},
        "source_order": route_evidence.get("source_order", "") if route_evidence else "",
        "date_order": route_evidence.get("date_order", "") if route_evidence else "",
        "require_monetary_value": route_evidence.get("require_monetary_value", False) if route_evidence else False,
        "reader_header_candidates": route_evidence.get("reader_header_candidates", []) if route_evidence else [],
        "row_anchor": route_evidence.get("row_anchor", {}) if route_evidence else {},
        "metadata_evidence": route_evidence.get("metadata_evidence", {}) if route_evidence else {},
        "style_evidence": route_evidence.get("style_evidence", []) if route_evidence else [],
        "date_format_evidence": route_evidence.get("date_format_evidence", []) if route_evidence else [],
    }


def _pdf_fallback(
    evidence,
    table_row_count,
    page_count,
    candidate_fingerprints=None,
    routing_evidence=None,
):
    reader_id = "pdfplumber_table" if table_row_count else "none"
    return {
        "reader_id": reader_id,
        "decision": "unmatched",
        "file_type": "pdf",
        "fingerprint_id": "",
        "account_type": "",
        "series_family": "",
        "dedupe_chars": False,
        "drop_chars": [],
        "column_mapping": {},
        "candidate_fingerprints": candidate_fingerprints or [],
        "routing_evidence": routing_evidence or {},
    }


def _choose_specific_candidate(candidates):
    if not candidates:
        return None
    def score(item):
        return (
            1 if item.get("decision") == "matched" else 0,
            len(item.get("columns_evidence", []))
            + len(item.get("metadata_evidence", {})) * 2
            + len(item.get("style_evidence", []))
            + len(item.get("date_format_evidence", [])),
        )

    by_score = sorted(candidates, key=score, reverse=True)
    if len(by_score) == 1:
        return by_score[0]
    if score(by_score[0]) > score(by_score[1]):
        return by_score[0]

    identified = [item for item in candidates if item.get("bank") and item.get("bank") != "未识别"]
    unidentified = [item for item in candidates if not item.get("bank") or item.get("bank") == "未识别"]
    if len(identified) == 1 and unidentified:
        return identified[0]
    return None


def _decide_pdf_route(
    candidates,
    evidence,
    table_row_count,
    page_count,
    candidate_fingerprints=None,
    routing_evidence=None,
):
    if len(candidates) == 1:
        return {**candidates[0], "routing_evidence": routing_evidence or {}}
    if not candidates:
        return _pdf_fallback(
            evidence,
            table_row_count,
            page_count,
            candidate_fingerprints=candidate_fingerprints,
            routing_evidence=routing_evidence,
        )
    specific = _choose_specific_candidate(candidates)
    if specific:
        return {**specific, "routing_evidence": routing_evidence or {}}
    return {
        "reader_id": "none",
        "decision": "ambiguous",
        "file_type": "pdf",
        "fingerprint_id": "",
        "column_mapping": {},
        "candidates": candidates,
        "candidate_fingerprints": candidate_fingerprints or [],
        "routing_evidence": routing_evidence or {},
    }


def route_pdf(text, table_row_count, page_count, context=None, rules=None):
    """识别 PDF 的解析路线。只判断模板和抽取模式，不在这里清洗交易数据。"""
    text = text or ""
    evidence = {
        "ext": ".pdf",
        "page_count": page_count,
        "text_length": len(text),
        "table_row_count": table_row_count,
    }
    candidates = []
    candidate_fingerprints = []
    routing_evidence = build_pdf_routing_evidence(
        context=context,
        reader_id="pdfplumber_table" if table_row_count else "none",
    )

    for rule in load_pdf_route_rules() if rules is None else rules:
        candidate = rule.fingerprint_candidate(text, context=context)
        if candidate:
            candidate_fingerprints.append(candidate)
        match = rule.match(text, context=context)
        if not match:
            continue
        candidates.append(_pdf_candidate(
            id=rule.id,
            reader_id=rule.reader_id,
            file_type=rule.file_type,
            bank=rule.bank,
            account_type=rule.account_type,
            series_family=rule.series_family,
            column_mapping=rule.column_mapping,
            identity_evidence=match["identity_evidence"],
            columns_evidence=match["columns_evidence"],
            decision=match["decision"],
            required_columns_evidence=match.get("required_columns_evidence"),
            optional_columns_evidence=match.get("optional_columns_evidence"),
            missing_required_columns=match.get("missing_required_columns"),
            missing_hints=match.get("missing_hints"),
            required_reader_headers=rule.required_reader_headers,
            route_evidence={
                "reader_header_candidates": (
                    rule.column_markers
                    + list(rule.required_reader_headers)
                    + list(rule.optional_columns)
                ),
                "word_filters": rule.word_filters,
                "drop_chars": rule.drop_chars,
                "direction_from_column": rule.direction_from_column,
                "drop_rows": rule.drop_rows,
                "split_amount_balance": rule.split_amount_balance,
                "amount_columns": rule.amount_columns,
                "extract_patterns": rule.extract_patterns,
                "dedupe_chars": rule.dedupe_chars,
                "header_merge": rule.header_merge,
                "repeated_header": rule.repeated_header,
                "preamble_mapping": rule.preamble_mapping,
                "preamble_extractors": rule.preamble_extractors,
                "conditional_mapping": rule.conditional_mapping,
                "extract_mapping": rule.extract_mapping,
                "text_table": rule.text_table,
                "source_order": rule.source_order,
                "date_order": rule.date_order,
                "require_monetary_value": rule.require_monetary_value,
                "row_anchor": rule.row_anchor,
                "metadata_evidence": match.get("metadata_evidence", {}),
                "style_evidence": match.get("style_evidence", []),
                "date_format_evidence": match.get("date_format_evidence", []),
            },
        ))

    return _decide_pdf_route(
        candidates,
        evidence,
        table_row_count,
        page_count,
        candidate_fingerprints=candidate_fingerprints,
        routing_evidence=routing_evidence,
    )


def read_pdf_rows(path, open_password=None):
    """兼容旧入口；实际 PDF 输入编排位于 readers.pdf_input。"""
    from ymb_standardization_core.readers.pdf_input import read_pdf_rows as implementation

    return implementation(path, open_password=open_password)
