"""PDF 打开、路由、Reader 分发与结果编排。"""

import re
from collections.abc import Sequence
from types import SimpleNamespace

from ymb_standardization_core.readers.registry import pdf_reader_registry
from ymb_standardization_core.readers.pdf.common import (
    _clean_pdf_cell,
    extract_pdf_text,
    should_drop_char,
)
from ymb_standardization_core.readers.pdf.coordinate_table import _coordinate_metadata_preamble
from ymb_standardization_core.readers.pdf.line_table import _extract_pdf_tables_from_horizontal_lines
from ymb_standardization_core.readers.pdf.table import _extract_pdf_tables_default
from ymb_standardization_core.readers.routing.evidence import (
    enrich_pdf_table_routing_evidence,
)


def _extract_pdf_rows_by_reader(pdf, reader_id, route_info=None):
    reader = pdf_reader_registry().get(reader_id)
    return reader.read(pdf, route_info or {}) if reader is not None else []


def _matches_zero_transaction(text, route_info):
    patterns = (
        ((route_info or {}).get("text_table") or {}).get("zero_transaction_patterns")
        or []
    )
    return bool(patterns) and all(re.search(pattern, text or "") for pattern in patterns)


class _PreparedPdfPages(Sequence):
    """按访问惰性生成派生页，避免预先物化整份 PDF 的字符缓存。"""

    def __init__(self, pages, route_info):
        self._pages = pages
        self._dedupe_chars = bool(route_info.get("dedupe_chars"))
        self._drop_chars = route_info.get("drop_chars") or []

    def __len__(self):
        return len(self._pages)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[item] for item in range(*index.indices(len(self)))]
        page = self._pages[index]
        if self._dedupe_chars:
            page = page.dedupe_chars()
        if self._drop_chars:
            page = page.filter(
                lambda char: not should_drop_char(char, self._drop_chars)
            )
        return page


def _prepare_pdf_reader_view(pdf, route_info):
    """按路由声明惰性生成供抬头和 Reader 共用的清洁页面视图。"""
    transformed = bool(
        route_info.get("dedupe_chars")
        or route_info.get("drop_chars")
    )
    if not transformed:
        return pdf
    return SimpleNamespace(pages=_PreparedPdfPages(pdf.pages, route_info))


def _extract_first_page_text(pdf):
    """抽取首页文本，并把页面缓存留给随后进行的路由和 Reader。"""
    if not pdf.pages:
        return ""
    return pdf.pages[0].extract_text() or ""


def _route_pdf_from_text(pdf, text, route_rules):
    from ymb_standardization_core.readers.router import route_pdf

    context = _pdf_context(pdf, text)
    return route_pdf(
        text,
        0,
        len(pdf.pages),
        context=context,
        rules=route_rules,
    )


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
        # 大多数正式导出文件的身份与表头都在首页。首页缓存由随后执行的
        # Reader 复用并释放；首页不能唯一命中时才逐页补充全文。
        text = _extract_first_page_text(pdf)
        preamble = text
        route_info = _route_pdf_from_text(pdf, text, route_rules)
        if route_info.get("decision") != "matched":
            text = extract_pdf_text(pdf)
            route_info = _route_pdf_from_text(pdf, text, route_rules)
        reader_pdf = _prepare_pdf_reader_view(pdf, route_info)
        if reader_pdf is not pdf:
            text = _extract_first_page_text(reader_pdf)
            preamble = text

        # fingerprint 已定位但银行导出选项不完整时，保留路由证据交给阶段一 QC；
        # 不继续执行 Reader，避免将不完整格式误当成普通解析失败或可交付流水。
        if route_info.get("decision") == "matched_incomplete":
            return preamble or "", [], route_info

        table_rows = _extract_pdf_rows_by_reader(
            reader_pdf,
            route_info.get("reader_id", ""),
            route_info,
        )
        if (
            not table_rows
            and route_info.get("decision") == "matched"
            and route_info.get("reader_id") == "pdfplumber_text_lines"
        ):
            full_text = extract_pdf_text(reader_pdf)
            if _matches_zero_transaction(full_text, route_info):
                route_info = {**route_info, "zero_transaction": True}
        if route_info.get("reader_id") == "pdfplumber_coordinate_table" and table_rows:
            metadata_preamble = _coordinate_metadata_preamble(reader_pdf, route_info)
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
        if route_info.get("decision") == "unmatched":
            table_rows = _extract_pdf_tables_default(reader_pdf)
            if table_rows:
                route_info = {
                    **route_info,
                    "reader_id": "pdfplumber_table",
                    "routing_evidence": enrich_pdf_table_routing_evidence(
                        route_info.get("routing_evidence"),
                        table_rows,
                        "pdfplumber_table",
                    ),
                }
            else:
                table_rows = _extract_pdf_tables_from_horizontal_lines(reader_pdf)
                if table_rows:
                    route_info = {
                        **route_info,
                        "reader_id": "pdfplumber_line_table",
                        "routing_evidence": enrich_pdf_table_routing_evidence(
                            route_info.get("routing_evidence"),
                            table_rows,
                            "pdfplumber_line_table",
                        ),
                    }

    if table_rows:
        preamble = _preamble_before_reader_header(preamble, table_rows[0])
    return preamble or "", table_rows, route_info
