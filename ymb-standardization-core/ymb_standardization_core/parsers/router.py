from ymb_standardization_core.parsers.abc_text_pdf import read_abc_text_pdf
from ymb_standardization_core.parsers.jiangxi_yumin_bank_pdf import read_jiangxi_yumin_bank_pdf
from ymb_standardization_core.parsers.jxrcb_pdf_text import read_jxrcb_text_pdf
from ymb_standardization_core.parsers.kasikorn_pdf_text import read_kasikorn_text_pdf
from ymb_standardization_core.parsers.routing.rule_loader import load_pdf_route_rules
from ymb_standardization_core.parsers.wechat_pay_proof_pdf import read_wechat_pay_proof_pdf
from ymb_standardization_core.parsers.zhejiang_qyrcb_pdf_text import read_zhejiang_qyrcb_text_pdf

ABC_TEXT_PDF_FINGERPRINTS = {"md5:ab5d413308d9d27f3aa913d772fa3494"}
JXRCB_TEXT_PDF_FINGERPRINTS = {"md5:e833fbf4a2171d66315c5a3bda64711c"}
KASIKORN_TEXT_PDF_FINGERPRINTS = {"md5:37399b38ddd3572cc70fc6f8b9be2900"}
ZHEJIANG_QYRCB_TEXT_PDF_FINGERPRINTS = {"md5:69c7df7286e238aef80ae49938fd397a"}
JIANGXI_YUMIN_BANK_PDF_FINGERPRINTS = {"md5:19c8a8f7513adce0f0ad32a5c0b05154"}
WECHAT_PAY_PROOF_PDF_FINGERPRINTS = {
    "md5:48a1a9cde662e1515e3d8f3238934e92",
    "md5:13cbd1af07e92414229d298a67bcf533",
}
TEXT_TABLE_FINGERPRINTS = {
    "md5:336aced4f33ef27ad250e418e5b5eb18": "currency",
    "md5:0818218cb218b9bdb699770e6a65e6dd": "currency",
    "md5:831325d33aa7b01f10771881ffc3ae76": "cmbc_personal",
}


def _pdf_candidate(id, parser_id, file_type, bank, account_type, column_mapping,
                   identity_evidence, columns_evidence, route_evidence=None):
    return {
        "id": id,
        "fingerprint_id": id,
        "parser_id": parser_id,
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
    return {
        "parser_id": "pdf_table" if table_row_count else "none",
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
        "parser_id": "none",
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
            parser_id=rule.parser_id,
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
    """读取 PDF 并按路由选择专属 parser 或通用表格 parser。

    返回 (preamble, rows, route_info)。preamble 供标准化层继续嗅探户名/账号。
    """
    with _open_pdf(path, open_password=open_password) as pdf:
        preamble = pdf.pages[0].extract_text() if pdf.pages else ""
        table_rows = _extract_pdf_tables(pdf)
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        route_info = route_pdf(text, len(table_rows), len(pdf.pages), context=_pdf_context(pdf, text))

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
        if fingerprint_id in TEXT_TABLE_FINGERPRINTS and not table_rows:
            rows = _extract_pdf_text_table_rows(text, TEXT_TABLE_FINGERPRINTS[fingerprint_id])
            return preamble or "", rows, route_info

    return preamble or "", table_rows, route_info
