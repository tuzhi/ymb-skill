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
import argparse, json, os, re, sys
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

ALIPAY_ORDER_RE = re.compile(r"支付宝(?:商家|交易)订单号=[^；;]+")
ALIPAY_STATUS_RE = re.compile(r"支付宝订单状态=[^；;]+")
ALIPAY_MERCHANT_ORDER_RE = re.compile(r"支付宝商家订单号=([^；;]+)")


def _compile_keyword_pattern(rules):
    """把同组关键词编译成字面量正则；只用于粗筛，最终仍按规则顺序确认。"""
    keywords = sorted({r["关键词"] for r in rules if r["关键词"]}, key=len, reverse=True)
    if not keywords:
        return None
    return re.compile("|".join(re.escape(k) for k in keywords))


def _build_rule_groups(candidates):
    groups = {}
    for direction, rules in candidates.items():
        group_list = []
        current = None
        current_key = None
        for order, r in enumerate(rules):
            key = (
                r["方向"], r["依据字段"], tuple(r["排除"]), tuple(r["对手含"]),
                r["L1"], r["L2"], r["L3"], r["优先级"],
            )
            if key != current_key:
                current = {
                    "方向": r["方向"],
                    "依据字段": r["依据字段"],
                    "排除": r["排除"],
                    "对手含": r["对手含"],
                    "L1": r["L1"], "L2": r["L2"], "L3": r["L3"],
                    "优先级": r["优先级"],
                    "order": order,
                    "rules": [],
                    "pattern": None,
                }
                group_list.append(current)
                current_key = key
            current["rules"].append(r)
        for g in group_list:
            g["pattern"] = _compile_keyword_pattern(g["rules"])
        groups[direction] = group_list
    return groups


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
    candidates = {}
    for direction in ("收入", "支出", "未知"):
        direction_rules = buckets.get(direction, [])
        merged = direction_rules + buckets["通用"]
        candidates[direction] = sorted(merged, key=lambda r: -r["优先级"]) if buckets["通用"] else direction_rules
    buckets["__candidates__"] = candidates
    buckets["__groups__"] = _build_rule_groups(candidates)
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


def direction_series(df):
    inc = pd.to_numeric(df.get("收入金额", pd.Series(index=df.index, dtype=object)), errors="coerce")
    exp = pd.to_numeric(df.get("支出金额", pd.Series(index=df.index, dtype=object)), errors="coerce")
    out = pd.Series("未知", index=df.index, dtype=object)

    income_only = inc.notna() & (exp.isna() | (exp == 0)) & (inc > 0)
    expense_only = exp.notna() & (inc.isna() | (inc == 0)) & (exp > 0)
    both = inc.notna() & exp.notna()

    out.loc[income_only] = "收入"
    out.loc[expense_only] = "支出"
    out.loc[both] = (inc.loc[both].fillna(0) >= exp.loc[both].fillna(0)).map({True: "收入", False: "支出"})
    return out


def _match_rule_in_group(group, fields, text, opp):
    pattern = group.get("pattern")
    if pattern is not None and not pattern.search(text):
        return None, None
    if any(x in text for x in group["排除"]):
        return None, None
    if group["对手含"] and not any(x in opp for x in group["对手含"]):
        return None, None
    for r in group["rules"]:
        if r["关键词"] not in text:
            continue
        hit_field = next((s for s in SCOPE.get(r["依据字段"], SCOPE["通用"]) if r["关键词"] in fields[s]), SCOPE.get(r["依据字段"], SCOPE["通用"])[0])
        return r, hit_field
    return None, None


def _strip_technical_memo(text):
    """Remove machine-readable Alipay tracing text before keyword tagging."""
    text = ALIPAY_ORDER_RE.sub("", str(text or ""))
    text = ALIPAY_STATUS_RE.sub("", text)
    text = re.sub(r"[；;]+", "；", text).strip("；; ")
    return text


