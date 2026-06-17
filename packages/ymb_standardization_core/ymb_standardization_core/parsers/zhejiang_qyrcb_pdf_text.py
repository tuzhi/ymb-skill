import re


HEADER = [
    "交易日期", "币种", "交易摘要", "交易金额", "账户余额",
    "对方账号", "对方户名", "对方行", "交易渠道", "备注",
    "本方名称", "本方账户", "开户行", "账户类型",
]

CHANNEL_MARKERS = {
    "人行接口", "移动前台", "网联平台", "综合前端", "前置", "柜面", "信用卡",
    "存款日终记账API",
}

TXN_RE = re.compile(
    r"^(20\d{2}-\d{2}-\d{2})\s+(人民币)\s+(.+?)\s+"
    r"([+-]?\d[\d,]*\.\d{2})\s+([+-]?\d[\d,]*\.\d{2})(?:\s+(.*))?$"
)


def _clean(text):
    return re.sub(r"\s+", "", text or "").strip()


def _first_match(pattern, text):
    m = re.search(pattern, text or "")
    return m.group(1).strip() if m else ""


def _split_tail(tail):
    """拆分交易行余额之后的对手、渠道和备注。

    浙江庆元农商 PDF 没有表格边框，文本层里对手行、渠道、备注经常压在同一行；
    这里只做保守结构化，无法确定的文字留在备注，不丢失。
    """
    tokens = (tail or "").split()
    if not tokens:
        return "", "", "", "", ""

    opponent_account = ""
    opponent_name = ""
    opponent_bank = ""
    channel = ""
    remark = ""

    if re.fullmatch(r"\d{6,}", tokens[0]):
        opponent_account = tokens.pop(0)

    channel_idx = None
    for idx, token in enumerate(tokens):
        if token in CHANNEL_MARKERS:
            channel_idx = idx
            break

    if channel_idx is None:
        if tokens and tokens[0] in CHANNEL_MARKERS:
            channel = tokens[0]
            remark = " ".join(tokens[1:])
        elif tokens and not opponent_account:
            channel = tokens[0]
            remark = " ".join(tokens[1:])
        else:
            opponent_name = " ".join(tokens)
    else:
        before = tokens[:channel_idx]
        after = tokens[channel_idx + 1:]
        channel = tokens[channel_idx]
        if opponent_account:
            if len(before) >= 2:
                opponent_name = before[0]
                opponent_bank = " ".join(before[1:])
            elif before:
                opponent_name = before[0]
        else:
            remark = " ".join(before)
        if after:
            remark = " ".join(x for x in [remark, " ".join(after)] if x)

    return opponent_account, opponent_name, opponent_bank, channel, remark


def parse_transaction_line(line):
    m = TXN_RE.match((line or "").strip())
    if not m:
        return None
    date, currency, summary, amount, balance, tail = m.groups()
    opponent_account, opponent_name, opponent_bank, channel, remark = _split_tail(tail or "")
    return {
        "交易日期": date,
        "币种": currency,
        "交易摘要": summary.strip(),
        "交易金额": amount.replace(",", ""),
        "账户余额": balance.replace(",", ""),
        "对方账号": opponent_account,
        "对方户名": opponent_name,
        "对方行": opponent_bank,
        "交易渠道": channel,
        "备注": remark,
    }


def _is_noise_line(line):
    line = (line or "").strip()
    if not line:
        return True
    if line.startswith("第") and "页" in line:
        return True
    if line.startswith("重要提示"):
        return True
    if set(line) <= {"."}:
        return True
    return any(marker in line for marker in ("电子", "回OV", "专VI", "用0J", "VP", "CN"))


def read_zhejiang_qyrcb_text_pdf(pdf):
    """解析浙江庆元农商银行“个人账户交易明细”文本层 PDF。"""
    page_texts = [page.extract_text() or "" for page in pdf.pages]
    first_page = page_texts[0] if page_texts else ""

    name = _first_match(r"户名[:：]\s*([^\s]+)", first_page)
    account = _first_match(r"账号[:：]\s*(\d[\d*]{5,}\d)", first_page)
    bank = _first_match(r"开户行[:：]\s*(.+?)\s+账户种类", first_page) or "浙江庆元农商银行"
    account_type = _first_match(r"账户种类[:：]\s*([^\s]+)", first_page) or "个人"

    rows = [HEADER]
    last_row = None
    for page_text in page_texts:
        for raw_line in page_text.splitlines():
            line = raw_line.strip()
            parsed = parse_transaction_line(line)
            if parsed:
                row = [
                    parsed["交易日期"],
                    parsed["币种"],
                    parsed["交易摘要"],
                    parsed["交易金额"],
                    parsed["账户余额"],
                    parsed["对方账号"],
                    parsed["对方户名"],
                    parsed["对方行"],
                    parsed["交易渠道"],
                    parsed["备注"],
                    name,
                    account,
                    bank,
                    account_type,
                ]
                rows.append(row)
                last_row = row
                continue

            # 交易行之后常有“系统”“款”“（大小额）”等续行；保留到备注，避免信息丢失。
            if last_row is not None and not _is_noise_line(line):
                last_row[9] = " ".join(x for x in [last_row[9], line] if x).strip()

    return first_page, rows if len(rows) > 1 else []
