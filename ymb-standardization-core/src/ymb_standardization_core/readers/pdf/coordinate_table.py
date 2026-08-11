"""pdfplumber_coordinate_table Reader。"""

import re

from ymb_standardization_core.readers.registry import FunctionPdfReader
from ymb_standardization_core.readers.pdf.common import (
    _clean_pdf_cell,
    close_pdf_page,
    drop_word_filter_char,
    iter_pdf_pages,
)
from ymb_standardization_core.transforms import apply_reader_options, repeated_header_bottom


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
        return None, None, None, None
    top, by_text = best
    headers = [header for header in candidate_headers if header in by_text]
    starts = [float(by_text[header].get("x0", 0)) for header in headers]
    if starts != sorted(starts):
        pairs = sorted(zip(headers, starts), key=lambda item: item[1])
        headers = [header for header, _start in pairs]
        starts = [start for _header, start in pairs]
    spans = [
        (
            float(by_text[header].get("x0", 0)),
            float(by_text[header].get("x1", by_text[header].get("x0", 0))),
        )
        for header in headers
    ]
    return top, headers, starts, spans


def _coordinate_boundaries(page_width, starts, header_spans=None):
    if not header_spans or len(header_spans) != len(starts):
        middle = [
            (left + right) / 2
            for left, right in zip(starts, starts[1:])
        ]
        return [0] + middle + [page_width + 10]

    middle = []
    for index, (left_span, right_span) in enumerate(
        zip(header_spans, header_spans[1:]),
    ):
        left_x0, left_x1 = left_span
        right_x0, right_x1 = right_span
        left_width = max(0, left_x1 - left_x0)
        right_width = max(0, right_x1 - right_x0)
        gap = right_x0 - left_x1
        if 0 <= gap <= min(left_width, right_width):
            # 相邻表头形成可信窄缝时，使用两个文字框之间空隙的中点。
            boundary = (left_x1 + right_x0) / 2
        elif gap < 0:
            # 表头框重叠时没有可靠空隙，退化为两个表头中心点的中点。
            left_center = (left_x0 + left_x1) / 2
            right_center = (right_x0 + right_x1) / 2
            boundary = (left_center + right_center) / 2
        else:
            # 短表头会留下很宽的视觉空隙，不能用空隙中点代表真实列边界。
            boundary = (starts[index] + starts[index + 1]) / 2
        middle.append(boundary)
    return [0] + middle + [page_width + 10]


def _coordinate_index(x, boundaries, header_spans=None):
    # 表头文字框是列的高置信核心区。正文文字起点若唯一落入某个表头范围，
    # 优先归入该列，避免右对齐短文本越过“相邻表头起点中点”而落入下一列。
    span_hits = [
        index
        for index, (left, right) in enumerate(header_spans or [])
        if left <= x <= right
    ]
    if len(span_hits) == 1:
        return span_hits[0]
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


def _coordinate_page_words(page, word_filters=None):
    if word_filters:
        page = page.filter(lambda char: not drop_word_filter_char(char, word_filters))
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
    try:
        words = _coordinate_page_words(page, word_filters=(route_info or {}).get("word_filters") or {})
        header_top, _headers, _starts, _spans = _coordinate_header(
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
    finally:
        close_pdf_page(page)


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


def _coordinate_cell_text(cell):
    lines = []
    for _top, group in sorted(_group_words_by_top(
        {"top": top, "x0": x0, "text": value}
        for top, x0, value in cell
    ).items()):
        lines.append(" ".join(
            str(word.get("text") or "").strip()
            for word in sorted(group, key=lambda item: float(item.get("x0", 0)))
            if str(word.get("text") or "").strip()
        ))
    return _clean_pdf_cell("\n".join(lines))


def _vertical_boundary_positions(page, min_segments=5, body_top_min=None):
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


def _vertical_boundary_anchor_text_match(word, row_anchor):
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


def _vertical_boundary_header_match(text, candidate_header):
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


def _vertical_boundary_headers(words, boundaries, candidate_headers, first_anchor_top):
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
            if _vertical_boundary_header_match(text, header):
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


def _extract_pdf_vertical_boundary_table_rows(pdf, candidate_headers, row_anchor=None, word_filters=None):
    """Coordinate-reader strategy: use stable vertical boundaries and row-anchor words."""
    all_rows = []
    output_headers = None
    row_anchor = row_anchor or {}
    for page in iter_pdf_pages(pdf.pages):
        words = _coordinate_page_words(page, word_filters=word_filters)
        first_anchor_top = min(
            (
                float(word.get("top", 0))
                for word in words
                if _vertical_boundary_anchor_text_match(word, row_anchor)
            ),
            default=None,
        )
        if first_anchor_top is None:
            continue
        boundaries = _vertical_boundary_positions(page, body_top_min=first_anchor_top)
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
        headers = _vertical_boundary_headers(words, boundaries, candidate_headers, anchors[0][0])
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
                _coordinate_cell_text(cell)
                for cell_index, cell in enumerate(cells)
            ]
            if row and row[0]:
                all_rows.append(row)
    return all_rows if len(all_rows) > 1 else []


