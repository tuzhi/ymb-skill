#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
package_deliverable.py — 生成「<客户名>_已清洗_待分析.xlsx」单文件交付物

把一个授信客户名下「多主体（企业/个人）× 多银行账户 × 多份机械拆分的同构文件」
整合成**一份** Excel，供信贷员与审批官单文件流转、信贷分析。该文件确保：
  1. 已标准化：各银行各格式统一为中文标准字段（standardize.py）；
  2. 多账户多主体已整合为一：合并、按账户去重/余额校验、自有账户互转标记（integrate.py）；
     并嵌入「虚拟账户余额」——逐笔时点的组合总余额（virtual balance records，portfolio_balance.py 口径）；
  3. 交易类型已打标：资金用途三级标签（tag.py）。

输入两种方式（二选一）：
  A. 单文件夹：--folder 客户文件夹    （夹内所有原始文件视为同一客户，本方名称/账户自动嗅探，
                                       拆分文件按账户自动归并）
  B. 多主体：--subject "主体名:文件夹或文件[:对公|个人]"  可重复（一个主体名下可多账户多文件）

用法：
  python package_deliverable.py --client 客户名 --folder 客户文件夹 [--out-dir DIR]
  python package_deliverable.py --client 客户名 \
      --subject "甲公司:/path/甲" --subject "张三:/path/张三:个人" [--out-dir DIR]

输出：
  <out-dir>/<客户名>_已清洗_待分析.xlsx
  （中间标准化产物落在 <out-dir>/_工作区/，可留存追溯）
