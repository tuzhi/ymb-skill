"""pdfplumber_line_table Reader。"""

from ymb_standardization_core.readers.registry import FunctionPdfReader
from ymb_standardization_core.readers.pdf.common import (
    _append_pdf_table_rows,
    _clean_pdf_table_cells,
)


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


def _extract_pdf_tables_from_horizontal_lines(pdf):
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
            header_sig = _append_pdf_table_rows(all_rows, tbl, header_sig)
    if not all_rows or not _looks_like_statement_header(all_rows[0]):
        return []
    return all_rows


def read(pdf, _options):
    return _extract_pdf_tables_from_horizontal_lines(pdf)


READER = FunctionPdfReader("pdfplumber_line_table", read)
