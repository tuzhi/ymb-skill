"""pdfplumber_table Reader。"""

import re

from ymb_standardization_core.readers.registry import FunctionPdfReader
from ymb_standardization_core.readers.pdf.common import (
    _append_pdf_table_rows,
    _clean_pdf_cell,
    drop_word_filter_char,
    iter_pdf_pages,
)
from ymb_standardization_core.transforms import apply_reader_options


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


def _merge_pdf_table_continuation(previous, continuation, headers):
    width = max(len(headers), len(previous), len(continuation))
    merged = []
    for index in range(width):
        parts = []
        for row in (previous, continuation):
            value = str(row[index] if index < len(row) else "").strip()
            if value:
                parts.append(value)
        merged.append(_clean_pdf_cell("".join(parts)))
    return merged


def _extract_pdf_tables_default(pdf, word_filters=None, row_anchor=None):
    all_rows = []
    header_sig = None
    merge_across_pages = (
        str((row_anchor or {}).get("continuation") or "").strip()
        == "until_next_anchor_across_pages"
    )
    for page in iter_pdf_pages(pdf.pages):
        page_start = len(all_rows)
        if word_filters:
            page = page.filter(lambda char: not drop_word_filter_char(char, word_filters))
        for tbl in page.extract_tables():
            header_sig = _append_pdf_table_rows(all_rows, tbl, header_sig)
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
            )
            previous = all_rows[page_start - 1]
            del all_rows[page_start]
    return all_rows


def read(pdf, options):
    rows = _extract_pdf_tables_default(
        pdf,
        word_filters=options.get("word_filters") or {},
        row_anchor=options.get("row_anchor") or {},
    )
    return apply_reader_options(rows, {"drop_rows": options.get("drop_rows") or []})


READER = FunctionPdfReader("pdfplumber_table", read)
