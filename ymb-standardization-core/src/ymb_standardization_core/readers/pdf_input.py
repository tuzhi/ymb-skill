"""PDF 打开、路由、Reader 分发与结果编排。"""

import re
from types import SimpleNamespace

from ymb_standardization_core.readers.registry import pdf_reader_registry
from ymb_standardization_core.readers.pdf.common import (
    _clean_pdf_cell,
    drop_word_filter_char,
)
from ymb_standardization_core.readers.pdf.coordinate_table import _coordinate_metadata_preamble
from ymb_standardization_core.readers.pdf.line_table import _extract_pdf_tables_from_horizontal_lines
from ymb_standardization_core.readers.pdf.table import _extract_pdf_tables_default
from ymb_standardization_core.readers.pdf.text_lines import _extract_pdf_text_table_rows
from ymb_standardization_core.transforms import annotate_payment_order_state


def _extract_pdf_rows_by_reader(pdf, reader_id, route_info=None):
    reader = pdf_reader_registry().get(reader_id)
    return reader.read(pdf, route_info or {}) if reader is not None else []


def _prepare_pdf_reader_view(pdf, route_info):
    """按路由声明统一生成供抬头、全文和 Reader 共用的清洁页面视图。"""
    pages = list(pdf.pages)
    transformed = False
    if route_info.get("dedupe_chars"):
        pages = [page.dedupe_chars() for page in pages]
        transformed = True
    word_filters = route_info.get("word_filters") or {}
    if word_filters.get("drop_chars"):
        pages = [
            page.filter(
                lambda char: not drop_word_filter_char(char, word_filters)
            )
            for page in pages
        ]
        transformed = True
    return SimpleNamespace(pages=pages) if transformed else pdf


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


def read_pdf_rows(path, open_password=None, route_rules=None):
    """读取 PDF 并按路由选择专属 reader 或通用表格 reader。

    返回 (preamble, rows, route_info)。preamble 供标准化层继续嗅探户名/账号。
    """
    with _open_pdf(path, open_password=open_password) as pdf:
        preamble = pdf.pages[0].extract_text() if pdf.pages else ""
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        from ymb_standardization_core.readers.router import route_pdf

        route_info = route_pdf(
            text,
            0,
            len(pdf.pages),
            context=_pdf_context(pdf, text),
            rules=route_rules,
        )
        reader_pdf = _prepare_pdf_reader_view(pdf, route_info)
        reader_options = route_info
        if reader_pdf is not pdf:
            preamble = reader_pdf.pages[0].extract_text() if reader_pdf.pages else ""
            text = "\n".join(page.extract_text() or "" for page in reader_pdf.pages)
        if (route_info.get("word_filters") or {}).get("drop_chars"):
            # 字符级水印已经在统一页面视图中移除；Reader 仍须保留页底、
            # 停止行等结构过滤，返回的 route_info 也保留原始 YAML 供审计。
            reader_options = {
                **route_info,
                "word_filters": {
                    key: value
                    for key, value in route_info["word_filters"].items()
                    if key != "drop_chars"
                },
            }

        # fingerprint 已定位但银行导出选项不完整时，保留路由证据交给阶段一 QC；
        # 不继续执行 Reader，避免将不完整格式误当成普通解析失败或可交付流水。
        if route_info.get("decision") == "matched_incomplete":
            return preamble or "", [], route_info

        table_rows = _extract_pdf_rows_by_reader(
            reader_pdf,
            route_info.get("reader_id", ""),
            reader_options,
        )
        table_rows = annotate_payment_order_state(table_rows)
        if route_info.get("reader_id") == "pdfplumber_coordinate_table" and table_rows:
            metadata_preamble = _coordinate_metadata_preamble(reader_pdf, reader_options)
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
            table_rows = _extract_pdf_tables_default(reader_pdf)
            if table_rows:
                route_info = {
                    **route_info,
                    "reader_id": "pdfplumber_table",
                }
            else:
                table_rows = _extract_pdf_tables_from_horizontal_lines(reader_pdf)
                if table_rows:
                    route_info = {
                        **route_info,
                        "reader_id": "pdfplumber_line_table",
                    }

    if table_rows:
        preamble = _preamble_before_reader_header(preamble, table_rows[0])
    return preamble or "", table_rows, route_info
