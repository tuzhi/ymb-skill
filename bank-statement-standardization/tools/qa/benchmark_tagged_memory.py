#!/usr/bin/env python3
"""测量“整合打标流水”在内存中的实际占用。

脚本读取真实交付物，按原始行循环扩展到指定行数，再比较：
1. Excel 读入后的原始 object/string 结构；
2. 按业务含义转换 dtype 后的 31 列结构。

示例：
python3 tools/qa/benchmark_tagged_memory.py \
  --input scripts/runs/<run_id>/artifacts/<客户>_已清洗_待分析.xlsx \
  --rows 100000
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd


SKILL_ROOT = Path(__file__).resolve().parents[2]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from runtime.deliverable import STD_ORDER  # noqa: E402


NUMERIC_COLUMNS = {
    "收入金额",
    "支出金额",
    "交易金额",
    "分析收入金额",
    "分析支出金额",
    "分析交易金额",
    "账户余额",
    "虚拟账户余额",
    "标签置信度",
}
INTEGER_COLUMNS = {"来源行号"}
CATEGORY_COLUMNS = {
    "客户名称",
    "账户类型",
    "本方名称",
    "本方账户",
    "开户行",
    "交易状态",
    "收支方向",
    "一级标签",
    "二级标签",
    "三级标签",
    "标签来源",
    "交易渠道",
    "来源文件名",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="已清洗待分析 xlsx")
    parser.add_argument("--sheet", default="整合打标流水", help="主表 sheet 名")
    parser.add_argument("--rows", type=int, default=100_000, help="目标行数")
    return parser.parse_args()


def _repeat_to_rows(source: pd.DataFrame, rows: int) -> pd.DataFrame:
    if rows <= 0:
        raise ValueError("--rows 必须大于 0")
    if source.empty:
        raise ValueError("输入主表没有数据")
    repeats = (rows + len(source) - 1) // len(source)
    return pd.concat([source] * repeats, ignore_index=True).iloc[:rows].copy()


def _select_contract_columns(source: pd.DataFrame) -> pd.DataFrame:
    missing = [column for column in STD_ORDER if column not in source.columns]
    if missing:
        raise ValueError(f"主表缺少 STD_ORDER 列: {missing}")
    return source.loc[:, STD_ORDER].copy()


def _optimize_dtypes(source: pd.DataFrame) -> pd.DataFrame:
    result = source.copy()
    result["交易时间"] = pd.to_datetime(result["交易时间"], errors="coerce")
    for column in NUMERIC_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    for column in INTEGER_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="coerce").astype("Int32")
    for column in CATEGORY_COLUMNS:
        result[column] = result[column].astype("category")
    return result


def _memory_summary(frame: pd.DataFrame) -> dict[str, float | int]:
    memory_bytes = int(frame.memory_usage(index=True, deep=True).sum())
    return {
        "rows": len(frame),
        "columns": len(frame.columns),
        "memory_bytes": memory_bytes,
        "memory_mib": round(memory_bytes / 1024**2, 2),
        "bytes_per_row": round(memory_bytes / len(frame), 2),
    }


def main() -> int:
    args = _parse_args()
    raw = pd.read_excel(args.input, sheet_name=args.sheet, dtype=object)
    raw = _select_contract_columns(raw)
    expanded = _repeat_to_rows(raw, args.rows)
    optimized = _optimize_dtypes(expanded)

    raw_summary = _memory_summary(expanded)
    optimized_summary = _memory_summary(optimized)
    saving = 1 - optimized_summary["memory_bytes"] / raw_summary["memory_bytes"]
    result = {
        "input": str(args.input.resolve()),
        "sheet": args.sheet,
        "source_rows": len(raw),
        "target_rows": args.rows,
        "raw_object": raw_summary,
        "typed_31_columns": optimized_summary,
        "saving_percent": round(saving * 100, 2),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
