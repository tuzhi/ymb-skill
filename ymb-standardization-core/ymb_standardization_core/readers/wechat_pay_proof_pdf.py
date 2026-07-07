OTHER_DIRECTION = {
    "零钱提现": "支出",
    "经营账户提现": "支出",
    "零钱充值": "收入",
    "转入零钱通-来自零钱": "支出",
    "零钱通转出-到零钱": "收入",
}


def _clean(value):
    return str(value or "").replace("\n", "").strip()


def read_wechat_pay_proof_pdf(pdf):
    """解析微信支付交易明细证明 PDF，保留微信“其他”类资金方向。"""
    header = ["交易单号", "交易时间", "银行备注", "收支方向", "交易渠道", "交易金额", "对手名称", "商户单号"]
    rows = [header]
    preamble = []
    source_header = None

    for page in pdf.pages:
        text = page.extract_text() or ""
        for line in text.splitlines()[:8]:
            if any(mark in line for mark in ("微信支付交易明细证明", "兹证明", "交易明细对应时间段")):
                preamble.append(line.strip())
        for table in page.extract_tables():
            for raw in table:
                cells = [_clean(cell) for cell in raw]
                if not any(cells):
                    continue
                if "交易单号" in cells and "金额(元)" in cells:
                    source_header = cells
                    continue
                if not source_header or len(cells) < len(source_header):
                    continue
                item = dict(zip(source_header, cells))
                tx_type = item.get("交易类型", "")
                direction = item.get("收/支/其他", "")
                if direction == "其他":
                    direction = OTHER_DIRECTION.get(tx_type, "")
                rows.append([
                    item.get("交易单号", ""),
                    item.get("交易时间", ""),
                    tx_type,
                    direction,
                    item.get("交易方式", ""),
                    item.get("金额(元)", ""),
                    item.get("交易对方", ""),
                    item.get("商户单号", ""),
                ])

    return "\n".join(preamble), rows if len(rows) > 1 else []
