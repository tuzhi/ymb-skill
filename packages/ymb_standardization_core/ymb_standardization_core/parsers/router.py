import re

from ymb_standardization_core.parsers.abc_text_pdf import read_abc_text_pdf
from ymb_standardization_core.parsers.jxrcb_pdf_text import read_jxrcb_text_pdf


def route_pdf(text, table_row_count, page_count):
    """识别 PDF 的解析路线。只判断模板和抽取模式，不在这里清洗交易数据。"""
    evidence = {
        "ext": ".pdf",
        "page_count": page_count,
        "text_length": len(text or ""),
        "table_row_count": table_row_count,
    }
    # 农行文本版清单与江西农商是两个不同模板，必须先独立命中，避免银行口径串线。
    if "中国农业银行账户活期交易明细清单" in text:
        return {
            "parser": "abc_text_pdf",
            "route_confidence": 0.95,
            "route_evidence": {**evidence, "bank_marker": "中国农业银行账户活期交易明细清单"},
            "ocr_used": False,
            "page_count": page_count,
            "账户类型线索": "",
        }

    # 江西农商这类 PDF 有文本层，但没有结构化表格；需要文本行 parser，而不是 OCR。
    jx_markers = ["江西·农商银行", "户 名", "账 号", "起止日期"]
    jx_hits = [m for m in jx_markers if m in text]
    date_amount_lines = len(re.findall(r"20\d{2}[-/]\d{1,2}[-/]\d{1,2}.*?[+-]?\d[\d,]*\.\d{1,2}", text))
    if len(jx_hits) >= 3 and date_amount_lines >= 5:
        return {
            "parser": "jiangxi_rural_commercial_pdf_text",
            "route_confidence": 0.95,
            "route_evidence": {**evidence, "bank_marker": "江西·农商银行",
                               "matched_markers": jx_hits, "date_amount_lines": date_amount_lines},
            "ocr_used": False,
            "page_count": page_count,
            "账户类型线索": "个人",
        }

    return {
        "parser": "generic_pdf_table" if table_row_count else "generic_pdf_text_unmatched",
        "route_confidence": 0.7 if table_row_count else 0.2,
        "route_evidence": evidence,
        "ocr_used": False,
        "page_count": page_count,
        "账户类型线索": "",
    }


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

    return preamble or "", table_rows, route_info
