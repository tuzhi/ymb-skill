#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
portfolio_balance.py — 组合（虚拟账户）余额时间序列 + 余额校验

为什么需要它：整合流水里的「账户余额」是**每个本方账户各自的交易后余额**，
不同账户的余额不能直接当成一个连续序列。要得到「可视为一个虚拟唯一账户」的时点总余额，必须：
对每个账户按时间取每日「日末余额」，**前向填充**到后续日期，再在每个时点把各账户余额**求和**。

产出：
  1. 每日组合总余额曲线（各账户日末余额前向填充后求和 = 虚拟账户余额）；
  2. 余额校验：对每个账户单独做余额连续性校验（账户余额 == 上一笔余额 + 收入 − 支出），
     并校验组合层面「相邻日总余额变动 == 当日净流入」（用于发现账户间缺口/解析断点）。
  输入含「客户编号」时按客户分别给出虚拟余额，并给出批次合计。

约定：账户首笔交易前余额贡献按 0 计；余额断点只报告、不自动修正（与技能红线一致）。

用法：
  python portfolio_balance.py <整合流水csv> [--out-dir DIR]
输出：
  <stem>__组合日余额.csv     日期 × 各账户日末余额 + 合计余额（虚拟账户余额）
  <stem>__余额校验.json      每账户余额连续性 + 组合连续性 + 期初/期末/峰谷 + 各客户组合余额
