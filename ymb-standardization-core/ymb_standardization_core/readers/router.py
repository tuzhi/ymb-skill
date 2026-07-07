from ymb_standardization_core.readers.alipay_proof_pdf import read_alipay_proof_pdf
from ymb_standardization_core.readers.abc_text_pdf import read_abc_text_pdf
from ymb_standardization_core.readers.jiangxi_yumin_bank_pdf import read_jiangxi_yumin_bank_pdf
from ymb_standardization_core.readers.jxrcb_pdf_text import read_jxrcb_text_pdf
from ymb_standardization_core.readers.kasikorn_pdf_text import read_kasikorn_text_pdf
from ymb_standardization_core.readers.routing.rule_loader import load_pdf_route_rules
from ymb_standardization_core.readers.wechat_pay_proof_pdf import read_wechat_pay_proof_pdf
from ymb_standardization_core.readers.zhejiang_qyrcb_pdf_text import read_zhejiang_qyrcb_text_pdf

ABC_TEXT_PDF_FINGERPRINTS = {"md5:ab5d413308d9d27f3aa913d772fa3494"}
JXRCB_TEXT_PDF_FINGERPRINTS = {"md5:e833fbf4a2171d66315c5a3bda64711c"}
KASIKORN_TEXT_PDF_FINGERPRINTS = {"md5:37399b38ddd3572cc70fc6f8b9be2900"}
ZHEJIANG_QYRCB_TEXT_PDF_FINGERPRINTS = {"md5:69c7df7286e238aef80ae49938fd397a"}
JIANGXI_YUMIN_BANK_PDF_FINGERPRINTS = {"md5:19c8a8f7513adce0f0ad32a5c0b05154"}
WECHAT_PAY_PROOF_PDF_FINGERPRINTS = {
    "md5:48a1a9cde662e1515e3d8f3238934e92",
    "md5:13cbd1af07e92414229d298a67bcf533",
}
ALIPAY_PROOF_PDF_FINGERPRINTS = {"md5:cf70380d7ed3124ebddb69119e9c8d36"}
TEXT_TABLE_FINGERPRINTS = {
    "md5:336aced4f33ef27ad250e418e5b5eb18": "currency",
    "md5:0818218cb218b9bdb699770e6a65e6dd": "currency",
    "md5:831325d33aa7b01f10771881ffc3ae76": "cmbc_personal",
}


def _pdf_candidate(id, reader_id, file_type, bank, account_type, column_mapping,
                   identity_evidence, columns_evidence, route_evidence=None):
    return {
        "id": id,
        "fingerprint_id": id,
        "reader_id": reader_id,
        "decision": "matched",
        "file_type": file_type,
        "bank": bank,
        "account_type": account_type,
        "column_mapping": column_mapping,
        "identity_evidence": identity_evidence,
        "columns_evidence": columns_evidence,
        "metadata_evidence": route_evidence.get("metadata_evidence", {}) if route_evidence else {},
        "style_evidence": route_evidence.get("style_evidence", []) if route_evidence else [],
        "date_format_evidence": route_evidence.get("date_format_evidence", []) if route_evidence else [],
    }


def _pdf_fallback(evidence, table_row_count, page_count, candidate_fingerprints=None):
    reader_id = "pdfplumber_table" if table_row_count else "none"
    return {
        "reader_id": reader_id,
        "decision": "unmatched",
        "file_type": "pdf",
        "fingerprint_id": "",
        "account_type": "",
        "column_mapping": {},
        "candidate_fingerprints": candidate_fingerprints or [],
    }


def _choose_specific_candidate(candidates):
    if not candidates:
        return None
    def score(item):
        return (
            len(item.get("columns_evidence", []))
            + len(item.get("metadata_evidence", {})) * 2
            + len(item.get("style_evidence", []))
            + len(item.get("date_format_evidence", []))
        )

    by_score = sorted(candidates, key=score, reverse=True)
    if len(by_score) == 1:
        return by_score[0]
    if score(by_score[0]) > score(by_score[1]):
        return by_score[0]

    identified = [item for item in candidates if item.get("bank") and item.get("bank") != "未识别"]
    unidentified = [item for item in candidates if not item.get("bank") or item.get("bank") == "未识别"]
    if len(identified) == 1 and unidentified:
        return identified[0]
    return None


