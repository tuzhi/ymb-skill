"""将稳定 PDF 文本行按 YAML 正则契约转换为原始表格行。"""

import re

from ymb_standardization_core.readers.registry import FunctionPdfReader


def _compiled_patterns(config, key):
    return [re.compile(item) for item in config.get(key, [])]


def _first_match(patterns, line):
    for pattern in patterns:
        match = pattern.match(line)
        if match:
            return match
    return None


def _row_from_match(match, headers, fields):
    groups = match.groupdict()
    return [str(groups.get(fields.get(header, "")) or "").strip() for header in headers]


def _append_continuation(row, match, headers, append_fields, joiner):
    groups = match.groupdict()
    for header, group in append_fields.items():
        value = str(groups.get(group) or "").strip()
        if not value or header not in headers:
            continue
        index = headers.index(header)
        row[index] = joiner.join(part for part in (row[index], value) if part).strip()


def _extract_pdf_text_table_rows(text, config):
    """按通用文本行契约提取记录，首行返回配置声明的表头。"""
    fields = dict(config.get("field_groups") or {})
    headers = list(fields)
    record_patterns = _compiled_patterns(config, "record_patterns")
    continuations = [
        (
            re.compile(item["pattern"]),
            dict(item.get("append") or {}),
            str(item.get("joiner", " ")),
        )
        for item in config.get("continuation_patterns", [])
    ]
    if not headers or not fields or not record_patterns:
        return []

    rows = [headers]
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        record = _first_match(record_patterns, line)
        if record:
            rows.append(_row_from_match(record, headers, fields))
            continue
        if len(rows) == 1:
            continue
        for pattern, append_fields, joiner in continuations:
            continuation = pattern.match(line)
            if continuation:
                _append_continuation(rows[-1], continuation, headers, append_fields, joiner)
                break
    return rows if len(rows) > 1 else []


def read(pdf, options):
    text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    return _extract_pdf_text_table_rows(text, options.get("text_table") or {})


READER = FunctionPdfReader("pdfplumber_text_lines", read)
