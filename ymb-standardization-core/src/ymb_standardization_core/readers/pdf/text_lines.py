"""pdfplumber_text_lines Reader。"""

import re

from ymb_standardization_core.readers.registry import FunctionPdfReader


def _is_noise_text_table_line(line):
    text = str(line or "").strip()
    if not text:
        return True
    if re.match(r"^\d+/\d+$", text):
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
        "温馨提示",
        "记账日期 货币 交易金额 联机余额 交易摘要 对手信息",
    )
    return text == "Transaction" or any(marker in text for marker in noise_markers)


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
        pending = []
        for raw_line in str(text or "").splitlines():
            line = raw_line.strip()
            if "温馨提示" in line:
                pending = []
                break
            parsed = _parse_currency_text_row(line)
            if parsed:
                if pending:
                    continuation = " ".join(pending).strip()
                    if not parsed[-1]:
                        parsed[-1] = continuation
                    elif len(rows) > 1:
                        rows[-1][-1] = (rows[-1][-1] + " " + continuation).strip()
                    pending = []
                rows.append(parsed)
            elif _is_noise_text_table_line(line):
                if pending and len(rows) > 1:
                    continuation = " ".join(pending).strip()
                    if not re.fullmatch(r"[—_-]{5,}", continuation):
                        rows[-1][-1] = (rows[-1][-1] + " " + continuation).strip()
                pending = []
            elif len(rows) > 1 and line and not re.fullmatch(r"[—_-]{5,}", line):
                pending.append(line)
        if pending and len(rows) > 1:
            rows[-1][-1] = (rows[-1][-1] + " " + " ".join(pending)).strip()
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


def read(pdf, options):
    text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    return _extract_pdf_text_table_rows(text, options.get("text_table_layout") or "")


READER = FunctionPdfReader("pdfplumber_text_lines", read)
