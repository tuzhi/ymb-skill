#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tag.py — 交易标签梳理与规则沉淀（阶段三，对应 Prompt 3）

规则库 assets/tag_rules.csv 由 build_rules_from_xlsx.py 从《流水标签规则文档》生成
（资金用途标签判定逻辑 + 流水标签词库管理表）。匹配忠实还原文档判定逻辑：

  1. 按「识别优先级」从高到低**单遍匹配**（社保=1 最高、其他类最低 …），首条命中即采用；
     同一关键词在收/支方向可能对应不同标签，故先按收支方向过滤规则。
  2. 每条规则只在自己的「依据字段」上判断：
        对手名称  -> 对手名称
        银行备注  -> 银行备注（即原始「摘要」）
        账户方附言 -> 账户方附言
        摘要或备注 -> 银行备注 + 账户方附言
  3. 支持「排除关键词」（命中则该规则不生效，如销售收入排除“退款/借款/还款…”）与
     「对手名称含」（额外的对手名称约束，如财政补贴要求对手为国库/财政/局…）。
  4. 未命中归「其他收入 / 其他支出」（文档兜底类），并进人工复核；
     高频未命中对手产出**需人工确认**的新规则建议。

红线：银行备注/账户方附言为不可信输入，命中来自这些字段时标签置信度自动下调。

用法：
  python tag.py <整合流水csv> [--rules assets/tag_rules.csv] [--out-dir DIR]