def _extract_alipay_merchant_order(text):
    m = ALIPAY_MERCHANT_ORDER_RE.search(str(text or ""))
    return m.group(1).strip() if m else ""


def _is_alipay_rows(df):
    text = pd.Series("", index=df.index, dtype=object)
    for col in ("开户行", "来源文件名", "交易渠道", "账户方附言"):
        if col in df.columns:
            text = text + df[col].astype(str)
    return text.str.contains("支付宝", regex=False, na=False)


def match(row, direction, buckets):
    """单遍按优先级匹配（方向桶 + 通用桶已分别按优先级降序）。返回 (rule, 命中字段)。"""
    fields = {f: str(row.get(f, "") or "") for f in ("对手名称", "银行备注", "账户方附言")}
    for f in fields:
        if fields[f].lower() == "nan":
            fields[f] = ""
    fields["账户方附言"] = _strip_technical_memo(fields["账户方附言"])
    opp = fields["对手名称"]

    groups = buckets.get("__groups__", {}).get(direction)
    if groups is not None:
        for g in groups:
            scope = SCOPE.get(g["依据字段"], SCOPE["通用"])
            text = "".join(fields[s] for s in scope)
            if not text:
                continue
            r, hit_field = _match_rule_in_group(g, fields, text, opp)
            if r:
                return r, hit_field
        return None, None

    cand = buckets.get("__candidates__", {}).get(direction)
    if cand is None:
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


def _money_series(df, column):
    return pd.to_numeric(df.get(column, pd.Series(index=df.index, dtype=object)), errors="coerce").fillna(0)


def _set_analysis_amounts(out_df):
    inc = _money_series(out_df, "收入金额")
    exp = _money_series(out_df, "支出金额")
    out_df["分析收入金额"] = inc
    out_df["分析支出金额"] = exp
    out_df["分析交易金额"] = (inc - exp).round(2)
    out_df["交易状态"] = "正常"
    out_df["关联冲正交易编号"] = ""


