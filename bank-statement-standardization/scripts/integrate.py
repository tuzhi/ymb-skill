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
import argparse, glob, hashlib, json, os, sys, unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from itertools import combinations

try:
    import pandas as pd
    import numpy as np
except ImportError:
    sys.exit("需要 pandas/numpy：pip install pandas numpy")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import standardize as S   # 复用余额连续性行序整理（best_continuity_order）

NUMERIC = ["收入金额", "支出金额", "交易金额", "账户余额"]


def _clean_text(value):
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _identity_text(value):
    """规范主体名称等身份文本，仅消除字符宽度、空白和大小写差异。"""
    return "".join(unicodedata.normalize("NFKC", _clean_text(value)).split()).casefold()


def _identity_bank(value):
    """优先复用阶段一的银行规范名；未知银行保留保守文本规范化结果。"""
    text = _clean_text(value)
    if _identity_text(text) in {"未知", "未识别", "unknown", "none", "nan"}:
        return ""
    canonical = S.infer_bank(text)
    if canonical in {"农村商业银行", "农村信用社", "村镇银行"}:
        return _identity_text(text)
    return _identity_text(canonical or text)


def _is_unknown_account(value):
    account = _clean_text(value)
    return not account or account.startswith("未识别账户#")


def _explicit_account(value):
    """返回可作为批次归并证据的明确银行账号；昵称、掩码和异常文本不参与。"""
    account = _clean_text(value)
    if _is_unknown_account(account) or "*" in account:
        return ""
    if not all(char.isdigit() or char in " -" for char in account):
        return ""
    return account if sum(char.isdigit() for char in account) >= 6 else ""


def _account_key(value):
    account = _explicit_account(value)
    if not account:
        return ""
    digits = "".join(char for char in account if char.isdigit())
    return digits or _identity_text(account)


def _overlap_amount(value):
    text = _clean_text(value).replace(",", "")
    if not text:
        return ""
    try:
        return f"{float(text):.2f}"
    except ValueError:
        return text


def _transaction_overlap_key(row):
    """不含本方账号的强交易证据，用于未知文件与明确账号之间的重合比较。"""
    time = _clean_text(row.get("交易时间"))
    income = _overlap_amount(row.get("收入金额"))
    expense = _overlap_amount(row.get("支出金额"))
    balance = _overlap_amount(row.get("账户余额"))
    opponent_name = _identity_text(row.get("对手名称"))
    opponent_account = _identity_text(row.get("对手账户"))
    if not time or not balance or not (income or expense) or not (opponent_name or opponent_account):
        return None
    return time, income, expense, balance, opponent_name, opponent_account


def _core_pair_key(row):
    """配套文件识别核心键；对手字段只作增强证据，不阻断 PDF/XLS 格式差异。"""
    time = _clean_text(row.get("交易时间"))
    income = _overlap_amount(row.get("收入金额"))
    expense = _overlap_amount(row.get("支出金额"))
    balance = _overlap_amount(row.get("账户余额"))
    if not time or not balance or not (income or expense):
        return None
    return time, income, expense, balance


def _counterparty_enhancement_keys(row):
    core = _core_pair_key(row)
    if core is None:
        return []
    keys = []
    opponent_name = _identity_text(row.get("对手名称"))
    if opponent_name:
        keys.append((*core, "name", opponent_name))
    opponent_digits = "".join(char for char in _clean_text(row.get("对手账户")) if char.isdigit())
    if opponent_digits:
        keys.append((*core, "account", opponent_digits))
    return keys


def _source_col(df):
    if "__源标准化文件路径" in df.columns:
        return "__源标准化文件路径"
    if "来源文件名" in df.columns:
        return "来源文件名"
    if "__源标准化文件" in df.columns:
        return "__源标准化文件"
    return ""


