"""为隔离 AI Fallback 提取受限、脱敏的 Router 结构证据。"""

import re


_DATE_RE = re.compile(
    r"(?:19|20)\d{2}(?:[-/.年]\d{1,2}){1,2}|(?:19|20)\d{6}"
)
_NUMBER_RE = re.compile(r"^[+-]?\d[\d,]*(?:\.\d+)?$")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_LONG_NUMBER_RE = re.compile(r"(?<!\d)\d{6,}(?!\d)")


def _safe_text(value, limit=200):
    text = str(value or "").strip()
    text = _EMAIL_RE.sub("<REDACTED_EMAIL>", text)
    text = _LONG_NUMBER_RE.sub("<REDACTED_NUMBER>", text)
    return text if len(text) <= limit else text[:limit] + "…"


def _looks_like_transaction(values):
    texts = [str(value or "").strip() for value in values]
    has_date = any(_DATE_RE.search(text) for text in texts)
    has_amount = any(
        isinstance(value, (int, float))
        or bool(_NUMBER_RE.fullmatch(text.replace(" ", "")))
        for value, text in zip(values, texts)
        if text
    )
    return has_date and (has_amount or len(texts) >= 3)


def _safe_metadata(context):
    return {
        _safe_text(key, 80): _safe_text(value, 160)
        for key, value in list((context.get("metadata") or {}).items())[:20]
        if value not in (None, "")
    }


def _safe_styles(context, allowed_texts):
    styles = []
    for item in context.get("styles") or []:
        text = _safe_text(item.get("text"))
        if not text or text not in allowed_texts:
            continue
        styles.append({
            key: item.get(key)
            for key in ("text", "font", "size", "bold", "row", "col", "number_format", "top")
            if item.get(key) not in (None, "")
        })
        styles[-1]["text"] = text
        if len(styles) >= 20:
            break
    return styles


def build_excel_routing_evidence(rows, context=None, sheet=""):
    """只暴露首个交易行之前的 Excel 模板结构。"""
    context = context or {}
    identities = []
    headers = []
    for row in list(rows or [])[:60]:
        values = [value for value in row if value not in (None, "")]
        if not values:
            continue
        if _looks_like_transaction(values):
            break
        safe_values = [_safe_text(value) for value in values[:30]]
        if len(safe_values) == 1 and len(identities) < 5:
            identities.append(safe_values[0])
        elif len(safe_values) >= 3 and len(headers) < 3:
            headers.append(safe_values)

    allowed_texts = set(identities)
    for row in headers:
        allowed_texts.update(row)
    return {
        "file_type": "excel",
        "reader_id": "openpyxl_grid",
        "sheet": _safe_text(sheet, 120),
        "identity_candidates": identities,
        "header_candidates": headers,
        "metadata": _safe_metadata(context),
        "style_candidates": _safe_styles(context, allowed_texts),
        "date_patterns": [
            _safe_text(value, 80)
            for value in list(context.get("date_patterns") or [])[:10]
        ],
    }


def build_pdf_routing_evidence(context=None, reader_id=""):
    """只暴露首个疑似交易行之前的 PDF 模板结构。"""
    context = context or {}
    leading_lines = []
    for line in list(context.get("lines") or [])[:80]:
        text = str(line or "").strip()
        if not text:
            continue
        if _looks_like_transaction([part for part in text.split() if part]):
            break
        leading_lines.append(_safe_text(text))
        if len(leading_lines) >= 12:
            break
    return {
        "file_type": "pdf",
        "reader_id": _safe_text(reader_id, 80),
        "leading_lines": leading_lines,
        "metadata": _safe_metadata(context),
        "style_candidates": _safe_styles(context, set(leading_lines)),
        "date_patterns": [
            _safe_text(value, 80)
            for value in list(context.get("date_patterns") or [])[:10]
        ],
    }
