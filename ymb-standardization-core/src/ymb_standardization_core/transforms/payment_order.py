"""支付平台订单状态行变换。"""

from ymb_standardization_core.readers.base import RawRows


def _clean_payment_cell(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _clean_payment_order_id(value: object) -> str:
    return "".join(str(value or "").split()).strip()


def annotate_payment_order_state(rows: RawRows) -> RawRows:
    if not rows:
        return rows
    headers = [_clean_payment_cell(header) for header in rows[0]]
    required = {"收/支", "交易订单号", "商家订单号"}
    if not required.issubset(set(headers)):
        return rows

    direction_index = headers.index("收/支")
    trade_order_index = headers.index("交易订单号")
    merchant_order_index = headers.index("商家订单号")
    normal_orders = set()
    normalized_rows = [headers]

    for row in rows[1:]:
        cells = list(row) + [""] * max(0, len(headers) - len(row))
        cells = cells[:len(headers)]
        merchant_order = _clean_payment_order_id(cells[merchant_order_index])
        direction = _clean_payment_cell(cells[direction_index])
        if merchant_order and direction in {"收入", "支出"}:
            normal_orders.add(merchant_order)
        normalized_rows.append(cells)

    output = [headers]
    for cells in normalized_rows[1:]:
        merchant_order = _clean_payment_order_id(cells[merchant_order_index])
        trade_order = _clean_payment_order_id(cells[trade_order_index])
        direction = _clean_payment_cell(cells[direction_index])
        parts = []
        if merchant_order:
            parts.append(f"支付宝商家订单号={merchant_order}")
        if trade_order:
            parts.append(f"支付宝交易订单号={trade_order}")
        if direction.startswith("不计"):
            if merchant_order and merchant_order in normal_orders:
                parts.append("支付宝订单状态=取消/退款关联")
            elif merchant_order:
                parts.append("支付宝订单状态=平台订单未配对不计收支")
            else:
                parts.append("支付宝订单状态=不计收支无商家订单号")
        if parts:
            cells = list(cells)
            cells[merchant_order_index] = "；".join(parts)
        output.append(cells)
    return output