def complete_metadata_by_explicit_account(df):
    """同批次相同明确账号之间补齐缺失的本方名称和银行，不覆盖冲突值。"""
    result = df.copy()
    report = {"补全文件数": 0, "补全交易数": 0, "补全明细": []}
    if result.empty or "本方账户" not in result.columns:
        return result, report
    source_col = _source_col(result)
    result["__account_key"] = result["本方账户"].map(_account_key)
    touched_sources = set()
    for account_key, group in result[result["__account_key"] != ""].groupby("__account_key", sort=True):
        names = {
            _identity_text(value): _clean_text(value)
            for value in group.get("本方名称", pd.Series(dtype=str))
            if _identity_text(value)
        }
        banks = {
            _identity_bank(value): _clean_text(value)
            for value in group.get("开户行", pd.Series(dtype=str))
            if _identity_bank(value)
        }
        fill_name = next(iter(names.values())) if len(names) == 1 else ""
        fill_bank = next(iter(banks.values())) if len(banks) == 1 else ""
        changed = []
        for index, row in group.iterrows():
            fields = []
            if fill_name and not _clean_text(row.get("本方名称")):
                result.at[index, "本方名称"] = fill_name
                fields.append("本方名称")
            if fill_bank and not _clean_text(row.get("开户行")):
                result.at[index, "开户行"] = fill_bank
                result.at[index, "__batch_pair_bank"] = fill_bank
                fields.append("开户行")
            if fields:
                changed.append(index)
                touched_sources.add(_clean_text(row.get(source_col)))
        if changed:
            report["补全交易数"] += len(changed)
            report["补全明细"].append({
                "本方账户": _clean_text(group["本方账户"].iloc[0]),
                "补全字段": sorted({field for index in changed for field in (
                    ["本方名称"] if fill_name and not _clean_text(df.at[index, "本方名称"]) else []
                ) + (
                    ["开户行"] if fill_bank and not _clean_text(df.at[index, "开户行"]) else []
                )}),
                "涉及来源文件": sorted({_clean_text(result.at[index, "来源文件名"]) for index in changed}),
            })
    report["补全文件数"] = len(touched_sources)
    return result.drop(columns=["__account_key"]), report


def _source_bank_judgment(rows):
    router_values = {
        _clean_text(value) for value in rows.get("__router_bank", pd.Series(dtype=str))
        if _identity_bank(value)
    }
    inferred_values = {
        _clean_text(value) for value in rows.get("__inferred_bank", pd.Series(dtype=str))
        if _identity_bank(value)
    }
    current_values = {
        _clean_text(value) for value in rows.get("开户行", pd.Series(dtype=str))
        if _identity_bank(value)
    }
    return {
        "router_bank": next(iter(router_values)) if len(router_values) == 1 else "未识别",
        "inferred_bank": next(iter(inferred_values)) if len(inferred_values) == 1 else "",
        "current_bank": next(iter(current_values)) if len(current_values) == 1 else "",
    }


