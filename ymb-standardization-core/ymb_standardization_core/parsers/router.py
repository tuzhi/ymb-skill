from ymb_standardization_core.parsers.abc_text_pdf import read_abc_text_pdf
from ymb_standardization_core.parsers.jxrcb_pdf_text import read_jxrcb_text_pdf
from ymb_standardization_core.parsers.kasikorn_pdf_text import read_kasikorn_text_pdf
from ymb_standardization_core.parsers.routing.rule_loader import load_pdf_route_rules
from ymb_standardization_core.parsers.zhejiang_qyrcb_pdf_text import read_zhejiang_qyrcb_text_pdf


def _pdf_candidate(parser, file_type, bank, version, account_type, identity_evidence, layout_evidence,
                   route_evidence=None):
    return {
        "parser": parser,
        "decision": "matched",
        "file_type": file_type,
        "bank": bank,
        "version": version,
        "account_type": account_type,
        "identity_evidence": identity_evidence,
        "layout_evidence": layout_evidence,
        "metadata_evidence": route_evidence.get("metadata_evidence", {}) if route_evidence else {},
        "style_evidence": route_evidence.get("style_evidence", []) if route_evidence else [],
        "data_evidence": route_evidence.get("data_evidence", []) if route_evidence else [],
        "date_format_evidence": route_evidence.get("date_format_evidence", []) if route_evidence else [],
    }


def _pdf_fallback(evidence, table_row_count, page_count):
    return {
        "parser": "generic_pdf_table" if table_row_count else "generic_pdf_text_unmatched",
        "decision": "unmatched",
        "file_type": "pdf",
        "account_type": "",
    }


def _decide_pdf_route(candidates, evidence, table_row_count, page_count):
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        return _pdf_fallback(evidence, table_row_count, page_count)
    return {
        "parser": "ambiguous_router_match",
        "decision": "ambiguous",
        "file_type": "pdf",
        "candidates": candidates,
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

    for rule in load_pdf_route_rules():
        match = rule.match(text, context=context)
        if not match:
            continue
        candidates.append(_pdf_candidate(
            parser=rule.parser,
            file_type=rule.file_type,
            bank=rule.bank,
            version=rule.version,
            account_type=rule.account_type,
            identity_evidence=match["identity_evidence"],
            layout_evidence=match["layout_evidence"],
            route_evidence={
                "metadata_evidence": match.get("metadata_evidence", {}),
                "style_evidence": match.get("style_evidence", []),
                "data_evidence": match.get("data_evidence", []),
                "date_format_evidence": match.get("date_format_evidence", []),
            },
        ))

    return _decide_pdf_route(candidates, evidence, table_row_count, page_count)


def _extract_pdf_tables(pdf):
    """通用 PDF 表格抽取，只处理 pdfplumber 能识别出的结构化表格。"""
    all_rows = []
    header_sig = None
    for page in pdf.pages:
        for tbl in page.extract_tables():
            for r in tbl:
                cells = [(c or "").replace("\n", "").strip() for c in r]
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
    return all_rows


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
    """读取 PDF 并按路由选择专属 parser 或通用表格 parser。

    返回 (preamble, rows, route_info)。preamble 供标准化层继续嗅探户名/账号。
    """
    with _open_pdf(path, open_password=open_password) as pdf:
        preamble = pdf.pages[0].extract_text() if pdf.pages else ""
        table_rows = _extract_pdf_tables(pdf)
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        route_info = route_pdf(text, len(table_rows), len(pdf.pages), context=_pdf_context(pdf, text))

        # 专属 parser 只接管已识别模板；未命中时回退到通用表格行，交给标准化层映射字段。
        if route_info["parser"] == "abc_text_pdf":
            preamble, rows = read_abc_text_pdf(pdf)
            return preamble, rows, route_info
        if route_info["parser"] == "jiangxi_rural_commercial_pdf_text":
            preamble, rows = read_jxrcb_text_pdf(pdf)
            return preamble, rows, route_info
        if route_info["parser"] == "kasikorn_pdf_text":
            preamble, rows = read_kasikorn_text_pdf(pdf)
            return preamble, rows, route_info
        if route_info["parser"] == "zhejiang_qyrcb_pdf_text":
            preamble, rows = read_zhejiang_qyrcb_text_pdf(pdf)
            return preamble, rows, route_info

    return preamble or "", table_rows, route_info