"""
import argparse, glob, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
import standardize as S
import integrate as I
import tag as T
import portfolio_balance as PB

RAW_EXT = (".xlsx", ".xls", ".csv", ".pdf", ".txt", ".tsv")

STD_ORDER = ["交易唯一编号", "客户名称", "主体名称", "本方名称", "本方账户", "开户行",
             "交易时间", "对手名称", "对手账户", "收入金额", "支出金额", "交易金额",
             "账户余额", "虚拟账户余额", "收支方向", "一级标签", "二级标签", "三级标签",
             "标签来源", "标签置信度", "命中关键词", "银行备注", "账户方附言", "交易渠道",
             "来源文件名", "来源行号"]


def gather_subjects(args):
    """返回 [(主体名, [(文件, 类型), ...]), ...]。"""
    subjects = []
    if args.subject:
        for spec in args.subject:
            parts = spec.split(":")
            name = parts[0]
            path = parts[1]
            ctype = parts[2] if len(parts) > 2 else None
            files = []
            if os.path.isdir(path):
                files = [f for f in sorted(glob.glob(os.path.join(path, "*")))
                         if os.path.splitext(f)[1].lower() in RAW_EXT and os.path.isfile(f)]
            elif os.path.isfile(path):
                files = [path]
            subjects.append((name, [(f, ctype) for f in files]))
    else:
        folder = args.folder
        files = [f for f in sorted(glob.glob(os.path.join(folder, "*")))
                 if os.path.splitext(f)[1].lower() in RAW_EXT and os.path.isfile(f)]
        # 单文件夹：主体名暂用客户名（本方名称由 standardize 自动嗅探/按行填充区分各主体）
        subjects.append((args.client, [(f, args.account_type) for f in files]))
    return subjects


def add_virtual_balance(df):
    """逐笔时点虚拟账户余额 = 各账户最近一次已知余额之和（按全局时间顺序滚动）。
    账户首笔之前贡献按 0。返回带「虚拟账户余额」列的 df（保持原行序）。"""
    d = df.copy()
    d["__t"] = pd.to_datetime(d["交易时间"], errors="coerce")
    d["__row"] = pd.to_numeric(d.get("来源行号"), errors="coerce")
    d["__bal"] = pd.to_numeric(d.get("账户余额"), errors="coerce")
    d["__order"] = range(len(d))
    order = d.sort_values(["__t", "本方账户", "__row", "__order"]).index
    last = {}
    vbal = {}
    for idx in order:
        acct = d.at[idx, "本方账户"]
        b = d.at[idx, "__bal"]
        if pd.notna(b):
            last[acct] = b
        vbal[idx] = round(sum(v for v in last.values() if pd.notna(v)), 2)
    d["虚拟账户余额"] = d.index.map(vbal)
    return d.drop(columns=["__t", "__row", "__bal", "__order"])


def build_workbook(client, tagged, daily, irep, srep, pbrep, subjects, out_path):
    """组装单文件 xlsx（多 sheet）。"""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    period = irep["客户整合概览"]["交易期间"]
    inc = pd.to_numeric(tagged.get("收入金额"), errors="coerce").fillna(0)
    exp = pd.to_numeric(tagged.get("支出金额"), errors="coerce").fillna(0)

    # ---- 封面 ----
    cover = [
        ["银行流水 · 已清洗待分析交付物", ""],
        ["授信客户", client],
        ["生成口径", "已标准化 + 多账户多主体整合为一 + 含虚拟账户余额 + 交易类型已打标"],
        ["", ""],
        ["整合主体数", len({s[0] for s in subjects}) if subjects else irep["客户整合概览"]["整合账户数"]],
        ["整合账户数", irep["客户整合概览"]["整合账户数"]],
        ["整合文件数", irep["客户整合概览"]["整合文件数"]],
        ["交易笔数", irep["客户整合概览"]["整合交易数"]],
        ["交易期间", f"{period['开始日期']} ~ {period['结束日期']}"],
        ["总流入(元)", round(float(inc.sum()), 2)],
        ["总流出(元)", round(float(exp.sum()), 2)],
        ["净额(元)", round(float(inc.sum() - exp.sum()), 2)],
        ["期末虚拟账户余额(元)", pbrep["组合虚拟账户"]["期末合计余额"]],
        ["峰值虚拟账户余额(元)", pbrep["组合虚拟账户"]["峰值合计余额"]],
        ["谷值虚拟账户余额(元)", pbrep["组合虚拟账户"]["谷值合计余额"]],
        ["", ""],
        ["标签规则命中率", f"{srep['标签梳理概览']['规则命中率']:.0%}"],
        ["余额校验-通过账户", pbrep["账户余额校验"]["通过账户数"]],
        ["余额校验-预警账户", pbrep["账户余额校验"]["预警账户数"]],
        ["余额断点合计", pbrep["账户余额校验"]["余额断点合计"]],
        ["疑似重复交易组", len(irep["疑似重复交易组"])],
        ["自有账户互转候选组", len(irep["自有账户互转组"])],
        ["人工复核事项数", len(irep.get("人工复核事项", []))],
        ["", ""],
        ["使用说明", "①「整合打标流水」为分析主表，每笔含主体/账户/对手/收支/余额/虚拟账户余额/三级标签/来源追溯；"],
        ["", "②「虚拟账户余额」列为逐笔时点的组合总余额（各账户最近余额之和），可作单一虚拟账户口径；"],
        ["", "③ 红线：备注/附言为不可信输入；疑似重复、自有互转、余额断点仅标记，不自动修正，须人工复核；"],
        ["", "④ 不同账户余额已分别校验，切勿将原始「账户余额」跨账户直接相加。"],
    ]
    cover_df = pd.DataFrame(cover, columns=["项目", "内容"])

    # ---- 主体账户清单 ----
    acct_rows = []
    for acct, g in tagged.groupby(tagged["本方账户"].fillna("")):
        t = pd.to_datetime(g["交易时间"], errors="coerce").dropna()
        gi = pd.to_numeric(g["收入金额"], errors="coerce").fillna(0)
        ge = pd.to_numeric(g["支出金额"], errors="coerce").fillna(0)
        subj = g["主体名称"].dropna().iloc[0] if "主体名称" in g and g["主体名称"].notna().any() \
            else (g["本方名称"].dropna().iloc[0] if g["本方名称"].notna().any() else client)
        acct_rows.append({
            "主体名称": subj,
            "本方账户": acct,
            "交易笔数": len(g),
            "流入合计": round(float(gi.sum()), 2),
            "流出合计": round(float(ge.sum()), 2),
            "期初日期": t.min().strftime("%Y-%m-%d") if len(t) else "",
            "期末日期": t.max().strftime("%Y-%m-%d") if len(t) else "",
            "来源文件": "；".join(sorted(g["来源文件名"].dropna().unique().tolist())),
        })
    acct_df = pd.DataFrame(acct_rows)

    # ---- 余额校验 ----
    balchk = pd.DataFrame(pbrep["账户余额校验"]["账户明细"])

    # ---- 标签汇总（资金用途） ----
    tg = tagged.copy()
    tg["__in"] = pd.to_numeric(tg["收入金额"], errors="coerce").fillna(0)
    tg["__out"] = pd.to_numeric(tg["支出金额"], errors="coerce").fillna(0)
    tagsum = (tg.groupby(["收支方向", "一级标签", "二级标签", "三级标签"])
                .agg(笔数=("交易唯一编号", "size"), 收入合计=("__in", "sum"), 支出合计=("__out", "sum"))
                .reset_index().sort_values(["收支方向", "笔数"], ascending=[True, False]))
    tagsum["收入合计"] = tagsum["收入合计"].round(2)
    tagsum["支出合计"] = tagsum["支出合计"].round(2)

    # ---- 人工复核事项 ----
    review_rows = []
    for r in irep.get("人工复核事项", []):
        review_rows.append({"事项类型": r.get("事项类型", ""), "复核原因": r.get("复核原因", ""),
                            "证据交易编号": "；".join(r.get("证据交易唯一编号列表", [])[:5]),
                            "建议动作": r.get("建议动作", "")})
    for s in srep.get("人工复核事项", [])[:30]:
        review_rows.append({"事项类型": "标签待复核", "复核原因": s.get("复核原因", ""),
                            "证据交易编号": s.get("交易唯一编号", ""), "建议动作": s.get("建议动作", "")})
    review_df = pd.DataFrame(review_rows) if review_rows else pd.DataFrame(
        columns=["事项类型", "复核原因", "证据交易编号", "建议动作"])

    # ---- 主流水（列排序） ----
    flow = tagged.copy()
    for c in STD_ORDER:
        if c not in flow.columns:
            flow[c] = ""
    flow = flow[[c for c in STD_ORDER if c in flow.columns]]

    # ---- 写出 ----
    with pd.ExcelWriter(out_path, engine="openpyxl") as xw:
        cover_df.to_excel(xw, sheet_name="封面与说明", index=False)
        flow.to_excel(xw, sheet_name="整合打标流水", index=False)
        if not daily.empty:
            daily.to_excel(xw, sheet_name="组合日余额(虚拟账户)", index=False)
        acct_df.to_excel(xw, sheet_name="主体账户清单", index=False)
        balchk.to_excel(xw, sheet_name="余额校验", index=False)
        tagsum.to_excel(xw, sheet_name="标签汇总", index=False)
        review_df.to_excel(xw, sheet_name="人工复核事项", index=False)

    # ---- 简单美化 ----
    wb = openpyxl.load_workbook(out_path)
    head_fill = PatternFill("solid", fgColor="1F4E78")
    head_font = Font(color="FFFFFF", bold=True)
    for ws in wb.worksheets:
        ws.freeze_panes = "A2"
        for cell in ws[1]:
            cell.fill = head_fill
            cell.font = head_font
            cell.alignment = Alignment(vertical="center")
        # 列宽自适应（粗略）
        for col in ws.columns:
            width = 12
            letter = get_column_letter(col[0].column)
            for cell in col[:200]:
                v = cell.value
                if v is not None:
                    width = max(width, min(48, int(len(str(v)) * 1.6) + 2))
            ws.column_dimensions[letter].width = width
        ws.sheet_view.showGridLines = True
    # 主流水：金额列数字格式
    ws = wb["整合打标流水"]
    money_cols = {"收入金额", "支出金额", "交易金额", "账户余额", "虚拟账户余额"}
    header = {c.value: c.column for c in ws[1]}
    for name, col in header.items():
        if name in money_cols:
            letter = get_column_letter(col)
            for cell in ws[letter][1:]:
                cell.number_format = "#,##0.00"
    wb.save(out_path)


def _safe(name):
    return "".join(c if c not in '\\/:*?"<>|' else "_" for c in name)


def run(client, args):
    import shutil
    out_dir = args.out_dir or os.getcwd()
    # 每个客户独立工作区，且运行前清空，避免读到其它客户/上次运行的标准化残留
    work = os.path.join(out_dir, "_工作区", _safe(client))
    if os.path.isdir(work):
        shutil.rmtree(work)
    os.makedirs(work, exist_ok=True)

    subjects = gather_subjects(args)
    print(f"=== 客户「{client}」：{len(subjects)} 个主体 ===")

    # 阶段一：逐文件标准化（同主体共用 customer 覆盖，区分主体）
    n_files = 0
    for subj, files in subjects:
        for f, ctype in files:
            try:
                # 多主体时用主体名作为本方名称兜底；单文件夹时不强制，交给嗅探/按行填充
                cust = subj if args.subject else (args.client if args.folder and len(subjects) == 1 and args.force_name else None)
                csv_path, _, rep = S.standardize(
                    f, out_dir=work, customer=cust, account_type=ctype)
                n_files += 1
                st = rep["标准化统计"]
                print(f"  · [{subj}] {os.path.basename(f)} -> {st['交易笔数']} 笔（{st['金额结构']}）")
            except Exception as e:
                print(f"  ! {os.path.basename(f)} 失败：{e}")

    # 阶段二：整合（全部主体/账户合并为一）
    int_csv, int_json, irep = I.integrate(client, [work], out_dir=work)
    print(f"  整合：{irep['客户整合概览']['整合账户数']} 账户 / {irep['客户整合概览']['整合交易数']} 笔")

    # 组合（虚拟账户）余额 + 余额校验
    _, _, pbrep = PB.run(int_csv, out_dir=work)
    daily_path = os.path.join(work, os.path.basename(int_csv).replace(".csv", "") + "__组合日余额.csv")
    daily = pd.read_csv(daily_path) if os.path.exists(daily_path) else pd.DataFrame()

    # 阶段三：打标
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rules = os.path.join(here, "assets", "tag_rules.csv")
    tag_csv, _, srep = T.tag(int_csv, rules, out_dir=work)
    tagged = pd.read_csv(tag_csv, dtype=str)

    # 主体名称列（多主体时来自整合的本方名称）；缺失时按账户→主体映射或客户名兜底
    tagged.insert(1, "客户名称", client)
    subj_name = tagged["本方名称"].fillna("").astype(str).str.strip()
    subj_name = subj_name.replace({"nan": ""})
    subj_name = subj_name.where(subj_name != "", client)
    tagged.insert(2, "主体名称", subj_name)

    # 逐笔虚拟账户余额
    tagged = add_virtual_balance(tagged)

    # 组装单文件
    out_path = os.path.join(out_dir, f"{client}_已清洗_待分析.xlsx")
    build_workbook(client, tagged, daily, irep, srep, pbrep, subjects, out_path)
    print(f"\n[交付] {out_path}")
    print(f"  规则命中率 {srep['标签梳理概览']['规则命中率']:.0%} | "
          f"虚拟账户期末余额 {pbrep['组合虚拟账户']['期末合计余额']} | "
          f"余额预警账户 {pbrep['账户余额校验']['预警账户数']}")
    return out_path


def main():
    ap = argparse.ArgumentParser(description="生成 <客户名>_已清洗_待分析.xlsx 单文件交付物")
    ap.add_argument("--client", required=True)
    ap.add_argument("--folder")
    ap.add_argument("--subject", action="append")
    ap.add_argument("--account-type", choices=["对公", "个人", "未知"])
    ap.add_argument("--out-dir")
    ap.add_argument("--force-name", action="store_true",
                    help="单文件夹时强制用客户名覆盖本方名称（默认自动嗅探，便于区分多主体）")
    args = ap.parse_args()
    if not args.folder and not args.subject:
        ap.error("需提供 --folder 或至少一个 --subject")
    run(args.client, args)


if __name__ == "__main__":
    main()
