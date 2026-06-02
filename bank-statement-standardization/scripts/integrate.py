#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
integrate.py — 单客户多流水文件整合与验证（阶段二，对应 Prompt 2）

把同一客户的多份标准化流水（standardize.py 的产物）合并为一张统一明细，并：
  - 按「本方账户」分别做余额连续性校验（不同账户不可混在一起校验）；
  - 识别疑似重复交易（PDF 与 Excel 同源最常见）；
  - 识别自有账户互转候选（仅标记，绝不删除原始交易）；
  - 给出每个账户的交易期间、期间提示；
  - 汇总数据质量问题与人工复核事项。

遵循附件约定：保留全部来源信息，低置信问题只进人工复核，不自动修正。

用法：
  python integrate.py <客户名> <standardized目录或多个csv> [--out-dir DIR]
      [--self-accounts 账号1 账号2 ...]   # 已知本方账户清单（可选，辅助互转判断）

输出：
  <客户名>__整合流水.csv     合并后的标准化明细（已排序、带账户分组）
  <客户名>__整合报告.json    Prompt 2 结构的整合与校验报告
"""
import argparse, glob, json, os, sys
from datetime import datetime, timedelta

try:
    import pandas as pd
    import numpy as np
except ImportError:
    sys.exit("需要 pandas/numpy：pip install pandas numpy")

NUMERIC = ["收入金额", "支出金额", "交易金额", "账户余额"]


def load_inputs(paths):
    files = []
    for p in paths:
        if os.path.isdir(p):
            files += sorted(glob.glob(os.path.join(p, "*__standardized.csv")))
        elif p.endswith(".csv"):
            files.append(p)
    frames = []
    for f in files:
        df = pd.read_csv(f, dtype=str)
        df["__源标准化文件"] = os.path.basename(f)
        frames.append(df)
    if not frames:
        sys.exit("未找到任何 *__standardized.csv 输入")
    df = pd.concat(frames, ignore_index=True)
    for c in NUMERIC:
        if c in df.columns:
            df[c + "_num"] = pd.to_numeric(df[c], errors="coerce")
    df["__t"] = pd.to_datetime(df["交易时间"], errors="coerce")
    df["来源行号_num"] = pd.to_numeric(df["来源行号"], errors="coerce")
    return df, files


def balance_check(df):
    """按本方账户分别做余额连续性校验。返回报告列表。"""
    results = []
    for acct, g in df.groupby(df["本方账户"].fillna("")):
        g = g.sort_values(["__t", "来源文件名", "来源行号_num"]).reset_index()
        bal = g["账户余额_num"]
        if bal.notna().sum() < 2:
            results.append({"本方账户": acct or "(空)", "校验状态": "未校验",
                            "异常数量": 0, "异常示例": [], "说明": "无足够余额数据"})
            continue
        net = g["收入金额_num"].fillna(0) - g["支出金额_num"].fillna(0)
        expected = bal.shift(1) + net
        diff = (bal - expected).abs()
        breaks = g[diff >= 0.01]
        examples = []
        for _, r in breaks.head(8).iterrows():
            examples.append({
                "交易唯一编号": r["交易唯一编号"], "交易时间": r["交易时间"],
                "收入金额": r.get("收入金额", ""), "支出金额": r.get("支出金额", ""),
                "账户余额": r.get("账户余额", ""), "来源文件名": r["来源文件名"],
                "来源行号": r["来源行号"],
            })
        nb = int((diff >= 0.01).sum())
        results.append({
            "本方账户": acct or "(空)",
            "校验状态": "通过" if nb == 0 else "预警",
            "异常数量": nb,
            "异常示例": examples,
            "说明": "余额连续" if nb == 0 else
                    "存在余额断点：常见于同秒多笔的排序歧义、对账单逆序、或解析缺行，需人工复核而非自动修正",
        })
    return results


def detect_duplicates(df):
    """疑似重复交易：同账户、同时间、同收入/支出、同余额、同对手，但来源文件不同。"""
    groups = []
    key_cols = ["本方账户", "交易时间", "收入金额", "支出金额", "账户余额", "对手名称"]
    for key, g in df.groupby([df[c].fillna("") for c in key_cols]):
        if len(g) < 2:
            continue
        srcs = g["来源文件名"].nunique()
        ids = g["交易唯一编号"].tolist()
        # 余额全相等才算高置信重复（仅时间金额相同也可能是真实多笔）
        conf = 0.9 if srcs > 1 and g["账户余额"].nunique() == 1 else 0.5
        groups.append({
            "组编号": f"DUP-{len(groups)+1:04d}",
            "交易唯一编号列表": ids,
            "涉及来源文件": sorted(g["来源文件名"].unique().tolist()),
            "置信度": conf,
            "判断原因": "同账户/时间/金额/余额/对手一致" +
                       ("，且跨多个来源文件（疑似同源重复导入）" if srcs > 1 else "，同一文件内重复"),
            "建议动作": "保留一笔" if conf >= 0.9 else "人工复核",
        })
    return groups


def detect_self_transfers(df, self_accounts):
    """自有账户互转候选：本方多个账户之间，A 支出 与 B 收入 金额相等、时间接近。仅标记。"""
    self_set = set(a for a in self_accounts if a)
    self_set |= set(df["本方账户"].dropna().unique().tolist())
    self_names = set(df["本方名称"].dropna().unique().tolist())

    out = df[df["支出金额_num"] > 0].copy()
    inn = df[df["收入金额_num"] > 0].copy()
    pairs = []
    used_in = set()
    for _, o in out.iterrows():
        amt = o["支出金额_num"]
        ot = o["__t"]
        # 对手是本方名称/本方账户，优先视为内部互转线索
        cand = inn[(inn["收入金额_num"].sub(amt).abs() < 0.01)]
        if pd.notna(ot):
            cand = cand[(cand["__t"] - ot).abs() <= timedelta(days=3)]
        for _, i in cand.iterrows():
            if i["交易唯一编号"] in used_in:
                continue
            if o["本方账户"] == i["本方账户"]:
                continue  # 同账户不是互转
            opp_is_self = (str(o["对手名称"]) in self_names or
                           str(o["对手账户"]) in self_set or
                           str(i["对手名称"]) in self_names or
                           str(i["对手账户"]) in self_set)
            conf = 0.85 if opp_is_self else 0.5
            pairs.append({
                "组编号": f"INT-{len(pairs)+1:04d}",
                "转出交易唯一编号": o["交易唯一编号"],
                "转入交易唯一编号": i["交易唯一编号"],
                "涉及账户": [o["本方账户"], i["本方账户"]],
                "金额": amt,
                "置信度": conf,
                "判断原因": ("对手为本方名称/账户且金额时间匹配" if opp_is_self
                            else "金额时间匹配但对手非本方，置信较低"),
            })
            used_in.add(i["交易唯一编号"])
            break
    return pairs


def account_index(df):
    idx = []
    for acct, g in df.groupby(df["本方账户"].fillna("")):
        t = g["__t"].dropna()
        idx.append({
            "本方账户": acct or "(空)",
            "本方名称": g["本方名称"].dropna().iloc[0] if g["本方名称"].notna().any() else "",
            "来源文件": sorted(g["来源文件名"].unique().tolist()),
            "交易数": int(len(g)),
            "交易期间": {
                "开始日期": t.min().strftime("%Y-%m-%d") if len(t) else "",
                "结束日期": t.max().strftime("%Y-%m-%d") if len(t) else "",
            },
        })
    return idx


def integrate(customer, paths, out_dir=None, self_accounts=None):
    df, files = load_inputs(paths)
    self_accounts = self_accounts or []

    df_sorted = df.sort_values(
        ["本方账户", "__t", "来源文件名", "来源行号_num"]).reset_index(drop=True)

    bal = balance_check(df)
    dups = detect_duplicates(df)
    selfs = detect_self_transfers(df, self_accounts)
    accts = account_index(df)

    t = df["__t"].dropna()
    quality_issues = []
    if df["对手名称"].fillna("").eq("").mean() > 0.3:
        quality_issues.append({
            "问题类型": "对手名称缺失率偏高", "影响范围": f"{df['对手名称'].fillna('').eq('').mean():.0%} 交易",
            "判断": "部分银行将对手信息写在备注中", "建议动作": "人工确认对手名称来源或增强解析"})
    bad_dir = df[(df["收入金额_num"] > 0) & (df["支出金额_num"] > 0)]
    if len(bad_dir):
        quality_issues.append({
            "问题类型": "收支方向冲突", "影响范围": f"{len(bad_dir)} 笔",
            "判断": "同笔交易收入与支出同时大于0", "建议动作": "人工复核金额方向"})

    review = []
    for b in bal:
        if b["校验状态"] == "预警":
            review.append({"事项类型": "余额断裂", "复核原因": b["说明"],
                           "证据交易唯一编号列表": [e["交易唯一编号"] for e in b["异常示例"]],
                           "建议动作": "人工核对该账户对账单顺序与缺行"})
    for d in dups:
        if d["建议动作"] == "人工复核":
            review.append({"事项类型": "疑似重复", "复核原因": d["判断原因"],
                           "证据交易唯一编号列表": d["交易唯一编号列表"], "建议动作": "人工确认是否重复"})

    blocking = []
    warnings = []
    if any(b["校验状态"] == "预警" for b in bal):
        warnings.append("存在余额断点，需人工复核")
    if dups:
        warnings.append(f"存在 {len(dups)} 组疑似重复交易")

    score = 100
    score -= min(30, sum(b["异常数量"] for b in bal) // 5)
    score -= min(20, len(dups))
    score -= 10 * len(quality_issues)
    score = max(0, score)

    report = {
        "客户整合概览": {
            "客户名称": customer,
            "整合文件数": len(files),
            "整合账户数": df["本方账户"].nunique(),
            "整合交易数": int(len(df)),
            "交易期间": {
                "开始日期": t.min().strftime("%Y-%m-%d") if len(t) else "",
                "结束日期": t.max().strftime("%Y-%m-%d") if len(t) else "",
            },
            "整体质量评分": score,
        },
        "账户索引": accts,
        "整合策略": {
            "排序规则": ["本方账户", "交易时间", "来源文件名", "来源行号"],
            "去重判断规则": ["本方账户", "交易时间", "收入金额", "支出金额", "账户余额", "对手名称"],
            "自有账户互转判断规则": "本方多账户间金额相等、时间≤3天、对手为本方名称/账户；仅标记不删除",
            "余额校验范围": "按本方账户分别校验",
        },
        "疑似重复交易组": dups,
        "自有账户互转组": selfs,
        "余额校验": bal,
        "数据质量问题": quality_issues,
        "人工复核事项": review,
        "最终判断": {
            "是否可进入标签分析": len(blocking) == 0,
            "阻断问题": blocking,
            "非阻断预警": warnings,
            "建议下一步": "进入阶段三：交易标签梳理（tag.py）",
        },
    }

    if out_dir is None:
        out_dir = os.path.dirname(files[0])
    os.makedirs(out_dir, exist_ok=True)
    out_csv = os.path.join(out_dir, f"{customer}__整合流水.csv")
    out_json = os.path.join(out_dir, f"{customer}__整合报告.json")
    keep = ["交易唯一编号", "交易时间", "本方名称", "本方账户", "对手名称", "对手账户",
            "收入金额", "支出金额", "交易金额", "账户余额", "银行备注", "账户方附言",
            "交易渠道", "来源文件名", "来源行号"]
    df_sorted[keep].to_csv(out_csv, index=False, encoding="utf-8-sig")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return out_csv, out_json, report


def main():
    ap = argparse.ArgumentParser(description="单客户多流水整合与验证")
    ap.add_argument("customer")
    ap.add_argument("inputs", nargs="+", help="standardized 目录 或 多个 *__standardized.csv")
    ap.add_argument("--out-dir")
    ap.add_argument("--self-accounts", nargs="*", default=[])
    args = ap.parse_args()
    out_csv, out_json, report = integrate(
        args.customer, args.inputs, out_dir=args.out_dir, self_accounts=args.self_accounts)
    o = report["客户整合概览"]
    print(f"[OK] {args.customer}: {o['整合文件数']} 文件 / {o['整合账户数']} 账户 / "
          f"{o['整合交易数']} 笔 / 质量评分 {o['整体质量评分']}")
    print(f"  期间 {o['交易期间']['开始日期']} ~ {o['交易期间']['结束日期']}")
    print(f"  疑似重复 {len(report['疑似重复交易组'])} 组 / 互转候选 {len(report['自有账户互转组'])} 组 / "
          f"人工复核 {len(report['人工复核事项'])} 项")
    print(f"  -> {out_csv}")
    print(f"  -> {out_json}")


if __name__ == "__main__":
    main()