def pair_unknown_account_sources(df, min_overlap=3, min_ratio=0.9):
    """将两个高重合、真实账号均未知的配套来源归到同一批次虚拟账户。"""
    result = df.copy()
    report = {"已配对组数": 0, "已配对文件数": 0, "已配对交易数": 0, "配对明细": []}
    if result.empty or "本方账户" not in result.columns:
        return result, report
    source_col = _source_col(result)
    source_groups = {
        _clean_text(source): rows
        for source, rows in result.groupby(result[source_col].map(_clean_text), sort=True, dropna=False)
        if rows["本方账户"].map(_is_unknown_account).all()
    }
    candidates = []
    candidate_counts = defaultdict(int)
    for left_source, right_source in combinations(sorted(source_groups), 2):
        left = source_groups[left_source]
        right = source_groups[right_source]
        left_keys = Counter(
            key for _, row in left.iterrows() if (key := _core_pair_key(row)) is not None
        )
        right_keys = Counter(
            key for _, row in right.iterrows() if (key := _core_pair_key(row)) is not None
        )
        overlap_keys = left_keys & right_keys
        overlap_count = sum(overlap_keys.values())
        denominator = min(sum(left_keys.values()), sum(right_keys.values()))
        ratio = overlap_count / denominator if denominator else 0
        left_coverage = overlap_count / len(left) if len(left) else 0
        right_coverage = overlap_count / len(right) if len(right) else 0
        if (
            overlap_count < min_overlap
            or ratio < min_ratio
            or left_coverage < min_ratio
            or right_coverage < min_ratio
        ):
            continue
        left_enhanced = Counter(
            key for _, row in left.iterrows() for key in _counterparty_enhancement_keys(row)
        )
        right_enhanced = Counter(
            key for _, row in right.iterrows() for key in _counterparty_enhancement_keys(row)
        )
        enhanced_matches = left_enhanced & right_enhanced
        enhanced_overlap_count = max(
            sum(count for key, count in enhanced_matches.items() if key[-2] == kind)
            for kind in ("name", "account")
        )
        if enhanced_overlap_count < min_overlap:
            continue
        judgments = {
            left_source: _source_bank_judgment(left),
            right_source: _source_bank_judgment(right),
        }
        bank_values = {
            _identity_bank(value): value
            for judgment in judgments.values()
            for value in (judgment["router_bank"], judgment["inferred_bank"], judgment["current_bank"])
            if _identity_bank(value)
        }
        if len(bank_values) > 1:
            continue
        account_types = {
            _clean_text(value) for value in pd.concat([left["账户类型"], right["账户类型"]])
            if _clean_text(value) not in {"", "未知"}
        }
        if len(account_types) > 1:
            continue
        candidate = {
            "sources": (left_source, right_source),
            "overlap_keys": overlap_keys,
            "ratio": ratio,
            "left_coverage": left_coverage,
            "right_coverage": right_coverage,
            "enhanced_overlap_count": enhanced_overlap_count,
            "bank": next(iter(bank_values.values())) if bank_values else "未识别",
            "judgments": judgments,
        }
        candidates.append(candidate)
        candidate_counts[left_source] += 1
        candidate_counts[right_source] += 1

    paired_sources = set()
    for candidate in candidates:
        left_source, right_source = candidate["sources"]
        if candidate_counts[left_source] != 1 or candidate_counts[right_source] != 1:
            continue
        if left_source in paired_sources or right_source in paired_sources:
            continue
        indices = source_groups[left_source].index.tolist() + source_groups[right_source].index.tolist()
        digest_raw = "\n".join(
            "|".join(key) for key in sorted(candidate["overlap_keys"].elements())
        )
        digest = hashlib.md5(digest_raw.encode("utf-8")).hexdigest()[:10]
        bank = candidate["bank"]
        account = f"批次虚拟账户#{bank}#PAIR-{digest}"
        result.loc[indices, "本方账户"] = account
        if bank != "未识别":
            result.loc[indices, "开户行"] = bank
        result.loc[indices, "__batch_pair_bank"] = bank
        names = {
            _clean_text(value) for value in result.loc[indices, "本方名称"] if _clean_text(value)
        }
        if len(names) == 1:
            result.loc[indices, "本方名称"] = next(iter(names))
        source_names = {
            source: sorted({_clean_text(value) for value in source_groups[source]["来源文件名"] if _clean_text(value)})
            for source in (left_source, right_source)
        }
        report["配对明细"].append({
            "批次虚拟账户": account,
            "batch_pair": bank,
            "核心交易重合数": sum(candidate["overlap_keys"].values()),
            "核心交易重合率": round(candidate["ratio"], 4),
            "左来源覆盖率": round(candidate["left_coverage"], 4),
            "右来源覆盖率": round(candidate["right_coverage"], 4),
            "对手增强重合数": candidate["enhanced_overlap_count"],
            "来源文件": source_names,
            "来源判断": {
                os.path.basename(source): candidate["judgments"][source]
                for source in (left_source, right_source)
            },
            "_证据行索引": indices,
        })
        report["已配对组数"] += 1
        report["已配对文件数"] += 2
        report["已配对交易数"] += len(indices)
        paired_sources.update((left_source, right_source))
    return result, report


def _match_explicit_account_by_overlap(source_rows, explicit_rows, min_overlap=3, min_ratio=0.9):
    source_key_rows = defaultdict(list)
    for index, row in source_rows.iterrows():
        key = _transaction_overlap_key(row)
        if key is not None:
            source_key_rows[key].append(index)
    source_keys = Counter({key: len(indices) for key, indices in source_key_rows.items()})
    if not source_keys:
        return "", {}, [], 0
    overlaps = {}
    matched = []
    for account, rows in explicit_rows.groupby("__explicit_account", sort=True):
        known_keys = Counter(
            key for _, row in rows.iterrows()
            if (key := _transaction_overlap_key(row)) is not None
        )
        matched_keys = source_keys & known_keys
        count = sum(matched_keys.values())
        ratio = count / len(source_rows)
        overlaps[account] = {"重合交易数": count, "重合率": round(ratio, 4)}
        if count >= min_overlap and ratio >= min_ratio:
            matched.append((account, matched_keys))
    if len(matched) != 1:
        return "", overlaps, [], 0
    account, matched_keys = matched[0]
    indices = [
        index
        for key, count in matched_keys.items()
        for index in source_key_rows[key][:count]
    ]
    return account, overlaps, indices, sum(matched_keys.values())


