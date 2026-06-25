#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
multi_customer.py — 多客户标准化流水整合与验证（阶段四，对应 Prompt 4）

把多个客户各自的整合/打标流水合并为批量分析底表，并做：
  - 客户隔离：以「客户编号+客户名称」为边界，绝不把不同客户合并成一个账户流水；
  - 共用账户检测：同一本方账户出现在多个客户下（高风险）；
  - 疑似重复提交：跨客户出现相同交易指纹；
  - 客户间交易：A 的对手是 B 的本方名称/账户（关联线索，不做抵消）；
  - 共同交易对手：被多个客户共享的对手。

所有异常保留客户/账户/文件/行号/交易编号证据，无法确认进人工复核。

用法：
  python multi_customer.py --batch 批次名 \
      --customer "客户名:整合或打标csv[:客户编号]" [--customer ...] \
      [--out-dir DIR]
  也可：--customer "客户名:目录"  自动取该目录下 *__整合流水.csv 或 *__打标流水.csv

输出：
  <批次名>__多客户底表.csv
  <批次名>__多客户报告.json   Prompt 4 结构
"""
import argparse, glob, json, os, sys
from collections import defaultdict

try:
    import pandas as pd
except ImportError:
    sys.exit("需要 pandas")


def resolve_csv(path):
    if os.path.isdir(path):
        for pat in ("*__打标流水.csv", "*__整合流水.csv", "*__standardized.csv"):
            hits = sorted(glob.glob(os.path.join(path, pat)))
            if hits:
                return hits[0]
        sys.exit(f"目录下无可用流水：{path}")
    return path


def load_customers(specs):
    frames = []
    meta = []
    for i, spec in enumerate(specs, 1):
        parts = spec.split(":")
        name = parts[0]
        path = parts[1]
        cid = parts[2] if len(parts) > 2 else f"C{i:03d}"
        csv = resolve_csv(path)
        df = pd.read_csv(csv, dtype=str)
        df.insert(0, "客户名称", name)
        df.insert(0, "客户编号", cid)
        frames.append(df)
        # 客户类型：取该客户各账户的 账户类型 去重；既有对公又有个人记为「混合」
        types = sorted(set(t for t in df.get("账户类型", pd.Series([], dtype=str))
                           .dropna().astype(str).str.strip()
                           if t and t not in ("nan", "None", "未知")))
        ctype = types[0] if len(types) == 1 else ("混合" if len(types) > 1 else "未知")
        meta.append({"客户编号": cid, "客户名称": name, "客户类型": ctype,
                     "来源文件": os.path.basename(csv),
                     "交易数": int(len(df)), "账户数": int(df["本方账户"].nunique())})
    return pd.concat(frames, ignore_index=True), meta


def balance_check_by_account(df):
    """整合后再次余额校验：按 (客户编号,本方账户) 分别校验余额连续性。
    不同账户/客户不可合并校验——技能红线。返回 list。"""
    out = []
    for kv, g in df.groupby(["客户编号", df["本方账户"].fillna("")]):
        cid, acct = kv
        g = g.sort_values("__seq")          # 保持整合后的原始时序，不按交易时间重排
        bal = g["账户余额_num"]
        if bal.notna().sum() < 2:
            out.append({"客户编号": cid, "本方账户": acct, "交易数": int(len(g)),
                        "校验状态": "未校验", "余额断点": 0, "断点交易示例": []})
            continue
        net = g["收入金额_num"].fillna(0) - g["支出金额_num"].fillna(0)
        diff = (bal - (bal.shift(1) + net)).abs()
        nb = int((diff >= 0.01).sum())
        out.append({"客户编号": cid, "本方账户": acct, "交易数": int(len(g)),
                    "校验状态": "通过" if nb == 0 else "预警", "余额断点": nb,
                    "断点交易示例": g.loc[diff >= 0.01, "交易唯一编号"].head(5).tolist()})
    return out


def multi(batch, specs, out_dir=None):
    df, meta = load_customers(specs)
    df["__t"] = pd.to_datetime(df["交易时间"], errors="coerce")
    df["来源行号_num"] = pd.to_numeric(df.get("来源行号"), errors="coerce")
    df["__seq"] = range(len(df))   # 保持各客户整合流水的原始时序，余额校验据此排序（不按时间重排）
    for c in ["收入金额", "支出金额", "账户余额"]:
        df[c + "_num"] = pd.to_numeric(df.get(c), errors="coerce")

    names = {m["客户编号"]: m["客户名称"] for m in meta}
    name_to_cid = {m["客户名称"]: m["客户编号"] for m in meta}

    # 整合后余额校验（按客户+账户分别），并回填到每个客户
    bal_checks = balance_check_by_account(df)
    _bbc = {}
    for b in bal_checks:
        _bbc.setdefault(b["客户编号"], []).append(b)
    for m in meta:
        cb = _bbc.get(m["客户编号"], [])
        m["余额校验"] = {"账户数": len(cb),
                       "通过账户": sum(1 for x in cb if x["校验状态"] == "通过"),
                       "预警账户": sum(1 for x in cb if x["校验状态"] == "预警"),
                       "余额断点合计": sum(x["余额断点"] for x in cb)}

    # 共用账户：同一本方账户跨多个客户
    shared = []
    for acct, g in df.groupby(df["本方账户"].fillna("")):
        if acct == "":
            continue
        cids = sorted(g["客户编号"].unique().tolist())
        if len(cids) > 1:
            shared.append({
                "本方账户": acct, "客户编号列表": cids,
                "客户名称列表": [names[c] for c in cids],
                "严重程度": "高", "判断原因": "同一账户归属多个客户，存在主体混用风险",
                "证据交易唯一编号列表": g["交易唯一编号"].head(10).tolist(),
            })

    # 疑似重复提交：跨客户的相同指纹
    dup = []
    fp = ["交易时间", "收入金额", "支出金额", "账户余额", "对手名称"]
    for key, g in df.groupby([df[c].fillna("") for c in fp]):
        cids = g["客户编号"].nunique()
        if cids > 1 and key[0] != "":
            dup.append({
                "组编号": f"XDUP-{len(dup)+1:04d}",
                "客户编号列表": sorted(g["客户编号"].unique().tolist()),
                "交易唯一编号列表": g["交易唯一编号"].tolist(),
                "置信度": 0.6,
                "判断原因": "跨客户出现相同时间/金额/余额/对手的交易指纹",
                "建议动作": "人工复核",
            })

    # 客户间交易：A 的对手名称/账户 命中 B 的本方名称/账户
    self_names = {}
    self_accts = {}
    for cid, g in df.groupby("客户编号"):
        self_names[cid] = set(g["本方名称"].dropna().unique())
        self_accts[cid] = set(g["本方账户"].dropna().unique())
    inter = []
    for _, r in df.iterrows():
        opp_n, opp_a = str(r.get("对手名称", "")), str(r.get("对手账户", ""))
        for cid in names:
            if cid == r["客户编号"]:
                continue
            if opp_n in self_names[cid] or (opp_a and opp_a in self_accts[cid]):
                inter.append({
                    "源客户编号": r["客户编号"], "对手客户编号": cid,
                    "交易唯一编号": r["交易唯一编号"], "交易时间": r["交易时间"],
                    "对手名称": opp_n, "金额": r.get("收入金额") or r.get("支出金额"),
                    "判断原因": "对手为另一客户的本方名称/账户（关联线索，不做抵消）",
                })
                break
    # 限制规模
    inter = inter[:200]

    # 共同交易对手
    common = []
    opp_group = df[df["对手名称"].fillna("") != ""].groupby("对手名称")
    for opp, g in opp_group:
        cids = g["客户编号"].nunique()
        if cids > 1:
            common.append({
                "对手名称": opp, "涉及客户编号列表": sorted(g["客户编号"].unique().tolist()),
                "交易数量": int(len(g)),
                "收入合计": float(g["收入金额_num"].fillna(0).sum()),
                "支出合计": float(g["支出金额_num"].fillna(0).sum()),
                "解释": "需要复核" if cids >= 3 else "常见交易对手",
            })
    common = sorted(common, key=lambda x: -x["交易数量"])[:30]

    cov = lambda c: round(df[c].fillna("").astype(str).str.strip().replace("nan", "").ne("").mean(), 3) if c in df else 0
    t = df["__t"].dropna()

    review = []
    for s in shared:
        review.append({"事项编号": f"RV-{len(review)+1:03d}", "事项类型": "共用账户", "优先级": "高",
                       "复核原因": s["判断原因"], "客户编号列表": s["客户编号列表"],
                       "证据交易唯一编号列表": s["证据交易唯一编号列表"], "建议动作": "确认账户主体归属"})
    if dup:
        review.append({"事项编号": f"RV-{len(review)+1:03d}", "事项类型": "重复提交", "优先级": "中",
                       "复核原因": f"{len(dup)} 组跨客户重复指纹", "客户编号列表": [],
                       "证据交易唯一编号列表": dup[0]["交易唯一编号列表"], "建议动作": "人工复核是否重复提交"})

    bal_warn = [b for b in bal_checks if b["校验状态"] == "预警"]
    for b in bal_warn[:50]:
        review.append({"事项编号": f"RV-{len(review)+1:03d}", "事项类型": "余额断裂", "优先级": "中",
                       "复核原因": f"客户{b['客户编号']} 账户{b['本方账户']} 存在 {b['余额断点']} 处余额断点",
                       "客户编号列表": [b["客户编号"]], "证据交易唯一编号列表": b["断点交易示例"],
                       "建议动作": "按账户核对对账单顺序与缺行（不自动修正）"})

    blocking = ["存在跨客户共用账户" for _ in shared[:1]]

    report = {
        "批次概览": {
            "批次名称": batch, "客户数量": len(meta),
            "交易数量": int(len(df)), "账户数量": int(df["本方账户"].nunique()),
            "交易期间": {"开始日期": t.min().strftime("%Y-%m-%d") if len(t) else "",
                       "结束日期": t.max().strftime("%Y-%m-%d") if len(t) else ""},
            "整体质量评分": max(0, 100 - 20 * len(shared) - 5 * (1 if dup else 0)),
        },
        "客户校验结果": meta,
        "跨客户校验": {
            "共用账户": shared,
            "疑似重复提交": dup[:50],
            "客户间交易": inter,
            "共同交易对手": common,
        },
        "整合后余额校验": {
            "校验口径": "按 客户编号+本方账户 分别做余额连续性校验；不同账户/客户不合并校验",
            "账户总数": len(bal_checks),
            "通过账户数": sum(1 for b in bal_checks if b["校验状态"] == "通过"),
            "预警账户数": len(bal_warn),
            "余额断点合计": sum(b["余额断点"] for b in bal_checks),
            "账户明细": bal_checks,
        },
        "标准化数据集画像": {
            "必要字段覆盖率": {
                "客户编号": cov("客户编号"), "交易时间": cov("交易时间"),
                "本方账户": cov("本方账户"), "对手名称": cov("对手名称"),
                "账户余额": cov("账户余额"), "来源追溯": cov("来源文件名"),
            },
            "去重策略": "客户内按账户/时间/金额/余额/对手去重；跨客户仅标记不抵消",
            "客户隔离策略": "以 客户编号+客户名称 为边界，不混用账户",
            "已知局限": ["对手名称依赖原始数据质量", "PDF 解析可能存在余额断点"],
        },
        "人工复核事项": review,
        "最终判断": {
            "是否可用于批量分析": len(shared) == 0,
            "阻断问题": blocking,
            "非阻断预警": ([f"{len(dup)} 组疑似跨客户重复"] if dup else []) +
                        ([f"{len(inter)} 笔疑似客户间交易"] if inter else []) +
                        ([f"{len(bal_warn)} 个账户余额校验预警"] if bal_warn else []),
            "建议下一步": "进入组合分析/风险筛查/模型特征加工",
        },
    }

    if out_dir is None:
        out_dir = os.getcwd()
    os.makedirs(out_dir, exist_ok=True)
    out_csv = os.path.join(out_dir, f"{batch}__多客户底表.csv")
    out_json = os.path.join(out_dir, f"{batch}__多客户报告.json")
    drop = [c for c in df.columns if c.endswith("_num") or c.startswith("__")]
    df.drop(columns=drop).to_csv(out_csv, index=False, encoding="utf-8-sig")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return out_csv, out_json, report


def main():
    ap = argparse.ArgumentParser(description="多客户标准化流水整合与验证")
    ap.add_argument("--batch", required=True)
    ap.add_argument("--customer", action="append", required=True,
                    help='格式 "客户名:csv或目录[:客户编号]"，可重复')
    ap.add_argument("--out-dir")
    args = ap.parse_args()
    out_csv, out_json, report = multi(args.batch, args.customer, out_dir=args.out_dir)
    o = report["批次概览"]
    x = report["跨客户校验"]
    print(f"[OK] 批次 {args.batch}: {o['客户数量']} 客户 / {o['交易数量']} 笔 / 质量评分 {o['整体质量评分']}")
    print(f"  共用账户 {len(x['共用账户'])} / 跨客户重复 {len(x['疑似重复提交'])} / "
          f"客户间交易 {len(x['客户间交易'])} / 共同对手 {len(x['共同交易对手'])}")
    b = report["整合后余额校验"]
    print(f"  整合后余额校验：{b['通过账户数']}/{b['账户总数']} 账户通过，"
          f"预警 {b['预警账户数']}，断点合计 {b['余额断点合计']}")
    print(f"  -> {out_csv}")
    print(f"  -> {out_json}")


if __name__ == "__main__":
    main()
