import re

from ymb_standardization_core.readers.routing.rule_loader import load_pdf_route_rules


def _pdf_candidate(id, reader_id, file_type, bank, account_type, column_mapping,
                   identity_evidence, columns_evidence, route_evidence=None):
    return {
        "id": id,
        "fingerprint_id": id,
        "reader_id": reader_id,
        "decision": "matched",
        "file_type": file_type,
        "bank": bank,
        "account_type": account_type,
        "column_mapping": column_mapping,
        "identity_evidence": identity_evidence,
        "columns_evidence": columns_evidence,
        "column_transforms": route_evidence.get("column_transforms", {}) if route_evidence else {},
        "word_filters": route_evidence.get("word_filters", {}) if route_evidence else {},
        "direction_from_column": route_evidence.get("direction_from_column", {}) if route_evidence else {},
        "drop_rows": route_evidence.get("drop_rows", []) if route_evidence else [],
        "split_amount_balance": route_evidence.get("split_amount_balance", {}) if route_evidence else {},
        "amount_columns": route_evidence.get("amount_columns", []) if route_evidence else [],
        "extract_patterns": route_evidence.get("extract_patterns", []) if route_evidence else [],
        "preamble_mapping": route_evidence.get("preamble_mapping", {}) if route_evidence else {},
        "preamble_extractors": route_evidence.get("preamble_extractors", []) if route_evidence else [],
        "conditional_mapping": route_evidence.get("conditional_mapping", []) if route_evidence else [],
        "extract_mapping": route_evidence.get("extract_mapping", []) if route_evidence else [],
        "text_table_layout": route_evidence.get("text_table_layout", "") if route_evidence else "",
        "source_order": route_evidence.get("source_order", "") if route_evidence else "",
        "date_order": route_evidence.get("date_order", "") if route_evidence else "",
        "require_monetary_value": route_evidence.get("require_monetary_value", False) if route_evidence else False,
        "reader_header_candidates": route_evidence.get("reader_header_candidates", []) if route_evidence else [],
        "row_anchor": route_evidence.get("row_anchor", {}) if route_evidence else {},
        "metadata_evidence": route_evidence.get("metadata_evidence", {}) if route_evidence else {},
        "style_evidence": route_evidence.get("style_evidence", []) if route_evidence else [],
        "date_format_evidence": route_evidence.get("date_format_evidence", []) if route_evidence else [],
    }


def _pdf_fallback(evidence, table_row_count, page_count, candidate_fingerprints=None):
    reader_id = "pdfplumber_table" if table_row_count else "none"
    return {
        "reader_id": reader_id,
        "decision": "unmatched",
        "file_type": "pdf",
        "fingerprint_id": "",
        "account_type": "",
        "column_mapping": {},
        "candidate_fingerprints": candidate_fingerprints or [],
    }


