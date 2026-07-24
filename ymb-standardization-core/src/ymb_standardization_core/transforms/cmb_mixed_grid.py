"""招商 Excel 首页压缩网格变换。"""

import re

from ymb_standardization_core.readers.base import RawRows


def _parse_cmb_compact_row(row):
    left = str(row[0] or "").strip() if row else ""
    middle = str(row[4] or "").strip() if len(row) > 4 else ""
    left_match = re.match(
        r"^(20\d{2}-\d{2}-\d{2})\s+([A-Z]{3})\s+([+-]?[\d,]+\.\d{2})$", left
    )
    middle_match = re.match(r"^([+-]?[\d,]+\.\d{2})\s+(.+)$", middle)
    if not left_match or not middle_match:
        return None
    return [
        left_match.group(1), left_match.group(2), left_match.group(3),
        middle_match.group(1), middle_match.group(2),
        str(row[7] or "").strip() if len(row) > 7 else "",
    ]


def _amount(value):
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _repair_cmb_amount_scale(records):
    """按逐笔余额恒等式修复招行导出中偶发丢失两位小数的数值单元格。"""
    previous_balance = None
    for record in records:
        amount = _amount(record[2])
        balance = _amount(record[3])
        if amount is None or balance is None:
            continue
        if previous_balance is not None:
            candidates = []
            for amount_scale in (1, 100):
                for balance_scale in (1, 100):
                    fixed_amount = amount / amount_scale
                    fixed_balance = balance / balance_scale
                    residual = abs(previous_balance + fixed_amount - fixed_balance)
                    scaled_fields = (amount_scale != 1) + (balance_scale != 1)
                    candidates.append((round(residual, 6), scaled_fields, fixed_amount, fixed_balance))
            residual, _scaled, amount, balance = min(candidates)
            # 只有余额方程能在分币精度内闭合时才修正，不对缺行场景作猜测。
            if residual <= 0.02:
                record[2], record[3] = round(amount, 2), round(balance, 2)
        previous_balance = _amount(record[3])
    return records


def normalize_cmb_mixed_grid(rows: RawRows) -> RawRows:
    """统一招行 Excel 首页压缩布局与后续普通网格，同时保留原始行号位置。"""
    normalized = [list(row) for row in rows]
    if len(normalized) < 31:
        return normalized
    header = ["记账日期", "货币", "交易金额", "联机余额", "交易摘要", "对手信息"]
    normalized[10] = header
    records = []
    positions = []
    for index in range(11, min(28, len(normalized))):
        record = _parse_cmb_compact_row(normalized[index])
        if record:
            records.append(record)
            positions.append(index)
    normalized[28] = [None] * len(header)
    normalized[29] = [None] * len(header)
    for index in range(30, len(normalized)):
        row = normalized[index]
        date = row[0] if row else None
        if not (hasattr(date, "year") or re.match(r"^20\d{2}-\d{2}-\d{2}(?:\s|$)", str(date or ""))):
            normalized[index] = [None] * len(header)
            continue
        record = [
            date,
            row[1] if len(row) > 1 else "",
            row[2] if len(row) > 2 else "",
            row[4] if len(row) > 4 else "",
            row[5] if len(row) > 5 else "",
            row[7] if len(row) > 7 else "",
        ]
        records.append(record)
        positions.append(index)
    _repair_cmb_amount_scale(records)
    for index, record in zip(positions, records):
        normalized[index] = record
    return normalized
