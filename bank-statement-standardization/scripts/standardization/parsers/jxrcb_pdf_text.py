import re


HEADER = ["记账日期", "交易金额", "交易后余额", "交易摘要",
          "对方户名", "对方账号", "本方名称", "本方账户", "开户行", "账户类型"]
WATERMARK_RE = re.compile(r"[\u6c5f\u897f\u519c\u5546\u94f6\u884c\u00b7]")
WATERMARK_TOKENS = {"江", "西", "农", "商", "银", "行", "·"}


def _clean_token(token):
    """清理江西农商 PDF 文本层中的水印字符。

    水印常以单字「江/西/农/商/银/行/·」插入日期、金额、账号中；仅对含数字的 token
    做强清理，避免误伤对手名称里的真实地名或业务词。
    """
    token = (token or "").strip()
    if not token or token in WATERMARK_TOKENS:
        return ""
    token = token.strip("·")
    if any(ch.isdigit() for ch in token):
        token = WATERMARK_RE.sub("", token)
    return token.strip()


def _clean_line_tokens(line):
    out = []
    for token in (line or "").split():
        cleaned = _clean_token(token)
        if cleaned and cleaned not in WATERMARK_TOKENS:
            out.append(cleaned)
    return out


def _clean_text_value(text):
    """清理已知水印误插入的中文短语。

    不能全局删除「行」字：真实摘要里有「跨行转出/跨行转入」，对手名称也可能包含地名。
    这里只修正样本中反复出现、语义确定的污染形态。
    """
    text = (text or "").strip()
    replacements = {
        "微信行支付": "微信支付",
        "微信行提现": "微信提现",
        "微信行转账": "微信转账",
        "扫二维行码付款": "扫二维码付款",
        "贷款自行动还款": "贷款自动还款",
        "利行息": "利息",
        "财行付通": "财付通",
        "张行华峰": "张华峰",
        "跨行转入行 -": "跨行转入-",
        "跨行转出行 -": "跨行转出-",
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return text.strip()


def parse_transaction_line(line):
    """把一行江西农商文本层交易还原成明细列。

    行格式大体为：记账日期 交易金额 交易后余额 交易摘要 对方户名 对方账号。
    对手信息可能缺失；水印可能插入数字 token，因此先做 token 级清理再定位日期和两列金额。
    """
    tokens = _clean_line_tokens(line)
    date_idx = None
    for idx, token in enumerate(tokens):
        if re.match(r"^20\d{2}-\d{1,2}-\d{1,2}$", token):
            date_idx = idx
            break
    if date_idx is None:
        return None

    amount_indexes = []
    for idx in range(date_idx + 1, len(tokens)):
        if re.match(r"^[+-]?\d[\d,]*\.\d{1,2}$", tokens[idx]):
            amount_indexes.append(idx)
            if len(amount_indexes) == 2:
                break
    if len(amount_indexes) < 2:
        return None

    amount_idx, balance_idx = amount_indexes
    tail = tokens[balance_idx + 1:]
    opponent_account = ""
    if tail:
        last = WATERMARK_RE.sub("", tail[-1])
        if re.match(r"^\d{6,}$", last):
            opponent_account = last
            tail = tail[:-1]

    summary = _clean_text_value(tail[0]) if tail else ""
    opponent_name = _clean_text_value(" ".join(tail[1:])) if len(tail) > 1 else ""
    return [tokens[date_idx], tokens[amount_idx], tokens[balance_idx],
            summary, opponent_name, opponent_account]


def read_jxrcb_text_pdf(pdf):
    """解析江西农商银行文本层 PDF。

    该模板可抽取文本，但 extract_tables() 往往为空；相比 OCR，直接解析文本层更可复现，
    且不会引入 OCR 中间 CSV 污染输入目录。
    """
    page_texts = [page.extract_text() or "" for page in pdf.pages]
    first_page = page_texts[0] if page_texts else ""
    name = ""
    account = ""
    bank = "江西农商银行"
    m = re.search(r"户\s*名[:：]\s*([^\s]+)", first_page)
    if m:
        name = m.group(1).strip()
    m = re.search(r"账\s*号[:：]\s*(\d[\d*]{5,}\d)", first_page)
    if m:
        account = m.group(1).strip()
    m = re.search(r"开\s*户\s*行\s*行?[:：]\s*(.+?)(?:申请|$)", first_page)
    if m:
        # PDF 文本层偶发把「行」插进开户行名称，只修正已知污染形态，不做宽泛替换。
        bank = re.sub(r"\s+", "", m.group(1)).replace("商业行银行", "商业银行").strip() or bank

    rows = [HEADER]
    for page_text in page_texts:
        for raw_line in page_text.splitlines():
            parsed = parse_transaction_line(raw_line)
            if not parsed:
                continue
            # 户名/账号来自首页抬头，交易行只负责补齐交易字段和对手信息。
            rows.append(parsed + [name, account, bank, "个人"])

    return first_page, rows if len(rows) > 1 else []
