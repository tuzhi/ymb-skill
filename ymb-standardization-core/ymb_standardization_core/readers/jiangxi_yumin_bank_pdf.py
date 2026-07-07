import re


DATE_RE = re.compile(r"^(\d{4}[‑-]\d{2}[‑-]\d{2})(转入|转出)?\s+(.*)$")
AMOUNT_RE = re.compile(r"^([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+(\S+)\s*(.*)$")
ACCOUNT_RE = re.compile(r"^\d{8,}$")


def _clean(value):
    return str(value or "").replace("‑", "-").replace("行", "行").strip()


def _split_tail(tail):
    tokens = tail.split()
    if not tokens:
        return "", "", ""
    category = tokens[0]
    rest = tokens[1:]
    account_at = next((i for i, token in enumerate(rest) if ACCOUNT_RE.match(token)), None)
    if account_at is None:
        return category, " ".join(rest), ""
    name_tokens = rest[:account_at] + rest[account_at + 1:]
    return category, " ".join(name_tokens), rest[account_at]


def read_jiangxi_yumin_bank_pdf(pdf):
    """解析江西裕民银行交易流水文本版 PDF。"""
    header = ["交易日期", "收支方向", "交易金额", "账户余额", "交易币种", "银行备注", "对手名称", "对手账户"]
    rows = [header]
    preamble = []
    current = None

    for page in pdf.pages:
        for raw_line in (page.extract_text() or "").splitlines():
            line = _clean(raw_line)
            if not line:
                continue
            if line.startswith(("江西裕民银行交易流水", "客户姓名:", "账号:", "交易日期 ")):
                preamble.append(line)
                continue
            m = DATE_RE.match(line)
            if m:
                if current:
                    rows.append(current)
                date, direction, rest = m.groups()
                am = AMOUNT_RE.match(rest)
                if not am:
                    current = None
                    continue
                amount, balance, currency, tail = am.groups()
                category, opponent, opponent_account = _split_tail(tail)
                current = [
                    date,
                    "收入" if direction == "转入" else "支出" if direction == "转出" else "",
                    amount,
                    balance,
                    currency,
                    " ".join(x for x in (direction or "", category) if x),
                    opponent,
                    opponent_account,
                ]
                continue
            if current:
                current[6] = (current[6] + line).strip()

    if current:
        rows.append(current)
    return "\n".join(preamble), rows if len(rows) > 1 else []
