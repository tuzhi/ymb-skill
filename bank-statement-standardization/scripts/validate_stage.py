#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic validators for production pipeline artifacts."""
import argparse
import glob
import json
import os
import sys

import pandas as pd


STD_REQUIRED = {
    "交易唯一编号", "交易时间", "本方账户", "收入金额", "支出金额",
    "交易金额", "账户余额", "来源文件名", "来源行号",
}
TAG_REQUIRED = STD_REQUIRED | {"收支方向", "一级标签", "二级标签", "三级标签", "标签来源"}
FINAL_SHEETS = {
    "封面与说明", "整合打标流水", "组合日余额(虚拟账户)", "主体账户清单",
    "余额校验", "标签汇总", "人工复核事项",
}


class ValidationError(RuntimeError):
    pass


def _one(folder, pattern):
    hits = sorted(glob.glob(os.path.join(folder, pattern)))
    if not hits:
        raise ValidationError(f"缺少产物：{pattern}")
    return hits[-1]


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        raise ValidationError(f"JSON 无法读取：{path}：{exc}")


def _load_csv(path):
    try:
        df = pd.read_csv(path, dtype=str)
    except Exception as exc:
        raise ValidationError(f"CSV 无法读取：{path}：{exc}")
    if df.empty:
        raise ValidationError(f"CSV 无交易数据：{path}")
    return df


def _require_columns(df, required, path):
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValidationError(f"缺少必需字段：{path}：{', '.join(missing)}")


def _traceability(df, path):
    for col in ("交易唯一编号", "来源文件名", "来源行号"):
        empty = df[col].fillna("").astype(str).str.strip().isin(["", "nan", "None"])
        if empty.any():
            raise ValidationError(f"来源追溯字段存在空值：{path}：{col} 空值 {int(empty.sum())} 行")


def validate_standardize(work_dir):
    csvs = sorted(glob.glob(os.path.join(work_dir, "*__standardized.csv")))
    reports = sorted(glob.glob(os.path.join(work_dir, "*__mapping.json")))
    if not csvs:
        raise ValidationError("阶段一未生成标准化 CSV")
    if len(csvs) != len(reports):
        raise ValidationError(f"阶段一 CSV 与映射报告数量不一致：{len(csvs)} != {len(reports)}")
    rows = 0
    for path in csvs:
        df = _load_csv(path)
        _require_columns(df, STD_REQUIRED, path)
        _traceability(df, path)
        rows += len(df)
    for path in reports:
        _load_json(path)
    return {"standardized_files": len(csvs), "standardized_rows": rows}


def validate_integrate(work_dir):
    csv_path = _one(work_dir, "*__整合流水.csv")
    json_path = _one(work_dir, "*__整合报告.json")
    df = _load_csv(csv_path)
    _require_columns(df, STD_REQUIRED, csv_path)
    _traceability(df, csv_path)
    report = _load_json(json_path)
    overview = report.get("客户整合概览", {})
    expected = overview.get("整合交易数")
    if expected is not None and int(expected) != len(df):
        raise ValidationError(f"阶段二整合交易数不一致：报告 {expected}，CSV {len(df)}")
    return {"integrated_rows": len(df), "integrated_csv": csv_path}


def validate_portfolio(work_dir):
    csv_path = _one(work_dir, "*__组合日余额.csv")
    json_path = _one(work_dir, "*__余额校验.json")
    try:
        df = pd.read_csv(csv_path, dtype=str)
    except Exception as exc:
        raise ValidationError(f"CSV 无法读取：{csv_path}：{exc}")
    report = _load_json(json_path)
    if "合计余额" not in df.columns:
        raise ValidationError("阶段二补充产物缺少合计余额")
    return {"portfolio_days": len(df), "portfolio_report": json_path, "report_keys": sorted(report)}


def validate_tag(work_dir, integrated_rows=None):
    csv_path = _one(work_dir, "*__打标流水.csv")
    json_path = _one(work_dir, "*__标签报告.json")
    df = _load_csv(csv_path)
    _require_columns(df, TAG_REQUIRED, csv_path)
    _traceability(df, csv_path)
    _load_json(json_path)
    if integrated_rows is not None and len(df) != integrated_rows:
        raise ValidationError(f"阶段三打标前后交易数不一致：{integrated_rows} != {len(df)}")
    return {"tagged_rows": len(df), "tagged_csv": csv_path}


def validate_final(out_dir, client, tagged_rows=None):
    path = os.path.join(out_dir, f"{client}_已清洗_待分析.xlsx")
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        raise ValidationError(f"最终交付物不存在或为空：{path}")
    try:
        book = pd.ExcelFile(path)
    except Exception as exc:
        raise ValidationError(f"最终交付物无法打开：{path}：{exc}")
    missing = sorted(FINAL_SHEETS - set(book.sheet_names))
    if missing:
        raise ValidationError(f"最终交付物缺少 sheet：{', '.join(missing)}")
    flow = pd.read_excel(path, sheet_name="整合打标流水", dtype=str)
    _require_columns(flow, TAG_REQUIRED, path)
    _traceability(flow, path)
    if tagged_rows is not None and len(flow) != tagged_rows:
        raise ValidationError(f"最终交付物主表行数不一致：{tagged_rows} != {len(flow)}")
    return {"deliverable": path, "deliverable_rows": len(flow), "sheets": book.sheet_names}


def validate_all(work_dir, out_dir, client):
    result = {}
    result["stage_1_standardize"] = validate_standardize(work_dir)
    result["stage_2_integrate"] = validate_integrate(work_dir)
    integrated_rows = result["stage_2_integrate"]["integrated_rows"]
    result["stage_2b_portfolio"] = validate_portfolio(work_dir)
    result["stage_3_tag"] = validate_tag(work_dir, integrated_rows=integrated_rows)
    result["stage_4_deliverable"] = validate_final(
        out_dir, client, tagged_rows=result["stage_3_tag"]["tagged_rows"])
    return result


def main():
    ap = argparse.ArgumentParser(description="校验银行流水标准化流水线产物")
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--client", required=True)
    args = ap.parse_args()
    try:
        result = validate_all(args.work_dir, args.out_dir, args.client)
    except ValidationError as exc:
        print(json.dumps({"status": "ERROR", "message": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "OK", "stages": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