"""
import argparse, json, os, sys

try:
    import pandas as pd
except ImportError:
    sys.exit("需要 pandas")

from ymb_standardization_core import core as S  # 统一复用 shared core 的批次级余额校验


def _prep(df):
    """在主明细上增加本阶段临时列；调用方负责计算后删除。"""
    # 整合流水/底表的行序即正确时序（整合阶段已按文件时序+文件内原始行序排好、倒序文件已翻正）。
    # 余额校验据此 __seq 排序，不再按交易时间重排，避免同秒多笔被打乱产生伪断点。
    df["__seq"] = range(len(df))
    times = df["交易时间"]
    if not pd.api.types.is_datetime64_any_dtype(times.dtype):
        raise TypeError("Stage 2 输出的交易时间必须是 datetime64")
    df["__t"] = times
    source_rows = df.get("来源行号")
    # 来源行号是追溯标识，允许 ``Sheet名!行号``；余额计算只保留可转换的
    # 临时数值，不改变公开字段。
    df["__row"] = (
        pd.to_numeric(source_rows, errors="coerce")
        if source_rows is not None
        else pd.Series(index=df.index, dtype=float)
    )
    for c in ["收入金额", "支出金额", "账户余额"]:
        values = df.get(c)
        if values is None or not pd.api.types.is_numeric_dtype(values.dtype):
            raise TypeError(f"Stage 2 输出的{c}必须是数值类型")
        df["__" + c] = values
    return df


def _acct_key(df):
    """账户唯一键：多客户时带客户编号前缀，避免不同客户同号账户混淆。"""
    if "客户编号" in df.columns:
        return df["客户编号"].fillna("").astype(str) + "|" + df["本方账户"].fillna("").astype(str)
    return df["本方账户"].fillna("").astype(str)


def balance_check_per_account(df):
    """按账户单独做余额连续性校验。返回 list。"""
    df = df.assign(__k=_acct_key(df))
    out = []
    for k, g in df.groupby("__k"):
        g = g.sort_values("__seq").reset_index(drop=True)  # 保持整合后的原始时序
        bal = g["__账户余额"]
        if bal.notna().sum() < 2:
            out.append({"账户": k, "交易数": int(len(g)), "校验状态": "未校验",
                        "余额断点": 0, "断点交易示例": [], "期末余额": None})
            continue
        # 批量、代发、工资等共享余额批次由 shared core 统一识别，本层不重复业务规则。
        rows = S.continuity_rows(g.to_dict("records"))
        break_indices = S.balance_break_indices(rows)
        nb = len(break_indices)
        rate = nb / max(1, S.continuity_unit_count(rows) - 1)
        out.append({"账户": k, "交易数": int(len(g)),
                    "校验状态": "通过" if nb == 0 else "预警", "余额断点": nb,
                    "断点率": round(rate, 3),
                    # 断点率畸高几乎必然是解析不全/漏行（大表被截断/抽样），而非真实排序问题
                    "疑似解析不全": bool(nb and rate > 0.3),
                    "断点交易示例": g.iloc[break_indices]["交易唯一编号"].head(5).tolist(),
                    "期末余额": float(bal.dropna().iloc[-1])})
    return out


def daily_portfolio(df):
    """每日组合总余额。返回 (daily_df[日期,各账户...,合计余额], 账户列表)。"""
    df = df.assign(__k=_acct_key(df)).dropna(subset=["__t"]).copy()  # 日聚合需要有效时间
    df["__day"] = df["__t"].dt.normalize()
    # 每账户每日日末余额（当天最后一笔 = 整合时序里当天的最后一笔）
    eod = (df.sort_values(["__k", "__seq"])
             .groupby(["__k", "__day"])["__账户余额"].last().reset_index())
    eod = eod.dropna(subset=["__账户余额"])
    if eod.empty:
        return pd.DataFrame(), []
    accounts = sorted(eod["__k"].unique().tolist())
    grid = pd.date_range(eod["__day"].min(), eod["__day"].max(), freq="D")
    wide = (eod.pivot(index="__day", columns="__k", values="__账户余额")
               .reindex(grid).ffill().fillna(0.0))
    wide["合计余额"] = wide[accounts].sum(axis=1)
    wide.index.name = "日期"
    return wide.reset_index(), accounts


def portfolio_continuity(df, daily):
    """组合连续性：相邻日总余额变动 ≈ 当日净流入(收入-支出)。返回异常日数。"""
    if daily.empty:
        return 0
    tmp = df.dropna(subset=["__t"]).assign(__day=lambda d: d["__t"].dt.normalize())
    net_by_day = (tmp["__收入金额"].fillna(0) - tmp["__支出金额"].fillna(0)).groupby(tmp["__day"]).sum()
    s = daily.set_index("日期")["合计余额"]
    chg = s.diff()
    breaks = 0
    for day in net_by_day.index:
        if day in chg.index and pd.notna(chg[day]):
            if abs(chg[day] - net_by_day[day]) >= 0.01:
                breaks += 1
    return breaks


def analyze(raw):
    """从内存流水计算组合日余额与余额校验报告。"""
    df = _prep(raw)
    try:
        has_cust = "客户编号" in df.columns

        acct_checks = balance_check_per_account(df)
        daily, accounts = daily_portfolio(df)
        pbreaks = portfolio_continuity(df, daily)

        per_customer = []
        if has_cust:
            for cid, g in df.groupby("客户编号"):
                cd, accs = daily_portfolio(g)
                if cd.empty:
                    continue
                per_customer.append({
                    "客户编号": cid,
                    "客户名称": (g["客户名称"].dropna().iloc[0]
                              if "客户名称" in g and g["客户名称"].notna().any() else ""),
                    "账户数": len(accs),
                    "期末组合余额": round(float(cd["合计余额"].iloc[-1]), 2),
                    "峰值组合余额": round(float(cd["合计余额"].max()), 2),
                    "谷值组合余额": round(float(cd["合计余额"].min()), 2),
                })

        warn = [b for b in acct_checks if b["校验状态"] == "预警"]
        summary = {
            "数据范围": {"开始日期": df["__t"].min().strftime("%Y-%m-%d"),
                       "结束日期": df["__t"].max().strftime("%Y-%m-%d"),
                       "账户数": len(accounts), "客户数": int(df["客户编号"].nunique()) if has_cust else 1},
            "组合虚拟账户": {
                "口径": "各账户日末余额前向填充后逐日求和；账户首笔前贡献按0。可用于时点资金占用等下游分析。",
                "期初合计余额": round(float(daily["合计余额"].iloc[0]), 2) if not daily.empty else None,
                "期末合计余额": round(float(daily["合计余额"].iloc[-1]), 2) if not daily.empty else None,
                "峰值合计余额": round(float(daily["合计余额"].max()), 2) if not daily.empty else None,
                "谷值合计余额": round(float(daily["合计余额"].min()), 2) if not daily.empty else None,
            },
            "账户余额校验": {
                "校验口径": "按账户分别校验：账户余额 == 上一笔余额 + 收入 − 支出",
                "账户总数": len(acct_checks),
                "通过账户数": sum(1 for b in acct_checks if b["校验状态"] == "通过"),
                "预警账户数": len(warn),
                "余额断点合计": sum(b["余额断点"] for b in acct_checks),
                "账户明细": acct_checks,
            },
            "组合连续性校验": {
                "校验口径": "相邻日总余额变动 == 当日净流入(收入-支出)",
                "异常日数": pbreaks,
                "结论": "通过" if pbreaks == 0 else "预警：存在账户间余额缺口/解析断点，需人工复核",
            },
            "各客户组合余额": per_customer,
            "人工复核提示": warn,
        }

        return daily, summary
    finally:
        raw.drop(
            columns=["__seq", "__t", "__row", "__收入金额", "__支出金额", "__账户余额"],
            errors="ignore",
            inplace=True,
        )


def run(csv_path, out_dir=None):
    """兼容命令行/历史调用方的文件输入输出包装。"""
    raw = pd.read_csv(csv_path, dtype=str)
    daily, summary = analyze(raw)
    if out_dir is None:
        out_dir = os.path.dirname(csv_path)
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(csv_path))[0]
    daily_csv = os.path.join(out_dir, f"{stem}__组合日余额.csv")
    sum_json = os.path.join(out_dir, f"{stem}__余额校验.json")
    if not daily.empty:
        outd = daily.copy()
        outd["日期"] = pd.to_datetime(outd["日期"]).dt.strftime("%Y-%m-%d")
        cols = ["日期"] + [c for c in outd.columns if c not in ("日期", "合计余额")] + ["合计余额"]
        outd[cols].round(2).to_csv(daily_csv, index=False, encoding="utf-8-sig")
    with open(sum_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return daily_csv, sum_json, summary


def main():
    ap = argparse.ArgumentParser(description="组合（虚拟账户）余额时间序列与余额校验")
    ap.add_argument("csv")
    ap.add_argument("--out-dir")
    args = ap.parse_args()
    daily_csv, sum_json, s = run(args.csv, out_dir=args.out_dir)
    v, bc = s["组合虚拟账户"], s["账户余额校验"]
    print(f"[OK] 组合虚拟账户余额：{s['数据范围']['开始日期']}~{s['数据范围']['结束日期']}，{s['数据范围']['账户数']} 账户")
    print(f"  期初合计 {v['期初合计余额']} -> 期末合计 {v['期末合计余额']}；峰值 {v['峰值合计余额']} 谷值 {v['谷值合计余额']}")
    print(f"  账户余额校验：{bc['通过账户数']}/{bc['账户总数']} 通过，预警 {bc['预警账户数']}，断点合计 {bc['余额断点合计']}")
    print(f"  组合连续性：{s['组合连续性校验']['结论']}")
    print(f"  -> {daily_csv}")
    print(f"  -> {sum_json}")


if __name__ == "__main__":
    main()