def resolve_batch_accounts(df, min_overlap=2):
    """在同一批次内按主体、银行和交易证据保守回填未知本方账号。

    归并以来源文件为单位。同主体同银行只有一个明确账号时可直接归并；存在多个明确账号时，
    必须有至少 ``min_overlap`` 笔完整交易重合，且只能有一个账号达到阈值。
    """
    result = df.copy()
    report = {
        "未知账号文件数": 0,
        "已归并文件数": 0,
        "已归并交易数": 0,
        "待复核文件数": 0,
        "归并明细": [],
        "待复核明细": [],
    }
    required = {"本方名称", "本方账户", "开户行"}
    if result.empty or not required.issubset(result.columns):
        return result, report

    source_col = (
        "__源标准化文件路径" if "__源标准化文件路径" in result.columns
        else "__源标准化文件" if "__源标准化文件" in result.columns
        else "来源文件名"
    )
    result["__identity_name"] = result["本方名称"].map(_identity_text)
    result["__identity_bank"] = result["开户行"].map(_identity_bank)
    result["__explicit_account"] = result["本方账户"].map(_explicit_account)
    all_explicit = result[result["__explicit_account"] != ""]
    unknown = result.loc[result["本方账户"].map(_is_unknown_account)]
    report["未知账号文件数"] = int(unknown[source_col].map(_clean_text).nunique())

    for source, source_rows in unknown.groupby(unknown[source_col].map(_clean_text), sort=True, dropna=False):
        name_keys = {value for value in source_rows["__identity_name"] if value}
        bank_keys = {value for value in source_rows["__identity_bank"] if value}
        name_key = next(iter(name_keys)) if len(name_keys) == 1 else ""
        bank_key = next(iter(bank_keys)) if len(bank_keys) == 1 else ""
        source_files = sorted({_clean_text(value) for value in source_rows["来源文件名"] if _clean_text(value)})
        target = ""
        method = ""
        overlap_count = 0
        overlap_indices = []
        accounts = []
        overlaps = {}
        review_reason = ""
        batch_pair_bank = ""

        if not name_key or not bank_key:
            review_reason = "主体名称或银行缺失/不一致，无法建立批次归并候选组"
        else:
            identity_group = result.loc[
                (result["__identity_name"] == name_key) & (result["__identity_bank"] == bank_key)
            ].copy()
            identity_group["__explicit_account"] = identity_group["本方账户"].map(_explicit_account)
            explicit = identity_group.loc[identity_group["__explicit_account"] != ""]
            accounts = sorted(explicit["__explicit_account"].unique().tolist())
            if not accounts:
                review_reason = "同主体同银行组内没有可验证的明确本方账号"
            elif len(accounts) == 1:
                target = accounts[0]
                method = "同主体同银行唯一明确账号"
            else:
                source_key_rows = defaultdict(list)
                for index, row in source_rows.iterrows():
                    key = _transaction_overlap_key(row)
                    if key is not None:
                        source_key_rows[key].append(index)
                source_keys = Counter({key: len(indices) for key, indices in source_key_rows.items()})
                known_keys = {}
                for account, account_rows in explicit.groupby("__explicit_account", sort=True):
                    known_keys[account] = Counter(
                        key for _, row in account_rows.iterrows()
                        if (key := _transaction_overlap_key(row)) is not None
                    )
                overlaps = {
                    account: sum((source_keys & known_keys[account]).values()) for account in accounts
                }
                matched = [account for account, count in overlaps.items() if count >= min_overlap]
                if len(matched) == 1:
                    target = matched[0]
                    method = "多笔交易重合唯一命中"
                    overlap_count = overlaps[target]
                    matched_keys = source_keys & known_keys[target]
                    overlap_indices = [
                        index
                        for key, count in matched_keys.items()
                        for index in source_key_rows[key][:count]
                    ]
                else:
                    review_reason = "同主体同银行存在多个明确账号，交易重合证据不能唯一确定归属"

        # 元数据缺失或分组无法建立时，允许用高覆盖率交易重合跨元数据唯一命中明确账号。
        if not target and not all_explicit.empty:
            fallback_target, fallback_overlaps, fallback_indices, fallback_count = (
                _match_explicit_account_by_overlap(source_rows, all_explicit)
            )
            if fallback_target:
                target = fallback_target
                method = "核心交易高重合跨元数据唯一命中"
                overlaps = fallback_overlaps
                overlap_indices = fallback_indices
                overlap_count = fallback_count
                matched_rows = all_explicit[all_explicit["__explicit_account"] == target]
                names = {
                    _clean_text(value) for value in matched_rows["本方名称"] if _clean_text(value)
                }
                banks = {
                    _clean_text(value) for value in matched_rows["开户行"] if _identity_bank(value)
                }
                if len(names) == 1:
                    result.loc[source_rows.index, "本方名称"] = next(iter(names))
                if len(banks) == 1:
                    batch_pair_bank = next(iter(banks))
                    result.loc[source_rows.index, "开户行"] = batch_pair_bank
                    result.loc[source_rows.index, "__batch_pair_bank"] = batch_pair_bank

        common = {
            "来源标准化文件": os.path.basename(_clean_text(source)),
            "来源文件": source_files,
            "主体名称": _clean_text(source_rows["本方名称"].iloc[0]),
            "银行": _clean_text(source_rows["开户行"].iloc[0]),
        }
        if target:
            result.loc[source_rows.index, "本方账户"] = target
            report["已归并文件数"] += 1
            report["已归并交易数"] += int(len(source_rows))
            report["归并明细"].append({
                **common,
                "归并账号": target,
                "归并方式": method,
                "重合交易数": overlap_count,
                "batch_pair": batch_pair_bank,
                "_证据行索引": overlap_indices,
            })
        else:
            report["待复核明细"].append({
                **common,
                "候选账号": accounts,
                "各账号重合交易数": overlaps,
                "复核原因": review_reason,
                "_证据行索引": source_rows.index.tolist(),
            })

    report["待复核文件数"] = len(report["待复核明细"])
    return result.drop(columns=["__identity_name", "__identity_bank", "__explicit_account"]), report