def _decide_pdf_route(candidates, evidence, table_row_count, page_count, candidate_fingerprints=None):
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        return _pdf_fallback(evidence, table_row_count, page_count, candidate_fingerprints=candidate_fingerprints)
    specific = _choose_specific_candidate(candidates)
    if specific:
        return specific
    return {
        "reader_id": "none",
        "decision": "ambiguous",
        "file_type": "pdf",
        "fingerprint_id": "",
        "column_mapping": {},
        "candidates": candidates,
        "candidate_fingerprints": candidate_fingerprints or [],
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
    candidate_fingerprints = []

    for rule in load_pdf_route_rules():
        candidate = rule.fingerprint_candidate(text, context=context)
        if candidate:
            candidate_fingerprints.append(candidate)
        match = rule.match(text, context=context)
        if not match:
            continue
        candidates.append(_pdf_candidate(
            id=rule.id,
            reader_id=rule.reader_id,
            file_type=rule.file_type,
            bank=rule.bank,
            account_type=rule.account_type,
            column_mapping=rule.column_mapping,
            identity_evidence=match["identity_evidence"],
            columns_evidence=match["columns_evidence"],
            route_evidence={
                "metadata_evidence": match.get("metadata_evidence", {}),
                "style_evidence": match.get("style_evidence", []),
                "date_format_evidence": match.get("date_format_evidence", []),
            },
        ))

    return _decide_pdf_route(
        candidates,
        evidence,
        table_row_count,
        page_count,
        candidate_fingerprints=candidate_fingerprints,
    )


def _extract_pdf_tables(pdf):
    """通用 PDF 表格抽取，只处理 pdfplumber 能识别出的结构化表格。"""
    rows = _extract_pdf_tables_default(pdf)
    if rows:
        return rows
    return _extract_pdf_tables_from_horizontal_lines(pdf)


def _extract_pdf_rows_by_reader(pdf, reader_id):
    if reader_id == "pdfplumber_text_separator_table":
        return _extract_pdf_text_separator_table_rows(pdf)
    if reader_id == "pdfplumber_word_column_table":
        return _extract_pdf_word_column_table_rows(pdf)
    if reader_id == "pdfplumber_line_table":
        return _extract_pdf_tables_from_horizontal_lines(pdf)
    if reader_id == "pdfplumber_table":
        return _extract_pdf_tables_default(pdf)
    return []


def _clean_pdf_table_cells(row):
    return [" ".join(str(c or "").split()).strip() for c in row]


def _append_pdf_table_rows(all_rows, table_rows, header_sig):
    for r in table_rows:
        cells = _clean_pdf_table_cells(r)
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
    return header_sig


def _extract_pdf_tables_default(pdf):
    all_rows = []
    header_sig = None
    for page in pdf.pages:
        for tbl in page.extract_tables():
            header_sig = _append_pdf_table_rows(all_rows, tbl, header_sig)
    return all_rows


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


WORD_COLUMN_TABLE_HEADERS = [
    "序号",
    "账务流水号",
    "提交时间",
    "交易时间",
    "交易名称",
    "借方金额（收）",
    "贷方金额（支）",
    "余额",
    "对方户名",
    "对方账号",
    "对方机构",
    "备注",
]


def _group_words_by_top(words):
    groups = {}
    for word in words:
        top = round(float(word.get("top", 0)), 1)
        groups.setdefault(top, []).append(word)
    return groups


def _word_column_header(words):
    header_set = set(WORD_COLUMN_TABLE_HEADERS)
    best = None
    for top, group in _group_words_by_top(words).items():
        by_text = {}
        for word in group:
            text = str(word.get("text") or "").strip()
            if text in header_set and text not in by_text:
                by_text[text] = word
        if len(by_text) < 8:
            continue
        if best is None or len(by_text) > len(best[1]):
            best = (top, by_text)
    if not best:
        return None, None
    top, by_text = best
    if not all(header in by_text for header in ("序号", "账务流水号", "交易时间", "余额", "备注")):
        return None, None
    starts = [float(by_text[header].get("x0", 0)) for header in WORD_COLUMN_TABLE_HEADERS]
    if starts != sorted(starts):
        return None, None
    return top, starts


def _word_column_boundaries(page_width, starts):
    return [0] + [(left + right) / 2 for left, right in zip(starts, starts[1:])] + [page_width + 10]


def _word_column_index(x, boundaries):
    for index in range(len(boundaries) - 1):
        if boundaries[index] <= x < boundaries[index + 1]:
            return index
    return None


def _is_word_column_row_anchor(word, serial_x):
    import re

    text = str(word.get("text") or "").strip()
    x0 = float(word.get("x0", 0))
    return serial_x - 10 <= x0 <= serial_x + 20 and re.fullmatch(r"\d{1,6}", text)


def _extract_pdf_word_column_table_rows(pdf):
    """Recover visual tables whose text words have stable column coordinates."""
    all_rows = [WORD_COLUMN_TABLE_HEADERS]
    for page in pdf.pages:
        words = page.extract_words(x_tolerance=1, y_tolerance=3, keep_blank_chars=False)
        header_top, starts = _word_column_header(words)
        if starts is None:
            continue
        boundaries = _word_column_boundaries(page.width, starts)
        body_words = [
            word for word in words
            if float(word.get("top", 0)) > header_top + 5
            and not str(word.get("text") or "").startswith("第")
        ]
        anchors = sorted(
            (float(word.get("top", 0)), word)
            for word in body_words
            if _is_word_column_row_anchor(word, starts[0])
        )
        for index, (anchor_top, _word) in enumerate(anchors):
            start_top = (anchors[index - 1][0] + anchor_top) / 2 if index else header_top + 5
            end_top = (anchor_top + anchors[index + 1][0]) / 2 if index + 1 < len(anchors) else page.height - 25
            cells = [[] for _ in WORD_COLUMN_TABLE_HEADERS]
            for word in body_words:
                top = float(word.get("top", 0))
                if not (start_top <= top < end_top):
                    continue
                col = _word_column_index(float(word.get("x0", 0)), boundaries)
                if col is None or col >= len(cells):
                    continue
                cells[col].append((top, float(word.get("x0", 0)), str(word.get("text") or "").strip()))
            row = [" ".join(text for _top, _x0, text in sorted(cell)).strip() for cell in cells]
            if row and row[0].isdigit():
                all_rows.append(row)
    return all_rows if len(all_rows) > 1 else []


TEXT_SEPARATOR_TABLE_HEADER = [
    "交易时间",
    "存入/支取",
    "对方账号",
    "对方户名",
    "对方行",
    "交易后余额",
    "交易渠道",
    "摘要",
    "备注",
]


def _split_text_separator_date_line(line):
    import re

    match = re.match(r"^(20\d{2}-\d{2}-\d{2})\s+(\d{6,})(?:\s+(.*))?$", (line or "").strip())
    if not match:
        return None
    return {
        "date": match.group(1),
        "account_head": match.group(2),
        "bank_head": (match.group(3) or "").strip(),
    }


def _split_text_separator_time_line(line):
    import re

    match = re.match(r"^(\d{2}:\d{2}:\d{2})(?:\s+(\d+))?(?:\s+(.*))?$", (line or "").strip())
    if not match:
        return None
    return {
        "time": match.group(1),
        "account_tail": (match.group(2) or "").strip(),
        "bank_tail": (match.group(3) or "").strip(),
    }


def _split_text_separator_amount_line(line, bank_head, bank_tail):
    import re

    tokens = (line or "").split()
    if len(tokens) < 5:
        return None
    transfer_flag = tokens[0]
    amount_idx = None
    amount_re = re.compile(r"^-+?\d[\d,]*\.\d{1,2}$")
    for idx in range(1, len(tokens)):
        if amount_re.match(tokens[idx]):
            amount_idx = idx
            break
    if amount_idx is None:
        return None
    balance_idx = None
    unsigned_amount_re = re.compile(r"^\d[\d,]*\.\d{1,2}$")
    for idx in range(amount_idx + 1, len(tokens)):
        if unsigned_amount_re.match(tokens[idx]):
            balance_idx = idx
            break
    if balance_idx is None:
        return None

    before_balance = tokens[amount_idx + 1:balance_idx]
    if bank_head or bank_tail:
        counterparty_name = " ".join(before_balance).strip()
        counterparty_bank = " ".join(x for x in [bank_head, bank_tail] if x).strip()
    else:
        counterparty_name = before_balance[0] if before_balance else ""
        counterparty_bank = " ".join(before_balance[1:]).strip()

    after_balance = tokens[balance_idx + 1:]
    channel = after_balance[0] if len(after_balance) > 0 else ""
    summary = after_balance[1] if len(after_balance) > 1 else ""
    remark = " ".join(after_balance[2:]).strip() if len(after_balance) > 2 else ""
    amount = tokens[amount_idx].replace(",", "")
    if amount.startswith("--"):
        amount = "-" + amount.lstrip("-")
    return {
        "transfer_flag": transfer_flag,
        "amount": amount,
        "counterparty_name": counterparty_name,
        "counterparty_bank": counterparty_bank,
        "balance": tokens[balance_idx].replace(",", ""),
        "channel": channel,
        "summary": summary,
        "remark": remark,
    }


def _parse_text_separator_transaction(lines, start_idx):
    date_part = _split_text_separator_date_line(lines[start_idx])
    if not date_part or start_idx + 2 >= len(lines):
        return None
    amount_line = lines[start_idx + 1]
    time_part = _split_text_separator_time_line(lines[start_idx + 2])
    if not time_part:
        return None
    amount_part = _split_text_separator_amount_line(
        amount_line,
        date_part["bank_head"],
        time_part["bank_tail"],
    )
    if not amount_part:
        return None

    account = " ".join(x for x in [date_part["account_head"], time_part["account_tail"]] if x).strip()
    return [
        f"{date_part['date']} {time_part['time']}",
        amount_part["amount"],
        account,
        amount_part["counterparty_name"],
        amount_part["counterparty_bank"],
        amount_part["balance"],
        amount_part["channel"],
        amount_part["summary"],
        amount_part["remark"],
    ]


def _is_text_separator_line(line):
    text = str(line or "").strip()
    return len(text) >= 8 and len(set(text)) == 1 and text[0] in {"—", "-", "_", "─"}


def _extract_pdf_text_separator_table_rows(pdf):
    """Read text-layer tables whose separators are text glyphs, not PDF line objects."""
    rows = [TEXT_SEPARATOR_TABLE_HEADER]
    import re

    for page in pdf.pages:
        text = page.extract_text() or ""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not any(_is_text_separator_line(line) for line in lines):
            continue
        idx = 0
        while idx < len(lines):
            if re.match(r"^20\d{2}-\d{2}-\d{2}\s+\d{6,}", lines[idx]):
                parsed = _parse_text_separator_transaction(lines, idx)
                if parsed:
                    rows.append(parsed)
                    idx += 3
                    continue
            idx += 1
    return rows if len(rows) > 1 else []


def _is_noise_text_table_line(line):
    text = str(line or "").strip()
    if not text:
        return True
    noise_markers = (
        "Transaction Statement",
        "Account No",
        "Account Type",
        "Sub Branch",
        "Verification Code",
        "Transaction Type Counter Party",
        "Transaction Type C o unter Party",
        "Date Currency",
        "Amount",
        "Balance",
        "Name Account",
        "合同ID号",
        "版本:",
        "发布时间:",
    )
    return any(marker in text for marker in noise_markers)


def _parse_currency_text_row(line):
    import re

    text = str(line or "").strip()
    match = re.match(
        r"^(?P<date>20\d{2}[-/]?\d{2}[-/]?\d{2})\s+"
        r"(?P<currency>[A-Z]{3})\s+"
        r"(?P<amount>[+-]?\d[\d,]*\.\d{2})\s+"
        r"(?P<balance>[+-]?\d[\d,]*\.\d{2})\s+"
        r"(?P<tail>.+)$",
        text,
    )
    if not match:
        return None
    tail = match.group("tail").strip()
    parts = tail.split(maxsplit=1)
    summary = parts[0] if parts else ""
    counterparty = parts[1] if len(parts) > 1 else ""
    return [
        match.group("date"),
        match.group("currency"),
        match.group("amount"),
        match.group("balance"),
        summary,
        counterparty,
    ]


def _parse_cmbc_personal_text_row(line):
    import re

    text = str(line or "").strip()
    match = re.match(
        r"^(?P<voucher_type>\S+)\s+"
        r"(?P<voucher_no>\d[\d*]{5,})\s+"
        r"(?P<date>20\d{2}/\d{2}/\d{2})\s+"
        r"(?P<time>\d{2}:\d{2}:\d{2})\s+"
        r"(?P<tail>.+)$",
        text,
    )
    if not match:
        match = re.match(
            r"^(?P<date>20\d{2}/\d{2}/\d{2})\s+"
            r"(?P<time>\d{2}:\d{2}:\d{2})\s+"
            r"(?P<tail>.+)$",
            text,
        )
    if not match:
        return None

    tokens = match.group("tail").split()
    amount_indexes = [
        idx for idx, token in enumerate(tokens)
        if re.match(r"^[+-]?\d[\d,]*\.\d{2}$", token)
    ]
    if len(amount_indexes) < 2:
        return None
    amount_idx, balance_idx = amount_indexes[:2]
    summary = " ".join(tokens[:amount_idx])
    after = tokens[balance_idx + 1:]
    current_flag = after[0] if len(after) > 0 else ""
    channel = after[1] if len(after) > 1 else ""
    institution = after[2] if len(after) > 2 else ""
    counterparty = " ".join(after[3:]) if len(after) > 3 else ""
    return [
        match.groupdict().get("voucher_type") or "",
        match.groupdict().get("voucher_no") or "",
        f"{match.group('date')} {match.group('time')}",
        summary,
        tokens[amount_idx],
        tokens[balance_idx],
        current_flag,
        channel,
        institution,
        counterparty,
        "",
    ]


def _extract_pdf_text_table_rows(text, text_table_kind):
    """Fallback for text-layer statement PDFs where extract_tables() returns no rows."""
    if text_table_kind == "currency":
        header = ["记账日期", "货币", "交易金额", "联机余额", "交易摘要", "对手信息"]
        rows = [header]
        for raw_line in str(text or "").splitlines():
            line = raw_line.strip()
            parsed = _parse_currency_text_row(line)
            if parsed:
                rows.append(parsed)
            elif len(rows) > 1 and line and not _is_noise_text_table_line(line):
                rows[-1][-1] = (rows[-1][-1] + " " + line).strip()
        return rows if len(rows) > 1 else []

    if text_table_kind == "cmbc_personal":
        header = [
            "凭证类型", "凭证号码", "交易时间", "摘要", "交易金额", "账户余额",
            "现转标志", "交易渠道", "交易机构", "对方户名/账号", "对方行名",
        ]
        rows = [header]
        for raw_line in str(text or "").splitlines():
            line = raw_line.strip()
            parsed = _parse_cmbc_personal_text_row(line)
            if parsed:
                rows.append(parsed)
            elif len(rows) > 1 and line and not _is_noise_text_table_line(line):
                import re

                voucher_continuation = re.match(r"^(\d{4,})(?:\s+(.*))?$", line)
                if voucher_continuation and rows[-1][1]:
                    rows[-1][1] = (rows[-1][1] + voucher_continuation.group(1)).strip()
                    rest = (voucher_continuation.group(2) or "").strip()
                    if rest:
                        rows[-1][9] = (rows[-1][9] + " " + rest).strip()
                else:
                    rows[-1][9] = (rows[-1][9] + " " + line).strip()
        return rows if len(rows) > 1 else []

    return []


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
    """读取 PDF 并按路由选择专属 reader 或通用表格 reader。

    返回 (preamble, rows, route_info)。preamble 供标准化层继续嗅探户名/账号。
    """
    with _open_pdf(path, open_password=open_password) as pdf:
        preamble = pdf.pages[0].extract_text() if pdf.pages else ""
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        route_info = route_pdf(text, 0, len(pdf.pages), context=_pdf_context(pdf, text))

        fingerprint_id = route_info.get("fingerprint_id", "")
        # 专用 reader 只接管已识别模板；未命中时回退到通用表格行，交给标准化层映射字段。
        if fingerprint_id in ABC_TEXT_PDF_FINGERPRINTS:
            preamble, rows = read_abc_text_pdf(pdf)
            return preamble, rows, route_info
        if fingerprint_id in JXRCB_TEXT_PDF_FINGERPRINTS:
            preamble, rows = read_jxrcb_text_pdf(pdf)
            return preamble, rows, route_info
        if fingerprint_id in JIANGXI_YUMIN_BANK_PDF_FINGERPRINTS:
            preamble, rows = read_jiangxi_yumin_bank_pdf(pdf)
            return preamble, rows, route_info
        if fingerprint_id in KASIKORN_TEXT_PDF_FINGERPRINTS:
            preamble, rows = read_kasikorn_text_pdf(pdf)
            return preamble, rows, route_info
        if fingerprint_id in ZHEJIANG_QYRCB_TEXT_PDF_FINGERPRINTS:
            preamble, rows = read_zhejiang_qyrcb_text_pdf(pdf)
            return preamble, rows, route_info
        if fingerprint_id in WECHAT_PAY_PROOF_PDF_FINGERPRINTS:
            preamble, rows = read_wechat_pay_proof_pdf(pdf)
            return preamble, rows, route_info
        if fingerprint_id in ALIPAY_PROOF_PDF_FINGERPRINTS:
            preamble, rows = read_alipay_proof_pdf(pdf)
            return preamble, rows, route_info
        table_rows = _extract_pdf_rows_by_reader(pdf, route_info.get("reader_id", ""))
        if fingerprint_id in TEXT_TABLE_FINGERPRINTS and not table_rows:
            rows = _extract_pdf_text_table_rows(text, TEXT_TABLE_FINGERPRINTS[fingerprint_id])
            return preamble or "", rows, route_info
        if route_info.get("decision") == "unmatched":
            table_rows = _extract_pdf_tables_default(pdf)
            if table_rows:
                route_info = {
                    **route_info,
                    "reader_id": "pdfplumber_table",
                }
            else:
                table_rows = _extract_pdf_tables_from_horizontal_lines(pdf)
                if table_rows:
                    route_info = {
                        **route_info,
                        "reader_id": "pdfplumber_line_table",
                    }

    return preamble or "", table_rows, route_info
