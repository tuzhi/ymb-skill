"""多个 PDF Reader 共享的底层工具。"""

import re


def close_pdf_page(page):
    """释放当前页及其派生页链上的 pdfplumber 缓存。"""
    seen = set()
    current = page
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        close = getattr(current, "close", None)
        if callable(close):
            close()
        current = getattr(current, "parent_page", None)


def iter_pdf_pages(pages):
    """逐页消费并在离开当前迭代时立即释放页面缓存。"""
    for page in pages:
        try:
            yield page
        finally:
            close_pdf_page(page)


def extract_pdf_text(pdf):
    """保留全文语义，但不让所有 Page 的字符/layout 缓存同时驻留。"""
    return "\n".join(
        page.extract_text() or ""
        for page in iter_pdf_pages(pdf.pages)
    )


def _clean_pdf_cell(value):
    """合并视觉换行但保留同行空格。"""
    normalized_value = (
        str(value or "")
        .replace("‑", "-")
        .replace("行", "行")
        .replace("易", "易")
    )
    normalized_value = re.sub(r"(?:\r\n?|\n|\u2028|\u2029)+", "", normalized_value)
    return re.sub(r"[^\S\r\n]+", " ", normalized_value).strip()


def _clean_pdf_table_cells(row):
    return [_clean_pdf_cell(cell) for cell in row]


def _append_pdf_table_rows(all_rows, table_rows, header_sig):
    for row in table_rows:
        cells = _clean_pdf_table_cells(row)
        if not any(cells):
            continue
        signature = "|".join(cells)
        if header_sig is None:
            header_sig = signature
            all_rows.append(cells)
        elif signature != header_sig:
            all_rows.append(cells)
    return header_sig


def drop_word_filter_char(char, word_filters):
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