def finalize_account_resolution_report(df, report):
    """在唯一编号重算后，把内部行索引转换为可追溯的交易编号。"""
    for bucket in ("归并明细", "待复核明细"):
        for item in report[bucket]:
            indices = item.pop("_证据行索引", [])
            item["证据交易唯一编号列表"] = df.loc[indices, "交易唯一编号"].head(20).tolist()
    return report


def finalize_pair_report(df, report):
    for item in report["配对明细"]:
        indices = item.pop("_证据行索引", [])
        item["证据交易唯一编号列表"] = df.loc[indices, "交易唯一编号"].head(20).tolist()
    return report


def regenerate_transaction_ids(df):
    """账号归并后按阶段一同一口径重算内容指纹，并保留文件内真实重复笔序号。"""
    result = df.copy()
    occurrences = defaultdict(int)
    unique_ids = []
    for _, row in result.iterrows():
        fingerprint = S.build_fingerprint(
            _clean_text(row.get("本方名称")),
            _clean_text(row.get("本方账户")),
            _clean_text(row.get("交易时间")),
            _clean_text(row.get("对手名称")),
            _clean_text(row.get("对手账户")),
            _clean_text(row.get("收入金额")),
            _clean_text(row.get("支出金额")),
            _clean_text(row.get("账户余额")),
        )
        source = (
            _clean_text(row.get("__源标准化文件路径"))
            or _clean_text(row.get("__源标准化文件"))
            or _clean_text(row.get("来源文件名"))
        )
        occurrence_key = (source, fingerprint)
        occurrence = occurrences[occurrence_key]
        occurrences[occurrence_key] += 1
        unique_ids.append(S.build_unique_id(fingerprint, occurrence))
    result["交易唯一编号"] = unique_ids
    return result


