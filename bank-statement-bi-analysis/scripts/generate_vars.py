#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
银行流水变量数据集生成器 V1.0
依据：
  - 银行流水数据分析工具功能规格说明书 v2.2
  - 广义现金流分析 (IPC + 交叉检验 + 反欺诈 + 知识图谱)
  - Debt OS Worksheet 个人和企业债务优化测算系统 V1.0
  - design-v3.md (行业变量 + 数据可信度)
  - methodology.md (口径定义)
  - metrics-processing-spec-v1.md (反欺诈AF/营销MK/信用风险LS指标层)

输入：标准化流水文件（整合打标流水）；可选 --loans 借据表、--whitelist 白名单
输出：变量数据集 Excel（sheet1 变量数据集 + sheet2 指标数据集 AF/MK/LS）
"""

import argparse, os, re, sys
import pandas as pd
import numpy as np

SHEET_CANDIDATES = ["整合打标流水", "标准化流水主表", "主表", "打标流水", "整合流水", "流水"]
FIN_KW = "银行|金融租赁|融资租赁|小额贷款|保理|消费金融|信托|金租"
GUAR_KW = "担保"
LEASE_HINT = "租金|租赁费|贷款|还款|月供"

# 行业推断关键词映射（design-v3 模块一：四源推断之对手名称源）
INDUSTRY_MAP = {
    "宠物用品|宠物食品|宠物": "宠物用品批发零售",
    "陶瓷|瓷厂|瓷业|陶艺|瓷店": "陶瓷制品批发零售",
    "电器|家电|电子": "家用电器批发零售",
    "货架|家具|货柜|展架|陈列|地毯|窗帘|建材经营|建材": "家居用品批发零售",
    "食品|零食|副食|餐饮|火锅|烘焙|糕点|烟酒|大米|果园|农产品": "食品批发零售",
    "服装|鞋帽|纺织|服饰|家纺": "服装纺织批发零售",
    "五金|卫浴|灯饰|管道|金属|塑料|胶业": "建材五金批发零售",
    "化妆品|美容|护肤|日化|美发": "日化用品批发零售",
    "电商|电子商务|抖音|淘宝|天猫|拼多多|京东|快手|视频号|跨境电商": "电商零售",
    "物流|运输|货运|快递|供应链管理": "物流运输",
    "传媒|文化|广告|营销|策划|影视|娱乐|知识|IP|直播": "文化传媒",
    "科技|软件|信息|数据|网络科技|支付科技|钉钉": "信息技术服务",
    "制造|加工|生产|工厂|加工厂": "加工制造业",
    "农业|食品股份|种植|养殖|畜牧|果蔬": "农产品加工",
    "教育|培训|学校|学院|咨询|留学": "教育培训",
    "医疗|药品|医药|医院|诊所|体检|健康产业": "医疗健康",
    "建筑|工程|装修|装饰|园林|设计|环保": "建筑工程",
    "酒店|住宿|旅游|民宿": "酒店旅游",
    "金融|保险|担保|投资|基金|融资租赁|保理": "金融服务",
    "汽车|车行|汽配|新能源": "汽车行业",
    "物业|房产|地产|中介|经纪|不动产": "房地产",
    "商贸|贸易|实业|商行|供应链|经销|经营部": "综合批发零售",
    "体育|健身|运动|户外|骑行": "体育用品",
    "桶装水|送水|花卉|园艺|绿植": "生活服务",
    "财税|会计|代理|律师|法律|知产|知识产权": "专业服务",
}

# ============================================================
# 1. 数据加载与预处理
# ============================================================
def load_data(path):
    if path.lower().endswith(".csv"):
        df = pd.read_csv(path)
    else:
        xl = pd.ExcelFile(path)
        sheet = next((s for c in SHEET_CANDIDATES for s in xl.sheet_names if c in s), xl.sheet_names[0])
        df = xl.parse(sheet)
    return df

def prep(df):
    for c in ["收入金额", "支出金额", "交易金额", "账户余额"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["收入金额"] = df.get("收入金额", pd.Series(0, index=df.index)).fillna(0.0)
    df["支出金额"] = df.get("支出金额", pd.Series(0, index=df.index)).fillna(0.0)
    if (df["收入金额"] == 0).all() and (df["支出金额"] == 0).all() and "交易金额" in df.columns:
        df["收入金额"] = df["交易金额"].clip(lower=0)
        df["支出金额"] = (-df["交易金额"]).clip(lower=0)
    df["交易时间"] = pd.to_datetime(df["交易时间"], errors="coerce")
    df["月份"] = df["交易时间"].dt.strftime("%Y-%m")
    for c in ["一级标签", "二级标签", "三级标签", "主体名称", "本方名称", "客户名称", "对手名称", "账户类型"]:
        if c not in df.columns:
            df[c] = ""
        df[c] = df[c].fillna("").astype(str)
    # 清除对手名称、对手账户中汉字间的空格（对手账户可能被读成数值/NaN，先转字符串）
    for c in ["对手名称", "对手账户"]:
        if c in df.columns:
            df[c] = df[c].fillna("").astype(str).str.replace(r'\s+', '', regex=True)
    df["摘要"] = (df.get("银行备注", pd.Series("", index=df.index)).fillna("").astype(str) + " " +
                  df.get("账户方附言", pd.Series("", index=df.index)).fillna("").astype(str))
    # 组合标签
    df["组合标签"] = df["一级标签"] + "-" + df["二级标签"] + "-" + df["三级标签"]
    # 交易金额绝对值
    df["交易金额_abs"] = df["收入金额"] + df["支出金额"]
    # 个人/对公判断
    df["对手性质"] = df["对手名称"].apply(nature)
    # 内部互转
    members = (set(df["主体名称"]) | set(df["本方名称"])) - {""}
    df["内部互转"] = df["对手名称"].isin(members)
    # 月份标签（距截止日期月份数）
    max_date = df["交易时间"].max()
    df["距截止月数"] = ((max_date.year - df["交易时间"].dt.year) * 12 + 
                         max_date.month - df["交易时间"].dt.month + 1)
    df["月份标签"] = df["距截止月数"].apply(lambda x: f"前{x}个月" if x >= 1 else "当月")
    return df, max_date

def nature(name):
    name = str(name).strip()
    if not name or name in ["--", "NaN", "nan"]:
        return "未知"
    # 第一轮：企业/机构关键词 → 对公
    if re.search(r"公司|厂|中心|商行|合作社|集团|医院|学校|局|院|所|有限|店|部|行", name):
        return "对公"
    # 第二轮：银行科目/平台专户/系统账户/财税科目 → 对公
    if re.search(r"对公|单位|系统|代发|代扣|应付|应收|平台|专户|保证金|待报解|预算|"
                 r"批量|暂收|暂付|汇划|汇兑|手续费|利息|赎回|科目|集中|清算|"
                 r"网上其他收|网上电子|电子商务交易资金|支付科技|网络支付", name):
        return "对公"
    # 第三轮：纯自然人姓名 → 个人
    # 姓名特征：2-4个汉字，不含数字、字母、标点
    if re.match(r"^[\u4e00-\u9fa5]{2,4}$", name):
        return "个人"
    # 兜底：含中文且非纯数字、非系统名 → 个人
    if re.search(r"[\u4e00-\u9fa5]", name):
        return "个人"
    return "未知"

def daily_balance_series(df, bal_col):
    """计算日余额Series（全日期范围，缺失日ffill）。
    口径：多账户拆分后各自填充再求和（与 build_bi_report_v3 一致）。
    路径A：仅原始余额口径可用虚拟账户余额列（已是合并口径）；
    派生余额列（如 余额_去筹资）必须走路径B，否则会静默退回原始余额。"""
    if bal_col == "账户余额" and "虚拟账户余额" in df.columns and df["虚拟账户余额"].notna().any():
        vdf = df[df["虚拟账户余额"].notna()].copy()
        vdf["日期"] = pd.to_datetime(vdf["交易时间"].dt.date)
        daily = vdf.groupby("日期")["虚拟账户余额"].last().sort_index()
    else:
        t = df[df[bal_col].notna()].copy()
        if len(t) == 0:
            return pd.Series(dtype=float)
        t["acct"] = t.get("本方账户", pd.Series("ALL", index=t.index)).fillna("ALL")
        t["日期"] = pd.to_datetime(t["交易时间"].dt.date)
        last = t.groupby(["acct", "日期"])[bal_col].last().unstack(0)
        last.index = pd.to_datetime(last.index)
        all_dates = pd.date_range(last.index.min(), last.index.max(), freq="D")
        daily = last.reindex(all_dates).ffill().fillna(0).sum(axis=1)
    idx = pd.date_range(daily.index.min(), daily.index.max(), freq="D")
    return daily.reindex(idx).ffill()

# ============================================================
# 2. 变量计算
# ============================================================
def compute_vars(df, max_date, home_city="", home_province_kw=()):
    """home_city: 客户经营地城市（销售半径"本地"判断基点）；
    home_province_kw: 本省地名关键词列表（销售半径"本省"判断，如 ["江西","景德镇","崇仁"]）。"""
    vars_list = []  # [(变量名, 变量值, 逻辑说明)]
    ext = df[~df["内部互转"]]
    MONTHS = sorted(m for m in df["月份"].dropna().unique() if m)
    L12 = MONTHS[-12:] if len(MONTHS) >= 12 else MONTHS
    month_labels = sorted(df["月份标签"].unique(), key=lambda x: int(re.findall(r'\d+', x)[0]) if re.findall(r'\d+', x) else 0)
    tag_combos = sorted(df["组合标签"].unique())

    def add(name, value, logic=""):
        if isinstance(value, float) and value != value:
            value = None
        vars_list.append((name, value, logic))

    # ---- A. 基本信息 ----
    add("客户名称", df["客户名称"].iloc[0] if len(df) else "", "标准化流水中的客户名称")
    add("主体名称", "、".join(sorted(set(df["主体名称"]) - {""})), "标准化流水中的关联主体")
    add("账户数量", int(df["本方账户"].nunique()), "本方账户去重计数")
    add("账户类型", "、".join(sorted(set(df["账户类型"]) - {""})), "账户类型（个人/对公）")
    add("数据起始日期", str(df["交易时间"].min().date()), "流水最早交易日期")
    add("数据截止日期", str(max_date.date()), "流水最晚交易日期")
    add("覆盖月数", len(MONTHS), "数据覆盖的自然月数")
    add("总交易笔数", int(len(df)), "全部交易笔数")

    # ---- A2. 按本方账户类型拆分（对公/个人流水）----
    corp_acct = df[df["账户类型"] == "对公"]
    priv_acct = df[df["账户类型"] == "个人"]
    add("对公流水账户数", int(corp_acct["本方账户"].nunique()), "本方账户类型为对公的账户去重数")
    add("对公流水流入总额", round(float(corp_acct["收入金额"].sum()), 2), "对公账户的流入合计")
    add("对公流水流出总额", round(float(corp_acct["支出金额"].sum()), 2), "对公账户的流出合计")
    add("对公流水交易笔数", int(len(corp_acct)), "对公账户的交易笔数")
    add("个人流水账户数", int(priv_acct["本方账户"].nunique()), "本方账户类型为个人的账户去重数")
    add("个人流水流入总金额", round(float(priv_acct["收入金额"].sum()), 2), "个人账户的流入合计")
    add("个人流水流出总金额", round(float(priv_acct["支出金额"].sum()), 2), "个人账户的流出合计")
    add("个人流水交易笔数", int(len(priv_acct)), "个人账户的交易笔数")
    # 校验
    add("账户类型拆分校验_流入", round(float(corp_acct["收入金额"].sum() + priv_acct["收入金额"].sum() - df["收入金额"].sum()), 2), "对公+个人流入-总流入=0即闭合")
    add("账户类型拆分校验_流出", round(float(corp_acct["支出金额"].sum() + priv_acct["支出金额"].sum() - df["支出金额"].sum()), 2), "对公+个人流出-总流出=0即闭合")

    # ---- B. 总体收支（双口径）----
    add("总流入金额_含互转", round(float(df["收入金额"].sum()), 2), "全部收入金额合计（含关联主体互转）")
    add("总流出金额_含互转", round(float(df["支出金额"].sum()), 2), "全部支出金额合计（含关联主体互转）")
    add("净流入金额_含互转", round(float(df["收入金额"].sum() - df["支出金额"].sum()), 2), "总流入-总流出（含互转）")
    add("内部互转流入金额", round(float(df[df["内部互转"]]["收入金额"].sum()), 2), "关联主体间互转流入")
    add("内部互转流出金额", round(float(df[df["内部互转"]]["支出金额"].sum()), 2), "关联主体间互转流出")
    add("对外流入金额", round(float(ext["收入金额"].sum()), 2), "剔除内部互转后的净对外流入")
    add("对外流出金额", round(float(ext["支出金额"].sum()), 2), "剔除内部互转后的净对外流出")
    add("对外净流入金额", round(float(ext["收入金额"].sum() - ext["支出金额"].sum()), 2), "对外流入-对外流出")
    add("收支比_对外", round(float(ext["支出金额"].sum() / max(ext["收入金额"].sum(), 1)), 4), "对外流出/对外流入")

    # ---- C. 近12月收支 ----
    l12_df = ext[ext["月份"].isin(L12)]
    add("近12月对外流入金额", round(float(l12_df["收入金额"].sum()), 2), "近12个月对外口径流入合计")
    add("近12月对外流出金额", round(float(l12_df["支出金额"].sum()), 2), "近12个月对外口径流出合计")
    add("近12月对外净流入金额", round(float(l12_df["收入金额"].sum() - l12_df["支出金额"].sum()), 2), "近12月对外净流入")
    add("近12月月均对外流入金额", round(float(l12_df["收入金额"].sum() / max(len(L12), 1)), 2), "近12月月均对外流入")
    add("近12月月均对外流出金额", round(float(l12_df["支出金额"].sum() / max(len(L12), 1)), 2), "近12月月均对外流出")

    # 月流入变异系数
    mon_in = ext.groupby("月份")["收入金额"].sum().reindex(MONTHS, fill_value=0)
    cv12 = float(mon_in[L12].std() / max(mon_in[L12].mean(), 1))
    add("近12月月流入变异系数_CV", round(cv12, 4), "近12月月流入标准差/均值")

    # ---- D. 个人/对公/未知对手 收支 ----
    priv = ext[ext["对手性质"] == "个人"]
    corp = ext[ext["对手性质"] == "对公"]
    unkn = ext[ext["对手性质"] == "未知"]
    add("个人对手流入金额", round(float(priv["收入金额"].sum()), 2), "对手性质为个人的流入合计")
    add("个人对手流出金额", round(float(priv["支出金额"].sum()), 2), "对手性质为个人的流出合计")
    add("对公对手流入金额", round(float(corp["收入金额"].sum()), 2), "对手性质为对公的流入合计")
    add("对公对手流出金额", round(float(corp["支出金额"].sum()), 2), "对手性质为对公的流出合计")
    add("未知对手流入金额", round(float(unkn["收入金额"].sum()), 2), "对手名称缺失/无法识别性质的流入合计")
    add("未知对手流出金额", round(float(unkn["支出金额"].sum()), 2), "对手名称缺失/无法识别性质的流出合计")
    add("未知对手流入笔数", int(len(unkn[unkn["收入金额"] > 0])), "未知对手的流入笔数")
    add("未知对手流出笔数", int(len(unkn[unkn["支出金额"] > 0])), "未知对手的流出笔数")
    add("对手分类校验_出入", round(float(priv["收入金额"].sum() + corp["收入金额"].sum() + unkn["收入金额"].sum() - ext["收入金额"].sum()), 2), "个人+对公+未知-对外流入=0即闭合")
    add("对私交易金额占比", round(float((priv[["收入金额", "支出金额"]].sum().sum()) / max(ext[["收入金额", "支出金额"]].sum().sum(), 1)), 4), "对私收支合计/总收支")

    # ---- E. 对手 TOP 分析 ----
    cp = ext[ext["对手名称"].notna() & (ext["对手名称"] != "")].groupby("对手名称").agg(
        流入=("收入金额", "sum"), 流出=("支出金额", "sum"), 笔数=("收入金额", "size"),
        流入笔数=("收入金额", lambda s: int((s > 0).sum())),
        流出笔数=("支出金额", lambda s: int((s > 0).sum()))).sort_values("流入", ascending=False)
    for i in range(1, 11):
        if i <= len(cp):
            row = cp.iloc[i - 1]
            add(f"Top{i}流入对手_名称", str(row.name), f"流入排名第{i}的对手名称")
            add(f"Top{i}流入对手_流入金额", round(float(row["流入"]), 2), f"流入排名第{i}的对手流入金额")
            add(f"Top{i}流入对手_流入占比", round(float(row["流入"] / max(ext["收入金额"].sum(), 1)), 4), f"流入排名第{i}的对手流入占比")
            add(f"Top{i}流入对手_笔数", int(row["流入笔数"]), f"流入排名第{i}的对手流入笔数")
        else:
            for suffix, fmt in [("名称", None), ("流入金额", 0), ("流入占比", 0), ("笔数", 0)]:
                add(f"Top{i}流入对手_{suffix}", fmt if fmt is not None else "", f"流入排名第{i}（无数据）")

    cp_out = cp.sort_values("流出", ascending=False)
    for i in range(1, 11):
        if i <= len(cp_out):
            row = cp_out.iloc[i - 1]
            add(f"Top{i}流出对手_名称", str(row.name), f"流出排名第{i}的对手名称")
            add(f"Top{i}流出对手_流出金额", round(float(row["流出"]), 2), f"流出排名第{i}的对手流出金额")
            add(f"Top{i}流出对手_流出占比", round(float(row["流出"] / max(ext["支出金额"].sum(), 1)), 4), f"流出排名第{i}的对手流出占比")
            add(f"Top{i}流出对手_笔数", int(row["流出笔数"]), f"流出排名第{i}的对手流出笔数")
        else:
            for suffix, fmt in [("名称", None), ("流出金额", 0), ("流出占比", 0), ("笔数", 0)]:
                add(f"Top{i}流出对手_{suffix}", fmt if fmt is not None else "", f"流出排名第{i}（无数据）")

    # HHI
    hhi_in = float(((cp["流入"] / max(cp["流入"].sum(), 1)) ** 2).sum())
    hhi_out = float(((cp["流出"] / max(cp["流出"].sum(), 1)) ** 2).sum())
    add("流入HHI", round(hhi_in, 4), "∑(对手流入份额²)")
    add("流出HHI", round(hhi_out, 4), "∑(对手流出租份额²)")
    add("活跃对手数_月均", round(float(ext[ext["对手名称"] != ""].groupby("月份")["对手名称"].nunique().mean()), 1), "各月不同对手数的均值")

    # 对手留存
    cpm = ext[(ext["对手名称"] != "") & (ext["收入金额"] > 0)].groupby("对手名称")["月份"].nunique()
    retain_in = cp.loc[cp.index.intersection(cpm[cpm >= 6].index), "流入"].sum() / max(ext["收入金额"].sum(), 1)
    add("老对手留存流入占比", round(float(retain_in), 4), "出现≥6个月的对手流入/总对外流入")

    # 方向过滤辅助函数（skill v1.0 规则）
    def skip_direction(tag, direction):
        """根据标签语义判断是否应跳过某方向。direction: '流入'/'流出'/'净额'"""
        parts = tag.split("-")
        l1, l2, l3 = parts[0], (parts[1] if len(parts) > 1 else ""), (parts[2] if len(parts) > 2 else "")
        # 三级标签含"支出"或"偿还" → 跳过流入、净额
        if ("支出" in l3 or "偿还" in l3) and direction in ("流入", "净额"):
            return True
        # 三级标签含"收入"或"还入" → 跳过流出、净额
        if ("收入" in l3 or "还入" in l3) and direction in ("流出", "净额"):
            return True
        # 二级标签包含"成本" → 跳过流入、净额
        if "成本" in l2 and direction in ("流入", "净额"):
            return True
        # 二级标签包含"结息" → 跳过流出、净额
        if "结息" in l2 and direction in ("流出", "净额"):
            return True
        # 二级标签为"退款交易" → 跳过流出、净额
        if l2 == "退款交易" and direction in ("流出", "净额"):
            return True
        # 一级标签为"其他收入" → 跳过流出、净额
        if l1 == "其他收入" and direction in ("流出", "净额"):
            return True
        return False

    # ---- F. 标签分布 ----
    for tag in tag_combos:
        parts = tag.split("-")
        tag_df = ext[ext["组合标签"] == tag]
        add(f"组合标签_{tag}_总笔数", int(len(tag_df)), f"组合标签[{tag}]的交易笔数")
        if not skip_direction(tag, "流入"):
            add(f"组合标签_{tag}_流入金额", round(float(tag_df["收入金额"].sum()), 2), f"组合标签[{tag}]的流入金额合计")
        if not skip_direction(tag, "流出"):
            add(f"组合标签_{tag}_流出金额", round(float(tag_df["支出金额"].sum()), 2), f"组合标签[{tag}]的流出金额合计")
        if not skip_direction(tag, "净额"):
            add(f"组合标签_{tag}_净额", round(float(tag_df["收入金额"].sum() - tag_df["支出金额"].sum()), 2), f"组合标签[{tag}]的净额")

    # ---- G. 组合标签 × 月份标签 × 收支方向 × 金额/笔数 ----
    for tag in tag_combos:
        for ml in month_labels:
            mldf = ext[(ext["组合标签"] == tag) & (ext["月份标签"] == ml)]
            if len(mldf) == 0:
                continue
            # 流入
            if not skip_direction(tag, "流入"):
                in_df = mldf[mldf["收入金额"] > 0]
                add(f"{tag}-{ml}-流入-金额", round(float(in_df["收入金额"].sum()), 2),
                    f"组合标签[{tag}]在[{ml}]的流入金额合计")
                add(f"{tag}-{ml}-流入-笔数", int(len(in_df)),
                    f"组合标签[{tag}]在[{ml}]的流入笔数")
            # 流出
            if not skip_direction(tag, "流出"):
                out_df = mldf[mldf["支出金额"] > 0]
                add(f"{tag}-{ml}-流出-金额", round(float(out_df["支出金额"].sum()), 2),
                    f"组合标签[{tag}]在[{ml}]的流出金额合计")
                add(f"{tag}-{ml}-流出-笔数", int(len(out_df)),
                    f"组合标签[{tag}]在[{ml}]的流出笔数")
            # 净额
            if not skip_direction(tag, "净额"):
                add(f"{tag}-{ml}-净额", round(float(mldf["收入金额"].sum() - mldf["支出金额"].sum()), 2),
                    f"组合标签[{tag}]在[{ml}]的净额")

    # ---- H. 余额按12个月份标签汇总（日平均余额）----
    # 建立 月份标签 → 实际月份(yyyy-mm) 的映射
    ml_to_ym = df.groupby("月份标签")["月份"].first().to_dict()

    # 辅助函数：从余额列计算各月份标签的日平均余额（日余额口径见模块级 daily_balance_series）
    def compute_daily_avg_bal(bal_col):
        """输入余额列名，返回 {月份标签: (日均余额, 天数)} 字典。"""
        daily_bal = daily_balance_series(df, bal_col)
        result = {}
        for ml in month_labels:
            yyyymm = ml_to_ym.get(ml, "")
            if yyyymm:
                ml_dates = [d for d in daily_bal.index if d.strftime("%Y-%m") == yyyymm]
                if ml_dates:
                    avg = float(daily_bal[ml_dates].mean())
                    result[ml] = (round(avg, 2), len(ml_dates))
                else:
                    result[ml] = (0, 0)
            else:
                result[ml] = (0, 0)
        return result

    # H1. 各月日平均余额
    if "账户余额" in df.columns and len(df["账户余额"].dropna()) > 0:
        h1 = compute_daily_avg_bal("账户余额")
        for ml in month_labels:
            avg_val, days = h1.get(ml, (0, 0))
            add(f"余额_{ml}_日均", avg_val, f"按月份标签[{ml}]的日平均余额（每天取日终余额，缺失日向前填充，总天数={days}）")
            add(f"余额_{ml}_覆盖天数", days, f"按月份标签[{ml}]的覆盖天数")

    # H2. 余额去掉筹资类交易影响后的日平均余额
    if "账户余额" in df.columns:
        is_finance = df["一级标签"].isin(["筹资类", "投资类"])
        # 累积筹资净流入 = 累积(筹资类收入) - 累积(筹资类支出)
        # 账户余额是各账户各自的交易后余额：必须按账户分组、组内按交易时间排序后各自累计，
        # 否则跨账户串扰、且依赖文件行序
        finance_net = df["收入金额"].where(is_finance, 0) - df["支出金额"].where(is_finance, 0)
        acct = df.get("本方账户", pd.Series("ALL", index=df.index)).fillna("ALL")
        order = df["交易时间"].sort_values(kind="stable").index
        finance_net_cum = finance_net.loc[order].groupby(acct.loc[order]).cumsum().reindex(df.index)
        # 去掉筹资类影响的余额 = 账户余额 - 累积筹资净流入（即加回筹资流出、减去筹资流入）
        df["余额_去筹资"] = df["账户余额"] - finance_net_cum
    else:
        df["余额_去筹资"] = np.nan

    if "账户余额" in df.columns and len(df["余额_去筹资"].dropna()) > 0:
        h2 = compute_daily_avg_bal("余额_去筹资")
        for ml in month_labels:
            avg_val, days = h2.get(ml, (0, 0))
            add(f"余额_去筹资_{ml}_日均", avg_val, f"去掉筹资类影响后，按月份标签[{ml}]的日平均余额（缺失日向前填充，总天数={days}）")
            add(f"余额_去筹资_{ml}_覆盖天数", days, f"去掉筹资类影响后，按月份标签[{ml}]的覆盖天数")

    # ---- I. 余额相关 ----
    if "账户余额" in df.columns:
        bal = df["账户余额"].dropna()
        if len(bal):
            add("期初余额", round(float(df["账户余额"].dropna().iloc[0]), 2), "第一条记录的账户余额")
            add("期末余额", round(float(df["账户余额"].dropna().iloc[-1]), 2), "最后一条记录的账户余额")
    if "虚拟账户余额" in df.columns:
        vbal = df["虚拟账户余额"].dropna()
        if len(vbal):
            add("期初虚拟账户余额", round(float(vbal.iloc[0]), 2), "组合日余额起始值")
            add("期末虚拟账户余额", round(float(vbal.iloc[-1]), 2), "组合日余额终值")

    # ---- J. 融资与负债 (Debt OS) ----
    # 金融机构对手
    fin_ext = ext[ext["对手名称"].str.contains(FIN_KW)]
    fin_loans = fin_ext[fin_ext["收入金额"] >= 200000]
    add("金融机构放款笔数", int(len(fin_loans)), "单笔≥20万的金融机构放款笔数")
    add("金融机构放款总金额", round(float(fin_loans["收入金额"].sum()), 2), "金融机构放款合计")
    fin_repay = fin_ext[(fin_ext["支出金额"] > 0) & (fin_ext["摘要"].str.contains(LEASE_HINT) | (fin_ext["三级标签"].str.contains("偿还")) | (fin_ext["二级标签"] == "负债"))]
    add("金融机构还款笔数", int(len(fin_repay)), "向金融机构的还款笔数")
    add("金融机构还款总金额", round(float(fin_repay["支出金额"].sum()), 2), "向金融机构的还款合计")

    # 民间借贷
    mj = ext[ext["二级标签"].str.contains("民间借贷")]
    add("民间借贷借入笔数", int(len(mj[mj["收入金额"] > 0])), "民间借贷流入笔数")
    add("民间借贷借入金额", round(float(mj["收入金额"].sum()), 2), "民间借贷流入合计")
    add("民间借贷归还笔数", int(len(mj[mj["支出金额"] > 0])), "民间借贷流出笔数")
    add("民间借贷归还金额", round(float(mj["支出金额"].sum()), 2), "民间借贷流出合计")
    add("民间借贷净敞口", round(float(mj["收入金额"].sum() - mj["支出金额"].sum()), 2), "民间借贷借入-归还")

    # 担保费
    guar = ext[(ext["支出金额"] > 0) & ext["对手名称"].str.contains(GUAR_KW)]
    add("担保费笔数", int(len(guar)), "向担保公司支付的笔数")
    add("担保费总金额", round(float(guar["支出金额"].sum()), 2), "向担保公司支付的合计")

    # ---- K. 数据可信度指标 (design-v3 模块〇) ----
    # 字段完整性
    add("对手名称非空率", round(float((df["对手名称"] != "").mean()), 4), "对手名称非空行占比")
    add("摘要非空率", round(float((df["摘要"].str.strip() != "").mean()), 4), "摘要/附言非空行占比")

    # 整数金额占比
    amts = df["收入金额"].add(df["支出金额"]).abs()
    amts_ge1w = amts[amts >= 10000]
    int_ratio = float(((amts_ge1w % 10000) == 0).mean()) if len(amts_ge1w) else 0.0
    add("整数金额占比_大于等于1万", round(int_ratio, 4), "≥1万的交易中整数金额占比")

    # 夜间交易
    has_time = df["交易时间"].dt.strftime("%H:%M:%S") != "00:00:00"
    night = ((df["交易时间"].dt.hour >= 21) | (df["交易时间"].dt.hour < 6)) & has_time
    add("夜间交易笔数", int(night.sum()), "21:00-06:00的交易笔数")
    add("夜间交易占比", round(float(night.sum() / max(has_time.sum(), 1)), 4), "夜间交易/有时点交易")

    # 冲正
    chz = df[df["摘要"].str.contains("冲正|退票")]
    add("冲正退票笔数", int(len(chz)), "摘要含冲正/退票的笔数")

    # 快进快出
    day = ext.groupby(ext["交易时间"].dt.date).agg(i=("收入金额", "sum"), o=("支出金额", "sum"))
    onx = day["o"].shift(-1).fillna(0)
    bid = day[day["i"] >= 100000]
    fastio = float((((bid["o"] + onx.loc[bid.index]) / bid["i"]) >= 0.8).mean()) if len(bid) else 0.0
    add("快进快出占比", round(fastio, 4), "大额入账日T+1流出≥80%天数占比")

    # ---- L. 现金流量表要素 (会小企03) ----
    # 行级互斥掩码：每笔交易只进一个科目，保证残差项（其他经营收支）非负、各科目相加=总额
    # 流入侧：筹资（标签命中 或 单笔≥50万金融机构放款）> 投资 > 主营 > 其他
    m_borrow = (ext["二级标签"].str.contains("民间借贷|票据|筹资|负债") |
                ((ext["收入金额"] >= 500000) & ext["对手名称"].str.contains(FIN_KW)))
    m_invb = ext["二级标签"].str.contains("出借|投资") & ~m_borrow
    m_zy_in = (ext["二级标签"] == "主营业务") & ~m_borrow & ~m_invb
    borrow = ext.loc[m_borrow, "收入金额"].sum()
    invb = ext.loc[m_invb, "收入金额"].sum()
    zy_in = ext.loc[m_zy_in, "收入金额"].sum()
    o_in = ext["收入金额"].sum() - zy_in - borrow - invb
    # 流出侧：主营 > 人力保险 > 税 > 偿债 > 投资 > 其他
    m_cg = ext["二级标签"] == "主营业务"
    m_xz = ext["二级标签"].str.contains("人力|保险") & ~m_cg
    m_tax = ext["二级标签"].str.contains("税") & ~m_cg & ~m_xz
    m_rp = ext["二级标签"].str.contains("民间借贷|负债|筹资") & ~m_cg & ~m_xz & ~m_tax
    m_invo = ext["二级标签"].str.contains("出借|投资") & ~m_cg & ~m_xz & ~m_tax & ~m_rp
    cg = ext.loc[m_cg, "支出金额"].sum()
    xz = ext.loc[m_xz, "支出金额"].sum()
    tax = ext.loc[m_tax, "支出金额"].sum()
    rp = ext.loc[m_rp, "支出金额"].sum()
    invo = ext.loc[m_invo, "支出金额"].sum()
    o_out = ext["支出金额"].sum() - cg - xz - tax - rp - invo

    add("CFS_销售收款", round(float(zy_in), 2), "主营业务收入=销售商品提供劳务收到的现金")
    add("CFS_其他经营收入", round(float(o_in), 2), "收到其他与经营活动有关的现金")
    add("CFS_采购付款", round(float(cg), 2), "主营业务支出=购买商品接受劳务支付的现金")
    add("CFS_职工薪酬", round(float(xz), 2), "人力成本+保险=支付的职工薪酬")
    add("CFS_税费支出", round(float(tax), 2), "税费支出")
    add("CFS_其他经营支出", round(float(o_out), 2), "支付其他与经营活动有关的现金")
    add("CFS_经营净额", round(float(zy_in + o_in - cg - xz - tax - o_out), 2), "经营活动净现金流")
    add("CFS_投资收回", round(float(invb), 2), "收回投资收到的现金")
    add("CFS_投资支出", round(float(invo), 2), "投资支付的现金")
    add("CFS_投资净额", round(float(invb - invo), 2), "投资活动净现金流")
    add("CFS_借款收到", round(float(borrow), 2), "取得借款收到的现金")
    add("CFS_偿债支出", round(float(rp), 2), "偿还借款本金及利息支付的现金")
    add("CFS_筹资净额", round(float(borrow - rp), 2), "筹资活动净现金流")
    add("CFS_现金净增加额", round(float(zy_in + o_in - cg - xz - tax - o_out + invb - invo + borrow - rp), 2), "现金净增加额")

    # ---- M. 风险信号 ----
    recent3 = MONTHS[-3:] if len(MONTHS) >= 3 else MONTHS
    new_fin = float(fin_ext[(fin_ext["收入金额"] >= 200000) & (fin_ext["月份"].isin(recent3))]["收入金额"].sum())
    add("近3月金融机构新增大额融资（单笔》20万）", round(new_fin, 2), "近3月单笔≥20万的金融机构放款合计")
    add("近3月金融机构放款机构数", int(fin_ext[(fin_ext["收入金额"] >= 200000) & (fin_ext["月份"].isin(recent3))]["对手名称"].nunique()), "近3月放款机构去重数")

    # 对私大额通道
    priv_hi = cp[(cp["笔数"] >= 100) & (cp.index.map(nature) == "个人")]
    add("对私高频对手数", int(len(priv_hi)), "交易≥100笔的个人对手数")

    # 司法执行
    jud = ext[ext["对手名称"].str.contains("法院|执行局|仲裁")]
    add("司法执行事件笔数", int(len(jud)), "对手含法院/执行局/仲裁的交易笔数")

    # 金融投机
    spec = ext[ext["对手名称"].str.contains("期货|交易所|数字货币|虚拟币|博彩|彩票")]
    add("金融投机赌博事件笔数", int(len(spec)), "对手含期货/数字货币/博彩类交易笔数")

    # ---- N. 人力/税费/运营 ----
    add("薪酬支出总额", round(float(df[df["二级标签"].str.contains("人力")]["支出金额"].sum()), 2), "人力成本标签支出合计")
    add("税费支出总额", round(float(df[df["二级标签"].str.contains("税")]["支出金额"].sum()), 2), "税收成本标签支出合计")
    add("运营成本支出总额", round(float(df[df["二级标签"].str.contains("运营")]["支出金额"].sum()), 2), "运营成本标签支出合计")

    # ---- P. 行业推断 (design-v3 模块一：四源推断 + 置信度) ----
    # P1. 源1：对手名称关键词 → 行业分布（仅对公对手参与，个人对手单独统计）
    opp_industry = {}
    for opp_name in ext[ext["对手名称"] != ""]["对手名称"].unique():
        opp_nature = nature(str(opp_name))
        if opp_nature == "个人":
            opp_industry[opp_name] = "个人对手"
            continue
        for kw, ind in INDUSTRY_MAP.items():
            if re.search(kw, str(opp_name)):
                opp_industry[opp_name] = ind
                break
        if opp_name not in opp_industry:
            opp_industry[opp_name] = "未分类"

    # 按对手流入金额加权统计行业（仅对公+SYS）
    opp_flow = ext[ext["对手名称"] != ""].groupby("对手名称")["收入金额"].sum()
    ind_flow = {}
    ind_cnt = {}
    for opp, flow in opp_flow.items():
        ind = opp_industry.get(opp, "未分类")
        ind_flow[ind] = ind_flow.get(ind, 0) + flow
        ind_cnt[ind] = ind_cnt.get(ind, 0) + 1
    opp_cnt_uniq = len(opp_flow)

    # 对公部分分开统计
    corp_ind_flow = {k: v for k, v in ind_flow.items() if k not in ("个人对手", "未分类")}
    total_corp_flow = sum(corp_ind_flow.values())
    personal_flow = ind_flow.get("个人对手", 0)
    unclassified_flow = ind_flow.get("未分类", 0)

    # 主行业 = 对公对手中加权流入最大的行业
    sorted_ind = sorted(corp_ind_flow.items(), key=lambda x: x[1], reverse=True)
    primary_industry = sorted_ind[0][0] if sorted_ind else "行业特征不显著"
    primary_flow = sorted_ind[0][1] if sorted_ind else 0
    primary_flow_share = primary_flow / max(total_corp_flow, 1)

    # 置信度
    corp_covered = sum(1 for o in opp_flow.index if opp_industry.get(o, "") not in ("个人对手", "未分类"))
    coverage = corp_covered / max(opp_cnt_uniq, 1)
    if primary_flow_share >= 0.5 and coverage >= 0.3:
        conf = "高置信"
    elif primary_flow_share >= 0.3:
        conf = "中置信"
    else:
        conf = "低置信"

    add("行业推断_主行业", primary_industry, "四源推断：基于对公对手名称关键词加权的最大流入行业")
    add("行业推断_置信度", conf, "高/中/低，基于对公对手覆盖率与行业集中度")
    add("行业推断_主导行业流入占比_对公口径", round(float(primary_flow_share), 4), "主导行业流入/对公可识别行业流入")
    add("行业推断_对公可识别对手数", int(corp_covered), "有行业关键词匹配的对公对手去重数")
    add("行业推断_个人对手流入金额", round(float(personal_flow), 2), "个人对手流入合计（不参与行业推断）")
    add("行业推断_个人对手流入占比", round(float(personal_flow / max(opp_flow.sum(), 1)), 4), "个人流入/总对外流入")
    add("行业推断_未分类对手流入金额", round(float(unclassified_flow), 2), "对公但未匹配到行业关键词的流入")
    add("行业推断_未分类对手数", int(ind_cnt.get("未分类", 0)), "有流入但未匹配行业的对公对手数")

    # Top 5 对公行业分布
    for i, (ind_name, ind_flow_val) in enumerate(sorted_ind[:5]):
        if ind_flow_val == 0:
            break
        add(f"行业推断_Top{i+1}行业", ind_name, f"基于对公对手流入加权排名第{i+1}的行业")
        add(f"行业推断_Top{i+1}行业_流入金额", round(float(ind_flow_val), 2), f"该行业对手流入合计")
        pct = float(ind_flow_val / max(total_corp_flow, 1))
        add(f"行业推断_Top{i+1}行业_占比", round(pct, 4), f"该行业流入/对公可识别行业总流入")

    # P2. 源2：一级标签结构 → 生意模式判断
    tag_flow = ext.groupby("一级标签")["收入金额"].sum()
    ops_ratio = float(tag_flow.get("经营类", 0) / max(tag_flow.sum(), 1))
    fin_ratio = float(tag_flow.get("筹资类", 0) / max(tag_flow.sum(), 1))
    inv_ratio = float(tag_flow.get("投资类", 0) / max(tag_flow.sum(), 1))

    # 模式推断
    if ops_ratio >= 0.7:
        biz_model = "经营主导型"
    elif ops_ratio >= 0.4:
        biz_model = "经营混合型"
    elif fin_ratio >= 0.5:
        biz_model = "融资主导型"
    else:
        biz_model = "综合型"

    add("商业模式_类型", biz_model, "基于一级标签收入结构推断：经营主导/经营混合/融资主导/综合型")
    add("商业模式_经营类流入占比", round(float(ops_ratio), 4), "经营类流入/总流入")
    add("商业模式_筹资类流入占比", round(float(fin_ratio), 4), "筹资类流入/总流入")
    add("商业模式_投资类流入占比", round(float(inv_ratio), 4), "投资类流入/总流入")

    # P3. 源3：对手地域 → 销售半径
    locations = []
    for opp_name in ext[ext["对手名称"] != ""]["对手名称"].unique():
        # 提取地名关键词（含省/市/区/县/镇 后缀）
        loc_m = re.findall(r'([\u4e00-\u9fa5]{2,6}(?:省|市|区|县|镇))', str(opp_name))
        locations.extend(loc_m)
        # 提取不含后缀的常见地名
        loc_m2 = re.findall(r'(南昌|深圳|广州|杭州|上海|北京|武汉|成都|重庆|南京|苏州|东莞|佛山|中山|江门|珠海|惠州|厦门|福州|泉州|合肥|郑州|长沙|西安|天津|济南|青岛|沈阳|大连)', str(opp_name))
        locations.extend(loc_m2)

    loc_cnt = {}
    for loc in locations:
        loc_cnt[loc] = loc_cnt.get(loc, 0) + 1

    sorted_loc = sorted(loc_cnt.items(), key=lambda x: x[1], reverse=True)
    top5_locs = [k for k, _ in sorted_loc[:5]]
    prov_kws = [k for k in home_province_kw if k]
    if not sorted_loc:
        local_tag = "无法识别"
    elif home_city and any(home_city in k for k in top5_locs):
        local_tag = "本地"
    elif prov_kws and any(kw in k for kw in prov_kws for k in top5_locs):
        local_tag = "本省"
    elif home_city or prov_kws:
        local_tag = "跨省"
    else:
        local_tag = "未配置经营地基点"
    unique_cities = len(set(re.sub(r'[省市县镇]', '', l) for l in locations))

    add("销售半径_类型", f"{local_tag}（覆盖{unique_cities}个城市）", "基于对手名称中地名关键词的分布推断")
    add("销售半径_覆盖城市数", unique_cities, "对手名称中识别到的不同城市数")
    for i, (loc, cnt) in enumerate(sorted_loc[:5]):
        add(f"销售半径_Top{i+1}城市_名称", loc, f"对手中第{i+1}高频城市")
        add(f"销售半径_Top{i+1}城市_对手数", cnt, f"该城市对手去重数")

    # P4. 源4：电商平台对手识别 → 线上/线下判断
    ecom_kw = "抖音|淘宝|天猫|拼多多|京东|快手|视频号|微信|支付宝|财付通|网银在线|平安银行平台|电子商|新网银行电子"
    ecom_flow = float(ext[ext["对手名称"].str.contains(ecom_kw, na=False)]["收入金额"].sum())
    ecom_cnt = int(ext[ext["对手名称"].str.contains(ecom_kw, na=False)]["对手名称"].nunique())
    ecom_ratio = ecom_flow / max(ext["收入金额"].sum(), 1)
    channel = "线上为主" if ecom_ratio >= 0.3 else ("线上线下混合" if ecom_ratio >= 0.1 else "线下为主")

    add("经营渠道_类型", channel, "基于电商平台对手流入占比推断")
    add("经营渠道_电商平台对手数", ecom_cnt, "电商/支付平台类对手去重数")
    add("经营渠道_电商平台流入占比", round(float(ecom_ratio), 4), "电商平台流入/总对外流入")

    # 客户类型
    if ecom_ratio >= 0.5:
        cust_type = "B2C（线上零售）"
    elif ecom_ratio >= 0.2:
        cust_type = "B2C+小B混合"
    else:
        # 看批发/贸易对手比例
        wholesale = float(ext[ext["对手名称"].str.contains("贸易|批发|供应链", na=False)]["收入金额"].sum())
        wholesale_ratio = wholesale / max(ext["收入金额"].sum(), 1)
        cust_type = "B2B（批发/供应链）" if wholesale_ratio >= 0.3 else "B2B+B2C混合"

    add("客户结构_类型", cust_type, "基于对手类型与电商占比推断")
    add("客户结构_批发贸易对手流入占比", round(float(wholesale / max(ext["收入金额"].sum(), 1)), 4) if 'wholesale' in dir() else None, "批发/贸易/供应链类对手流入占比")

    # P5. 经营品类（采购vs销售 自洽性）
    buy_tags = ext[ext["二级标签"].str.contains("采购|进货|货款") | ext["三级标签"].str.contains("采购")]
    sell_tags = ext[ext["二级标签"].str.contains("销售|收入|回款|货款") | ext["三级标签"].str.contains("销售|收入|回款")]
    add("经营品类_采购支出", round(float(buy_tags["支出金额"].sum()), 2), "采购相关标签支出合计")
    add("经营品类_销售收入", round(float(sell_tags["收入金额"].sum()), 2), "销售相关标签流入合计")
    add("经营品类_购销比", round(float(sell_tags["收入金额"].sum() / max(buy_tags["支出金额"].sum(), 1)), 4), "销售收入/采购支出")

    # ---- O. Debt OS 核心 KPI ----
    op = ext[(ext["一级标签"] == "经营类") & (~ext["二级标签"].str.contains("往来"))]
    # 月债务服务（从流水中识别规律月供）
    svc_total = 0.0
    for k, g in fin_ext.groupby("对手名称"):
        pays = g[(g["支出金额"] > 0) & ((g["摘要"].str.contains(LEASE_HINT)) | (g["三级标签"].str.contains("偿还")) | (g["二级标签"] == "负债"))]
        if len(pays) >= 2 and pays["支出金额"].std() / max(pays["支出金额"].mean(), 1) < 0.1:
            svc_total += float(pays["支出金额"].median())
        elif len(pays) == 1:
            svc_total += float(pays["支出金额"].iloc[0])
    add("月债务服务_识别月供", round(svc_total, 2), "从流水中识别的规律性月供合计")
    add("DSR_还款负担率", round(float(svc_total / max(l12_df["收入金额"].sum() / max(len(L12), 1), 1)), 4), "月债务服务/月均对外流入")

    # 经营净CF
    op_net_l12 = op[op["月份"].isin(L12)]["收入金额"].sum() - op[op["月份"].isin(L12)]["支出金额"].sum()
    add("近12月经营净现金流", round(float(op_net_l12), 2), "近12月经营流入-经营流出（剔往来）")
    dscr = float(op_net_l12 / max(len(L12), 1) / max(svc_total, 1)) if svc_total else None
    add("DSCR_本息覆盖倍数", round(dscr, 4) if dscr is not None else None, "月均经营净CF/月债务服务")

    return vars_list

# ============================================================
# 4. 指标加工层（AF 反欺诈 / MK 营销响应 / LS 信用风险）
#    依据 references/metrics-processing-spec-v1.md
#    口径：行内表字段按附录映射到标准化流水；锚点=数据截止日期；
#    近N月按自然月（距截止月数<=N）；月序列缺月补0（首笔之后）。
# ============================================================
# 特殊值哨兵（§1.6）：表空 / 流水不足6月 / 外部数据缺失（口径不可用）/ 无法复算
S_EMPTY, S_SHORT, S_NOEXT, S_NOCALC = -9999, -9986, -9998, -9997

# 阈值参数（spec §5 参数与阈值配置表）
BIG_NO_MEMO_AMT = 100000    # 大额无摘要金额下限（AF06/LS0009）
NIGHT_BIG_AMT = 50000       # 夜间大额金额下限（AF09）
FAST_IN_AMT = 100000        # 快进快出单日入账下限（AF10）
FAST_OUT_RATIO = 0.8        # 快进快出流出比例
RING_WINDOW_DAYS = 30       # 环形交易回流窗口（AF03）
RING_TOL = 0.10             # 环形交易金额匹配容差
DEMAND_RATE = 0.002         # 活期挂牌利率年化（AF02 理论结息）
GRADE_MIN_AMT = 100         # 分级对手单笔金额下限（T6）
POS_GROWTH_EPS = 100        # 正增长判定阈值（T5）

# spec 标签体系 → 标准化流水谓词映射（spec 附录：标签映射表）
SPEC_TAG_RULES = {
    "经营类":   lambda d: d["一级标签"] == "经营类",
    "投资类":   lambda d: d["一级标签"] == "投资类",
    "融资类":   lambda d: d["一级标签"] == "筹资类",
    "工资与奖金": lambda d: d["二级标签"].str.contains("人力") | d["摘要"].str.contains("工资|薪酬|薪资|奖金|代发"),
    "纳税":     lambda d: d["二级标签"].str.contains("税") | d["对手名称"].str.contains("税务|国库"),
    "水电能源":  lambda d: d["摘要"].str.contains("水费|电费|燃气|煤气") | d["对手名称"].str.contains("供电|自来水|水务|燃气"),
    "诉讼费":   lambda d: d["对手名称"].str.contains("法院|执行局|仲裁") | d["摘要"].str.contains("诉讼|仲裁"),
    "银证转账":  lambda d: d["对手名称"].str.contains("证券") | d["摘要"].str.contains("银证"),
    "结息":     lambda d: d["摘要"].str.contains("结息") | d["三级标签"].str.contains("结息") | d["二级标签"].str.contains("结息"),
    "银行借款":  lambda d: d["对手名称"].str.contains("银行") & ((d["一级标签"] == "筹资类") | d["摘要"].str.contains("贷款|放款|借款|还款")),
    "非银借款":  lambda d: d["对手名称"].str.contains("小额贷款|保理|融资租赁|金融租赁|消费金融|信托|融资担保"),
    "民间借款":  lambda d: d["二级标签"].str.contains("民间借贷"),
}

# T1 合计家族参数表：(编码, 名称, 标签, 方向)
T1_DEFS = [
    ("LS0003", "近12月现金入账总额", None, "in"), ("LS0004", "近12月现金出账总额", None, "out"),
    ("LS0005", "近12月经营类现金入账总额", "经营类", "in"), ("LS0006", "近12月经营类现金出账总额", "经营类", "out"),
    ("LS0007", "近12个月投资类现金出账金额", "投资类", "out"), ("LS0008", "近12个月证券投资类现金出账金额", "银证转账", "out"),
    ("LS0045", "近12个月代发工资总额", "工资与奖金", "out"), ("LS0083", "近12个月融资类入账金额", "融资类", "in"),
    ("LS0085", "银行类融资-贷记加总", "银行借款", "in"), ("LS0130", "银行类融资-借记加总", "银行借款", "out"),
    ("LS0086", "非银类融资-贷记加总", "非银借款", "in"), ("LS0128", "非银类融资-借记加总", "非银借款", "out"),
    ("LS0088", "民间借款-贷记加总", "民间借款", "in"), ("LS0129", "民间借款-借记加总", "民间借款", "out"),
]
# T2 增长率家族参数表：(编码, 名称, 标签, 方向)
T2_DEFS = [
    ("LS0016", "近12月现金入账总额增长率", None, "in"), ("LS0017", "近12月现金出账总额增长率", None, "out"),
    ("LS0018", "近12月经营类现金入账总额增长率", "经营类", "in"), ("LS0019", "近12月经营类现金出账总额增长率", "经营类", "out"),
    ("LS0036", "近12个月水电能源支出增长率", "水电能源", "out"), ("LS0037", "近12个月诉讼仲裁支出增长率", "诉讼费", "out"),
    ("LS0043", "近12个月投资类现金出账金额增长率", "投资类", "out"), ("LS0044", "近12个月证券投资类现金出账金额增长率", "银证转账", "out"),
    ("LS0046", "近12个月代发工资总额增长率", "工资与奖金", "out"), ("LS0084", "近12个月融资类入账金额增长率", "融资类", "in"),
]
# T3 稳定性三件套参数表：(对象名, 标签, 方向, 稳定性编码, 波动率编码, 为0月份数编码)
T3_DEFS = [
    ("全部入账", None, "in", "LS0047", "LS0048", "LS0049"),
    ("全部出账", None, "out", "LS0050", "LS0051", "LS0052"),
    ("经营类入账", "经营类", "in", "LS0053", "LS0054", "LS0055"),
    ("经营类出账", "经营类", "out", "LS0056", "LS0057", "LS0058"),
    ("代发工资", "工资与奖金", "out", "LS0067", "LS0068", "LS0069"),
    ("水电能源支出", "水电能源", "out", "LS0070", "LS0071", "LS0072"),
]
# T5 正增长次数参数表：(编码, 名称, 标签, 方向)
T5_DEFS = [
    ("LS0027", "近12个月每月代发工资正增长次数", "工资与奖金", "out"),
    ("LS0028", "近12个月每月水电能源支出正增长次数", "水电能源", "out"),
    ("LS0029", "近12月逐月环比入账增长数", None, "in"),
    ("LS0030", "近12月逐月环比经营入账正增长数", "经营类", "in"),
    ("LS0031", "近12月逐月环比经营出账正增长数", "经营类", "out"),
]

def load_loans(path):
    """加载借据表（csv/xlsx），按借据号取最后一次报送快照。"""
    L = pd.read_csv(path) if path.lower().endswith(".csv") else pd.read_excel(path)
    for c in ["贷款发放日期", "贷款到期日期", "上报日期"]:
        if c in L.columns:
            L[c] = pd.to_datetime(L[c], errors="coerce")
    for c in ["贷款余额", "欠本金额", "欠息金额", "实际利率", "展期次数"]:
        if c in L.columns:
            L[c] = pd.to_numeric(L[c], errors="coerce").fillna(0)
    if "借据号" in L.columns and "上报日期" in L.columns:
        L = L.sort_values("上报日期").groupby("借据号", as_index=False).tail(1)
    return L

def load_whitelist(path):
    """加载白名单（文本每行一个名称，或 csv/xlsx 取第一列）。"""
    if path.lower().endswith((".csv", ".xlsx", ".xls")):
        w = pd.read_csv(path) if path.lower().endswith(".csv") else pd.read_excel(path)
        names = w.iloc[:, 0].astype(str)
    else:
        with open(path, encoding="utf-8") as f:
            names = [ln.strip() for ln in f]
    return {n.strip() for n in names if str(n).strip()}

def compute_spec_metrics(df, loans=None, whitelist=None, demand_rate=DEMAND_RATE):
    """按 metrics-processing-spec-v1 计算 AF/MK/LS 全量指标。
    返回 [(指标编码, 指标名称, 指标值, 加工逻辑说明)]。全量口径（不剔内部互转，AF03 除外）。"""
    rows = []

    def add(code, name, value, logic):
        if isinstance(value, float):
            value = None if value != value else round(value, 4)
        rows.append((code, name, value, logic))

    if len(df) == 0:
        add("-", "全部指标", S_EMPTY, "流水表为空（§1.6 特殊值序位1）")
        return rows

    d = df
    k = d["距截止月数"]
    lsm = int(k.max()) if k.notna().any() else 0
    short = lsm < 6
    anchor = d["交易时间"].max()
    amt = d["收入金额"] + d["支出金额"]
    is_ent = d["对手性质"] == "对公"
    has_opp = d["对手名称"] != ""
    tags = {name: fn(d) for name, fn in SPEC_TAG_RULES.items()}
    note6 = "；不足6月返回-9986"

    def W(v):
        """窗口依赖指标的不足6月哨兵包装。"""
        return S_SHORT if short else v

    def wmask(n, off=0):
        return (k > off) & (k <= off + n)

    def tsum(tag, dirn, n=12, off=0):
        m = wmask(n, off)
        if tag:
            m &= tags[tag]
        return float(d.loc[m, "收入金额" if dirn == "in" else "支出金额"].sum())

    def mseries(tag, dirn, n=12, off=0):
        """逐月合计数组（时间正序，首笔之后缺月补0）。"""
        hi = min(off + n, lsm)
        ks = list(range(off + 1, hi + 1))
        if not ks:
            return pd.Series(dtype=float)
        m = k.between(off + 1, hi)
        if tag:
            m &= tags[tag]
        col = "收入金额" if dirn == "in" else "支出金额"
        g = d.loc[m].groupby(d.loc[m, "距截止月数"])[col].sum()
        return g.reindex(ks, fill_value=0.0).astype(float).iloc[::-1]

    def stdevp(s):
        return float(s.std(ddof=0)) if len(s) else 0.0

    def growth2(tag, dirn, n, prev_off, prev_n):
        """T2 增长率：近n月 vs 前期窗口（不足月年化折算；前期无覆盖返回-9986）。"""
        cur = tsum(tag, dirn, n, 0)
        avail = max(0, min(prev_off + prev_n, lsm) - prev_off)
        if avail <= 0:
            return S_SHORT
        prev = tsum(tag, dirn, prev_n, prev_off) / avail * prev_n
        return (cur - prev) / (prev + 0.001)

    def dev_ratio(s):
        """T4 离差率 = avg(|x-avg|)/(avg+0.001)。"""
        if not len(s):
            return 0.0
        return float((s - s.mean()).abs().mean()) / (float(s.mean()) + 0.001)

    # ---- CLN 清洗质检 ----
    dupkey = [c for c in ["交易时间", "收入金额", "支出金额", "对手名称", "本方账户", "账户余额"] if c in d.columns]
    add("CLN01", "疑似重复交易笔数", int(d.duplicated(subset=dupkey).sum()),
        f"判重键[{'+'.join(dupkey)}]全等的重复行数（§1.1；标准化阶段已去重，此处仅复核提示，不删除）")

    # ---- AF 反欺诈 ----
    has_bal = "账户余额" in d.columns and d["账户余额"].notna().any()
    # AF01 余额一致性（原币/人民币口径按输入；按账户时间序逐笔勾稽）
    if has_bal:
        bp = 0
        t = d[d["账户余额"].notna()].sort_values("交易时间", kind="stable")
        for _, g in t.groupby("本方账户"):
            exp = g["账户余额"].shift(1) + g["收入金额"] - g["支出金额"]
            bp += int(((g["账户余额"] - exp).abs() > 0.01).iloc[1:].sum())
        add("AF01", "余额一致性验证-不匹配记录计数", bp,
            "按(本方账户)分组、组内按交易时间排序，|余额-（上笔余额+收入-支出）|>0.01 的笔数（§2 AF01，只标注不修正）")
    else:
        add("AF01", "余额一致性验证-不匹配记录计数", S_NOEXT, "无账户余额字段，口径不可用（-9998）")

    # AF02 季度结息复算
    if has_bal:
        daily = daily_balance_series(d, "账户余额")
        ratios, miss_q = [], 0
        ap = anchor.to_period("M")
        qs = [p for p in [ap - i for i in range(0, 15)] if p.month in (3, 6, 9, 12)][:4]
        for q in qs:
            span = pd.period_range(q - 2, q, freq="M")
            act = float(d.loc[tags["结息"] & (d["收入金额"] > 0) &
                              (d["交易时间"].dt.to_period("M") == q), "收入金额"].sum())
            db = daily[[ts for ts in daily.index if ts.to_period("M") in set(span)]] if len(daily) else daily
            if len(db) < 80:  # 季度余额覆盖不足不复算
                continue
            theo = float(db.sum()) * demand_rate / 360
            if act > 0:
                ratios.append(abs(act - theo) / (theo + 0.001))
            else:
                miss_q += 1
        add("AF02", "季度实际结息与理论结息的差异", W(max(ratios) if ratios else S_NOCALC),
            f"近4个结息季 |实际结息-Σ日余额×{demand_rate}/360|/理论 的最大值；无结息记录返回-9997（§2 AF02）")
        add("AF02b", "结息缺失季度数", W(miss_q), "余额覆盖完整但无结息入账的季度数（结息持续缺失本身是造假信号）")
    else:
        add("AF02", "季度实际结息与理论结息的差异", S_NOEXT, "无账户余额字段，无法复算理论结息（-9998）")
        add("AF02b", "结息缺失季度数", S_NOEXT, "同上")

    # AF03 环形交易金额占比（对外口径，30天窗口双向配对，金额容差10%）
    ring_amt = 0.0
    dd = d[~d["内部互转"] & wmask(12) & has_opp]
    for _, g in dd.groupby("对手名称"):
        outs = g[g["支出金额"] > 0][["交易时间", "支出金额"]].values.tolist()
        ins = g[g["收入金额"] > 0][["交易时间", "收入金额"]].values.tolist()
        if not outs or not ins:
            continue
        outs.sort(); ins.sort()
        used_i, used_o = [False] * len(ins), [False] * len(outs)
        for oi, (t0, a0) in enumerate(outs):        # 出→入回流
            for j, (t1, a1) in enumerate(ins):
                if used_i[j] or t1 < t0:
                    continue
                if (t1 - t0).days > RING_WINDOW_DAYS:
                    break
                if abs(a1 - a0) / max(a1, a0) <= RING_TOL:
                    ring_amt += a0 + a1; used_i[j] = True; used_o[oi] = True; break
        for j, (t1, a1) in enumerate(ins):          # 入→出过桥（未配对部分）
            if used_i[j]:
                continue
            for oi, (t0, a0) in enumerate(outs):
                if used_o[oi] or t0 < t1:
                    continue
                if (t0 - t1).days > RING_WINDOW_DAYS:
                    break
                if abs(a1 - a0) / max(a1, a0) <= RING_TOL:
                    ring_amt += a0 + a1; used_o[oi] = True; break
    add("AF03", "环形交易的金额占比", W(ring_amt / (float(amt[wmask(12)].sum()) + 0.001)),
        f"同一对手{RING_WINDOW_DAYS}天内双向金额匹配（容差{RING_TOL:.0%}）的两侧金额合计/近12月总交易金额（§2 AF03 两跳实现）" + note6)

    # AF04 关联体内部交易金额占比（关联体=流水可见同名/关联主体；工商关联需外部数据扩充）
    add("AF04", "关联体内部交易金额占比", W(float(amt[d["内部互转"] & wmask(12)].sum()) / (float(amt[wmask(12)].sum()) + 0.001)),
        "内部互转（对手∈主体名称∪本方名称，先并集后剔空串）收支合计/近12月总交易金额（§2 AF04）" + note6)

    # AF05 同名账户缺失个数（缺失账户反推）
    if "对手账户" in d.columns and (d["对手账户"].astype(str).str.strip() != "").any():
        same = d[d["内部互转"] & (d["对手账户"].astype(str).str.strip() != "")]
        vis = set(same["对手账户"].astype(str)) - {""}
        missing = vis - set(d["本方账户"].astype(str))
        add("AF05", "同名账户缺失个数", len(missing), "同名/关联划转对手账号去重 − 已提交流水账号集合（§2 AF05）")
        add("AF05b", "缺失账户关联交易金额", round(float(amt[d["对手账户"].astype(str).isin(missing)].sum()), 4),
            "与缺失账户发生的交易金额合计（隐藏账户资金体量）")
    else:
        add("AF05", "同名账户缺失个数", S_NOEXT, "无对手账户字段或全空，无法反推（-9998）")

    # AF06-AF10 数据质量类
    memo_blank = d["摘要"].str.strip() == ""
    big_nomemo = wmask(12) & memo_blank & (amt > BIG_NO_MEMO_AMT)
    add("AF06", "近12个月大额交易无交易摘要笔数", W(int(big_nomemo.sum())),
        f"摘要与附言均空且交易金额>{BIG_NO_MEMO_AMT}（=LS0009 互见）" + note6)
    add("AF06b", "近12个月大额交易无交易摘要总额", W(float(amt[big_nomemo].sum())),
        "同上口径的交易金额合计（=LS0010 互见；原文'入账金额'按交易金额合计实施并注记）" + note6)
    add("AF07", "近12个月流水摘要缺失率", W(float(memo_blank[wmask(12)].mean()) if wmask(12).any() else 0.0),
        "无摘要且无附言笔数/近12月总笔数（=LS0131 互见）" + note6)
    cents = (amt[wmask(12) & (amt >= 10000)] * 100).round().astype("int64")
    add("AF08", "大额整数交易占比", W(float((cents % 1000000 == 0).mean()) if len(cents) else 0.0),
        "近12月金额≥1万交易中整万倍数占比（分单位整数判断，规避浮点取模，勘误后口径）" + note6)
    has_time = d["交易时间"].dt.strftime("%H:%M:%S") != "00:00:00"
    night_big = wmask(12) & has_time & ((d["交易时间"].dt.hour >= 21) | (d["交易时间"].dt.hour < 6)) & (amt > NIGHT_BIG_AMT)
    add("AF09", "夜间大额交易笔数", W(int(night_big.sum())),
        f"21:00-06:00 且金额>{NIGHT_BIG_AMT}，仅统计有时点记录" + note6)
    d12 = d[wmask(12)]
    if len(d12):
        days = d12["交易时间"].dt.normalize()
        cal = pd.date_range(days.min(), days.max(), freq="D")
        di = d12.groupby(days)["收入金额"].sum().reindex(cal, fill_value=0.0)
        do = d12.groupby(days)["支出金额"].sum().reindex(cal, fill_value=0.0)
        big = di >= FAST_IN_AMT
        fast = float(((do[big] + do.shift(-1).fillna(0)[big]) >= FAST_OUT_RATIO * di[big]).mean()) if big.any() else 0.0
    else:
        fast = 0.0
    add("AF10", "快进快出天数占比", W(fast),
        f"单日入账≥{FAST_IN_AMT}且当日+次日历日出账≥入账{FAST_OUT_RATIO:.0%}的天数占比（日历日T+1，勘误后口径）" + note6)

    # ---- LS 覆盖与活跃 ----
    add("LS0001", "最近一笔交易距今天数", 0, "锚点−最近交易日期；标准化流水锚点=数据截止日期，故恒为0（行内实施以上报日期为锚）")
    add("LS0002", "最远一笔交易距今天数", int((anchor - d["交易时间"].min()).days), "锚点−最早交易日期")
    add("lstime", "流水月份数", lsm, "首笔交易月至锚点月的自然月数（§1.5）")
    w12_start = max((anchor.to_period("M") - 11).start_time, d["交易时间"].min().normalize())
    total_days12 = (anchor.normalize() - w12_start).days + 1
    add("LS0023", "近12月均交易活跃度", W(d.loc[wmask(12), "交易时间"].dt.date.nunique() / max(total_days12, 1)),
        "有效交易天数/窗口总天数（窗口起点取max(窗口起点,首笔交易日)，勘误E11）" + note6)
    ks12 = list(range(1, min(12, lsm) + 1))
    mact = d.loc[wmask(12)].groupby(d.loc[wmask(12), "距截止月数"])["交易时间"].apply(lambda s: s.dt.date.nunique()).reindex(ks12, fill_value=0)
    dim = pd.Series([(anchor.to_period("M") - (kk - 1)).days_in_month for kk in ks12], index=ks12)
    add("LS0024", "近12月均交易活跃波动率", W(dev_ratio(mact / dim)), "每月活跃度（有效天数/当月天数）数组的离差率（T4）" + note6)
    w6 = pd.Series(d.loc[wmask(6), "交易时间"].dt.normalize().sort_values().unique())
    if len(w6):
        gaps = list((w6.diff().dt.days - 1).dropna()) + [(anchor.normalize() - w6.iloc[-1]).days]
        add("LS0033", "近6月最长连续无交易天数", W(int(max(gaps + [0]))), "近6月相邻交易日最大间隔−1（含末笔至锚点）" + note6)
    else:
        add("LS0033", "近6月最长连续无交易天数", W(0), "近6月无交易记录")
    add("LS0076", "对公存款账户数", int(d["本方账户"].nunique()), "本方账户去重计数")
    add("LS0077", "近3个月活跃账户数", W(int(d.loc[wmask(3), "本方账户"].nunique())), "近3月有交易记录的账户数" + note6)
    add("LS0125", "流水中是否含有基本户", S_NOEXT, "需对公客户信息表（基本存款账户），外部数据缺失（-9998）")

    # ---- LS 规模总量（T1）----
    for code, name, tag, dirn in T1_DEFS:
        add(code, name, W(tsum(tag, dirn)), f"T1合计：近12月[{tag or '全部'}]×[{'贷' if dirn == 'in' else '借'}]" + note6)
    add("LS0087", "非银融资-机构数", W(int(d.loc[wmask(12) & tags["非银借款"] & has_opp, "对手名称"].nunique())), "近12月非银借款标签对手去重数" + note6)
    add("LS0089", "民间借款-对手数", W(int(d.loc[wmask(12) & tags["民间借款"] & has_opp, "对手名称"].nunique())), "近12月民间借款标签对手去重数" + note6)
    add("LS0009", "近12个月大额交易无交易摘要笔数", W(int(big_nomemo.sum())), "=AF06 互见")
    add("LS0010", "近12个月大额交易无交易摘要总额", W(float(amt[big_nomemo].sum())), "=AF06b 互见")
    add("隐债总额", "近12个月隐债总额（民间借贷+非银借贷）", W(tsum("非银借款", "out") + tsum("民间借款", "out")), "LS0128+LS0129" + note6)
    if "币种" in d.columns:
        fx = ~d["币种"].astype(str).str.upper().isin(["", "CNY", "RMB", "人民币", "NAN"])
        add("LS0126", "近12个月外币收入占比", W(float(d.loc[wmask(12) & fx, "收入金额"].sum()) / (tsum(None, "in") + 0.001)), "外币入账/总入账（按原币种字段判定）" + note6)
    else:
        add("LS0126", "近12个月外币收入占比", S_NOEXT, "无币种字段（-9998）")
    for code, name, tag in [("年均收入", "近24个月年均收入", None), ("年均支出", "近24个月年均支出", None), ("年均有效收入", "近24个月年均有效收入", "经营类")]:
        dirn = "out" if "支出" in name else "in"
        add(code, name, W(tsum(tag, dirn, 24) / max(min(lsm, 24), 1) * 12), "近24月合计/min(流水月份数,24)×12（勘误E12：月均×12统一折算）" + note6)

    # ---- LS 结构占比 ----
    add("LS0011", "近12月经营类入账占比", W(tsum("经营类", "in") / (tsum(None, "in") + 0.001)), "经营入账/总入账" + note6)
    add("LS0012", "近12月经营类出账占比", W(tsum("经营类", "out") / (tsum(None, "out") + 0.001)), "经营出账/总出账" + note6)
    add("LS0013", "近12月经营类现金速动率", W(tsum("经营类", "in") / (tsum("经营类", "out") + 0.001)), "经营入账/经营出账" + note6)
    for dirn, g_code, s_code, v_code, z_code in [("in", "LS0059", "LS0060", "LS0061", "LS0062"), ("out", "LS0063", "LS0064", "LS0065", "LS0066")]:
        lab = "入账" if dirn == "in" else "出账"
        cur = tsum("经营类", dirn) / (tsum(None, dirn) + 0.001)
        avail = max(0, min(24, lsm) - 12)
        gv = S_SHORT if avail <= 0 else (cur - tsum("经营类", dirn, 12, 12) / (tsum(None, dirn, 12, 12) + 0.001)) / (tsum("经营类", dirn, 12, 12) / (tsum(None, dirn, 12, 12) + 0.001) + 0.001)
        add(g_code, f"近12月经营类{lab}占比增长率", W(gv), "近12月期间占比 vs 近13-24月期间占比（前期无覆盖返回-9986）" + note6)
        a, b = mseries("经营类", dirn), mseries(None, dirn)
        r = (a / b.replace(0, np.nan)).fillna(0.0)
        add(s_code, f"近12月经营类{lab}占比稳定性", W(stdevp(r) / (float(r.mean()) + 0.001)), "月占比数组 stdevp/avg（0/0月记0，勘误E14）" + note6)
        add(v_code, f"近12月经营类{lab}占比波动率", W(stdevp(r)), "月占比数组 stdevp" + note6)
        add(z_code, f"近12月经营类{lab}占比为0月份数", W(int((r == 0).sum())), "月占比=0的月份数" + note6)

    # ---- LS 增长趋势（T2/T5）----
    for code, name, tag, dirn in T2_DEFS:
        add(code, name, W(growth2(tag, dirn, 12, 12, 12)), f"T2增长率：近12月 vs 近13-24月[{tag or '全部'}]×[{'贷' if dirn == 'in' else '借'}]（前期不足年化折算，勘误E1/E5）" + note6)
    for code, name, tag, dirn in T5_DEFS:
        s = mseries(tag, dirn)
        add(code, name, W(int(((s - s.shift(1)) > POS_GROWTH_EPS).sum())), f"T5：逐月合计滚动计数 当月−上月>{POS_GROWTH_EPS}" + note6)
    s32 = mseries("经营类", "in")
    if len(s32) >= 2 and float(s32.max()) > float(s32.min()):
        norm = (s32 - s32.min()) / (s32.max() - s32.min())
        v32 = float(np.dot(norm.values, np.linspace(-1, 1, len(s32))))
    else:
        v32 = 0.0
    add("LS0032", "近12月经营类入账增长趋势", W(v32), "月入账数组minmax归一化·linspace(-1,1,月数)点积（勘误E6：模板取12等分点）" + note6)
    lit = wmask(12) & tags["诉讼费"] & (d["支出金额"] > 0)
    add("LS0022", "近12个月诉讼仲裁支出平均金额", W(float(d.loc[lit, "支出金额"].sum()) / (int(lit.sum()) + 0.001)),
        "近12月诉讼费借方合计/笔数（原文窗口近24月与名称冲突，按名称近12月实施，勘误E4待业务确认）" + note6)

    # ---- LS 稳定性（T3/T4）----
    for lab, tag, dirn, c_st, c_vo, c_ze in T3_DEFS:
        s = mseries(tag, dirn)
        add(c_st, f"近12月{lab}稳定性", W(stdevp(s) / (float(s.mean()) + 0.001)), f"T3：[{lab}]月合计 stdevp/avg（LS0053-58段方向按勘误E3实施）" + note6)
        add(c_vo, f"近12月{lab}波动率", W(stdevp(s)), f"T3：[{lab}]月合计 stdevp" + note6)
        add(c_ze, f"近12月{lab}为0月份数", W(int((s == 0).sum())), f"T3：[{lab}]月合计=0的月份数" + note6)
    add("LS0025", "近12月经营类入账离差率", W(dev_ratio(mseries("经营类", "in"))), "T4离差率" + note6)
    add("LS0026", "近12月经营类出账离差率", W(dev_ratio(mseries("经营类", "out"))), "T4离差率" + note6)
    sal = d[tags["工资与奖金"] & (d["支出金额"] > 0) & wmask(12)]
    if len(sal):
        wk = (sal["交易时间"].dt.day - 1) // 7 + 1
        pw = sal.groupby([sal["距截止月数"], wk])["支出金额"].sum()
        payweek = pw.groupby(level=0).idxmax().map(lambda t: t[1]).astype(float)
        add("LS0081", "近12个月发薪日稳定性", W(stdevp(payweek) / (float(payweek.mean()) + 0.001)),
            "每月发薪周序号（金额最大周，周=ceil(日/7)）的 stdevp/avg，缺月剔除（勘误E7）" + note6)
        add("LS0082", "近12个月发薪日波动率", W(stdevp(payweek)), "同上，stdevp" + note6)
    else:
        add("LS0081", "近12个月发薪日稳定性", W(0), "近12月无工资类出账")
        add("LS0082", "近12个月发薪日波动率", W(0), "同上")

    # ---- LS 交易对手 ----
    def cp_amt(dirn, off=0, ent_only=True):
        m = wmask(12, off) & tags["经营类"] & has_opp & (d["收入金额" if dirn == "in" else "支出金额"] > 0)
        if ent_only:
            m &= is_ent
        return d.loc[m].groupby("对手名称")["收入金额" if dirn == "in" else "支出金额"].sum().sort_values(ascending=False)

    cin, cout = cp_amt("in"), cp_amt("out")
    add("LS0014", "近12月经营类入账交易对手数", W(len(cin)), "经营类×贷×企业对手去重数" + note6)
    add("LS0015", "近12月经营类出账交易对手数", W(len(cout)), "经营类×借×企业对手去重数" + note6)
    add("LS0020", "TOP10交易对手入账金额", W(float(cin.head(10).sum())), "经营类企业对手入账倒序前10合计" + note6)
    add("LS0021", "近12个月TOP10现金入账集中度", W(float(cin.head(10).sum()) / (tsum("经营类", "in") + 0.001)),
        "TOP10入账/近12月经营类入账总额（勘误E2：分母改为同方向总额）" + note6)
    add("LS0090", "近12个月TOP10出账金额", W(float(cout.head(10).sum())), "经营类企业对手出账倒序前10合计" + note6)
    add("LS0091", "近12个月TOP10现金出账集中度", W(float(cout.head(10).sum()) / (tsum("经营类", "out") + 0.001)), "TOP10出账/经营类出账总额" + note6)
    add("TO3", "近12个月TOP3现金入账集中度", W(float(cin.head(3).sum()) / (tsum("经营类", "in") + 0.001)), "TOP3入账/经营类入账总额（勘误E2同步）" + note6)
    avail_prev = max(0, min(24, lsm) - 12)
    for code, name, dirn in [("LS0073", "近12个月经营类出账交易对手增长率", "out"), ("LS0074", "近12个月经营类入账交易对手增长率", "in")]:
        if avail_prev <= 0:
            add(code, name, S_SHORT, "前期窗口无覆盖（-9986）")
        else:
            prev = len(cp_amt(dirn, off=12))
            cur = len(cin if dirn == "in" else cout)
            add(code, name, W((cur - prev) / (prev + 0.001)), "近12月对手数 vs 近13-24月对手数" + note6)
    if whitelist:
        top10 = cin.head(10)
        hits = [n for n in top10.index if n in whitelist]
        add("LS0075", "近12个月经营类入账TOP10交易对手白名单率", W(len(hits) / max(len(top10), 1)), "TOP10中白名单户数占比" + note6)
        add("LS0079", "近12个月经营类入账TOP10交易对手白名单金额占比", W(float(top10.loc[hits].sum()) / (float(top10.sum()) + 0.001)), "TOP10中白名单对手金额占比" + note6)
        wl_in = d[wmask(12) & tags["经营类"] & (d["收入金额"] > 0) & d["对手名称"].isin(whitelist)]["收入金额"].sum()
        add("LS0080", "近12个月经营类入账白名单入账金额占比", W(float(wl_in) / (tsum("经营类", "in") + 0.001)), "白名单对手经营入账/全部经营入账" + note6)
    else:
        for code, name in [("LS0075", "近12个月经营类入账TOP10交易对手白名单率"), ("LS0079", "近12个月经营类入账TOP10交易对手白名单金额占比"), ("LS0080", "近12个月经营类入账白名单入账金额占比")]:
            add(code, name, S_NOEXT, "未提供白名单（--whitelist），外部数据缺失（-9998）")

    # T6 分级对手（三级≥3月 / 二级≥6月 / 一级≥9月）
    def graded(dirn, off=0):
        m = wmask(12, off) & tags["经营类"] & is_ent & (amt > GRADE_MIN_AMT) & has_opp & (d["收入金额" if dirn == "in" else "支出金额"] > 0)
        cnt = d.loc[m].groupby("对手名称")["距截止月数"].nunique()
        return [int((cnt >= t).sum()) for t in (3, 6, 9)]

    for dirn, codes, gcodes in [("in", ["LS0096_1", "LS0097_1", "LS0098_1"], ["LS0108_1", "LS0109_1", "LS0110_1"]),
                                ("out", ["LS0099_1", "LS0100_1", "LS0101_1"], ["LS0111_1", "LS0112_1", "LS0113_1"])]:
        lab = "入账" if dirn == "in" else "出账"
        cur3 = graded(dirn)
        prev3 = graded(dirn, off=12) if avail_prev > 0 else None
        for i, lvl in enumerate(["三级", "二级", "一级"]):
            add(codes[i], f"近12个月{lvl}以上{lab}交易对手数量", W(cur3[i]),
                f"T6：经营类×企业×金额>{GRADE_MIN_AMT}×{lab}，交易月份数≥{(3, 6, 9)[i]}的对手数" + note6)
        for i, lvl in enumerate(["三级", "二级", "一级"]):
            gv = S_SHORT if prev3 is None else (cur3[i] - prev3[i]) / (prev3[i] + 0.001)
            add(gcodes[i], f"近12个月{lvl}以上{lab}交易对手增长率", W(gv), "T6+T2：近12月 vs 近13-24月分级对手数" + note6)

    # ---- LS 余额家族 ----
    if has_bal:
        daily = daily_balance_series(d, "账户余额")
        d12b = daily[(daily.index >= w12_start) & (daily.index <= anchor.normalize())]
        avg12 = float(d12b.mean()) if len(d12b) else 0.0
        add("近12月日均余额", "近12月日均余额", W(avg12), "组合日余额（按账户日末ffill求和）近12月均值" + note6)
        r12 = float((d12b > avg12).mean()) if len(d12b) else 0.0
        add("近12月日均余额覆盖率", "近12个月日均余额覆盖率", W(r12), "每日余额>近12月日均余额的天数占比" + note6)
        w3_start = max((anchor.to_period("M") - 2).start_time, daily.index.min()) if len(daily) else anchor
        d3b = daily[(daily.index >= w3_start) & (daily.index <= anchor.normalize())]
        r3 = float((d3b > float(d3b.mean())).mean()) if len(d3b) else 0.0
        add("余额覆盖率变化幅度", "近期余额覆盖率变化幅度", W(r3 - r12), "近3月覆盖率−近12月覆盖率（各自窗口内计算）" + note6)
        add("余额覆盖率变化幅度_绝对值", "近期余额覆盖率变化幅度（绝对值）", W(abs(r3 - r12)), "同上取绝对值" + note6)
        mb = d12b.groupby(d12b.index.to_period("M")).mean()
        add("月日均余额离差率", "近12个月月日均余额离差率", W(dev_ratio(mb)), "月日均余额数组T4离差率" + note6)
        add("月日均余额正增长数", "近12个月逐月日均余额正增长数", W(int(((mb - mb.shift(1)) > POS_GROWTH_EPS).sum())), f"T5：当月−上月>{POS_GROWTH_EPS}" + note6)
        add("对公账户流入净额", "对公账户流入净额", W(float(d12b.iloc[-1] - d12b.iloc[0]) if len(d12b) else 0.0),
            "近12月组合日余额末值−首值（勘误E13：多账户先合并再取首末）" + note6)
    else:
        for code, name in [("近12月日均余额", "近12月日均余额"), ("近12月日均余额覆盖率", "近12个月日均余额覆盖率"),
                           ("余额覆盖率变化幅度", "近期余额覆盖率变化幅度"), ("余额覆盖率变化幅度_绝对值", "近期余额覆盖率变化幅度（绝对值）"),
                           ("月日均余额离差率", "近12个月月日均余额离差率"), ("月日均余额正增长数", "近12个月逐月日均余额正增长数"),
                           ("对公账户流入净额", "对公账户流入净额")]:
            add(code, name, S_NOEXT, "无账户余额字段（-9998）")

    add("LS0131", "近12个月流水摘要缺失率", W(float(memo_blank[wmask(12)].mean()) if wmask(12).any() else 0.0), "=AF07 互见")

    # ---- MK 营销响应 ----
    add("MK01", "近12月经营类入账金额/账户日均余额",
        W(tsum("经营类", "in") / (avg12 + 0.001)) if has_bal else S_NOEXT,
        "经营入账合计/近12月日均余额（资金周转倍数）" + note6)
    for code, name, n, prev_off, prev_n in [("MK02", "近3个月经营流水出账增幅（环比）", 3, 3, 3),
                                            ("MK03", "近3个月经营流水出账增幅（同比）", 3, 12, 3),
                                            ("MK04", "近6个月经营流水出账增幅（环比）", 6, 6, 6),
                                            ("MK05", "近6个月经营流水出账增幅（同比）", 6, 12, 6),
                                            ("MK06", "近12个月经营流水出账增幅", 12, 12, 12)]:
        add(code, name, W(growth2("经营类", "out", n, prev_off, prev_n)),
            f"近{n}月经营出账 vs 前期窗口经营出账（勘误E1：前后期同为出账方向）" + note6)
    add("MK07", "近12个月月均薪资支出金额", W(float(mseries("工资与奖金", "out").mean()) if lsm else 0.0), "逐月合计（缺月补0）均值" + note6)
    add("MK08", "近12个月月均缴税支出金额", W(float(mseries("纳税", "out").mean()) if lsm else 0.0), "逐月合计（缺月补0）均值" + note6)
    if has_bal:
        dm = daily.groupby(daily.index.to_period("M")).mean()
        ap = anchor.to_period("M")
        def mb_avg(n, off=0):
            vals = [float(dm[ap - i]) for i in range(off, off + n) if (ap - i) in dm.index]
            return (sum(vals) / len(vals), len(vals)) if vals else (0.0, 0)
        for code, name, n in [("MK10", "月均账户余额近3个月环比增长率", 3), ("MK11", "月均账户余额近6个月环比增长率", 6)]:
            cur, _ = mb_avg(n)
            prev, av = mb_avg(n, n)
            add(code, name, W((cur - prev) / (prev + 0.001)) if av else S_SHORT, f"近{n}月月均日均余额 vs 前{n}月" + note6)
    else:
        add("MK10", "月均账户余额近3个月环比增长率", S_NOEXT, "无账户余额字段（-9998）")
        add("MK11", "月均账户余额近6个月环比增长率", S_NOEXT, "无账户余额字段（-9998）")
    add("MK13", "近6个月互联网银行交易次数", W(int((wmask(6) & d["对手名称"].str.contains("微众|网商|新网|金城")).sum())),
        "对手含微众/网商/新网/金城的笔数（机构名单可配置）" + note6)

    # ---- 借据类（loans 表；未提供返回-9998，表空按 spec 返回0/-9999）----
    if loans is None:
        for code, name in [("MK09", "近12个月月均贷款还款金额/月均入账金额"), ("MK12", "近6个月贷款机构净增个数"),
                           ("MK14", "已有未结清贷款距离到期的最短天数"), ("MK15", "近24个月放款的最高利率"),
                           ("当前逾期借据数", "当前逾期借据数"), ("当前逾期金额", "当前逾期金额"),
                           ("未结清贷款余额", "未结清贷款余额"), ("未结清短期贷款余额", "未结清短期贷款余额"),
                           ("近12月展期借据数", "近12个月发生展期借据数(不含逻辑判断)"), ("近12月不良发生数", "近12个月累计发生次级可疑损失以及核销数"),
                           ("近24月逾期期数", "近24个月累计发生逾期期数")]:
            add(code, name, S_NOEXT, "未提供借据表（--loans），外部数据缺失（-9998）")
    else:
        L = loans
        bal_l = L["贷款余额"] if "贷款余额" in L.columns else pd.Series(0.0, index=L.index)
        owe = (L["欠本金额"] if "欠本金额" in L.columns else 0) + (L["欠息金额"] if "欠息金额" in L.columns else 0)
        owe = pd.Series(owe, index=L.index) if not isinstance(owe, pd.Series) else owe
        grade = L["五级分类"].astype(str) if "五级分类" in L.columns else pd.Series("", index=L.index)
        kind = L["贷款种类"].astype(str) if "贷款种类" in L.columns else pd.Series("", index=L.index)
        not_excl = ~kind.str.contains("贴现|信用凭证|福费廷")
        overdue = (owe > 0) | (grade.isin(["关注", "次级", "可疑", "损失"]) & (bal_l > 0))
        add("当前逾期借据数", "当前逾期借据数", int(overdue.sum()), "欠本+欠息>0 或 五级分类关注级以下且余额>0（最后报送快照）")
        add("当前逾期金额", "当前逾期金额", round(float(owe.sum()), 4), "欠本金额+欠息金额合计")
        add("未结清贷款余额", "未结清贷款余额", round(float(bal_l[not_excl].sum()), 4), "余额合计（剔除贴现/信用凭证/福费廷）")
        if {"贷款发放日期", "贷款到期日期"} <= set(L.columns):
            term = (L["贷款到期日期"].dt.to_period("M").astype("int64") - L["贷款发放日期"].dt.to_period("M").astype("int64"))
            add("未结清短期贷款余额", "未结清短期贷款余额", round(float(bal_l[not_excl & (term <= 12)].sum()), 4), "期限≤12个月的余额合计（同剔除）")
            due_days = (L["贷款到期日期"] - anchor.normalize()).dt.days
            future = due_days[due_days >= 0]
            add("MK14", "已有未结清贷款距离到期的最短天数", int(future.min()) if len(future) else S_EMPTY, "min(到期日−锚点)；无未到期借据返回-9999")
            due12 = (due_days >= 0) & (due_days <= 365)
            elapsed = anchor.to_period("M").ordinal - L["贷款发放日期"].dt.to_period("M").astype("int64")
            rem_m = np.maximum(term - elapsed, 1)
            repay12 = float(bal_l[due12].sum()) + float((bal_l[~due12 & (due_days > 365)] / rem_m[~due12 & (due_days > 365)] * 12).sum())
            mk09 = (repay12 / 12) / (float(mseries(None, "in").mean()) + 0.001) if lsm else S_SHORT
            add("MK09", "近12个月月均贷款还款金额/月均入账金额", W(mk09),
                "（未来12月到期余额+未到期按剩余期数折算的未来12月应还本金）/12 ÷ 月均入账（勘误E10重写式）" + note6)
        else:
            for code, name in [("未结清短期贷款余额", "未结清短期贷款余额"), ("MK14", "已有未结清贷款距离到期的最短天数"), ("MK09", "近12个月月均贷款还款金额/月均入账金额")]:
                add(code, name, S_NOEXT, "借据表缺少发放/到期日期字段（-9998）")
        if "机构" in L.columns and "贷款发放日期" in L.columns:
            recent = set(L.loc[L["贷款发放日期"] >= anchor - pd.DateOffset(months=6), "机构"].dropna().astype(str))
            older = set(L.loc[L["贷款发放日期"] < anchor - pd.DateOffset(months=6), "机构"].dropna().astype(str))
            add("MK12", "近6个月贷款机构净增个数", len(recent - older), "近6月放款机构中不在此前放款机构集合内的个数")
        else:
            add("MK12", "近6个月贷款机构净增个数", S_NOEXT, "借据表缺少机构/发放日期字段（-9998）")
        if "实际利率" in L.columns and "贷款发放日期" in L.columns:
            m24 = (anchor - L["贷款发放日期"]).dt.days <= 730
            r = L.loc[m24 & not_excl, "实际利率"]
            add("MK15", "近24个月放款的最高利率", round(float(r.max()), 4) if len(r) else 0, "放款距锚点≤730天的实际利率最大值（同剔除）；无命中返回0")
        else:
            add("MK15", "近24个月放款的最高利率", S_NOEXT, "借据表缺少利率/发放日期字段（-9998）")
        ext_cnt = 0
        if "展期次数" in L.columns:
            ext_cnt += int((L["展期次数"] > 0).sum())
        if "借据发放类型" in L.columns:
            ext_cnt += int(L["借据发放类型"].astype(str).str.contains("延期|展期|重组").sum())
        add("近12月展期借据数", "近12个月发生展期借据数(不含逻辑判断)", ext_cnt, "展期次数>0 或 发放类型含延期/展期/重组（简版口径）")
        bad = grade.isin(["次级", "可疑", "损失"]) | (L["贷款状态"].astype(str).str.contains("核销") if "贷款状态" in L.columns else False)
        add("近12月不良发生数", "近12个月累计发生次级可疑损失以及核销数", int(pd.Series(bad).sum()), "五级分类次级/可疑/损失 或 状态核销的借据数")
        add("近24月逾期期数", "近24个月累计发生逾期期数", int((grade.isin(["关注", "次级", "可疑", "损失"]) | (owe > 0)).sum()), "关注级以下 或 欠本+欠息>0 的借据数")
    add("客户所处行业", "客户所处行业", S_NOEXT, "需对公客户信息表/借据行业字段，外部数据缺失（-9998）")

    return rows

# ============================================================
# 3. 输出
# ============================================================
def output_vars(vars_list, out_dir, client, spec_list=None):
    df_vars = pd.DataFrame(vars_list, columns=["变量名", "变量值", "逻辑说明"])
    out_xlsx = os.path.join(out_dir, f"{client}__变量数据集.xlsx")
    with pd.ExcelWriter(out_xlsx) as writer:
        df_vars.to_excel(writer, index=False, sheet_name="变量数据集")
        if spec_list:
            pd.DataFrame(spec_list, columns=["指标编码", "指标名称", "指标值", "加工逻辑说明"]).to_excel(
                writer, index=False, sheet_name="指标数据集")
    n_spec = len(spec_list) if spec_list else 0
    print(f"[OK] 变量数据集: {out_xlsx} ({len(df_vars)} 个变量 + {n_spec} 个AF/MK/LS指标)")
    return out_xlsx

# ============================================================
# Main
# ============================================================
def main():
    ap = argparse.ArgumentParser(description="银行流水变量数据集生成器")
    ap.add_argument("--input", required=True, help="标准化流水文件路径")
    ap.add_argument("--client", default="客户", help="客户名称")
    ap.add_argument("--out-dir", default="", help="输出目录")
    ap.add_argument("--home-city", default="", help="客户经营地城市，用于销售半径本地判断（如：南昌）")
    ap.add_argument("--home-province-kw", default="", help="本省地名关键词，逗号分隔，用于销售半径本省判断（如：江西,景德镇,崇仁）")
    ap.add_argument("--loans", default="", help="借据表文件路径（csv/xlsx），启用 MK09/MK12/MK14/MK15 及借据类指标")
    ap.add_argument("--whitelist", default="", help="白名单文件路径（每行一个名称，或 csv/xlsx 第一列），启用 LS0075/0079/0080")
    ap.add_argument("--demand-rate", type=float, default=DEMAND_RATE, help=f"活期挂牌利率年化，用于 AF02 结息复算（默认 {DEMAND_RATE}）")
    args = ap.parse_args()

    df = load_data(args.input)
    df, max_date = prep(df)
    home_kws = [s.strip() for s in args.home_province_kw.split(",") if s.strip()]
    vars_list = compute_vars(df, max_date, home_city=args.home_city, home_province_kw=home_kws)
    loans = load_loans(args.loans) if args.loans else None
    whitelist = load_whitelist(args.whitelist) if args.whitelist else None
    spec_list = compute_spec_metrics(df, loans=loans, whitelist=whitelist, demand_rate=args.demand_rate)

    out_dir = args.out_dir or os.path.dirname(os.path.abspath(args.input))
    output_vars(vars_list, out_dir, args.client, spec_list=spec_list)

if __name__ == "__main__":
    main()