def _apply_alipay_order_reversals(out_df):
    """Pair Alipay no-income/no-expense cancellation rows with their order row.

    The raw transaction rows are kept for traceability. Analysis amount columns
    are zeroed for the paired order and cancellation rows so downstream summaries
    can use the business-effective amount without overwriting original amounts.
    """
    if out_df.empty or "账户方附言" not in out_df.columns:
        _set_analysis_amounts(out_df)
        return {"配对组数": 0, "冲正原始交易数": 0, "冲正记录数": 0}

    _set_analysis_amounts(out_df)
    is_alipay = _is_alipay_rows(out_df)
    memo = out_df["账户方附言"].astype(str)
    merchant_orders = memo.map(_extract_alipay_merchant_order).where(is_alipay, "")
    if not merchant_orders.any():
        return {"配对组数": 0, "冲正原始交易数": 0, "冲正记录数": 0}

    inc = _money_series(out_df, "收入金额")
    exp = _money_series(out_df, "支出金额")
    has_cash_flow = (inc > 0) | (exp > 0)
    is_cancel = memo.str.contains("支付宝订单状态=取消/退款关联", regex=False, na=False)
    if "银行备注" in out_df.columns:
        is_cancel = is_cancel | (
            (merchant_orders != "")
            & (~has_cash_flow)
            & out_df["银行备注"].astype(str).str.contains("退款|取消|退货", regex=True, na=False)
        )

    group_cols = []
    if "本方账户" in out_df.columns:
        group_cols.append(out_df["本方账户"].astype(str))
    else:
        group_cols.append(pd.Series("", index=out_df.index))
    group_cols.append(merchant_orders)
    keys = pd.Series(list(zip(*group_cols)), index=out_df.index)

    paired_groups = original_count = cancel_count = 0
    for key, idx in keys[(merchant_orders != "") & is_alipay].groupby(keys).groups.items():
        idx = list(idx)
        normal_idx = [i for i in idx if bool(has_cash_flow.loc[i]) and not bool(is_cancel.loc[i])]
        cancel_idx = [i for i in idx if bool(is_cancel.loc[i])]
        if len(normal_idx) < 1 or len(cancel_idx) < 1:
            continue
        normal_directions = {"收入" if inc.loc[i] > 0 else "支出" for i in normal_idx}
        if len(normal_directions) != 1:
            continue

        direction = next(iter(normal_directions))
        refund_l3 = "退款收入" if direction == "收入" else "退款支出"

        paired_groups += 1
        original_count += len(normal_idx)
        cancel_count += len(cancel_idx)

        paired = normal_idx + cancel_idx
        out_df.loc[paired, ["分析收入金额", "分析支出金额", "分析交易金额"]] = 0
        normal_ids = "；".join(str(out_df.loc[i, "交易唯一编号"]) for i in normal_idx)
        cancel_ids = "；".join(str(out_df.loc[i, "交易唯一编号"]) for i in cancel_idx)
        out_df.loc[normal_idx, "交易状态"] = "被取消"
        out_df.loc[cancel_idx, "交易状态"] = "取消"
        out_df.loc[normal_idx, "关联冲正交易编号"] = cancel_ids
        out_df.loc[cancel_idx, "关联冲正交易编号"] = normal_ids

        out_df.loc[cancel_idx, "收支方向"] = direction
        out_df.loc[cancel_idx, "一级标签"] = "经营类"
        out_df.loc[cancel_idx, "二级标签"] = "退款交易"
        out_df.loc[cancel_idx, "三级标签"] = refund_l3
        out_df.loc[cancel_idx, "标签来源"] = "支付宝订单配对"
        out_df.loc[cancel_idx, "标签置信度"] = 0.95
        out_df.loc[cancel_idx, "命中规则编号"] = "ALIPAY_ORDER_REVERSAL"
        out_df.loc[cancel_idx, "命中关键词"] = "商家订单号取消/退款关联"
        out_df.loc[cancel_idx, "命中字段"] = "账户方附言"

    return {"配对组数": paired_groups, "冲正原始交易数": original_count, "冲正记录数": cancel_count}


def _relation_text(value):
    text = str(value or "").strip()
    return "" if text.lower() in {"nan", "none"} else re.sub(r"\s+", "", text)


def _relation_account(value):
    return re.sub(r"[^0-9A-Za-z*]", "", _relation_text(value))


def _bank_reversal_type(value):
    """Return the bank's cancellation term without changing pairing semantics."""
    text = _relation_text(value)
    if "抹账" in text:
        return "抹账"
    if "冲正" in text:
        return "冲正"
    if "冲销" in text:
        return "冲销"
    return ""


def _same_transaction_day(left, right):
    values = pd.to_datetime(pd.Series([left, right]), errors="coerce", format="mixed")
    if values.notna().all():
        return values.iloc[0].date() == values.iloc[1].date()
    return _relation_text(left)[:10] == _relation_text(right)[:10]


def _same_precise_transaction_time(left, right):
    """隐式冲正只接受两边都明确到秒（或更细）且时间完全一致。"""
    pattern = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?$")
    left_text = str(left or "").strip()
    right_text = str(right or "").strip()
    return bool(pattern.fullmatch(left_text) and pattern.fullmatch(right_text) and left_text == right_text)


def _signed_transaction_amount(row):
    income = pd.to_numeric(pd.Series([row.get("收入金额")]), errors="coerce").fillna(0).iloc[0]
    expense = pd.to_numeric(pd.Series([row.get("支出金额")]), errors="coerce").fillna(0).iloc[0]
    return float(income - expense)