def order_accounts_for_continuity(df):
    """对每个本方账户，把（多文件合并、跨文件去重后的）行重排为「余额连续性最优」的顺序。

    余额是对账真值。各文件导出排序各异（整体倒序、日内倒序、同秒多笔、内部记账序≠时间戳），
    去重后还可能出现跨文件混排——统一交给 best_continuity_order 按余额断点择优（原序/翻转/按日期升序/
    余额链重建）。这样既保留每个文件的原始对账口径、又能跨文件正确归并，不靠交易时间硬排。"""
    parts = []
    for _acct, g in df.groupby(df["本方账户"].fillna(""), sort=True):
        g = g.reset_index(drop=True)
        rows = [(None if pd.isna(b) else float(b),
                 None if pd.isna(i) else float(i),
                 None if pd.isna(e) else float(e),
                 "" if pd.isna(t) else str(t))
                for b, i, e, t in zip(g["账户余额_num"], g["收入金额_num"],
                                      g["支出金额_num"], g["交易时间"])]
        order, _strategy = S.best_continuity_order(rows)
        parts.append(g.iloc[order])
    return pd.concat(parts, ignore_index=True) if parts else df


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
        df["__源标准化文件路径"] = os.path.abspath(f)
        mapping_path = f[:-len("__standardized.csv")] + "__mapping.json"
        image = {}
        if os.path.exists(mapping_path):
            try:
                with open(mapping_path, encoding="utf-8") as mapping_file:
                    image = (json.load(mapping_file).get("文件画像") or {})
            except (OSError, ValueError, TypeError):
                image = {}
        df["__router_bank"] = image.get("router_bank") or image.get("bank") or "未识别"
        df["__inferred_bank"] = image.get("inferred_bank") or ""
        df["__bank_source"] = image.get("bank_source") or image.get("开户行识别来源") or "unknown"
        # 文件内行序：standardize 已把倒序文件翻正，故 0..n 即该文件的时间正序（含同秒多笔的原始相对顺序）。
        # 余额校验与输出排序都用它，绝不用交易时间当主键，避免同时刻多笔被打乱产生伪断点。
        df["__fileseq"] = range(len(df))
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


def dedup_cross_file(df):
    """折叠跨文件/重复导入的完全相同交易：同一「交易唯一编号」（内容指纹）出现多次时只保留一笔。

    交易唯一编号由 standardize 用 本方名称/账户+时间+对手+收支金额+余额 生成；同一文件内的真实重复笔
    已带序号后缀，因此此处出现的重复编号必然是同一笔交易的跨文件再导入（PDF 与 Excel 同源最常见）
    或整份文件重复提交——折叠安全、且全程可追溯（保留笔仍带来源文件名/行号，移除明细写入报告）。
    保留优先级：结构化表格优于 PDF 抽取、字段更完整者、来源行号更小者。
    返回 (deduped_df, dedup_info)。"""
    info = {"折叠组数": 0, "移除笔数": 0, "明细": []}
    if "交易唯一编号" not in df.columns or df.empty:
        return df, info
    std_cols = [c for c in df.columns if not c.endswith("_num") and not c.startswith("__")]
    d = df.copy()
    d["__nonempty"] = d[std_cols].apply(
        lambda r: sum(1 for v in r if str(v).strip() not in ("", "nan", "None")), axis=1)
    d["__rank"] = d["来源文件名"].fillna("").map(
        lambda fn: 1 if os.path.splitext(str(fn))[1].lower() == ".pdf" else 0)

    dup_mask = d.duplicated("交易唯一编号", keep=False)
    if dup_mask.any():
        for uid, g in d[dup_mask].groupby("交易唯一编号"):
            info["折叠组数"] += 1
            info["移除笔数"] += int(len(g) - 1)
            if len(info["明细"]) < 30:
                info["明细"].append({
                    "交易唯一编号": uid, "出现次数": int(len(g)),
                    "涉及来源文件": sorted(g["来源文件名"].fillna("").unique().tolist()),
                })
    d = d.sort_values(["交易唯一编号", "__rank", "__nonempty", "来源行号_num"],
                      ascending=[True, True, False, True])
    kept = d.drop_duplicates("交易唯一编号", keep="first").drop(columns=["__nonempty", "__rank"])
    return kept.reset_index(drop=True), info


