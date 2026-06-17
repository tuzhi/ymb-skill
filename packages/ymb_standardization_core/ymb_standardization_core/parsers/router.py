from ymb_standardization_core.parsers.abc_text_pdf import read_abc_text_pdf
from ymb_standardization_core.parsers.jxrcb_pdf_text import read_jxrcb_text_pdf
from ymb_standardization_core.parsers.kasikorn_pdf_text import read_kasikorn_text_pdf
from ymb_standardization_core.parsers.routing.rule_loader import load_pdf_route_rules
from ymb_standardization_core.parsers.zhejiang_qyrcb_pdf_text import read_zhejiang_qyrcb_text_pdf


def _pdf_candidate(parser, file_type, bank, version, identity_evidence, layout_evidence,
                   route_evidence=None):
    return {
        "parser": parser,
        "decision": "matched",
        "file_type": file_type,
        "bank": bank,
        "version": version,
        "identity_evidence": identity_evidence,
        "layout_evidence": layout_evidence,
    }


def _pdf_fallback(evidence, table_row_count, page_count):
    return {
        "parser": "generic_pdf_table" if table_row_count else "generic_pdf_text_unmatched",
        "decision": "unmatched",
        "file_type": "pdf",
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


def route_pdf(text, table_row_count, page_count):
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
        match = rule.match(text)
        if not match:
            continue
        candidates.append(_pdf_candidate(
            parser=rule.parser,
            file_type=rule.file_type,
            bank=rule.bank,
            version=rule.version,
            identity_evidence=match["identity_evidence"],
            layout_evidence=match["layout_evidence"],
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


def read_pdf_rows(path):
    """读取 PDF 并按路由选择专属 parser 或通用表格 parser。

    返回 (preamble, rows, route_info)。preamble 供标准化层继续嗅探户名/账号。
    """
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        preamble = pdf.pages[0].extract_text() if pdf.pages else ""
        table_rows = _extract_pdf_tables(pdf)
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        route_info = route_pdf(text, len(table_rows), len(pdf.pages))

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