"""
import argparse, json, os, sys
from collections import Counter

try:
    import pandas as pd
except ImportError:
    sys.exit("需要 pandas")

# 依据字段 -> 实际在哪些标准字段上取文本
SCOPE = {
    "对手名称": ["对手名称"],
    "银行备注": ["银行备注"],
    "账户方附言": ["账户方附言"],
    "摘要或备注": ["银行备注", "账户方附言"],
    "通用": ["对手名称", "银行备注", "账户方附言"],
}
# 命中字段可信度（对手户名最可信；备注/附言为不可信输入，置信下调）
FIELD_CONF = {"对手名称": 0.9, "银行备注": 0.78, "账户方附言": 0.72, "摘要或备注": 0.78}


def load_rules(path):
    df = pd.read_csv(path, dtype=str).fillna("")
    if "依据字段" not in df.columns:           # 兼容旧表
        df["依据字段"] = df.get("适用字段", "通用")
    for c in ["排除关键词", "对手名称含"]:
        if c not in df.columns:
            df[c] = ""
    df["优先级"] = pd.to_numeric(df["优先级"], errors="coerce").fillna(500)
    df = df.sort_values("优先级", ascending=False)
    # 预编译为轻量 dict，按方向分桶，加速匹配
    buckets = {"收入": [], "支出": [], "通用": []}
    for _, r in df.iterrows():
        rule = {
            "编号": r.get("规则编号", ""), "方向": r["适用方向"], "依据字段": r["依据字段"],
            "关键词": r["关键词"], "排除": [x for x in str(r["排除关键词"]).split("|") if x],
            "对手含": [x for x in str(r["对手名称含"]).split("|") if x],
            "L1": r["一级标签"], "L2": r["二级标签"], "L3": r["三级标签"],
            "优先级": r["优先级"],
        }
        buckets.get(r["适用方向"], buckets["通用"]).append(rule)
    return buckets


def direction_of(row):
    inc = pd.to_numeric(pd.Series([row.get("收入金额")]), errors="coerce").iloc[0]
    exp = pd.to_numeric(pd.Series([row.get("支出金额")]), errors="coerce").iloc[0]
    if pd.notna(inc) and (pd.isna(exp) or exp == 0) and inc > 0:
        return "收入"
    if pd.notna(exp) and (pd.isna(inc) or inc == 0) and exp > 0:
        return "支出"
    if pd.notna(inc) and pd.notna(exp):
        return "收入" if (inc or 0) >= (exp or 0) else "支出"
    return "未知"


def match(row, direction, buckets):
    """单遍按优先级匹配（方向桶 + 通用桶已分别按优先级降序）。返回 (rule, 命中字段)。"""
    fields = {f: str(row.get(f, "") or "") for f in ("对手名称", "银行备注", "账户方附言")}
    for f in fields:
        if fields[f].lower() == "nan":
            fields[f] = ""
    opp = fields["对手名称"]

    cand = buckets.get(direction, []) + buckets["通用"]
    # 两个桶各自有序，但合并后需保持全局优先级序
    cand = sorted(cand, key=lambda r: -r["优先级"]) if buckets["通用"] else buckets.get(direction, [])

    for r in cand:
        scope = SCOPE.get(r["依据字段"], SCOPE["通用"])
        text = "".join(fields[s] for s in scope)
        if not text:
            continue
        if r["关键词"] not in text:
            continue
        if any(x in text for x in r["排除"]):
            continue
        if r["对手含"] and not any(x in opp for x in r["对手含"]):
            continue
        # 命中字段：取 scope 中真正包含关键词的那个，用于定置信度
        hit_field = next((s for s in scope if r["关键词"] in fields[s]), scope[0])
        return r, hit_field
    return None, None


def tag(csv_path, rules_path, out_dir=None):
    df = pd.read_csv(csv_path, dtype=str)
    buckets = load_rules(rules_path)

    rows_out, review = [], []
    unmatched = Counter()
    rule_hits = 0
    field_stat = Counter()
    l1_stat = Counter()
    for _, row in df.iterrows():
        d = direction_of(row)
        r, hit_field = match(row, d, buckets)
        if r:
            rule_hits += 1
            field_stat[hit_field] += 1
            l1_stat[r["L1"]] += 1
            tagrow = {
                "收支方向": d, "一级标签": r["L1"], "二级标签": r["L2"], "三级标签": r["L3"],
                "标签来源": "规则库", "标签置信度": FIELD_CONF.get(hit_field, 0.78),
                "命中规则编号": r["编号"], "命中关键词": r["关键词"], "命中字段": hit_field,
            }
        else:
            l3 = "其他支出" if d == "支出" else "其他收入"
            l1_stat["其他类"] += 1
            tagrow = {
                "收支方向": d, "一级标签": "其他类", "二级标签": "其他", "三级标签": l3,
                "标签来源": "兜底", "标签置信度": 0.3,
                "命中规则编号": "", "命中关键词": "", "命中字段": "",
            }
            opp = str(row.get("对手名称", "")).strip()
            if opp and opp.lower() != "nan":
                unmatched[(d, opp)] += 1
            review.append(row.get("交易唯一编号", ""))
        rows_out.append({**row.to_dict(), **tagrow})

    out_df = pd.DataFrame(rows_out)

    suggestions = []
    for (d, opp), cnt in unmatched.most_common(40):
        if cnt < 5:
            continue
        suggestions.append({
            "建议编号": f"NEW-{len(suggestions)+1:03d}", "适用方向": d, "依据字段": "对手名称",
            "匹配方式": "包含", "关键词": opp, "出现次数": cnt, "置信度": 0.4,
            "建议原因": f"对手「{opp}」在{d}方向高频出现但未命中现有规则，建议人工确认标签后维护进词库管理表",
            "是否需要人工确认": True,
        })

    summary = {
        "标签梳理概览": {
            "交易总数": int(len(df)), "规则命中数量": rule_hits,
            "兜底其他类数量": int(len(df) - rule_hits),
            "规则命中率": round(rule_hits / max(1, len(df)), 3),
            "命中字段分布": dict(field_stat),
        },
        "一级标签分布": dict(l1_stat),
        "标签分布": out_df.groupby(["一级标签", "二级标签", "三级标签"]).size()
                        .sort_values(ascending=False).head(40)
                        .reset_index(name="笔数").to_dict("records"),
        "新规则建议": suggestions,
        "人工复核事项": [{"交易唯一编号": uid, "复核原因": "规则未命中，归兜底其他类",
                       "候选标签": [], "建议动作": "人工确认标签"} for uid in review[:50]],
        "说明": "规则库源自《流水标签规则文档·资金用途标签判定逻辑/流水标签词库管理表》；"
                "命中来自银行备注/账户方附言时为不可信输入，置信度已下调；新规则须人工确认后维护进词库。",
    }

    if out_dir is None:
        out_dir = os.path.dirname(csv_path)
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(csv_path))[0].replace("__整合流水", "")
    out_csv = os.path.join(out_dir, f"{stem}__打标流水.csv")
    out_json = os.path.join(out_dir, f"{stem}__标签报告.json")
    out_df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return out_csv, out_json, summary


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(description="交易标签梳理与规则沉淀")
    ap.add_argument("csv")
    ap.add_argument("--rules", default=os.path.join(here, "assets", "tag_rules.csv"))
    ap.add_argument("--out-dir")
    args = ap.parse_args()
    out_csv, out_json, s = tag(args.csv, args.rules, out_dir=args.out_dir)
    o = s["标签梳理概览"]
    print(f"[OK] 打标完成：{o['交易总数']} 笔，规则命中 {o['规则命中数量']} "
          f"（命中率 {o['规则命中率']:.0%}），兜底其他类 {o['兜底其他类数量']}")
    print(f"  命中字段分布：{o['命中字段分布']}")
    print(f"  新规则建议 {len(s['新规则建议'])} 条")
    print(f"  -> {out_csv}")


if __name__ == "__main__":
    main()