def balance_check(df):
    """按本方账户分别做余额连续性校验。返回报告列表。"""
    results = []
    for acct, g in df.groupby(df["本方账户"].fillna(""), sort=True):
        # df 已由 order_accounts_for_continuity 按账户余额连续性排好序，这里直接按现有顺序校验。
        g = g.reset_index()
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
        checkable = max(1, int(bal.notna().sum()) - 1)
        rate = nb / checkable
        if nb == 0:
            note = "余额连续"
        elif rate > 0.3:
            # 真实对账单绝大多数行余额连续；断点率畸高几乎必然是「解析不全/漏行/只读了文件的部分行」
            # （大表被截断或抽样），而非排序问题——这正是某些大模型不跑脚本、手工读大表时的典型 bug。
            note = (f"断点率 {rate:.0%} 异常偏高（{nb}/{checkable}）：极可能是解析不全/漏行/只读取了文件部分行"
                    f"（如大表被截断或抽样），而非真实排序问题。请核对原始文件总行数、用脚本完整重跑后再判断。")
        else:
            note = "存在余额断点：多在跨月/跨文件边界或同秒多笔，少量也可能是对账单缺行，需人工复核而非自动修正"
        results.append({
            "本方账户": acct or "(空)",
            "校验状态": "通过" if nb == 0 else "预警",
            "异常数量": nb,
            "断点率": round(rate, 3),
            "疑似解析不全": bool(nb and rate > 0.3),
            "异常示例": examples,
            "说明": note,
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


def _first_nonempty(g, col):
    if col not in g.columns:
        return ""
    s = g[col].dropna().astype(str).str.strip()
    s = s[~s.isin(["", "nan", "None"])]
    return s.iloc[0] if len(s) else ""


def account_index(df):
    idx = []
    for acct, g in df.groupby(df["本方账户"].fillna("")):
        t = g["__t"].dropna()
        idx.append({
            "本方账户": acct or "(空)",
            "本方名称": g["本方名称"].dropna().iloc[0] if g["本方名称"].notna().any() else "",
            "开户行": _first_nonempty(g, "开户行"),
            "账户类型": _first_nonempty(g, "账户类型") or "未知",
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
    raw_count = int(len(df))

    # 相同明确账号先互补本方名称/银行；随后将两份高重合、账号都未知的配套文件归到共享虚拟账户。
    df, metadata_completion = complete_metadata_by_explicit_account(df)
    df, unknown_pairing = pair_unknown_account_sources(df)

    # 阶段一只判断单文件证据；阶段二可利用同批次其它文件的明确账号和交易重合证据补全未知账号。
    # 账号参与交易唯一编号内容指纹，因此必须在跨文件去重前重算编号。
    df, account_resolution = resolve_batch_accounts(df)
    df["router_bank"] = df["__router_bank"].fillna("未识别")
    df["inferred_bank"] = df["__inferred_bank"].fillna("")
    df["batch_pair"] = df.get("__batch_pair_bank", pd.Series("", index=df.index)).fillna("")
    df["bank_source"] = df.apply(
        lambda row: (
            "batch_pair" if _clean_text(row.get("batch_pair")) not in {"", "未知", "未识别"}
            else "internal_transaction_profile" if _clean_text(row.get("inferred_bank"))
                 and _clean_text(row.get("router_bank")) in {"", "未知", "未识别"}
            else "router" if _clean_text(row.get("router_bank")) not in {"", "未知", "未识别"}
            else _clean_text(row.get("__bank_source")) or "unknown"
        ),
        axis=1,
    )
    df = regenerate_transaction_ids(df)
    account_resolution = finalize_account_resolution_report(df, account_resolution)
    unknown_pairing = finalize_pair_report(df, unknown_pairing)

    # 跨文件/重复导入去重：折叠内容指纹（交易唯一编号）完全相同的交易，仅保留一笔（移除明细记入报告）。
    df, dedup_info = dedup_cross_file(df)

    # 先恢复「每个文件标准化后的余额正序」（去重会按编号哈希打乱顺序），再做账户级连续性重排——
    # 使「日内原序」候选的兜底序是文件内余额序、而非哈希乱序。
    df = df.sort_values(["本方账户", "来源文件名", "__fileseq"], kind="mergesort").reset_index(drop=True)
    # 账户级行序整理：按余额连续性把每个账户的跨文件交易排成可对账的正序（不靠交易时间硬排）。
    df = order_accounts_for_continuity(df)
    df_sorted = df

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
    for item in account_resolution["待复核明细"]:
        review.append({
            "事项类型": "本方账号归并待确认",
            "复核原因": item["复核原因"],
            "证据交易唯一编号列表": item["证据交易唯一编号列表"],
            "建议动作": "结合账户类型、余额链或原始对账单确认本方账号",
        })

    blocking = []
    warnings = []
    if dedup_info["移除笔数"]:
        warnings.append(f"跨文件去重折叠 {dedup_info['移除笔数']} 笔完全相同交易"
                        f"（{dedup_info['折叠组数']} 组）")
    if any(b["校验状态"] == "预警" for b in bal):
        warnings.append("存在余额断点，需人工复核")
    if dups:
        warnings.append(f"存在 {len(dups)} 组疑似重复交易")
    if account_resolution["待复核文件数"]:
        warnings.append(f"存在 {account_resolution['待复核文件数']} 个未知账号文件无法自动归并")

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
            "原始交易数": raw_count,
            "跨文件去重笔数": dedup_info["移除笔数"],
            "整合交易数": int(len(df)),
            "交易期间": {
                "开始日期": t.min().strftime("%Y-%m-%d") if len(t) else "",
                "结束日期": t.max().strftime("%Y-%m-%d") if len(t) else "",
            },
            "整体质量评分": score,
        },
        "账户索引": accts,
        "整合策略": {
            "同账号元数据补全": "相同明确账号且名称/银行候选唯一时，只补空值，不覆盖冲突值",
            "双未知账号配对": "两个来源核心交易重合率≥90%、至少3笔、银行和账户类型不冲突且候选唯一时，"
                          "共享批次虚拟账户；真实账号仍保持未知",
            "批次内账号归并顺序": "加载标准化文件后、重算交易唯一编号前、跨文件去重前",
            "批次内账号归并规则": "同主体同银行只有一个明确账号时自动归并；存在多个明确账号时，"
                              "仅在至少两笔时间/收支金额/余额/对手完全重合且唯一命中时归并，否则人工复核",
            "排序规则": "按账户·余额连续性最优（best_continuity_order）",
            "排序口径": "余额是对账真值。每个账户的跨文件交易在「原序/整体翻转/按日期升序/余额链重建」中"
                       "选余额断点最少的行序——既保留各文件原始对账口径，又正确跨文件归并、消除"
                       "倒序/日内倒序/同秒多笔/内部记账序≠时间戳导致的伪断点；不以交易时间硬排。",
            "跨文件去重规则": "交易唯一编号（内容指纹：本方名称/账户+时间+对手+收支金额+余额）完全相同即视为"
                          "同一笔交易的跨文件再导入，仅保留一笔；保留优先级：结构化表格>PDF、字段更全、行号更小",
            "疑似重复判断规则": ["本方账户", "交易时间", "收入金额", "支出金额", "账户余额", "对手名称"],
            "自有账户互转判断规则": "本方多账户间金额相等、时间≤3天、对手为本方名称/账户；仅标记不删除",
            "余额校验范围": "按本方账户分别校验",
        },
        "同账号元数据补全": metadata_completion,
        "批次未知账户配对": unknown_pairing,
        "批次内账号归并": account_resolution,
        "跨文件去重": dedup_info,
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
    keep = ["交易唯一编号", "交易时间", "本方名称", "本方账户", "开户行", "账户类型",
            "router_bank", "inferred_bank", "batch_pair", "bank_source",
            "对手名称", "对手账户", "收入金额", "支出金额", "交易金额", "账户余额",
            "银行备注", "账户方附言", "交易渠道", "来源文件名", "来源行号"]
    for c in keep:                       # 兼容旧版 standardized.csv（无开户行/账户类型列）
        if c not in df_sorted.columns:
            df_sorted[c] = ""
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
          f"{o['整合交易数']} 笔（原始 {o['原始交易数']}，跨文件去重 {o['跨文件去重笔数']}）/ "
          f"质量评分 {o['整体质量评分']}")
    print(f"  期间 {o['交易期间']['开始日期']} ~ {o['交易期间']['结束日期']}")
    print(f"  疑似重复 {len(report['疑似重复交易组'])} 组 / 互转候选 {len(report['自有账户互转组'])} 组 / "
          f"人工复核 {len(report['人工复核事项'])} 项")
    print(f"  -> {out_csv}")
    print(f"  -> {out_json}")


if __name__ == "__main__":
    main()