def _is_local_bank_implicit_reversal_pair(original, reversal):
    """无冲正字样时，仅用秒级同刻、反向等额和余额闭环识别隐式冲正。"""
    if _bank_reversal_type(original.get("银行备注")) or _bank_reversal_type(reversal.get("银行备注")):
        return False
    if not _same_precise_transaction_time(original.get("交易时间"), reversal.get("交易时间")):
        return False
    own_account = _relation_account(original.get("本方账户"))
    if not own_account or own_account != _relation_account(reversal.get("本方账户")):
        return False

    original_name = _relation_text(original.get("对手名称"))
    reversal_name = _relation_text(reversal.get("对手名称"))
    if not original_name or original_name != reversal_name:
        return False
    original_account = _relation_account(original.get("对手账户"))
    reversal_account = _relation_account(reversal.get("对手账户"))
    if original_account and reversal_account and original_account != reversal_account:
        return False

    original_amount = _signed_transaction_amount(original)
    reversal_amount = _signed_transaction_amount(reversal)
    if original_amount == 0 or round(original_amount + reversal_amount, 2) != 0:
        return False

    original_balance = pd.to_numeric(pd.Series([original.get("账户余额")]), errors="coerce").iloc[0]
    reversal_balance = pd.to_numeric(pd.Series([reversal.get("账户余额")]), errors="coerce").iloc[0]
    if pd.isna(original_balance) or pd.isna(reversal_balance):
        return False
    balance_before_original = float(original_balance) - original_amount
    return abs(balance_before_original - float(reversal_balance)) <= 0.005


def _is_local_bank_reversal_pair(original, reversal):
    """严格判断原文件内相邻两笔是否构成银行冲正或抹账。"""
    reversal_type = _bank_reversal_type(reversal.get("银行备注"))
    if not reversal_type:
        return False
    if not _same_transaction_day(original.get("交易时间"), reversal.get("交易时间")):
        return False
    if _relation_account(original.get("本方账户")) != _relation_account(reversal.get("本方账户")):
        return False

    original_name = _relation_text(original.get("对手名称"))
    reversal_name = _relation_text(reversal.get("对手名称"))
    original_account = _relation_account(original.get("对手账户"))
    reversal_account = _relation_account(reversal.get("对手账户"))
    placeholder_reversal = reversal_type == "冲销" and reversal_name in {"", "/", "-", "***", "***/"}
    if placeholder_reversal:
        if not original_name:
            return False
    else:
        if not original_name or original_name != reversal_name:
            return False
        if original_account and reversal_account and original_account != reversal_account:
            return False

    original_inc = pd.to_numeric(pd.Series([original.get("收入金额")]), errors="coerce").fillna(0).iloc[0]
    original_exp = pd.to_numeric(pd.Series([original.get("支出金额")]), errors="coerce").fillna(0).iloc[0]
    reversal_inc = pd.to_numeric(pd.Series([reversal.get("收入金额")]), errors="coerce").fillna(0).iloc[0]
    reversal_exp = pd.to_numeric(pd.Series([reversal.get("支出金额")]), errors="coerce").fillna(0).iloc[0]
    original_amount = float(original_inc - original_exp)
    reversal_amount = float(reversal_inc - reversal_exp)
    if original_amount == 0:
        return False
    # 部分银行的“抹账”行仍沿用原交易借贷方向，金额列本身不反向，
    # 但交易后余额会恢复至原交易发生前。冲正仍要求金额明确反向。
    if reversal_type in {"冲正", "冲销"} and round(original_amount + reversal_amount, 2) != 0:
        return False

    original_balance = pd.to_numeric(pd.Series([original.get("账户余额")]), errors="coerce").iloc[0]
    reversal_balance = pd.to_numeric(pd.Series([reversal.get("账户余额")]), errors="coerce").iloc[0]
    if pd.isna(original_balance) or pd.isna(reversal_balance):
        return False
    balance_before_original = float(original_balance) - original_amount
    if abs(balance_before_original - float(reversal_balance)) > 0.005:
        return False

    reversal_memo = _relation_text(reversal.get("账户方附言"))
    original_context = {
        _relation_text(original.get("银行备注")),
        _relation_text(original.get("账户方附言")),
    }
    if reversal_memo and reversal_memo not in original_context:
        return False
    return True


