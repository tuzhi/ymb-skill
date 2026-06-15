"""Kasikorn Bank（开泰银行）英文文本版 PDF parser。"""

import re
from datetime import datetime


# 开泰银行交易类型里的金额通常为正数，收支方向需要从交易类型推断。
INCOME_TYPES = {
    "Transfer Deposit",
    "Cash Deposit",
    "Interest Deposit",
    "Inter-Region Transfer",
}
EXPENSE_TYPES = {
    "Transfer Withdrawal",
    "Payment",
    "Cash Withdrawal",
    "Fee",
    "Annual Debit Card Fee",
    "Withholding Tax Payable",
}

CHANNEL_RE = re.compile(
    r"^(K PLUS|K BIZ|ATM(?:\s+K-Lobby(?:\s+\S+)?)?|EDC/K SHOP/MYQR|"
    r"Internet/Mobile\s+(?:SCB|BBL|Across Banks)|"
    r"Automatic Transfer|CDM(?:\s+\S+)?)\b"
)
TXN_LINE_RE = re.compile(r"^(\d{2}-\d{2}-\d{2})\s+(\d{2}:\d{2})\b")
AMOUNT_RE = re.compile(r"([\d,]+\.\d{2})")
SKIP_LINE_PATTERNS = [
    re.compile(p) for p in [
        r"^PAGE/OF\s",
        r"^Ref\.\s*No\.",
        r"^AccountMR\.",
        r"^\d+/\d+\s",
        r"^Owner Branch",
        r"^Period\s",
        r"^Total Withdrawal",
        r"^Total Deposit",
        r"^Time/\s*$",
        r"^Date\s+Descriptions",
        r"^Eff\.Date",
        r"^For more information",
        r"^Issued by",
        r"^FDPBK$",
        r"^\)\d+-\d+\($",
        r"^\)[\w.\-]+\($",
    ]
]


def _is_skip_line(line):
    return any(pat.match(line) for pat in SKIP_LINE_PATTERNS)


def _parse_amount(value):
    if not value:
        return None
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None


def _find_amounts(text):
    return [(match.group(1), match.start()) for match in AMOUNT_RE.finditer(text)]


def _classify_transaction(desc):
    desc_lower = desc.strip().lower()
    for item in INCOME_TYPES:
        if desc_lower == item.lower() or desc_lower.startswith(item.lower()):
            return "收入"
    for item in EXPENSE_TYPES:
        if desc_lower == item.lower() or desc_lower.startswith(item.lower()):
            return "支出"
    if "deposit" in desc_lower:
        return "收入"
    if any(key in desc_lower for key in ("withdrawal", "payment", "fee", "tax")):
        return "支出"
    return "未知"


def _split_channel_details(after_balance):
    after_balance = after_balance.strip()
    if not after_balance:
        return "", ""

    match = CHANNEL_RE.match(after_balance)
    if match:
        channel = match.group(1)
        return channel, after_balance[match.end():].strip()

    if "Branch" in after_balance or "Ref Code" in after_balance:
        return "Branch", after_balance

    return "", after_balance


def _extract_type_amount_balance_rest(after_time):
    amounts = _find_amounts(after_time)
    if not amounts:
        return after_time.strip(), None, None, "", after_time.strip(), ""

    if len(amounts) == 1:
        amount, amount_pos = amounts[0]
        type_text = after_time[:amount_pos].strip()
        if "beginning balance" in type_text.lower():
            return type_text, None, _parse_amount(amount), "", "", ""
        return type_text, _parse_amount(amount), None, "", "", ""

    balance, balance_pos = amounts[-1]
    amount, amount_pos = amounts[0]
    type_text = after_time[:amount_pos].strip()
    after_balance = after_time[balance_pos + len(balance):].strip()
    channel, details = _split_channel_details(after_balance)

    return (
        type_text,
        _parse_amount(amount),
        _parse_amount(balance),
        channel,
        details,
        _classify_transaction(type_text),
    )


def _parse_page(page_text):
    rows = []
    preamble = []
    in_table = False
    previous = None

    for raw_line in page_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if _is_skip_line(line):
            if "AccountMR" in line or "Account Number" in line or "Ending Balance" in line:
                preamble.append(line)
            continue

        match = TXN_LINE_RE.match(line)
        if match:
            in_table = True
            date_text = match.group(1)
            time_text = match.group(2)
            after_time = line[match.end():].strip()

            if "beginning balance" in after_time.lower():
                amounts = _find_amounts(after_time)
                balance = _parse_amount(amounts[0][0]) if amounts else None
                previous = {
                    "date": date_text,
                    "time": time_text,
                    "type": "Beginning Balance",
                    "amount": None,
                    "balance": balance,
                    "channel": "",
                    "details": "",
                    "direction": "",
                }
                rows.append(previous)
                continue

            txn_type, amount, balance, channel, details, direction = _extract_type_amount_balance_rest(after_time)
            previous = {
                "date": date_text,
                "time": time_text,
                "type": txn_type,
                "amount": amount,
                "balance": balance,
                "channel": channel,
                "details": details,
                "direction": direction,
            }
            rows.append(previous)
        elif in_table and previous:
            if line in ("FDPBK",) or re.match(r"^\)\d+-\d+\($", line):
                continue
            if not _is_skip_line(line):
                previous["details"] = (previous["details"] + " " + line).strip()

    return preamble, rows


def read_kasikorn_text_pdf(pdf):
    """读取开泰银行文本层 PDF，返回 standardize.py 可消费的表格行。"""
    all_preamble = []
    all_rows = []

    for page in pdf.pages:
        preamble, rows = _parse_page(page.extract_text() or "")
        all_preamble.extend(preamble)
        all_rows.extend(rows)

    header = ["交易日期", "交易时间", "交易摘要", "交易金额", "本次余额", "对手信息", "交易渠道", "交易附言"]
    output = [header]

    preamble_text = " ".join(all_preamble)
    name_match = re.search(r"AccountMR\.\s*(.+?)\s+Reference", preamble_text)
    account_name = name_match.group(1).strip() if name_match else ""
    account_match = re.search(r"Account Number\s+([\d\-]+)", preamble_text)
    account_number = account_match.group(1).strip() if account_match else ""

    for row in all_rows:
        if row["type"] == "Beginning Balance":
            continue

        opponent = ""
        details = row["details"]
        if details:
            to_from = re.match(r"(To|From)\s+(.+?)(?:\+\+|$)", details)
            if to_from:
                opponent = to_from.group(2).rstrip("+").strip()
            elif "Paid for Ref" in details or "Ref Code" in details:
                opponent = details

        amount = row["amount"] or 0
        signed_amount = -abs(amount) if row["direction"] == "支出" else abs(amount)

        output.append([
            row["date"],
            row["time"],
            row["type"],
            f"{signed_amount:.2f}",
            f"{row['balance']:.2f}" if row["balance"] is not None else "",
            opponent,
            row["channel"],
            details,
        ])

    structured_preamble = f"户名：{account_name}\n账号：{account_number}\n" + "\n".join(all_preamble)
    return structured_preamble, output if len(output) > 1 else []