def _choose_specific_candidate(candidates):
    if not candidates:
        return None
    def score(item):
        return (
            len(item.get("columns_evidence", []))
            + len(item.get("metadata_evidence", {})) * 2
            + len(item.get("style_evidence", []))
            + len(item.get("date_format_evidence", []))
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


def _decide_pdf_route(candidates, evidence, table_row_count, page_count, candidate_fingerprints=None):
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        return _pdf_fallback(evidence, table_row_count, page_count, candidate_fingerprints=candidate_fingerprints)
    specific = _choose_specific_candidate(candidates)
    if specific:
        return specific
    return {
        "reader_id": "none",
        "decision": "ambiguous",
        "file_type": "pdf",
        "fingerprint_id": "",
        "column_mapping": {},
        "candidates": candidates,
        "candidate_fingerprints": candidate_fingerprints or [],
    }


def route_pdf(text, table_row_count, page_count, context=None):
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

    for rule in load_pdf_route_rules():
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
            column_mapping=rule.column_mapping,
            identity_evidence=match["identity_evidence"],
            columns_evidence=match["columns_evidence"],
            route_evidence={
                "reader_header_candidates": rule.column_markers,
                "column_transforms": rule.column_transforms,
                "word_filters": rule.word_filters,
                "direction_from_column": rule.direction_from_column,
                "drop_rows": rule.drop_rows,
                "split_amount_balance": rule.split_amount_balance,
                "amount_columns": rule.amount_columns,
                "extract_patterns": rule.extract_patterns,
                "preamble_mapping": rule.preamble_mapping,
                "preamble_extractors": rule.preamble_extractors,
                "conditional_mapping": rule.conditional_mapping,
                "extract_mapping": rule.extract_mapping,
                "text_table_layout": rule.text_table_layout,
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
    )


def _extract_pdf_tables(pdf):
    """通用 PDF 表格抽取，只处理 pdfplumber 能识别出的结构化表格。"""
    rows = _extract_pdf_tables_default(pdf)
    if rows:
        return rows
    return _extract_pdf_tables_from_horizontal_lines(pdf)


def _drop_configured_rows(rows, rules):
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


def _split_amount_balance_column(rows, config):
    if not rows or not config:
        return rows
    import re

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


def _normalize_amount_columns(rows, columns):
    if not rows or not columns:
        return rows
    import re

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


def _extract_column_patterns(rows, patterns):
    if not rows or not patterns:
        return rows
    import re

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


def _apply_direction_from_column(rows, config):
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


def _postprocess_reader_rows(rows, route_info):
    rows = _drop_configured_rows(rows, (route_info or {}).get("drop_rows") or [])
    rows = _split_amount_balance_column(rows, (route_info or {}).get("split_amount_balance") or {})
    rows = _normalize_amount_columns(rows, (route_info or {}).get("amount_columns") or [])
    rows = _extract_column_patterns(rows, (route_info or {}).get("extract_patterns") or [])
    rows = _apply_direction_from_column(rows, (route_info or {}).get("direction_from_column") or {})
    return rows


def _extract_pdf_rows_by_reader(pdf, reader_id, route_info=None):
    route_info = route_info or {}
    column_transforms = route_info.get("column_transforms") or {}
    if reader_id == "pdfplumber_coordinate_table":
        coordinate_rows = _extract_pdf_coordinate_table_rows(
            pdf,
            route_info.get("reader_header_candidates") or [],
            route_info.get("row_anchor") or {},
            column_transforms=column_transforms,
            word_filters=route_info.get("word_filters") or {},
        )
        separator_rows = _extract_pdf_text_separator_table_rows(pdf)
        if separator_rows and (
            not coordinate_rows or len(coordinate_rows[0]) < len(separator_rows[0])
        ):
            return separator_rows
        return coordinate_rows
    if reader_id == "pdfplumber_grid_line_table":
        return _postprocess_reader_rows(_extract_pdf_grid_line_table_rows(
            pdf,
            route_info.get("reader_header_candidates") or [],
            route_info.get("row_anchor") or {},
            column_transforms=column_transforms,
            word_filters=route_info.get("word_filters") or {},
        ), route_info)
    if reader_id == "pdfplumber_line_table":
        return _extract_pdf_tables_from_horizontal_lines(pdf, column_transforms=column_transforms)
    if reader_id == "pdfplumber_table":
        rows = _extract_pdf_tables_default(
            pdf,
            column_transforms=column_transforms,
            word_filters=route_info.get("word_filters") or {},
            row_anchor=route_info.get("row_anchor") or {},
        )
        return _drop_configured_rows(rows, route_info.get("drop_rows") or [])
    return []


def _is_cjk_char(value):
    if not value:
        return False
    code = ord(value)
    return (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
    )


def _join_cjk_fragments(parts):
    cjk_join_punctuation = "，。；：！？、（(【《〈“‘"
    cjk_closing_punctuation = "）)】》〉”’"
    output = ""
    for part in parts:
        if not part:
            continue
        if not output:
            output = part
        elif (
            (_is_cjk_char(output[-1]) or output[-1] in cjk_join_punctuation)
            and (_is_cjk_char(part[0]) or part[0] in cjk_join_punctuation or part[0] in cjk_closing_punctuation)
        ):
            output += part
        else:
            output += " " + part
    return output.strip()


def _clean_pdf_cell(value, column_name="", column_transforms=None):
    normalized_value = (
        str(value or "")
        .replace("‑", "-")
        .replace("行", "行")
        .replace("易", "易")
    )
    parts = normalized_value.split()
    if not parts:
        return ""
    transform = (column_transforms or {}).get(str(column_name or "").strip(), {})
    newline = str(transform.get("newline") or "space").strip()
    if newline == "remove_all":
        cleaned = "".join(parts).strip()
    elif newline == "cjk_join":
        cleaned = _join_cjk_fragments(parts)
    else:
        cleaned = " ".join(parts).strip()
    return cleaned


def _clean_pdf_table_cells(row, headers=None, column_transforms=None):
    headers = headers or []
    return [
        _clean_pdf_cell(
            cell,
            headers[index] if index < len(headers) else "",
            column_transforms=column_transforms,
        )
        for index, cell in enumerate(row)
    ]


def _append_pdf_table_rows(all_rows, table_rows, header_sig, column_transforms=None):
    headers = all_rows[0] if header_sig and all_rows else []
    transform_columns = set((column_transforms or {}).keys())
    if transform_columns:
        for candidate in [*reversed(all_rows), *table_rows]:
            candidate_headers = _clean_pdf_table_cells(candidate)
            if transform_columns.intersection(candidate_headers):
                headers = candidate_headers
                break
    for r in table_rows:
        cells = _clean_pdf_table_cells(r, headers=headers, column_transforms=column_transforms)
        if not any(cells):
            continue
        sig = "|".join(cells)
        if header_sig is None:
            header_sig = sig
            all_rows.append(cells)
        elif sig == header_sig:
            continue
        else:
            all_rows.append(cells)
    return header_sig


def _pdf_table_row_anchor_matches(row, headers, row_anchor):
    import re

    column = str((row_anchor or {}).get("column") or "").strip()
    if not column or column not in headers:
        return False
    index = headers.index(column)
    text = str(row[index] if index < len(row) else "").strip()
    values = {
        str(value).strip()
        for value in (row_anchor or {}).get("values", [])
        if str(value).strip()
    }
    if values:
        return text in values
    pattern = str((row_anchor or {}).get("pattern") or "").strip()
    return bool(re.fullmatch(pattern, text)) if pattern else bool(text)


def _merge_pdf_table_continuation(previous, continuation, headers, column_transforms=None):
    width = max(len(headers), len(previous), len(continuation))
    merged = []
    for index in range(width):
        parts = []
        for row in (previous, continuation):
            value = str(row[index] if index < len(row) else "").strip()
            if value:
                parts.append(value)
        merged.append(_clean_pdf_cell(
            " ".join(parts),
            headers[index] if index < len(headers) else "",
            column_transforms=column_transforms,
        ))
    return merged


def _extract_pdf_tables_default(pdf, column_transforms=None, word_filters=None, row_anchor=None):
    all_rows = []
    header_sig = None
    merge_across_pages = (
        str((row_anchor or {}).get("continuation") or "").strip()
        == "until_next_anchor_across_pages"
    )
    for page in pdf.pages:
        page_start = len(all_rows)
        if word_filters:
            page = page.filter(lambda char: not _drop_word_filter_char(char, word_filters))
        for tbl in page.extract_tables():
            header_sig = _append_pdf_table_rows(all_rows, tbl, header_sig, column_transforms=column_transforms)
        if not merge_across_pages or page_start <= 1 or len(all_rows) <= page_start:
            continue
        headers = all_rows[0]
        previous = all_rows[page_start - 1]
        anchor_column = str((row_anchor or {}).get("column") or "").strip()
        anchor_index = headers.index(anchor_column) if anchor_column in headers else None
        while (
            anchor_index is not None
            and len(all_rows) > page_start
            and _pdf_table_row_anchor_matches(previous, headers, row_anchor)
        ):
            continuation = all_rows[page_start]
            continuation_anchor = str(
                continuation[anchor_index] if anchor_index < len(continuation) else ""
            ).strip()
            if continuation_anchor or not any(str(value or "").strip() for value in continuation):
                break
            all_rows[page_start - 1] = _merge_pdf_table_continuation(
                previous,
                continuation,
                headers,
                column_transforms=column_transforms,
            )
            previous = all_rows[page_start - 1]
            del all_rows[page_start]
    return all_rows


def _is_horizontal_edge(edge):
    return abs(float(edge.get("y0", 0)) - float(edge.get("y1", 0))) < 1


def _infer_vertical_boundaries_from_horizontal_edges(page):
    groups = {}
    for edge in getattr(page, "edges", []):
        if not _is_horizontal_edge(edge):
            continue
        top = round(float(edge.get("top", edge.get("y0", 0))), 1)
        groups.setdefault(top, []).append(edge)
    if not groups:
        return []
    segment_group = max(groups.values(), key=len)
    if len(segment_group) < 3:
        return []
    xs = []
    for edge in segment_group:
        xs.extend([float(edge.get("x0", 0)), float(edge.get("x1", 0))])
    boundaries = []
    for x in sorted(xs):
        if not boundaries or abs(x - boundaries[-1]) > 2:
            boundaries.append(x)
    return boundaries if len(boundaries) >= 4 else []


def _looks_like_statement_header(row):
    text = "|".join(str(c or "") for c in row)
    markers = [
        "交易日期", "交易时间", "借方", "贷方", "收入", "支出",
        "余额", "摘要", "收(付)方", "对方", "账号", "交易类型",
    ]
    return sum(1 for marker in markers if marker in text) >= 3


def _extract_pdf_tables_from_horizontal_lines(pdf, column_transforms=None):
    """Fallback for ruled PDFs with horizontal row lines but no vertical borders."""
    all_rows = []
    header_sig = None
    for page in pdf.pages:
        boundaries = _infer_vertical_boundaries_from_horizontal_edges(page)
        if not boundaries:
            continue
        settings = {
            "vertical_strategy": "explicit",
            "explicit_vertical_lines": boundaries,
            "horizontal_strategy": "lines",
            "intersection_tolerance": 5,
            "snap_tolerance": 3,
            "join_tolerance": 3,
            "text_x_tolerance": 1,
            "text_y_tolerance": 3,
        }
        for tbl in page.extract_tables(table_settings=settings):
            header_sig = _append_pdf_table_rows(all_rows, tbl, header_sig, column_transforms=column_transforms)
    if not all_rows or not _looks_like_statement_header(all_rows[0]):
        return []
    return all_rows


def _group_words_by_top(words):
    groups = {}
    for word in words:
        top = round(float(word.get("top", 0)), 1)
        groups.setdefault(top, []).append(word)
    return groups


def _coordinate_header_match(group, candidate_header):
    tokens = [token for token in str(candidate_header or "").split() if token]
    if not tokens:
        return None
    sorted_group = sorted(group, key=lambda item: float(item.get("x0", 0)))
    for index in range(0, len(sorted_group) - len(tokens) + 1):
        chunk = sorted_group[index:index + len(tokens)]
        if [str(word.get("text") or "").strip() for word in chunk] != tokens:
            continue
        return {
            "x0": float(chunk[0].get("x0", 0)),
            "x1": float(chunk[-1].get("x1", chunk[-1].get("x0", 0))),
        }
    return None


def _coordinate_header(words, candidate_headers):
    candidate_headers = [str(header).strip() for header in candidate_headers if str(header).strip()]
    best = None
    for top, group in _group_words_by_top(words).items():
        by_text = {}
        for header in candidate_headers:
            match = _coordinate_header_match(group, header)
            if match:
                by_text[header] = match
        if len(by_text) < 3:
            continue
        if best is None or len(by_text) > len(best[1]):
            best = (top, by_text)
    if not best:
        return None, None, None
    top, by_text = best
    headers = [header for header in candidate_headers if header in by_text]
    starts = [float(by_text[header].get("x0", 0)) for header in headers]
    if starts != sorted(starts):
        pairs = sorted(zip(headers, starts), key=lambda item: item[1])
        headers = [header for header, _start in pairs]
        starts = [start for _header, start in pairs]
    return top, headers, starts


def _coordinate_boundaries(page_width, starts):
    return [0] + [(left + right) / 2 for left, right in zip(starts, starts[1:])] + [page_width + 10]


def _coordinate_index(x, boundaries):
    for index in range(len(boundaries) - 1):
        if boundaries[index] <= x < boundaries[index + 1]:
            return index
    return None


def _is_coordinate_row_anchor(word, anchor_x, anchor_header, row_anchor=None):
    import re

    text = str(word.get("text") or "").strip()
    x0 = float(word.get("x0", 0))
    if not (anchor_x - 10 <= x0 <= anchor_x + 20):
        return False
    anchor_values = {
        str(value).strip()
        for value in (row_anchor or {}).get("values", [])
        if str(value).strip()
    }
    if anchor_values:
        return text in anchor_values
    pattern = str((row_anchor or {}).get("pattern") or "").strip()
    if pattern:
        return bool(re.fullmatch(pattern, text))
    if "序号" in str(anchor_header):
        return bool(re.fullmatch(r"\d{1,6}", text))
    return bool(text)


def _is_coordinate_noise_word(word):
    text = str(word.get("text") or "").strip()
    if not text:
        return True
    return (
        text.startswith("第")
        or text.startswith("该交易明细因")
        or text == "明细内容仅供参考"
    )


def _drop_word_filter_char(char, word_filters):
    text = str(char.get("text") or "")
    for item in (word_filters or {}).get("drop_chars", []):
        if item.get("rotated"):
            matrix = char.get("matrix") or (1, 0, 0, 1)
            if len(matrix) < 4 or (
                abs(float(matrix[1])) <= 0.001 and abs(float(matrix[2])) <= 0.001
            ):
                continue
        chars = {str(value) for value in item.get("text_any", [])}
        if chars and text not in chars:
            continue
        font_contains = str(item.get("fontname_contains") or "")
        if font_contains and font_contains not in str(char.get("fontname") or ""):
            continue
        min_size = item.get("min_size")
        if min_size is not None and float(char.get("size") or 0) < float(min_size):
            continue
        max_size = item.get("max_size")
        if max_size is not None and float(char.get("size") or 0) > float(max_size):
            continue
        return True
    return False


def _coordinate_page_words(page, word_filters=None):
    if word_filters:
        page = page.filter(lambda char: not _drop_word_filter_char(char, word_filters))
    return page.extract_words(x_tolerance=1, y_tolerance=3, keep_blank_chars=False)


def _coordinate_label_tokens(group):
    labels = []
    current = []
    current_x0 = None
    for word in sorted(group, key=lambda item: float(item.get("x0", 0))):
        text = str(word.get("text") or "").strip()
        if not text:
            continue
        if current_x0 is None:
            current_x0 = float(word.get("x0", 0))
        current.append(text)
        if ":" in text or "：" in text:
            label = "".join(current).replace(":", "").replace("：", "").strip()
            if label:
                labels.append((label, current_x0))
            current = []
            current_x0 = None
    return labels


def _coordinate_metadata_preamble(pdf, route_info):
    if not pdf.pages:
        return ""
    page = pdf.pages[0]
    words = _coordinate_page_words(page, word_filters=(route_info or {}).get("word_filters") or {})
    header_top, _headers, _starts = _coordinate_header(
        words,
        (route_info or {}).get("reader_header_candidates") or [],
    )
    if header_top is None:
        return ""
    groups = {
        top: sorted(group, key=lambda item: float(item.get("x0", 0)))
        for top, group in sorted(_group_words_by_top(words).items())
        if top < header_top
    }
    tops = sorted(groups)
    output = []
    for index, top in enumerate(tops[:-1]):
        labels = _coordinate_label_tokens(groups[top])
        if not labels:
            continue
        next_group = groups[tops[index + 1]]
        if tops[index + 1] - top > 18:
            continue
        label_bounds = [x0 for _label, x0 in labels] + [page.width + 10]
        for label_index, (label, _x0) in enumerate(labels):
            left = label_bounds[label_index] - 5
            right = label_bounds[label_index + 1] - 5
            value = " ".join(
                str(word.get("text") or "").strip()
                for word in next_group
                if left <= float(word.get("x0", 0)) < right
            ).strip()
            if value:
                output.append(f"{label}: {value}")
    return "\n".join(output)


def _coordinate_stop_top(words, word_filters=None):
    markers = [
        str(value).strip()
        for value in (word_filters or {}).get("stop_line_contains_any", [])
        if str(value).strip()
    ]
    if not markers:
        return None
    for top, group in sorted(_group_words_by_top(words).items()):
        line = " ".join(str(word.get("text") or "") for word in group)
        if any(marker in line for marker in markers):
            return float(top)
    return None


def _coordinate_cell_text(header, cell, column_transforms=None):
    text = " ".join(value for _top, _x0, value in sorted(cell)).strip()
    return _clean_pdf_cell(text, header, column_transforms=column_transforms)


def _grid_line_vertical_boundaries(page, min_segments=5, body_top_min=None):
    counts = {}
    for edge in page.edges:
        if edge.get("orientation") != "v":
            continue
        top = float(edge.get("top", 0))
        bottom = float(edge.get("bottom", 0))
        if body_top_min is not None and bottom <= float(body_top_min):
            continue
        if bottom - top < 8:
            continue
        x = round(float(edge.get("x0", 0)), 1)
        counts[x] = counts.get(x, 0) + 1
    boundaries = sorted(x for x, count in counts.items() if count >= min_segments)
    return boundaries if len(boundaries) >= 3 else []


def _grid_line_anchor_text_match(word, row_anchor):
    import re

    text = str(word.get("text") or "").strip()
    anchor_values = {
        str(value).strip()
        for value in (row_anchor or {}).get("values", [])
        if str(value).strip()
    }
    if anchor_values:
        return text in anchor_values
    pattern = str((row_anchor or {}).get("pattern") or "").strip()
    if pattern:
        return bool(re.fullmatch(pattern, text))
    return bool(text)


def _grid_line_header_match(text, candidate_header):
    tokens = [token.lower() for token in str(candidate_header or "").split() if token]
    if not tokens:
        return False
    text_tokens = [token.lower() for token in str(text or "").split() if token]
    pos = 0
    for token in tokens:
        try:
            found = text_tokens.index(token, pos)
        except ValueError:
            return False
        pos = found + 1
    return True


def _grid_line_headers(words, boundaries, candidate_headers, first_anchor_top):
    candidate_headers = [str(header).strip() for header in candidate_headers if str(header).strip()]
    headers = []
    for index, (left, right) in enumerate(zip(boundaries, boundaries[1:])):
        col_words = [
            word for word in words
            if left <= float(word.get("x0", 0)) < right
            and first_anchor_top - 45 <= float(word.get("top", 0)) < first_anchor_top
        ]
        text = " ".join(str(word.get("text") or "").strip() for word in sorted(
            col_words,
            key=lambda item: (float(item.get("top", 0)), float(item.get("x0", 0))),
        ))
        match = ""
        for header in candidate_headers:
            if _grid_line_header_match(text, header):
                match = header
                break
        headers.append(match or f"列{index + 1}")
    return headers


def _coordinate_row_bounds(index, anchors, body_top_min, page_height, row_anchor):
    anchor_top = anchors[index][0]
    continuation = str((row_anchor or {}).get("continuation") or "").strip()
    if continuation == "until_next_anchor":
        start_top = max(body_top_min, anchor_top - 0.5)
        end_top = anchors[index + 1][0] - 0.5 if index + 1 < len(anchors) else page_height - 25
        return start_top, end_top
    start_top = (anchors[index - 1][0] + anchor_top) / 2 if index else body_top_min
    end_top = (anchor_top + anchors[index + 1][0]) / 2 if index + 1 < len(anchors) else page_height - 25
    return start_top, end_top


def _extract_pdf_grid_line_table_rows(pdf, candidate_headers, row_anchor=None, column_transforms=None,
                                      word_filters=None):
    """Use real vertical ruling lines for x columns and row_anchor words for y rows."""
    all_rows = []
    output_headers = None
    row_anchor = row_anchor or {}
    for page in pdf.pages:
        words = _coordinate_page_words(page, word_filters=word_filters)
        first_anchor_top = min(
            (float(word.get("top", 0)) for word in words if _grid_line_anchor_text_match(word, row_anchor)),
            default=None,
        )
        if first_anchor_top is None:
            continue
        boundaries = _grid_line_vertical_boundaries(page, body_top_min=first_anchor_top)
        if not boundaries:
            continue
        anchor_column = str(row_anchor.get("column") or "").strip()
        anchor_index_hint = 0
        anchors = []
        for index, (left, right) in enumerate(zip(boundaries, boundaries[1:])):
            for word in words:
                if not (left <= float(word.get("x0", 0)) < right):
                    continue
                if _is_coordinate_row_anchor(word, left, anchor_column, row_anchor):
                    anchors.append((float(word.get("top", 0)), word, index))
            if anchors:
                anchor_index_hint = index
                break
        anchors = sorted((top, word) for top, word, _index in anchors)
        if not anchors:
            continue
        headers = _grid_line_headers(words, boundaries, candidate_headers, anchors[0][0])
        if anchor_column and anchor_column in headers:
            anchor_index = headers.index(anchor_column)
        else:
            anchor_index = anchor_index_hint
        if output_headers is None:
            output_headers = headers
            all_rows.append(headers)
        elif headers != output_headers:
            headers = output_headers
        body_top_min = anchors[0][0] - 1
        stop_top = _coordinate_stop_top(words, word_filters=word_filters)
        drop_bottom_margin = (word_filters or {}).get("drop_words_below_page_bottom")
        body_words = [
            word for word in words
            if float(word.get("top", 0)) > body_top_min
            and (stop_top is None or float(word.get("top", 0)) < stop_top)
            and (
                drop_bottom_margin is None
                or float(word.get("top", 0)) < page.height - float(drop_bottom_margin)
            )
            and not _is_coordinate_noise_word(word)
        ]
        anchors = sorted(
            (float(word.get("top", 0)), word)
            for word in body_words
            if (
                boundaries[anchor_index] <= float(word.get("x0", 0)) < boundaries[anchor_index + 1]
                and _is_coordinate_row_anchor(word, boundaries[anchor_index], headers[anchor_index], row_anchor)
            )
        )
        for index, (anchor_top, _word) in enumerate(anchors):
            start_top, end_top = _coordinate_row_bounds(index, anchors, body_top_min, page.height, row_anchor)
            cells = [[] for _ in headers]
            for word in body_words:
                top = float(word.get("top", 0))
                if not (start_top < top <= end_top):
                    continue
                col = _coordinate_index(float(word.get("x0", 0)), boundaries)
                if col is None or col >= len(cells):
                    continue
                cells[col].append((top, float(word.get("x0", 0)), str(word.get("text") or "").strip()))
            row = [
                _coordinate_cell_text(headers[cell_index], cell, column_transforms=column_transforms)
                for cell_index, cell in enumerate(cells)
            ]
            if row and row[0]:
                all_rows.append(row)
    return all_rows if len(all_rows) > 1 else []


def _extract_pdf_coordinate_table_rows(pdf, candidate_headers, row_anchor=None, column_transforms=None,
                                        word_filters=None):
    """Recover visual tables whose text words have stable column coordinates."""
    all_rows = []
    output_headers = None
    output_starts = None
    row_anchor = row_anchor or {}
    for page in pdf.pages:
        words = _coordinate_page_words(page, word_filters=word_filters)
        header_top, page_headers, page_starts = _coordinate_header(words, candidate_headers)
        if page_starts is not None:
            headers = page_headers
            starts = page_starts
            body_top_min = header_top + 5
        elif output_headers is not None and output_starts is not None:
            headers = output_headers
            starts = output_starts
            body_top_min = 0
        else:
            continue
        anchor_column = str(row_anchor.get("column") or "").strip()
        anchor_index = headers.index(anchor_column) if anchor_column in headers else 0
        if output_headers is None:
            output_headers = headers
            output_starts = starts
            all_rows.append(headers)
        elif headers != output_headers:
            continue
        else:
            output_starts = starts
        boundaries = _coordinate_boundaries(page.width, starts)
        drop_bottom_margin = (word_filters or {}).get("drop_words_below_page_bottom")
        stop_top = _coordinate_stop_top(words, word_filters=word_filters)
        body_words = [
            word for word in words
            if float(word.get("top", 0)) > body_top_min
            and (stop_top is None or float(word.get("top", 0)) < stop_top)
            and (
                drop_bottom_margin is None
                or float(word.get("top", 0)) < page.height - float(drop_bottom_margin)
            )
            and not _is_coordinate_noise_word(word)
        ]
        anchors = sorted(
            (float(word.get("top", 0)), word)
            for word in body_words
            if _is_coordinate_row_anchor(
                word,
                starts[anchor_index],
                headers[anchor_index],
                row_anchor,
            )
        )
        for index, (anchor_top, _word) in enumerate(anchors):
            start_top, end_top = _coordinate_row_bounds(
                index,
                anchors,
                body_top_min,
                page.height,
                row_anchor,
            )
            cells = [[] for _ in headers]
            for word in body_words:
                top = float(word.get("top", 0))
                if not (start_top < top <= end_top):
                    continue
                col = _coordinate_index(float(word.get("x0", 0)), boundaries)
                if col is None or col >= len(cells):
                    continue
                cells[col].append((top, float(word.get("x0", 0)), str(word.get("text") or "").strip()))
            row = [
                _coordinate_cell_text(
                    headers[cell_index],
                    cell,
                    column_transforms=column_transforms,
                )
                for cell_index, cell in enumerate(cells)
            ]
            if row and row[0]:
                all_rows.append(row)
    return all_rows if len(all_rows) > 1 else []


def _preamble_before_reader_header(text, headers):
    headers = [str(header or "").strip() for header in headers if str(header or "").strip()]
    if not headers:
        return str(text or "")
    lines = str(text or "").splitlines()
    for index, line in enumerate(lines):
        normalized = _clean_pdf_cell(line)
        hits = sum(1 for header in headers if header in normalized)
        if hits >= min(3, len(headers)):
            return "\n".join(lines[:index])
    return str(text or "")


def _clean_payment_cell(value):
    return " ".join(str(value or "").split()).strip()


def _clean_payment_order_id(value):
    return "".join(str(value or "").split()).strip()


def _annotate_payment_order_state(rows):
    if not rows:
        return rows
    headers = [_clean_payment_cell(header) for header in rows[0]]
    required = {"收/支", "交易订单号", "商家订单号"}
    if not required.issubset(set(headers)):
        return rows

    direction_index = headers.index("收/支")
    trade_order_index = headers.index("交易订单号")
    merchant_order_index = headers.index("商家订单号")
    normal_orders = set()
    normalized_rows = [headers]

    for row in rows[1:]:
        cells = list(row) + [""] * max(0, len(headers) - len(row))
        cells = cells[:len(headers)]
        merchant_order = _clean_payment_order_id(cells[merchant_order_index])
        direction = _clean_payment_cell(cells[direction_index])
        if merchant_order and direction in {"收入", "支出"}:
            normal_orders.add(merchant_order)
        normalized_rows.append(cells)

    output = [headers]
    for cells in normalized_rows[1:]:
        merchant_order = _clean_payment_order_id(cells[merchant_order_index])
        trade_order = _clean_payment_order_id(cells[trade_order_index])
        direction = _clean_payment_cell(cells[direction_index])
        parts = []
        if merchant_order:
            parts.append(f"支付宝商家订单号={merchant_order}")
        if trade_order:
            parts.append(f"支付宝交易订单号={trade_order}")
        if direction.startswith("不计"):
            if merchant_order and merchant_order in normal_orders:
                parts.append("支付宝订单状态=取消/退款关联")
            elif merchant_order:
                parts.append("支付宝订单状态=平台订单未配对不计收支")
            else:
                parts.append("支付宝订单状态=不计收支无商家订单号")
        if parts:
            cells = list(cells)
            cells[merchant_order_index] = "；".join(parts)
        output.append(cells)
    return output


TEXT_SEPARATOR_TABLE_HEADER = [
    "交易时间",
    "存入/支取",
    "对方账号",
    "对方户名",
    "对方行",
    "交易后余额",
    "交易渠道",
    "摘要",
    "备注",
]


def _split_text_separator_date_line(line):
    import re

    match = re.match(r"^(20\d{2}-\d{2}-\d{2})(?:\s+(.*))?$", (line or "").strip())
    if not match:
        return None
    remainder = (match.group(2) or "").strip()
    parts = remainder.split(maxsplit=1)
    has_account = bool(parts and re.fullmatch(r"\d{6,}", parts[0]))
    return {
        "date": match.group(1),
        "account_head": parts[0] if has_account else "",
        "bank_head": (parts[1] if len(parts) > 1 else "") if has_account else remainder,
    }


def _split_text_separator_time_line(line):
    import re

    match = re.match(r"^(\d{2}:\d{2}:\d{2})(?:\s+(\d+))?(?:\s+(.*))?$", (line or "").strip())
    if not match:
        return None
    return {
        "time": match.group(1),
        "account_tail": (match.group(2) or "").strip(),
        "bank_tail": (match.group(3) or "").strip(),
    }


def _split_text_separator_amount_line(line, bank_head, bank_tail):
    import re

    tokens = (line or "").split()
    if len(tokens) < 5:
        return None
    transfer_flag = tokens[0]
    amount_idx = None
    amount_re = re.compile(r"^(?:--|[+-])\d[\d,]*\.\d{1,2}$")
    for idx in range(1, len(tokens)):
        if amount_re.match(tokens[idx]):
            amount_idx = idx
            break
    if amount_idx is None:
        return None
    balance_idx = None
    unsigned_amount_re = re.compile(r"^\d[\d,]*\.\d{1,2}$")
    for idx in range(amount_idx + 1, len(tokens)):
        if unsigned_amount_re.match(tokens[idx]):
            balance_idx = idx
            break
    if balance_idx is None:
        return None

    before_balance = tokens[amount_idx + 1:balance_idx]
    if bank_head or bank_tail:
        bank_token_idx = next(
            (
                idx for idx, token in enumerate(before_balance[1:], start=1)
                if any(marker in token for marker in ("银行", "信用社", "农商", "农村商业", "村镇"))
            ),
            None,
        )
        if bank_token_idx is None:
            counterparty_name = " ".join(before_balance).strip()
            inline_bank = ""
        else:
            counterparty_name = " ".join(before_balance[:bank_token_idx]).strip()
            inline_bank = " ".join(before_balance[bank_token_idx:]).strip()
        counterparty_bank = " ".join(x for x in [bank_head, inline_bank, bank_tail] if x).strip()
    else:
        counterparty_name = before_balance[0] if before_balance else ""
        counterparty_bank = " ".join(before_balance[1:]).strip()

    after_balance = tokens[balance_idx + 1:]
    channel = after_balance[0] if len(after_balance) > 0 else ""
    summary = after_balance[1] if len(after_balance) > 1 else ""
    remark = " ".join(after_balance[2:]).strip() if len(after_balance) > 2 else ""
    amount = tokens[amount_idx].replace(",", "")
    if amount.startswith("--"):
        amount = "+" + amount.lstrip("-")
    return {
        "transfer_flag": transfer_flag,
        "amount": amount,
        "counterparty_name": counterparty_name,
        "counterparty_bank": counterparty_bank,
        "balance": tokens[balance_idx].replace(",", ""),
        "channel": channel,
        "summary": summary,
        "remark": remark,
    }


def _parse_text_separator_transaction(lines, start_idx):
    date_part = _split_text_separator_date_line(lines[start_idx])
    if not date_part or start_idx + 2 >= len(lines):
        return None
    amount_line = lines[start_idx + 1]
    time_part = _split_text_separator_time_line(lines[start_idx + 2])
    if not time_part:
        return None
    amount_part = _split_text_separator_amount_line(
        amount_line,
        date_part["bank_head"],
        time_part["bank_tail"],
    )
    if not amount_part:
        return None

    account = "".join(x for x in [date_part["account_head"], time_part["account_tail"]] if x).strip()
    return [
        f"{date_part['date']} {time_part['time']}",
        amount_part["amount"],
        account,
        amount_part["counterparty_name"],
        amount_part["counterparty_bank"],
        amount_part["balance"],
        amount_part["channel"],
        amount_part["summary"],
        amount_part["remark"],
    ]


def _parse_text_separator_page_boundary_transaction(date_line, time_line):
    """Parse a row whose first two physical lines were merged at a PDF page footer."""
    import re

    match = re.match(
        r"^(20\d{2}-\d{2}-\d{2})\s+(\S+)\s+((?:--|[+-])\d[\d,]*\.\d{1,2})"
        r"(?:\s+(\d{6,}))?(?:\s+(.*))?$",
        str(date_line or "").strip(),
    )
    time_part = _split_text_separator_time_line(time_line)
    if not match or not time_part:
        return None
    account_head = (match.group(4) or "").strip()
    amount_line = " ".join(
        part for part in [match.group(2), match.group(3), (match.group(5) or "").strip()] if part
    )
    amount_part = _split_text_separator_amount_line(amount_line, "", time_part["bank_tail"])
    if not amount_part:
        return None
    return [
        f"{match.group(1)} {time_part['time']}",
        amount_part["amount"],
        "".join(part for part in [account_head, time_part["account_tail"]] if part),
        amount_part["counterparty_name"],
        amount_part["counterparty_bank"],
        amount_part["balance"],
        amount_part["channel"],
        amount_part["summary"],
        amount_part["remark"],
    ]


def _is_text_separator_line(line):
    text = str(line or "").strip()
    return len(text) >= 8 and len(set(text)) == 1 and text[0] in {"—", "-", "_", "─"}


def _extract_pdf_text_separator_table_rows(pdf):
    """Read text-layer tables whose separators are text glyphs, not PDF line objects."""
    rows = [TEXT_SEPARATOR_TABLE_HEADER]
    import re

    all_lines = []
    for page in pdf.pages:
        text = page.extract_text() or ""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not any(_is_text_separator_line(line) for line in lines):
            continue
        all_lines.extend(lines)

    idx = 0
    while idx < len(all_lines):
        if re.match(r"^20\d{2}-\d{2}-\d{2}(?:\s|$)", all_lines[idx]):
            parsed = _parse_text_separator_transaction(all_lines, idx)
            if not parsed and re.search(r"\s(?:--|[+-])\d[\d,]*\.\d{1,2}(?:\s|$)", all_lines[idx]):
                for continuation_idx in range(idx + 1, min(idx + 20, len(all_lines))):
                    if re.match(r"^20\d{2}-\d{2}-\d{2}(?:\s|$)", all_lines[continuation_idx]):
                        break
                    if re.match(r"^\d{2}:\d{2}:\d{2}(?:\s|$)", all_lines[continuation_idx]):
                        parsed = _parse_text_separator_page_boundary_transaction(
                            all_lines[idx], all_lines[continuation_idx]
                        )
                        break
            if parsed:
                rows.append(parsed)
                idx += 3
                continue
        idx += 1
    return rows if len(rows) > 1 else []


def _is_noise_text_table_line(line):
    text = str(line or "").strip()
    if not text:
        return True
    if re.match(r"^\d+/\d+$", text):
        return True
    noise_markers = (
        "Transaction Statement",
        "Account No",
        "Account Type",
        "Sub Branch",
        "Verification Code",
        "Transaction Type Counter Party",
        "Transaction Type C o unter Party",
        "Date Currency",
        "Amount",
        "Balance",
        "Name Account",
        "合同ID号",
        "版本:",
        "发布时间:",
        "温馨提示",
        "记账日期 货币 交易金额 联机余额 交易摘要 对手信息",
    )
    return text == "Transaction" or any(marker in text for marker in noise_markers)


def _parse_currency_text_row(line):
    import re

    text = str(line or "").strip()
    match = re.match(
        r"^(?P<date>20\d{2}[-/]?\d{2}[-/]?\d{2})\s+"
        r"(?P<currency>[A-Z]{3})\s+"
        r"(?P<amount>[+-]?\d[\d,]*\.\d{2})\s+"
        r"(?P<balance>[+-]?\d[\d,]*\.\d{2})\s+"
        r"(?P<tail>.+)$",
        text,
    )
    if not match:
        return None
    tail = match.group("tail").strip()
    parts = tail.split(maxsplit=1)
    summary = parts[0] if parts else ""
    counterparty = parts[1] if len(parts) > 1 else ""
    return [
        match.group("date"),
        match.group("currency"),
        match.group("amount"),
        match.group("balance"),
        summary,
        counterparty,
    ]


def _parse_cmbc_personal_text_row(line):
    import re

    text = str(line or "").strip()
    match = re.match(
        r"^(?P<voucher_type>\S+)\s+"
        r"(?P<voucher_no>\d[\d*]{5,})\s+"
        r"(?P<date>20\d{2}/\d{2}/\d{2})\s+"
        r"(?P<time>\d{2}:\d{2}:\d{2})\s+"
        r"(?P<tail>.+)$",
        text,
    )
    if not match:
        match = re.match(
            r"^(?P<date>20\d{2}/\d{2}/\d{2})\s+"
            r"(?P<time>\d{2}:\d{2}:\d{2})\s+"
            r"(?P<tail>.+)$",
            text,
        )
    if not match:
        return None

    tokens = match.group("tail").split()
    amount_indexes = [
        idx for idx, token in enumerate(tokens)
        if re.match(r"^[+-]?\d[\d,]*\.\d{2}$", token)
    ]
    if len(amount_indexes) < 2:
        return None
    amount_idx, balance_idx = amount_indexes[:2]
    summary = " ".join(tokens[:amount_idx])
    after = tokens[balance_idx + 1:]
    current_flag = after[0] if len(after) > 0 else ""
    channel = after[1] if len(after) > 1 else ""
    institution = after[2] if len(after) > 2 else ""
    counterparty = " ".join(after[3:]) if len(after) > 3 else ""
    return [
        match.groupdict().get("voucher_type") or "",
        match.groupdict().get("voucher_no") or "",
        f"{match.group('date')} {match.group('time')}",
        summary,
        tokens[amount_idx],
        tokens[balance_idx],
        current_flag,
        channel,
        institution,
        counterparty,
        "",
    ]


def _extract_pdf_text_table_rows(text, text_table_kind):
    """Fallback for text-layer statement PDFs where extract_tables() returns no rows."""
    if text_table_kind == "currency":
        header = ["记账日期", "货币", "交易金额", "联机余额", "交易摘要", "对手信息"]
        rows = [header]
        pending = []
        for raw_line in str(text or "").splitlines():
            line = raw_line.strip()
            if "温馨提示" in line:
                pending = []
                break
            parsed = _parse_currency_text_row(line)
            if parsed:
                if pending:
                    continuation = " ".join(pending).strip()
                    if not parsed[-1]:
                        parsed[-1] = continuation
                    elif len(rows) > 1:
                        rows[-1][-1] = (rows[-1][-1] + " " + continuation).strip()
                    pending = []
                rows.append(parsed)
            elif _is_noise_text_table_line(line):
                if pending and len(rows) > 1:
                    continuation = " ".join(pending).strip()
                    if not re.fullmatch(r"[—_-]{5,}", continuation):
                        rows[-1][-1] = (rows[-1][-1] + " " + continuation).strip()
                pending = []
            elif len(rows) > 1 and line and not re.fullmatch(r"[—_-]{5,}", line):
                pending.append(line)
        if pending and len(rows) > 1:
            rows[-1][-1] = (rows[-1][-1] + " " + " ".join(pending)).strip()
        return rows if len(rows) > 1 else []

    if text_table_kind == "cmbc_personal":
        header = [
            "凭证类型", "凭证号码", "交易时间", "摘要", "交易金额", "账户余额",
            "现转标志", "交易渠道", "交易机构", "对方户名/账号", "对方行名",
        ]
        rows = [header]
        for raw_line in str(text or "").splitlines():
            line = raw_line.strip()
            parsed = _parse_cmbc_personal_text_row(line)
            if parsed:
                rows.append(parsed)
            elif len(rows) > 1 and line and not _is_noise_text_table_line(line):
                voucher_continuation = re.match(r"^(\d{4,})(?:\s+(.*))?$", line)
                if voucher_continuation and rows[-1][1]:
                    rows[-1][1] = (rows[-1][1] + voucher_continuation.group(1)).strip()
                    rest = (voucher_continuation.group(2) or "").strip()
                    if rest:
                        rows[-1][9] = (rows[-1][9] + " " + rest).strip()
                else:
                    rows[-1][9] = (rows[-1][9] + " " + line).strip()
        return rows if len(rows) > 1 else []

    return []


def _pdf_context(pdf, text):
    """抽取 PDF 元数据、首页字体样式、文本行和日期格式指纹。"""
    context = {
        "metadata": dict(pdf.metadata or {}),
        "styles": [],
        "lines": str(text or "").splitlines(),
        "date_patterns": [],
    }
    import re

    if re.search(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}", text or ""):
        context["date_patterns"].append("yyyy-mm-dd hh:mm:ss")
    if re.search(r"\d{4}-\d{2}-\d{2}(?!\s+\d{2}:\d{2}:\d{2})", text or ""):
        context["date_patterns"].append("yyyy-mm-dd")
    if re.search(r"\d{2}-\d{2}-\d{2}", text or ""):
        context["date_patterns"].append("yy-mm-dd")

    if not pdf.pages:
        return context
    page = pdf.pages[0]
    try:
        words = page.extract_words(extra_attrs=["fontname", "size"])
    except TypeError:
        words = page.extract_words()
    for word in words[:300]:
        context["styles"].append({
            "text": str(word.get("text") or "").strip(),
            "font": word.get("fontname") or "",
            "size": word.get("size"),
            "bold": False,
            "row": None,
            "col": None,
            "top": word.get("top"),
            "x0": word.get("x0"),
            "x1": word.get("x1"),
            "page_width": page.width,
        })
    return context


def _pdf_password_candidates(open_password):
    if not open_password:
        return [None]
    candidates = [open_password]
    for encoding in ("utf-8", "gbk", "gb18030"):
        try:
            proxy = str(open_password).encode(encoding).decode("latin1")
        except UnicodeError:
            continue
        if proxy not in candidates:
            candidates.append(proxy)
    return candidates


def _open_pdf(path, open_password=None):
    import pdfplumber
    from pdfminer.pdfdocument import PDFPasswordIncorrect

    last_error = None
    for password in _pdf_password_candidates(open_password):
        try:
            open_kwargs = {"password": password} if password else {}
            return pdfplumber.open(path, **open_kwargs)
        except (UnicodeEncodeError, PDFPasswordIncorrect) as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    return pdfplumber.open(path)


def read_pdf_rows(path, open_password=None):
    """读取 PDF 并按路由选择专属 reader 或通用表格 reader。

    返回 (preamble, rows, route_info)。preamble 供标准化层继续嗅探户名/账号。
    """
    with _open_pdf(path, open_password=open_password) as pdf:
        preamble = pdf.pages[0].extract_text() if pdf.pages else ""
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        route_info = route_pdf(text, 0, len(pdf.pages), context=_pdf_context(pdf, text))

        table_rows = _extract_pdf_rows_by_reader(pdf, route_info.get("reader_id", ""), route_info)
        table_rows = _annotate_payment_order_state(table_rows)
        if route_info.get("reader_id") == "pdfplumber_coordinate_table" and table_rows:
            metadata_preamble = _coordinate_metadata_preamble(pdf, route_info)
            text_preamble = _preamble_before_reader_header(text, table_rows[0])
            preamble = "\n".join(
                part for part in [metadata_preamble, text_preamble]
                if str(part or "").strip()
            )
            route_info = {**route_info, "reader_headers": table_rows[0]}
        elif table_rows:
            # 通用 PDF reader 的首屏文本可能包含整页交易数据。只保留表头之前的固定抬头，
            # 避免标准化层把交易行里的对手开户行误判成本方银行。
            preamble = _preamble_before_reader_header(preamble, table_rows[0])
        text_table_layout = route_info.get("text_table_layout", "")
        if text_table_layout and not table_rows:
            rows = _extract_pdf_text_table_rows(text, text_table_layout)
            if rows:
                preamble = _preamble_before_reader_header(preamble, rows[0])
            return preamble or "", rows, route_info
        if route_info.get("decision") == "unmatched":
            table_rows = _extract_pdf_tables_default(pdf)
            if table_rows:
                route_info = {
                    **route_info,
                    "reader_id": "pdfplumber_table",
                }
            else:
                table_rows = _extract_pdf_tables_from_horizontal_lines(pdf)
                if table_rows:
                    route_info = {
                        **route_info,
                        "reader_id": "pdfplumber_line_table",
                    }

    if table_rows:
        preamble = _preamble_before_reader_header(preamble, table_rows[0])
    return preamble or "", table_rows, route_info