def _apply_bank_reversals(out_df):
    """只在同一来源文件内，将冲正/抹账行与上一笔有效交易作确定性配对。"""
    summary = {
        "配对组数": 0,
        "被冲正原始交易数": 0,
        "冲正记录数": 0,
        "隐式冲正数": 0,
        "待复核冲正数": 0,
        "待复核交易编号列表": [],
    }
    required = {"来源文件名", "来源行号", "交易唯一编号", "银行备注"}
    if out_df.empty or not required.issubset(out_df.columns):
        return summary

    reversal_types = out_df["银行备注"].map(_bank_reversal_type)
    reversal_mask = reversal_types != ""
    unresolved = set(out_df.index[reversal_mask])
    source_names = out_df["来源文件名"].fillna("").astype(str)
    for _, source_idx in out_df.groupby(source_names, sort=False).groups.items():
        ordered = out_df.loc[list(source_idx)].copy()
        ordered["__source_row"] = pd.to_numeric(ordered["来源行号"], errors="coerce")
        ordered = ordered[ordered["__source_row"].notna()].sort_values("__source_row", kind="mergesort")
        indices = ordered.index.tolist()
        for position in range(1, len(indices)):
            reversal_idx = indices[position]
            if reversal_idx not in unresolved:
                continue
            original_idx = indices[position - 1]
            original_row = out_df.loc[original_idx]
            reversal_row = out_df.loc[reversal_idx]
            original_source_row = float(ordered.loc[original_idx, "__source_row"])
            reversal_source_row = float(ordered.loc[reversal_idx, "__source_row"])
            if reversal_source_row - original_source_row != 1:
                continue
            if str(out_df.loc[original_idx, "交易状态"]) != "正常":
                continue
            if not _is_local_bank_reversal_pair(original_row, reversal_row):
                continue

            original_id = str(original_row.get("交易唯一编号") or "")
            reversal_id = str(reversal_row.get("交易唯一编号") or "")
            reversal_type = reversal_types.loc[reversal_idx]
            paired = [original_idx, reversal_idx]
            out_df.loc[paired, ["分析收入金额", "分析支出金额", "分析交易金额"]] = 0
            out_df.loc[original_idx, "交易状态"] = f"被{reversal_type}"
            out_df.loc[reversal_idx, "交易状态"] = reversal_type
            out_df.loc[original_idx, "关联冲正交易编号"] = reversal_id
            out_df.loc[reversal_idx, "关联冲正交易编号"] = original_id
            out_df.loc[reversal_idx, "一级标签"] = "其他类"
            out_df.loc[reversal_idx, "二级标签"] = "冲正交易"
            out_df.loc[reversal_idx, "三级标签"] = reversal_type
            out_df.loc[reversal_idx, "标签来源"] = "银行冲正配对"
            out_df.loc[reversal_idx, "标签置信度"] = 0.95
            out_df.loc[reversal_idx, "命中规则编号"] = "BANK_LOCAL_REVERSAL"
            out_df.loc[reversal_idx, "命中关键词"] = f"{reversal_type}+相邻交易+余额闭环"
            out_df.loc[reversal_idx, "命中字段"] = "银行备注"
            summary["配对组数"] += 1
            summary["被冲正原始交易数"] += 1
            summary["冲正记录数"] += 1
            unresolved.remove(reversal_idx)

        # 显式冲正处理完后，再在同一原文件的相邻正常记录中识别强证据隐式冲正。
        for position in range(1, len(indices)):
            original_idx = indices[position - 1]
            reversal_idx = indices[position]
            if str(out_df.loc[original_idx, "交易状态"]) != "正常":
                continue
            if str(out_df.loc[reversal_idx, "交易状态"]) != "正常":
                continue
            if bool(_is_alipay_rows(out_df.loc[[original_idx, reversal_idx]]).any()):
                continue
            original_source_row = float(ordered.loc[original_idx, "__source_row"])
            reversal_source_row = float(ordered.loc[reversal_idx, "__source_row"])
            if reversal_source_row - original_source_row != 1:
                continue
            original_row = out_df.loc[original_idx]
            reversal_row = out_df.loc[reversal_idx]
            if not _is_local_bank_implicit_reversal_pair(original_row, reversal_row):
                continue

            original_id = str(original_row.get("交易唯一编号") or "")
            reversal_id = str(reversal_row.get("交易唯一编号") or "")
            paired = [original_idx, reversal_idx]
            out_df.loc[paired, ["分析收入金额", "分析支出金额", "分析交易金额"]] = 0
            out_df.loc[original_idx, "交易状态"] = "被隐式冲正"
            out_df.loc[reversal_idx, "交易状态"] = "隐式冲正"
            out_df.loc[original_idx, "关联冲正交易编号"] = reversal_id
            out_df.loc[reversal_idx, "关联冲正交易编号"] = original_id
            out_df.loc[reversal_idx, "一级标签"] = "其他类"
            out_df.loc[reversal_idx, "二级标签"] = "冲正交易"
            out_df.loc[reversal_idx, "三级标签"] = "隐式冲正"
            out_df.loc[reversal_idx, "标签来源"] = "银行隐式冲正配对"
            out_df.loc[reversal_idx, "标签置信度"] = 0.9
            out_df.loc[reversal_idx, "命中规则编号"] = "BANK_IMPLICIT_REVERSAL"
            out_df.loc[reversal_idx, "命中关键词"] = "同秒反向等额+相邻交易+余额闭环"
            out_df.loc[reversal_idx, "命中字段"] = "标准字段组合"
            summary["配对组数"] += 1
            summary["被冲正原始交易数"] += 1
            summary["冲正记录数"] += 1
            summary["隐式冲正数"] += 1

    unresolved_ids = [str(out_df.loc[index, "交易唯一编号"]) for index in sorted(unresolved)]
    summary["待复核冲正数"] = len(unresolved_ids)
    summary["待复核交易编号列表"] = unresolved_ids
    return summary


