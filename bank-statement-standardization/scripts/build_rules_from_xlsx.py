#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_rules_from_xlsx.py — 从《流水标签规则文档》生成 assets/tag_rules.csv

权威来源（同一个 .xlsx 的三张表）：
  - 资金用途标签判定逻辑：每个三级标签的 一级/二级、收/支、依据字段、识别优先级、特殊判断条件；
  - 流水标签词库管理表：关键词 -> 收入标签 / 支出标签（同词分方向，约 3600 条）；
  - 资金用途标签体系：标签层级总表（仅用于校验）。

生成规则后，tag.py 按「识别优先级」从高到低单遍匹配（社保=1 最高 …），每条规则只在自己的
「依据字段」上判断，支持「排除关键词」「对手名称需包含」等条件，忠实还原文档判定逻辑。

少数无词库支撑的特殊标签（财政补贴、结息）与「销售收入·款规则」按文档条件内联补全。

用法：
  python build_rules_from_xlsx.py [--xlsx references/流水标签规则文档v20220517.xlsx] [--out assets/tag_rules.csv]
"""
import argparse, csv, os, sys

try:
    import pandas as pd
except ImportError:
    sys.exit("需要 pandas/openpyxl")

# 词库 FlowField -> 标准字段「依据字段」口径
FLOW2FIELD = {"摘要或备注": "摘要或备注", "对方户名": "对手名称", "摘要": "银行备注"}
INVALID = {"—", "-", "", "nan", "None"}

# 销售收入「款规则」排除清单（文档 规则2/3）
SALES_EXCLUDE = ("销户款|开户款|定期款|赔款|退款|借款|还款|贷款|按揭款|退余款|回款|罚款|违约款|"
                 "执行款|打款|划款|汇款|付款|借出款|拨款|转款|股东款|调解款|代付款|返款|扣款|股权款|"
                 "利息款|捐款|往来款|往款|住来款|存款|取款|收款|投资款|公司款|部分款|租赁款|款项|放款|"
                 "税款|保险款|提款|报销款|工资款|个人款|借支款|款已汇|款金多|汇缴款|融资款|保理款|薪酬款|汇入款|汇出款")
# 财政补贴（文档：对手名称含其一 且 备注含其一）
SUBSIDY_OPP = "国库|金库|财政|局|委员会|政府"
SUBSIDY_MEMO = "扶持|支持|奖|资金|资助|经费|激励|补助|补贴|高新技术|高新区|经发局|科技局|整治|整顿|整改|经济和信息化局|非税"
# 结息（文档简化：备注含其一，且不含「利息税」；归收入）
INTEREST_MEMO = "利息|结息|入息|存息|季息"


def prio_val(v):
    v = str(v).strip()
    if "最高" in v:
        return 1000
    try:
        return 1000 - int(float(v)) * 10
    except ValueError:
        return 500


def load_dim(xlsx):
    lg = pd.read_excel(xlsx, sheet_name="资金用途标签判定逻辑", dtype=str)
    lg.columns = ["L1", "L2", "L3", "EN", "普通", "规则集", "收支", "依据字段",
                  "判断条件", "收入优先级", "支出优先级"]
    lg["L1"] = lg["L1"].ffill()
    lg["L2"] = lg["L2"].ffill()
    lg = lg.dropna(subset=["L3"])
    dim = {}
    for _, r in lg.iterrows():
        dim[str(r["L3"]).strip()] = {
            "L1": str(r["L1"]).strip(), "L2": str(r["L2"]).strip(),
            "inc": prio_val(r["收入优先级"]), "exp": prio_val(r["支出优先级"]),
        }
    return dim


def build(xlsx, out):
    dim = load_dim(xlsx)
    w = pd.read_excel(xlsx, sheet_name="流水标签词库管理表", dtype=str)
    w.columns = ["FlowField", "Operator", "Keyword", "Tags_Income", "Tags_Expenditure", "ut"]

    rules = []   # 每条：[方向, 依据字段, 关键词, 排除关键词, 对手名称含, L1, L2, L3, 优先级, 备注]
    seen = set()

    def add(direction, field, kw, l3, exclude="", opp="", note="词库"):
        if not l3 or l3 in INVALID:
            return
        d = dim.get(l3)
        if not d:
            return
        prio = d["inc"] if direction == "收入" else d["exp"]
        key = (direction, field, kw, exclude, opp, l3)
        if key in seen:
            return
        seen.add(key)
        rules.append([direction, field, kw, exclude, opp, d["L1"], d["L2"], l3, prio, note])

    # 1) 词库批量规则（同词分方向）
    for _, r in w.iterrows():
        kw = str(r["Keyword"]).strip()
        if not kw or kw in INVALID:
            continue
        field = FLOW2FIELD.get(str(r["FlowField"]).strip(), "摘要或备注")
        add("收入", field, kw, str(r["Tags_Income"]).strip())
        add("支出", field, kw, str(r["Tags_Expenditure"]).strip())

    # 2) 内联特殊规则
    # 结息（收入，优先级最高）
    for kw in INTEREST_MEMO.split("|"):
        add("收入", "摘要或备注", kw, "结息", exclude="利息税|贷款", note="内联:结息")
    # 财政补贴（收入；对手名称含其一 且 备注含其一）
    for kw in SUBSIDY_MEMO.split("|"):
        add("收入", "摘要或备注", kw, "财政补贴", opp=SUBSIDY_OPP, note="内联:财政补贴")
    # 销售收入·款规则（收入；备注含「款」且不含排除清单）
    add("收入", "摘要或备注", "款", "销售收入", exclude=SALES_EXCLUDE, note="内联:销售收入款规则")

    # 3) 排序：优先级降序，便于阅读与匹配
    rules.sort(key=lambda x: (-x[8], x[5], x[7]))

    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        wr = csv.writer(f)
        wr.writerow(["规则编号", "适用方向", "依据字段", "匹配方式", "关键词", "排除关键词",
                     "对手名称含", "一级标签", "二级标签", "三级标签", "优先级", "备注"])
        for n, r in enumerate(rules, 1):
            direction, field, kw, exclude, opp, l1, l2, l3, prio, note = r
            wr.writerow([f"R{n:04d}", direction, field, "包含", kw, exclude, opp,
                         l1, l2, l3, prio, note])
    return len(rules)


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", default=os.path.join(here, "references", "流水标签规则文档v20220517.xlsx"))
    ap.add_argument("--out", default=os.path.join(here, "assets", "tag_rules.csv"))
    args = ap.parse_args()
    n = build(args.xlsx, args.out)
    print(f"[OK] 生成 {n} 条规则 -> {args.out}")


if __name__ == "__main__":
    main()
