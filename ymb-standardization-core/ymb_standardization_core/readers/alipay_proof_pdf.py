HEADER = ["收/支", "交易对方", "商品说明", "收/付款方式", "金额", "交易订单号", "商家订单号", "交易时间"]


def _clean(value):
    return str(value or "").replace("\n", "").strip()


def _column_for_word(word):
    x0 = float(word.get("x0") or 0)
    if x0 < 75:
        return "收/支"
    if x0 < 130:
        return "交易对方"
    if x0 < 200:
        return "商品说明"
    if x0 < 255:
        return "收/付款方式"
    if x0 < 295:
        return "金额"
    if x0 < 395:
        return "交易订单号"
    if x0 < 490:
        return "商家订单号"
    return "交易时间"


def _append_cell(row, column, text):
    if not text:
        return
    current = row.get(column, "")
    if column == "交易时间" and current and not current.endswith(" ") and ":" in text:
        row[column] = f"{current} {text}"
        return
    row[column] = f"{current}{text}" if current else text


def _line_words(page):
    words = page.extract_words(x_tolerance=2, y_tolerance=3, keep_blank_chars=False)
    lines = []
    for word in words:
        text = _clean(word.get("text"))
        if not text:
            continue
        top = float(word.get("top") or 0)
        for line in lines:
            if abs(line["top"] - top) <= 3:
                line["words"].append(word)
                break
        else:
            lines.append({"top": top, "words": [word]})
    for line in lines:
        line["words"].sort(key=lambda item: float(item.get("x0") or 0))
    return sorted(lines, key=lambda item: item["top"])


def _is_record_start(words):
    if not words:
        return False
    first = _clean(words[0].get("text"))
    return first in {"收入", "支出", "不计"} and float(words[0].get("x0") or 0) < 75


def _is_noise_line(words):
    text = "".join(_clean(word.get("text")) for word in words)
    return (
        not text
        or text.startswith("编号:")
        or "支付宝支付科技有限公司" in text
        or text.startswith("兹证明:")
        or text.startswith("币种：")
        or text.startswith("交易时间段：")
        or text.startswith("交易类型：")
        or text.startswith("收/支交易对方商品说明")
        or text.startswith("第") and "页/共" in text
    )


def _row_values(row):
    return [_clean(row.get(column, "")) for column in HEADER]


def _annotate_order_state(rows):
    """Put Alipay order tracing information into the merchant-order cell.

    The standard schema has no dedicated Alipay order-id fields. Keeping this
    information in the source column lets YAML map it to 账户方附言 without
    changing the delivery workbook schema.
    """
    if len(rows) <= 1:
        return rows

    normal_merchant_orders = {
        row[6]
        for row in rows[1:]
        if len(row) >= 7 and row[0] in {"收入", "支出"} and row[6]
    }
    annotated = [rows[0]]
    for row in rows[1:]:
        if len(row) < len(HEADER):
            annotated.append(row)
            continue
        direction, order_id, merchant_order = row[0], row[5], row[6]
        parts = []
        if direction == "不计收支":
            if merchant_order and merchant_order in normal_merchant_orders:
                parts.append("支付宝订单状态=取消/退款关联")
            elif merchant_order:
                parts.append("支付宝订单状态=平台订单未配对不计收支")
            else:
                parts.append("支付宝订单状态=不计收支无商家订单号")
        if merchant_order:
            parts.append(f"支付宝商家订单号={merchant_order}")
        if order_id:
            parts.append(f"支付宝交易订单号={order_id}")
        row = list(row)
        row[6] = "；".join(parts)
        annotated.append(row)
    return annotated


def read_alipay_proof_pdf(pdf):
    """解析支付宝交易流水证明 PDF。"""
    rows = [HEADER]
    preamble = []
    current = None

    for page in pdf.pages:
        text = page.extract_text() or ""
        for line in text.splitlines()[:8]:
            if any(mark in line for mark in ("支付宝支付科技有限公司", "交易流水证明", "兹证明", "交易时间段")):
                preamble.append(line.strip())

        for line in _line_words(page):
            words = line["words"]
            if _is_noise_line(words):
                continue
            if _is_record_start(words):
                if current:
                    rows.append(_row_values(current))
                current = {column: "" for column in HEADER}
            if current is None:
                continue
            for word in words:
                _append_cell(current, _column_for_word(word), _clean(word.get("text")))

    if current:
        rows.append(_row_values(current))

    rows = _annotate_order_state(rows)
    return "\n".join(preamble), rows if len(rows) > 1 else []