def _apply_transaction_relations(out_df):
    """统一识别并应用交易关系；各策略自行约束候选范围。"""
    alipay = _apply_alipay_order_reversals(out_df)
    bank = _apply_bank_reversals(out_df)
    return {"支付宝取消": alipay, "银行冲正": bank}


def tag(csv_path, rules_path, out_dir=None):
    df = pd.read_csv(csv_path, dtype=str)
    # 银行识别过程证据只保留在 mapping/整合报告，不能进入标准业务流水。
    df = df.drop(
        columns=["router_bank", "inferred_bank", "batch_pair", "bank_source"],
        errors="ignore",
    )
    buckets = load_rules(rules_path)

    rows_out, review = [], []
    unmatched = Counter()
    rule_hits = 0
    field_stat = Counter()
    l1_stat = Counter()
    directions = direction_series(df)
    for _, row in df.iterrows():
        d = directions.loc[row.name]
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
            l3 = "其他支出" if d == "支出" else "其他收入" if d == "收入" else "其他"
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
    relation_summary = _apply_transaction_relations(out_df)

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
            "交易关系汇总": relation_summary,
        },
        "一级标签分布": dict(l1_stat),
        "标签分布": out_df.groupby(["一级标签", "二级标签", "三级标签"]).size()
                        .sort_values(ascending=False).head(40)
                        .reset_index(name="笔数").to_dict("records"),
        "新规则建议": suggestions,
        "人工复核事项": [
            {
                "交易唯一编号": uid,
                "复核原因": "银行备注含冲正/抹账，但未与同文件上一笔交易形成金额、对手和余额闭环",
                "候选标签": [],
                "建议动作": "核对原始文件中的原交易与冲正/抹账关系",
            }
            for uid in relation_summary["银行冲正"]["待复核交易编号列表"]
        ] + [{"交易唯一编号": uid, "复核原因": "规则未命中，归兜底其他类",
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