def _extract_pdf_coordinate_table_rows(
        pdf, candidate_headers, row_anchor=None, word_filters=None,
        repeated_header=None):
    """Recover visual tables whose text words have stable column coordinates."""
    all_rows = []
    output_headers = None
    output_starts = None
    output_spans = None
    row_anchor = row_anchor or {}
    for page in iter_pdf_pages(pdf.pages):
        words = _coordinate_page_words(page, word_filters=word_filters)
        header_top, page_headers, page_starts, page_spans = _coordinate_header(
            words,
            candidate_headers,
        )
        if page_starts is not None:
            headers = page_headers
            starts = page_starts
            spans = page_spans
            body_top_min = header_top + 5
        elif (
            output_headers is not None
            and output_starts is not None
            and output_spans is not None
        ):
            headers = output_headers
            starts = output_starts
            spans = output_spans
            body_top_min = 0
        else:
            continue
        anchor_column = str(row_anchor.get("column") or "").strip()
        anchor_index = headers.index(anchor_column) if anchor_column in headers else 0
        if output_headers is None:
            output_headers = headers
            output_starts = starts
            output_spans = spans
            all_rows.append(headers)
        elif headers != output_headers:
            continue
        else:
            output_starts = starts
            output_spans = spans
        boundaries = _coordinate_boundaries(
            page.width,
            starts,
            header_spans=spans,
        )
        first_anchor_top = min(
            (
                float(word.get("top", 0))
                for word in words
                if _is_coordinate_row_anchor(
                    word,
                    starts[anchor_index],
                    headers[anchor_index],
                    row_anchor,
                )
            ),
            default=None,
        )
        repeated_header_bottom_value = repeated_header_bottom(
            words,
            header_top if page_starts is not None else body_top_min,
            first_anchor_top,
            repeated_header,
        )
        if repeated_header_bottom_value is not None:
            body_top_min = max(body_top_min, repeated_header_bottom_value)
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
                col = _coordinate_index(
                    float(word.get("x0", 0)),
                    boundaries,
                    header_spans=spans,
                )
                if col is None or col >= len(cells):
                    continue
                cells[col].append((top, float(word.get("x0", 0)), str(word.get("text") or "").strip()))
            row = [
                _coordinate_cell_text(cell)
                for cell_index, cell in enumerate(cells)
            ]
            if row and row[0]:
                all_rows.append(row)
    return all_rows if len(all_rows) > 1 else []


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


def _starts_with_text_separator_table(pdf, probe_pages=3):
    """只探测开头少量页面，避免已成功的 coordinate 结果触发全文重复扫描。"""
    for page in iter_pdf_pages(pdf.pages[:probe_pages]):
        text = page.extract_text() or ""
        if any(_is_text_separator_line(line) for line in text.splitlines()):
            return True
    return False


def _extract_pdf_text_separator_table_rows(pdf):
    """Read text-layer tables whose separators are text glyphs, not PDF line objects."""
    rows = [TEXT_SEPARATOR_TABLE_HEADER]
    import re

    all_lines = []
    for page in iter_pdf_pages(pdf.pages):
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


def read(pdf, options):
    rows = _extract_pdf_vertical_boundary_table_rows(
        pdf,
        options.get("reader_header_candidates") or [],
        options.get("row_anchor") or {},
        word_filters=options.get("word_filters") or {},
    )
    if not rows:
        rows = _extract_pdf_coordinate_table_rows(
            pdf,
            options.get("reader_header_candidates") or [],
            options.get("row_anchor") or {},
            word_filters=options.get("word_filters") or {},
            repeated_header=options.get("repeated_header") or {},
        )
    separator_rows = []
    if not rows or _starts_with_text_separator_table(pdf):
        separator_rows = _extract_pdf_text_separator_table_rows(pdf)
    if separator_rows and (not rows or len(rows[0]) < len(separator_rows[0])):
        rows = separator_rows
    return apply_reader_options(rows, options)


READER = FunctionPdfReader("pdfplumber_coordinate_table", read)
