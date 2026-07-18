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
import argparse, glob, hashlib, json, os, re, sys, unicodedata
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from itertools import combinations

try:
    import pandas as pd
    import numpy as np
except ImportError:
    sys.exit("需要 pandas/numpy：pip install pandas numpy")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import standardize as S   # 复用余额连续性行序整理（best_continuity_order）

NUMERIC = ["收入金额", "支出金额", "交易金额", "账户余额"]
ALIPAY_TRADE_ORDER_RE = re.compile(r"支付宝交易订单号=([^；;]+)")


def _clean_text(value):
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _alipay_trade_order(value):
    match = ALIPAY_TRADE_ORDER_RE.search(_clean_text(value))
    return match.group(1).strip() if match else ""


def _is_alipay_record(row):
    return "支付宝" in "".join(
        _clean_text(row.get(column))
        for column in ("开户行", "来源文件名", "交易渠道", "账户方附言")
    )


def _identity_text(value):
    """规范本方户名等身份文本，仅消除字符宽度、空白和大小写差异。"""
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


def _is_reciprocal_replaceable_account(value):
    """强反向互转证据可把未知或掩码账号升级为唯一完整账号。"""
    account = _clean_text(value)
    return _is_unknown_account(account) or "*" in account


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


def _accounts_equal(left, right):
    """账号确权只接受清洗后的完整数字串精确相等。"""
    a = "".join(char for char in _clean_text(left) if char.isdigit())
    b = "".join(char for char in _clean_text(right) if char.isdigit())
    return bool(a and b and a == b)


def _reciprocal_lookup_key(row, reverse=False):
    if _clean_text(row.get("__time_precision")) != "second":
        return None
    time = _clean_text(row.get("交易时间"))
    income = _overlap_amount(row.get("收入金额"))
    expense = _overlap_amount(row.get("支出金额"))
    if not time or bool(income) == bool(expense):
        return None
    direction = "收入" if income else "支出"
    if reverse:
        direction = "支出" if direction == "收入" else "收入"
    return time, direction, income or expense


RECIPROCAL_TIME_TOLERANCE_SECONDS = 5
RECIPROCAL_TOLERANT_MIN_MATCHES = 3
RECIPROCAL_TOLERANT_MIN_DATES = 3


def _reciprocal_time_second(row):
    """把标准交易时间转成秒级时间轴；无法解析时只参与原有精确匹配。"""
    value = pd.to_datetime(_clean_text(row.get("交易时间")), errors="coerce", format="mixed")
    if pd.isna(value):
        return None
    return int(value.value // 1_000_000_000)


def _reciprocal_window_keys(row, reverse=False):
    """返回同方向同金额、允许跨行记账秒差的有限候选键。"""
    exact = _reciprocal_lookup_key(row, reverse=reverse)
    second = _reciprocal_time_second(row)
    if exact is None or second is None:
        return []
    _, direction, amount = exact
    return [
        (second + offset, direction, amount)
        for offset in range(-RECIPROCAL_TIME_TOLERANCE_SECONDS,
                            RECIPROCAL_TIME_TOLERANCE_SECONDS + 1)
    ]


def infer_identity_from_reciprocal_transfers(df):
    """用同批次另一账户的反向互转记录补齐本方账号、户名和开户行。

    证据必须同时满足：金额反向一致，且目标行的对手名称能与证据行本方名称精确互证；
    双方来源时间都明确精确到秒时，时间完全一致沿用单笔强证据；仅时间相差不超过 5 秒时，
    要求至少三笔、跨三个日期形成唯一账号共识。日期级、分钟级或精度未知的记录不参与身份反推。
    名称有一侧缺失时，才允许用完整账号精确相等代替。长账号前缀关系不能单独触发身份推断。
    对手开户行只作为阶段二内部证据，最终标准流水不会输出该内部列。
    """
    result = df.copy()
    report = {"补全文件数": 0, "补全交易数": 0, "补全明细": [], "末四位归并文件数": 0}
    if result.empty or not {"本方名称", "本方账户", "开户行"}.issubset(result.columns):
        return result, report
    source_col = _source_col(result)
    if not source_col:
        return result, report

    identity_text_cache = {}
    identity_bank_cache = {}

    def cached_identity_text(value):
        text = _clean_text(value)
        if text not in identity_text_cache:
            identity_text_cache[text] = _identity_text(text)
        return identity_text_cache[text]

    def cached_identity_bank(value):
        text = _clean_text(value)
        if text not in identity_bank_cache:
            identity_bank_cache[text] = _identity_bank(text)
        return identity_bank_cache[text]

    source_keys = result[source_col].map(_clean_text)
    self_accounts = result["本方账户"].map(_explicit_account)
    opponent_accounts = result.get(
        "对手账户", pd.Series("", index=result.index, dtype=object)
    ).map(_explicit_account)
    self_account_keys = self_accounts.map(
        lambda value: "".join(char for char in value if char.isdigit()) if value else ""
    )
    opponent_account_keys = opponent_accounts.map(
        lambda value: "".join(char for char in value if char.isdigit()) if value else ""
    )
    self_name_keys = result["本方名称"].map(cached_identity_text)
    opponent_names = result.get(
        "对手名称", pd.Series("", index=result.index, dtype=object)
    ).map(_clean_text)
    opponent_name_keys = opponent_names.map(cached_identity_text)
    opponent_bank_texts = result.get(
        "__对手开户行", pd.Series("", index=result.index, dtype=object)
    ).map(_clean_text)
    transaction_ids = result.get(
        "交易唯一编号", pd.Series("", index=result.index, dtype=object)
    ).map(_clean_text)
    time_text = result["交易时间"].map(_clean_text)
    time_precision = result.get(
        "__time_precision", pd.Series("", index=result.index, dtype=object)
    ).map(_clean_text)
    income_amounts = result["收入金额"].map(_overlap_amount)
    expense_amounts = result["支出金额"].map(_overlap_amount)
    parsed_times = pd.to_datetime(time_text, errors="coerce", format="mixed")
    time_seconds = pd.Series(index=result.index, dtype="Int64")
    valid_times = parsed_times.notna()
    time_seconds.loc[valid_times] = (
        parsed_times.loc[valid_times].astype("int64") // 1_000_000_000
    ).astype("int64")

    exact_keys = {}
    reverse_keys = {}
    for index in result.index:
        income = income_amounts.loc[index]
        expense = expense_amounts.loc[index]
        if time_precision.loc[index] != "second" or not time_text.loc[index] or bool(income) == bool(expense):
            continue
        direction = "收入" if income else "支出"
        amount = income or expense
        exact_keys[index] = (time_text.loc[index], direction, amount)
        reverse_keys[index] = (
            time_text.loc[index],
            "支出" if direction == "收入" else "收入",
            amount,
        )

    evidence_by_key = defaultdict(list)
    evidence_by_window_key = defaultdict(list)
    for index in result.index:
        self_account = self_accounts.loc[index]
        opponent_account = opponent_accounts.loc[index]
        key = reverse_keys.get(index)
        if self_account and opponent_account and key:
            evidence_by_key[key].append(index)
            second = time_seconds.loc[index]
            if pd.notna(second):
                _, direction, amount = key
                evidence_by_window_key[(int(second), direction, amount)].append(index)

    touched_sources = set()
    for source, rows in result.groupby(source_keys, sort=True, dropna=False):
        exact_candidates = []
        tolerant_candidates = []
        tolerant_pairs = set()
        for target_index in rows.index:
            key = exact_keys.get(target_index)
            if not key:
                continue
            evidence_matches = [
                (evidence_index, "exact")
                for evidence_index in evidence_by_key.get(key, [])
            ]
            exact_evidence_indices = {item[0] for item in evidence_matches}
            second = time_seconds.loc[target_index]
            window_keys = []
            if pd.notna(second):
                _, direction, amount = key
                window_keys = [
                    (int(second) + offset, direction, amount)
                    for offset in range(
                        -RECIPROCAL_TIME_TOLERANCE_SECONDS,
                        RECIPROCAL_TIME_TOLERANCE_SECONDS + 1,
                    )
                ]
            for window_key in window_keys:
                evidence_matches.extend(
                    (evidence_index, "tolerant")
                    for evidence_index in evidence_by_window_key.get(window_key, [])
                    if evidence_index not in exact_evidence_indices
                )
            for evidence_index, match_mode in evidence_matches:
                if source_keys.loc[evidence_index] == source:
                    continue
                opponent_name = opponent_name_keys.loc[target_index]
                evidence_name = self_name_keys.loc[evidence_index]
                name_match = bool(opponent_name and evidence_name and opponent_name == evidence_name)
                exact_account_match = bool(
                    opponent_account_keys.loc[target_index]
                    and opponent_account_keys.loc[target_index] == self_account_keys.loc[evidence_index]
                )
                identity_match = name_match or (
                    (not opponent_name or not evidence_name) and exact_account_match
                )
                if not identity_match:
                    continue
                target_account = opponent_accounts.loc[evidence_index]
                if not target_account:
                    continue
                bank_text = opponent_bank_texts.loc[evidence_index]
                canonical_bank = S.infer_bank(bank_text) if bank_text else ""
                if canonical_bank in {"农村商业银行", "农村信用社", "村镇银行"}:
                    canonical_bank = ""
                candidate = {
                    "account": target_account,
                    "name": opponent_names.loc[evidence_index],
                    "bank": canonical_bank,
                    "evidence_id": transaction_ids.loc[evidence_index],
                    "target_date": time_text.loc[target_index][:10],
                    "match_mode": match_mode,
                }
                if match_mode == "exact":
                    exact_candidates.append(candidate)
                elif (target_index, evidence_index) not in tolerant_pairs:
                    tolerant_pairs.add((target_index, evidence_index))
                    tolerant_candidates.append(candidate)
        candidates = exact_candidates
        tolerant_dates = {item["target_date"] for item in tolerant_candidates if item["target_date"]}
        if not candidates:
            if (len(tolerant_candidates) < RECIPROCAL_TOLERANT_MIN_MATCHES
                    or len(tolerant_dates) < RECIPROCAL_TOLERANT_MIN_DATES):
                continue
            candidates = tolerant_candidates
        accounts = sorted({item["account"] for item in candidates})
        if len(accounts) != 1:
            continue
        account = accounts[0]
        matched = [item for item in candidates if item["account"] == account]
        names = {
            cached_identity_text(item["name"]): item["name"]
            for item in matched if cached_identity_text(item["name"])
        }
        banks = {
            cached_identity_bank(item["bank"]): item["bank"]
            for item in matched if cached_identity_bank(item["bank"])
        }
        name = next(iter(names.values())) if len(names) == 1 else ""
        bank = next(iter(banks.values())) if len(banks) == 1 else ""
        indices = rows.index.tolist()
        changed_fields = []
        if rows["本方账户"].map(_is_reciprocal_replaceable_account).all():
            result.loc[indices, "本方账户"] = account
            changed_fields.append("本方账户")
        if name and not any(_clean_text(value) for value in rows["本方名称"]):
            result.loc[indices, "本方名称"] = name
            changed_fields.append("本方名称")
        if bank and not any(cached_identity_bank(value) for value in rows["开户行"]):
            result.loc[indices, "开户行"] = bank
            changed_fields.append("开户行")
        if not changed_fields:
            continue
        touched_sources.add(source)
        report["补全交易数"] += len(indices)
        report["补全明细"].append({
            "来源文件": sorted({_clean_text(value) for value in rows["来源文件名"] if _clean_text(value)}),
            "本方账户": account,
            "本方名称": name,
            "开户行": bank,
            "补全字段": changed_fields,
            "证据交易唯一编号列表": sorted({item["evidence_id"] for item in matched if item["evidence_id"]}),
            "归并方式": (
                "跨账户反向互转记录"
                if exact_candidates else "跨账户反向互转记录（时间容差共识）"
            ),
            "时间容差秒数": 0 if exact_candidates else RECIPROCAL_TIME_TOLERANCE_SECONDS,
            "容差证据数": 0 if exact_candidates else len(matched),
            "容差证据日期数": 0 if exact_candidates else len(tolerant_dates),
        })

    # 已确认账号还可从批次内其它流水的“对手账号/对手开户行”获得银行元数据。
    current_self_accounts = result["本方账户"].map(_explicit_account)
    current_opponent_accounts = result.get(
        "对手账户", pd.Series("", index=result.index, dtype=object)
    ).map(_explicit_account)
    current_opponent_account_keys = current_opponent_accounts.map(
        lambda value: "".join(char for char in value if char.isdigit()) if value else ""
    )
    verified_accounts = sorted({value for value in current_self_accounts if value})
    for account in verified_accounts:
        account_key = "".join(char for char in account if char.isdigit())
        own_mask = current_self_accounts == account
        counterpart = result[current_opponent_account_keys == account_key]
        names = {
            cached_identity_text(value): _clean_text(value)
            for value in pd.concat([result.loc[own_mask, "本方名称"], counterpart["对手名称"]])
            if cached_identity_text(value)
        }
        banks = {}
        for value in pd.concat([
            result.loc[own_mask, "开户行"],
            counterpart.get("__对手开户行", pd.Series(dtype=str)),
        ]):
            canonical = S.infer_bank(_clean_text(value)) if _clean_text(value) else ""
            if canonical and canonical not in {"农村商业银行", "农村信用社", "村镇银行"}:
                banks[cached_identity_bank(canonical)] = canonical
        name = next(iter(names.values())) if len(names) == 1 else ""
        bank = next(iter(banks.values())) if len(banks) == 1 else ""
        for source, rows in result[own_mask].groupby(
            result.loc[own_mask, source_col].map(_clean_text), sort=True, dropna=False
        ):
            fields = []
            if name and not any(_clean_text(value) for value in rows["本方名称"]):
                result.loc[rows.index, "本方名称"] = name
                fields.append("本方名称")
            if bank and not any(cached_identity_bank(value) for value in rows["开户行"]):
                result.loc[rows.index, "开户行"] = bank
                fields.append("开户行")
            if not fields:
                continue
            if source not in touched_sources:
                report["补全交易数"] += len(rows)
            touched_sources.add(source)
            report["补全明细"].append({
                "来源文件": sorted({_clean_text(value) for value in rows["来源文件名"] if _clean_text(value)}),
                "本方账户": account,
                "本方名称": name,
                "开户行": bank,
                "补全字段": fields,
                "证据交易唯一编号列表": [],
                "归并方式": "批次对手账号元数据唯一匹配",
            })

    # 分月文件本身没有抬头账号时，用文件名中明确标注的末四位与本批次已验证账号做唯一匹配。
    profiles = {}
    final_explicit_accounts = result["本方账户"].map(_explicit_account)
    explicit = result[final_explicit_accounts != ""].copy()
    explicit["__explicit_account"] = final_explicit_accounts.loc[explicit.index]
    for account, rows in explicit.groupby("__explicit_account", sort=True):
        names = {
            cached_identity_text(value): _clean_text(value)
            for value in rows["本方名称"] if cached_identity_text(value)
        }
        banks = {
            cached_identity_bank(value): _clean_text(value)
            for value in rows["开户行"] if cached_identity_bank(value)
        }
        if len(names) == 1 and len(banks) == 1:
            profiles[account] = (next(iter(names.values())), next(iter(banks.values())))
    profile_account_keys = {account: _account_key(account) for account in profiles}
    unknown_mask = result["本方账户"].map(_is_unknown_account)
    unknown = result[unknown_mask]
    for source, rows in unknown.groupby(source_keys.loc[unknown.index], sort=True, dropna=False):
        source_text = " ".join({_clean_text(value) for value in rows["来源文件名"] if _clean_text(value)})
        matches = [
            account for account in profiles
            if len(profile_account_keys[account]) >= 4
            and re.search(rf"(?<!\d){re.escape(profile_account_keys[account][-4:])}(?!\d)", source_text)
        ]
        if len(matches) != 1:
            continue
        account = matches[0]
        name, bank = profiles[account]
        indices = rows.index.tolist()
        result.loc[indices, "本方账户"] = account
        result.loc[indices, "本方名称"] = name
        result.loc[indices, "开户行"] = bank
        touched_sources.add(source)
        report["补全交易数"] += len(indices)
        report["末四位归并文件数"] += 1
        report["补全明细"].append({
            "来源文件": sorted({_clean_text(value) for value in rows["来源文件名"] if _clean_text(value)}),
            "本方账户": account,
            "本方名称": name,
            "开户行": bank,
            "补全字段": ["本方账户", "本方名称", "开户行"],
            "证据交易唯一编号列表": [],
            "归并方式": "来源文件标注末四位与批次已验证账号唯一匹配",
        })
    report["补全文件数"] = len(touched_sources)
    return result, report


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


LEGAL_ENTITY_NAME_MARKERS = ("有限责任公司", "股份有限公司", "有限公司", "农民专业合作社", "合作社")


def complete_account_type_by_verified_identity(df):
    """明确账号对应唯一法定企业户名时，将空白/未知/拟对公提升为对公。

    本方名称必须来自标准字段中的账户身份链；自然人名称、名称冲突或已有个人证据均不改写。
    """
    result = df.copy()
    report = {"补全账户数": 0, "补全交易数": 0, "补全明细": []}
    if result.empty or not {"本方账户", "本方名称", "账户类型"}.issubset(result.columns):
        return result, report

    result["__verified_account"] = result["本方账户"].map(_explicit_account)
    for account, rows in result[result["__verified_account"] != ""].groupby("__verified_account", sort=True):
        names = {
            _identity_text(value): _clean_text(value)
            for value in rows["本方名称"]
            if _identity_text(value)
        }
        if len(names) != 1:
            continue
        name = next(iter(names.values()))
        if not any(marker in name for marker in LEGAL_ENTITY_NAME_MARKERS):
            continue
        known_types = {
            _clean_text(value) for value in rows["账户类型"]
            if _clean_text(value) in {"个人", "对公"}
        }
        if "个人" in known_types:
            continue
        fill_mask = rows["账户类型"].map(_clean_text).isin({"", "未知", "拟对公"})
        indices = rows.index[fill_mask]
        if len(indices) == 0:
            continue
        result.loc[indices, "账户类型"] = "对公"
        report["补全账户数"] += 1
        report["补全交易数"] += len(indices)
        report["补全明细"].append({
            "本方账户": account,
            "本方名称": name,
            "补全交易数": len(indices),
            "判断依据": "明确本方账号对应唯一法定企业户名",
        })
    return result.drop(columns=["__verified_account"]), report


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


def _filename_series_family(value):
    """文件名只用于召回同类分卷候选。"""
    stem = unicodedata.normalize("NFKC", os.path.splitext(os.path.basename(_clean_text(value)))[0])
    compact = re.sub(r"\s+", "", stem).casefold()
    return re.sub(
        r"(?:[+_\-][a-z]|(?<=[\u4e00-\u9fff])[a-z]|(?:part|第)\d+(?:部分|卷)?|\d+)$",
        "", compact, flags=re.IGNORECASE,
    ).rstrip("+_-")


def _cents(value):
    try:
        return int(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100)
    except (InvalidOperation, TypeError, ValueError):
        return None


def merge_balance_continuous_sources(df):
    """以精确余额链归并相邻分卷。

    默认仍要求同 fingerprint、银行、账户类型和文件名家族。只有 YAML 明确声明
    ``series_family`` 时，才允许兼容模板跨 fingerprint 建链。有明确账号锚点时
    两卷即可；完全没有身份锚点时也允许两卷以一条无分叉的精确余额边界归并。
    空白身份可以组成逻辑账户，但不能伪造真实户名、账号或银行。
    """
    result = df.copy()
    report = {"已归并组数": 0, "已归并文件数": 0, "已归并交易数": 0, "归并明细": []}
    source_col = _source_col(result)
    required = {"本方账户", "本方名称", "开户行", "账户类型", "来源文件名",
                "__fingerprint_id", "__t", "__fileseq"}
    if result.empty or not source_col or not required.issubset(result.columns):
        return result, report

    buckets = defaultdict(list)
    for source, rows in result.groupby(result[source_col].map(_clean_text), sort=True):
        fingerprint = _first_nonempty(rows, "__fingerprint_id")
        series_family = _first_nonempty(rows, "__series_family")
        filename = _first_nonempty(rows, "来源文件名")
        family = _filename_series_family(filename)
        banks = {_identity_bank(value) for value in rows["开户行"] if _identity_bank(value)}
        types = {_clean_text(value) for value in rows["账户类型"]
                 if _clean_text(value) in {"个人", "对公"}}
        proposed_corporate = any(
            _clean_text(value) == "拟对公" for value in rows["账户类型"]
        )
        if series_family:
            # series_family 是 YAML 显式兼容声明，不再依赖文件名或每卷都带银行抬头。
            # 账户类型允许空白/未知/拟对公参与，整链阶段仍会阻断个人/对公冲突。
            bucket_key = ("series_family", series_family)
        else:
            if (not fingerprint or len(family) < 4 or len(banks) != 1
                    or len(types) != 1):
                continue
            bucket_key = (
                "fingerprint", fingerprint, next(iter(banks)), next(iter(types)), family,
            )
        ordered = rows.sort_values("__fileseq", kind="mergesort")
        usable = ordered[
            ordered["账户余额_num"].notna()
            & (ordered["收入金额_num"].notna() ^ ordered["支出金额_num"].notna())
        ]
        times = rows["__t"].dropna()
        if usable.empty or times.empty:
            continue
        buckets[bucket_key].append({
            "source": _clean_text(source), "rows": rows, "filename": filename,
            "first": usable.iloc[0], "last": usable.iloc[-1],
            "start": times.min(), "end": times.max(),
            "fingerprint": fingerprint, "series_family": series_family,
            "filename_family": family, "banks": banks,
            "account_types": types, "proposed_corporate": proposed_corporate,
            "accounts": {_explicit_account(value) for value in rows["本方账户"]
                         if _explicit_account(value)},
            "names": {_identity_text(value): _clean_text(value)
                      for value in rows["本方名称"] if _identity_text(value)},
        })

    def boundary(left, right):
        accounts = left["accounts"] | right["accounts"]
        names = set(left["names"]) | set(right["names"])
        banks = left["banks"] | right["banks"]
        account_types = left["account_types"] | right["account_types"]
        if (len(accounts) > 1 or len(names) > 1 or len(banks) > 1
                or len(account_types) > 1
                or left["end"] > right["start"]):
            return None
        previous = _cents(left["last"].get("账户余额_num"))
        next_balance = _cents(right["first"].get("账户余额_num"))
        income = _cents(right["first"].get("收入金额_num"))
        expense = _cents(right["first"].get("支出金额_num"))
        if previous is None or next_balance is None or (income is None) == (expense is None):
            return None
        amount = income if income is not None else -expense
        if amount == 0 or previous + amount != next_balance:
            return None
        return {
            "from": left["filename"], "to": right["filename"],
            "previous_balance": previous / 100, "next_amount": amount / 100,
            "next_balance": next_balance / 100, "difference": 0.0,
            "_indices": [left["last"].name, right["first"].name],
        }

    def merge_chain(key, members, links, ambiguous_sources=None):
        if len(members) < 2:
            return
        ambiguous_sources = ambiguous_sources or set()
        mode = key[0]
        if mode == "series_family":
            _, series_family = key
            family = ""
        else:
            _, fingerprint, _bank_key, account_type, family = key
            series_family = ""
        accounts = {account for member in members for account in member["accounts"]}
        names = {name_key: name for member in members
                 for name_key, name in member["names"].items()}
        banks = {bank for member in members for bank in member["banks"]}
        account_types = {
            account_type for member in members
            for account_type in member["account_types"]
        }
        if (len(accounts) > 1 or len(names) > 1 or len(banks) > 1
                or len(account_types) > 1):
            return
        if mode == "series_family":
            # series_family 已限定兼容分卷家族；无明确账号时仍要求无分叉的精确余额边界。
            if not accounts:
                if any(member["source"] in ambiguous_sources for member in members):
                    return
                if len(members) < 2 or len(links) < 1:
                    return
            account_type = (
                next(iter(account_types)) if account_types
                else "拟对公" if any(member["proposed_corporate"] for member in members)
                else "未知"
            )
        files = sorted(member["filename"] for member in members)
        fingerprints = sorted({member["fingerprint"] for member in members if member["fingerprint"]})
        identity_key = series_family or (fingerprints[0] if fingerprints else "")
        digest = hashlib.md5("\n".join([identity_key, *files]).encode()).hexdigest()[:10]
        series_id = f"SERIES-{digest}"
        rows = pd.concat([member["rows"] for member in members])
        bank = next(iter(banks)) if banks else _first_nonempty(rows, "开户行")
        account = (
            next(iter(accounts)) if accounts
            else f"批次虚拟账户#{bank or '未识别'}#{series_id}"
        )
        result.loc[rows.index, "本方账户"] = account
        if len(names) == 1:
            result.loc[rows.index, "本方名称"] = next(iter(names.values()))
        result.loc[rows.index, "开户行"] = bank
        result.loc[rows.index, "账户类型"] = account_type
        evidence_indices = [index for link in links for index in link.pop("_indices")]
        report["归并明细"].append({
            "source_series_id": series_id, "逻辑本方账户": account,
            "成员文件": files,
            "fingerprint_id": fingerprints[0] if len(fingerprints) == 1 else "",
            "fingerprint_ids": fingerprints,
            "series_family": series_family,
            "身份锚点": "明确账号" if accounts else "无身份锚点强余额链",
            "filename_family": family, "balance_links": links,
            "_证据行索引": evidence_indices,
        })
        report["已归并组数"] += 1
        report["已归并文件数"] += len(members)
        report["已归并交易数"] += len(rows)

    for key, candidates in buckets.items():
        candidates.sort(key=lambda item: (item["start"], item["source"]))
        ambiguous_sources = set()
        if key[0] == "series_family":
            predecessors = defaultdict(set)
            successors = defaultdict(set)
            for index, left in enumerate(candidates):
                for right in candidates[index + 1:]:
                    if boundary(left, right):
                        successors[left["source"]].add(right["source"])
                        predecessors[right["source"]].add(left["source"])
            ambiguous_sources = {
                source for source, values in successors.items() if len(values) > 1
            } | {
                source for source, values in predecessors.items() if len(values) > 1
            }
        chain, links = [candidates[0]], []
        for left, right in zip(candidates, candidates[1:]):
            link = boundary(left, right)
            if link:
                chain.append(right)
                links.append(link)
            else:
                merge_chain(key, chain, links, ambiguous_sources)
                chain, links = [right], []
        merge_chain(key, chain, links, ambiguous_sources)
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
            review_reason = "本方名称或银行缺失/不一致，无法建立批次归并候选组"
        else:
            identity_group = result.loc[
                (result["__identity_name"] == name_key) & (result["__identity_bank"] == bank_key)
            ].copy()
            identity_group["__explicit_account"] = identity_group["本方账户"].map(_explicit_account)
            explicit = identity_group.loc[identity_group["__explicit_account"] != ""]
            accounts = sorted(explicit["__explicit_account"].unique().tolist())
            if not accounts:
                review_reason = "同户名同银行组内没有可验证的明确本方账号"
            elif len(accounts) == 1:
                target = accounts[0]
                method = "同户名同银行唯一明确账号"
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
                    review_reason = "同户名同银行存在多个明确账号，交易重合证据不能唯一确定归属"

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
            "本方名称": _clean_text(source_rows["本方名称"].iloc[0]),
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


def finalize_series_report(df, report):
    for item in report["归并明细"]:
        indices = item.pop("_证据行索引", [])
        item["证据交易唯一编号列表"] = df.loc[indices, "交易唯一编号"].tolist()
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
        # 批量代发的识别与余额口径统一放在 shared core，本阶段不复制业务判断。
        rows = S.continuity_rows(g.to_dict("records"))
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
        df["__fingerprint_id"] = image.get("fingerprint_id") or ""
        df["__series_family"] = image.get("series_family") or ""
        # 时间精度来自每笔标准化时间文本：日期级记录保持 YYYY-MM-DD，不能因同文件
        # 其它行含秒而被整体提升为 second。文件画像仅保留汇总审计值，不参与逐笔运算。
        df["__time_precision"] = df["交易时间"].map(S.normalized_transaction_time_precision)
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
    df["__t"] = pd.to_datetime(df["交易时间"], errors="coerce", format="mixed")
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

    def dedup_key(row):
        uid = _clean_text(row.get("交易唯一编号"))
        if not _is_alipay_record(row):
            return uid
        trade_order = _alipay_trade_order(row.get("账户方附言"))
        if trade_order:
            return f"{uid}#支付宝交易订单号={trade_order}"
        # 支付宝缺交易订单号时证据不足，不允许跨来源自动折叠。
        return "#".join((uid, "支付宝交易订单号缺失", _clean_text(row.get("__源标准化文件路径")),
                         _clean_text(row.get("来源文件名")), _clean_text(row.get("来源行号"))))

    d["__dedup_key"] = d.apply(dedup_key, axis=1)

    dup_mask = d.duplicated("__dedup_key", keep=False)
    if dup_mask.any():
        for _, g in d[dup_mask].groupby("__dedup_key"):
            info["折叠组数"] += 1
            info["移除笔数"] += int(len(g) - 1)
            if len(info["明细"]) < 30:
                info["明细"].append({
                    "交易唯一编号": _clean_text(g.iloc[0].get("交易唯一编号")),
                    "支付宝交易订单号": _alipay_trade_order(g.iloc[0].get("账户方附言")),
                    "出现次数": int(len(g)),
                    "涉及来源文件": sorted(g["来源文件名"].fillna("").unique().tolist()),
                })
    d = d.sort_values(["__dedup_key", "__rank", "__nonempty", "来源行号_num"],
                      ascending=[True, True, False, True])
    kept = d.drop_duplicates("__dedup_key", keep="first").drop(
        columns=["__dedup_key", "__nonempty", "__rank"])
    return kept.reset_index(drop=True), info


def _exact_overlap_key(row):
    """同一明确账户跨文件重叠所需的强键；任一核心字段缺失即不参与。"""
    account = _account_key(row.get("本方账户"))
    time = pd.to_datetime(row.get("交易时间"), errors="coerce", format="mixed")
    amount = _cross_format_amount_key(row)
    balance = _overlap_amount(row.get("账户余额"))
    if not account or pd.isna(time) or amount is None or balance == "":
        return None
    return account, time.isoformat(), *amount, balance


def dedup_same_account_exact_overlap(df):
    """折叠同一明确账户在不同文件中的精确重叠交易。

    强键同时要求交易时间、收支方向、金额和交易后余额完全一致。相同来源内出现重复键，或任一侧
    不能形成跨来源唯一对应时一律保留；自动折叠时保留标准字段更完整的记录。该规则主要处理同一
    银行分段导出区间重叠、但其中一版缺少对手字段而导致内容指纹不同的情况。
    """
    info = {"规则版本": "same-account-exact-overlap-v1", "折叠组数": 0, "移除笔数": 0, "明细": []}
    if df.empty or not {"本方账户", "来源文件名"}.issubset(df.columns):
        return df, info

    result = df.copy()
    groups = defaultdict(list)
    for index, row in result.iterrows():
        key = _exact_overlap_key(row)
        if key is not None:
            groups[key].append(index)

    std_cols = [c for c in result.columns if not c.endswith("_num") and not c.startswith("__")]
    remove_indices = set()
    for key, indices in groups.items():
        if len(indices) < 2:
            continue
        by_source = defaultdict(list)
        for index in indices:
            by_source[_clean_text(result.at[index, "来源文件名"])].append(index)
        if len(by_source) < 2 or any(len(values) != 1 for values in by_source.values()):
            continue

        ranked = sorted(
            indices,
            key=lambda index: (
                -sum(
                    1 for column in std_cols
                    if _clean_text(result.at[index, column])
                ),
                _clean_text(result.at[index, "来源文件名"]),
                index,
            ),
        )
        keep_index = ranked[0]
        removed = ranked[1:]
        remove_indices.update(removed)
        info["折叠组数"] += 1
        info["移除笔数"] += len(removed)
        if len(info["明细"]) < 30:
            info["明细"].append({
                "重复组编号": f"EO-{info['折叠组数']:06d}",
                "本方账户": _clean_text(result.at[keep_index, "本方账户"]),
                "交易时间": _clean_text(result.at[keep_index, "交易时间"]),
                "保留交易唯一编号": _clean_text(result.at[keep_index, "交易唯一编号"]),
                "保留来源文件": _clean_text(result.at[keep_index, "来源文件名"]),
                "移除交易唯一编号列表": [
                    _clean_text(result.at[index, "交易唯一编号"]) for index in removed
                ],
                "移除来源文件列表": [
                    _clean_text(result.at[index, "来源文件名"]) for index in removed
                ],
            })

    if remove_indices:
        result = result.drop(index=sorted(remove_indices))
    return result.reset_index(drop=True), info


def _source_format(source):
    return os.path.splitext(_clean_text(source))[1].lower()


def _source_stem(source):
    stem = os.path.splitext(os.path.basename(_clean_text(source)))[0]
    return _identity_text(stem)


def _cross_format_amount_key(row):
    """返回跨格式对齐使用的（方向, 金额分值）；方向不明确时拒绝参与。"""
    def cents(value):
        text = _clean_text(value).replace(",", "")
        if not text:
            return None
        try:
            amount = Decimal(text).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError):
            return None
        return int(amount * 100)

    income = cents(row.get("收入金额"))
    expense = cents(row.get("支出金额"))
    income = income if income not in (None, 0) else None
    expense = expense if expense not in (None, 0) else None
    if bool(income) == bool(expense):
        return None
    return ("收入", abs(income)) if income else ("支出", abs(expense))


def _cross_format_match_key(row):
    amount = _cross_format_amount_key(row)
    opponent_account = "".join(
        char for char in _clean_text(row.get("对手账户")) if char.isdigit()
    )
    time = pd.to_datetime(row.get("交易时间"), errors="coerce", format="mixed")
    if amount is None or len(opponent_account) < 8 or pd.isna(time):
        return None
    return (*amount, opponent_account), time


def _source_identity(rows):
    accounts = {
        _identity_text(value) for value in rows["本方账户"]
        if _clean_text(value) and not _is_unknown_account(value)
    }
    banks = {
        _identity_bank(value) for value in rows["开户行"]
        if _identity_bank(value)
    }
    return accounts, banks


def align_same_source_cross_format(df, min_matches=20, min_coverage=0.98, max_seconds=60):
    """逐笔折叠同名 PDF 与 XLS/XLSX 的强匹配交易。

    仅在同一文件名主体、同一已知本方账户、银行不冲突，且较小来源覆盖率达到门槛时启用。
    强匹配键为方向、金额分值、完整数字对手账户，再要求时间差不超过 max_seconds；只有双方
    候选都唯一的一对一记录才会匹配。结构化表格记录保留，PDF 独有记录始终保留。
    """
    info = {
        "规则版本": "same-source-cross-format-v1",
        "折叠组数": 0,
        "移除笔数": 0,
        "来源组": [],
        "明细": [],
        "待核查候选": [],
    }
    if df.empty or not {"来源文件名", "本方账户", "开户行"}.issubset(df.columns):
        return df, info

    result = df.copy()
    sources = sorted({_clean_text(value) for value in result["来源文件名"] if _clean_text(value)})
    by_stem = defaultdict(list)
    for source in sources:
        if _source_format(source) in {".pdf", ".xls", ".xlsx"}:
            by_stem[_source_stem(source)].append(source)

    remove_indices = set()
    for stem, group_sources in sorted(by_stem.items()):
        pdf_sources = [source for source in group_sources if _source_format(source) == ".pdf"]
        table_sources = [source for source in group_sources if _source_format(source) in {".xls", ".xlsx"}]
        if len(pdf_sources) != 1 or len(table_sources) != 1:
            continue

        pdf_source, table_source = pdf_sources[0], table_sources[0]
        pdf_rows = result[result["来源文件名"].fillna("").eq(pdf_source)]
        table_rows = result[result["来源文件名"].fillna("").eq(table_source)]
        pdf_accounts, pdf_banks = _source_identity(pdf_rows)
        table_accounts, table_banks = _source_identity(table_rows)
        account_ok = len(pdf_accounts) == 1 and pdf_accounts == table_accounts
        bank_ok = not pdf_banks or not table_banks or pdf_banks == table_banks
        group_report = {
            "文件名主体": stem,
            "PDF来源": pdf_source,
            "表格来源": table_source,
            "PDF笔数": int(len(pdf_rows)),
            "表格笔数": int(len(table_rows)),
            "强匹配笔数": 0,
            "较小来源覆盖率": 0.0,
            "歧义记录数": 0,
            "待核查候选数": 0,
            "自动折叠": False,
            "未启用原因": "",
        }
        if not account_ok or not bank_ok:
            group_report["未启用原因"] = "本方账户不一致或开户行冲突"
            info["来源组"].append(group_report)
            continue

        pdf_keys = {}
        table_keys = {}
        pdf_index = defaultdict(list)
        table_index = defaultdict(list)
        for index, row in pdf_rows.iterrows():
            matched = _cross_format_match_key(row)
            if matched:
                pdf_keys[index] = matched
                pdf_index[matched[0]].append((index, matched[1]))
        for index, row in table_rows.iterrows():
            matched = _cross_format_match_key(row)
            if matched:
                table_keys[index] = matched
                table_index[matched[0]].append((index, matched[1]))

        pdf_candidates = defaultdict(set)
        table_candidates = defaultdict(set)
        for key in set(pdf_index) & set(table_index):
            table_entries = sorted(table_index[key], key=lambda item: item[1])
            table_times = [item[1] for item in table_entries]
            for pdf_index_value, pdf_time in pdf_index[key]:
                lower = pdf_time - timedelta(seconds=max_seconds)
                upper = pdf_time + timedelta(seconds=max_seconds)
                start = bisect_left(table_times, lower)
                end = bisect_right(table_times, upper)
                for table_index_value, _table_time in table_entries[start:end]:
                    pdf_candidates[pdf_index_value].add(table_index_value)
                    table_candidates[table_index_value].add(pdf_index_value)

        pairs = []
        ambiguous = set()
        for pdf_index_value, candidates in pdf_candidates.items():
            if len(candidates) != 1:
                ambiguous.add(pdf_index_value)
                ambiguous.update(candidates)
                continue
            table_index_value = next(iter(candidates))
            if len(table_candidates[table_index_value]) != 1:
                ambiguous.add(pdf_index_value)
                ambiguous.add(table_index_value)
                continue
            pairs.append((pdf_index_value, table_index_value))

        # 缺少完整对手账户时绝不自动折叠；若方向、金额、时间窗口仍互相唯一，只登记待核查候选。
        weak_pdf = defaultdict(list)
        weak_table = defaultdict(list)
        for index, row in pdf_rows.iterrows():
            if index in pdf_keys:
                continue
            amount = _cross_format_amount_key(row)
            time = pd.to_datetime(row.get("交易时间"), errors="coerce", format="mixed")
            if amount and not pd.isna(time):
                weak_pdf[amount].append((index, time))
        for index, row in table_rows.iterrows():
            if index in table_keys:
                continue
            amount = _cross_format_amount_key(row)
            time = pd.to_datetime(row.get("交易时间"), errors="coerce", format="mixed")
            if amount and not pd.isna(time):
                weak_table[amount].append((index, time))
        weak_candidates = []
        for amount in set(weak_pdf) & set(weak_table):
            for pdf_index_value, pdf_time in weak_pdf[amount]:
                candidates = [
                    (table_index_value, table_time)
                    for table_index_value, table_time in weak_table[amount]
                    if abs((pdf_time - table_time).total_seconds()) <= max_seconds
                ]
                if len(candidates) != 1:
                    continue
                table_index_value, table_time = candidates[0]
                reverse = [
                    other_pdf_index
                    for other_pdf_index, other_pdf_time in weak_pdf[amount]
                    if abs((other_pdf_time - table_time).total_seconds()) <= max_seconds
                ]
                if len(reverse) == 1:
                    weak_candidates.append((pdf_index_value, table_index_value, pdf_time, table_time, amount))

        group_report["待核查候选数"] = len(weak_candidates)
        for pdf_index_value, table_index_value, pdf_time, table_time, amount in weak_candidates:
            pdf_row = result.loc[pdf_index_value]
            table_row = result.loc[table_index_value]
            info["待核查候选"].append({
                "PDF交易唯一编号": _clean_text(pdf_row.get("交易唯一编号")),
                "表格交易唯一编号": _clean_text(table_row.get("交易唯一编号")),
                "PDF来源文件": pdf_source,
                "表格来源文件": table_source,
                "收支方向": amount[0],
                "金额分值": amount[1],
                "时间差秒": abs((pdf_time - table_time).total_seconds()),
                "未自动折叠原因": "缺少完整对手账户",
            })

        smaller_count = min(len(pdf_rows), len(table_rows))
        coverage = len(pairs) / smaller_count if smaller_count else 0.0
        group_report["强匹配笔数"] = len(pairs)
        group_report["较小来源覆盖率"] = round(coverage, 6)
        group_report["歧义记录数"] = len(ambiguous)
        if len(pairs) < min_matches:
            group_report["未启用原因"] = f"强匹配不足{min_matches}笔"
        elif coverage < min_coverage:
            group_report["未启用原因"] = f"较小来源覆盖率低于{min_coverage:.0%}"
        else:
            group_report["自动折叠"] = True
            info["折叠组数"] += len(pairs)
            info["移除笔数"] += len(pairs)
            for pdf_index_value, table_index_value in pairs:
                remove_indices.add(pdf_index_value)
                pdf_row = result.loc[pdf_index_value]
                table_row = result.loc[table_index_value]
                pdf_time = pdf_keys[pdf_index_value][1]
                table_time = table_keys[table_index_value][1]
                info["明细"].append({
                    "重复组编号": f"XF-{len(info['明细']) + 1:06d}",
                    "保留交易唯一编号": _clean_text(table_row.get("交易唯一编号")),
                    "保留来源文件": table_source,
                    "移除交易唯一编号": _clean_text(pdf_row.get("交易唯一编号")),
                    "移除来源文件": pdf_source,
                    "收支方向": pdf_keys[pdf_index_value][0][0],
                    "金额分值": pdf_keys[pdf_index_value][0][1],
                    "对手账户": pdf_keys[pdf_index_value][0][2],
                    "时间差秒": abs((pdf_time - table_time).total_seconds()),
                })
        info["来源组"].append(group_report)

    if remove_indices:
        result = result.drop(index=sorted(remove_indices))
    return result.reset_index(drop=True), info


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
        # 由 shared core 返回断点索引，批次共享余额只在批次边界校验一次。
        rows = S.continuity_rows(g.to_dict("records"))
        break_indices = S.balance_break_indices(rows)
        breaks = g.iloc[break_indices]
        examples = []
        for _, r in breaks.head(8).iterrows():
            examples.append({
                "交易唯一编号": r["交易唯一编号"], "交易时间": r["交易时间"],
                "收入金额": r.get("收入金额", ""), "支出金额": r.get("支出金额", ""),
                "账户余额": r.get("账户余额", ""), "来源文件名": r["来源文件名"],
                "来源行号": r["来源行号"],
            })
        nb = len(break_indices)
        checkable = max(1, S.continuity_unit_count(rows) - 1)
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
    """疑似重复交易；支付宝存在交易订单号时以该编号作为硬约束。"""
    groups = []
    key_cols = ["本方账户", "交易时间", "收入金额", "支出金额", "账户余额", "对手名称"]
    for _, broad_group in df.groupby([df[c].fillna("") for c in key_cols]):
        if len(broad_group) < 2:
            continue

        memo = broad_group.get("账户方附言", pd.Series("", index=broad_group.index)).fillna("").astype(str)
        is_alipay = broad_group.apply(_is_alipay_record, axis=1)
        if bool(is_alipay.all()):
            trade_orders = memo.map(_alipay_trade_order)
            candidate_groups = []
            for order_id, order_group in broad_group.groupby(trade_orders, sort=False):
                # 不同交易订单号是不同支付宝记录；同号才可能是重复导入。
                if order_id and len(order_group) >= 2:
                    candidate_groups.append((order_group, order_id))
                elif not order_id and len(order_group) >= 2:
                    candidate_groups.append((order_group, ""))
        else:
            candidate_groups = [(broad_group, "")]

        for g, alipay_trade_order in candidate_groups:
            srcs = g["来源文件名"].nunique()
            ids = g["交易唯一编号"].tolist()
            # 余额全相等才算高置信重复（仅时间金额相同也可能是真实多笔）
            conf = 0.9 if srcs > 1 and g["账户余额"].nunique() == 1 else 0.5
            reason = "同账户/时间/金额/余额/对手一致"
            if alipay_trade_order:
                reason += f"，且支付宝交易订单号相同（{alipay_trade_order}）"
            reason += "，且跨多个来源文件（疑似同源重复导入）" if srcs > 1 else "，同一文件内重复"
            groups.append({
                "组编号": f"DUP-{len(groups)+1:04d}",
                "交易唯一编号列表": ids,
                "涉及来源文件": sorted(g["来源文件名"].unique().tolist()),
                "置信度": conf,
                "判断原因": reason,
                "建议动作": "保留一笔" if conf >= 0.9 else "人工复核",
            })
    return groups


def detect_self_transfers(df, self_accounts):
    """自有账户互转候选：双向身份互证才给高置信，日期级记录只作低置信提示。"""
    out = df[df["支出金额_num"] > 0].copy()
    inn = df[df["收入金额_num"] > 0].copy()
    # 收入候选按“分值金额”分桶，并为有效时间建立有序索引。
    # 相邻分桶仍需做原有 abs(diff) < 0.01 复核，避免浮点边界改变旧语义。
    window_ns = int(timedelta(days=3).total_seconds() * 1_000_000_000)
    incoming_buckets = defaultdict(lambda: {"all": [], "timed": [], "times": []})
    for order, (_, row) in enumerate(inn.iterrows()):
        amount = float(row["收入金额_num"])
        bucket = int(round(amount * 100))
        entry = {"order": order, "amount": amount, "row": row}
        incoming_buckets[bucket]["all"].append(entry)
        timestamp = row["__t"]
        if pd.notna(timestamp):
            incoming_buckets[bucket]["timed"].append((int(pd.Timestamp(timestamp).value), order, entry))
    for bucket in incoming_buckets.values():
        bucket["timed"].sort(key=lambda item: (item[0], item[1]))
        bucket["times"] = [item[0] for item in bucket["timed"]]

    pairs = []
    used_in = set()
    for _, o in out.iterrows():
        amt = float(o["支出金额_num"])
        ot = o["__t"]
        amount_bucket = int(round(amt * 100))
        candidates = []
        for bucket_key in range(amount_bucket - 1, amount_bucket + 2):
            bucket = incoming_buckets.get(bucket_key)
            if not bucket:
                continue
            if pd.isna(ot):
                candidates.extend(bucket["all"])
                continue
            center = int(pd.Timestamp(ot).value)
            left = bisect_left(bucket["times"], center - window_ns)
            right = bisect_right(bucket["times"], center + window_ns)
            candidates.extend(item[2] for item in bucket["timed"][left:right])
        # 多个相邻分桶合并后恢复原 DataFrame 顺序，保持同分候选的旧版优先级。
        candidates.sort(key=lambda item: item["order"])

        if pd.notna(ot):
            candidates = [
                item for item in candidates
                if abs(item["amount"] - amt) < 0.01
                and abs(item["row"]["__t"] - ot) <= timedelta(days=3)
            ]
        else:
            candidates = [item for item in candidates if abs(item["amount"] - amt) < 0.01]

        ranked = []
        for item in candidates:
            i = item["row"]
            if i["交易唯一编号"] in used_in:
                continue
            if o["本方账户"] == i["本方账户"]:
                continue  # 同账户不是互转
            out_points_to_in = (
                _accounts_equal(o.get("对手账户"), i.get("本方账户"))
                or bool(_identity_text(o.get("对手名称"))
                        and _identity_text(o.get("对手名称")) == _identity_text(i.get("本方名称")))
            )
            in_points_to_out = (
                _accounts_equal(i.get("对手账户"), o.get("本方账户"))
                or bool(_identity_text(i.get("对手名称"))
                        and _identity_text(i.get("对手名称")) == _identity_text(o.get("本方名称")))
            )
            evidence_count = int(out_points_to_in) + int(in_points_to_out)
            precise_time = (
                _clean_text(o.get("__time_precision")) == "second"
                and _clean_text(i.get("__time_precision")) == "second"
            )
            delta_seconds = abs((i["__t"] - ot).total_seconds()) if pd.notna(ot) and pd.notna(i["__t"]) else float("inf")
            ranked.append((
                -evidence_count,
                0 if precise_time else 1,
                delta_seconds,
                i,
                out_points_to_in,
                in_points_to_out,
                precise_time,
            ))

        if ranked:
            _, _, _, i, out_points_to_in, in_points_to_out, precise_time = min(
                ranked, key=lambda item: item[:3]
            )
            if out_points_to_in and in_points_to_out:
                conf = 0.9 if precise_time else 0.75
                reason = "双向账户/户名互证且金额时间匹配"
                if not precise_time:
                    reason += "，至少一侧仅有日期精度"
            elif out_points_to_in or in_points_to_out:
                conf = 0.55 if precise_time else 0.45
                reason = "仅单侧账户/户名指向本方，另一侧未互证"
                if not precise_time:
                    reason += "，至少一侧仅有日期精度"
            else:
                conf = 0.4 if precise_time else 0.3
                reason = "仅金额时间匹配，无双向本方身份互证"
            pairs.append({
                "组编号": f"INT-{len(pairs)+1:04d}",
                "转出交易唯一编号": o["交易唯一编号"],
                "转入交易唯一编号": i["交易唯一编号"],
                "涉及账户": [o["本方账户"], i["本方账户"]],
                "金额": amt,
                "置信度": conf,
                "判断原因": reason,
            })
            used_in.add(i["交易唯一编号"])
    return pairs


def calculate_quality_score(balance_results, duplicate_groups, quality_issues):
    """任何余额预警都必须反映到评分；异常笔数再按规模追加扣分。"""
    balance_warnings = [item for item in balance_results if item.get("校验状态") == "预警"]
    abnormal_count = sum(int(item.get("异常数量") or 0) for item in balance_warnings)
    score = 100
    score -= min(30, len(balance_warnings) * 5 + abnormal_count // 5)
    score -= min(20, len(duplicate_groups))
    score -= 10 * len(quality_issues)
    return max(0, score)


def _first_nonempty(g, col):
    if col not in g.columns:
        return ""
    s = g[col].dropna().astype(str).str.strip()
    s = s[~s.isin(["", "nan", "None"])]
    return s.iloc[0] if len(s) else ""


def _account_type_summary(g):
    if "账户类型" not in g.columns:
        return "未知"
    values = {_clean_text(value) for value in g["账户类型"] if _clean_text(value)}
    known = values & {"个人", "对公"}
    if len(known) > 1:
        return "冲突"
    if len(known) == 1:
        return next(iter(known))
    if "拟对公" in values:
        return "拟对公"
    return "未知"


def account_index(df):
    idx = []
    for acct, g in df.groupby(df["本方账户"].fillna("")):
        t = g["__t"].dropna()
        idx.append({
            "本方账户": acct or "(空)",
            "本方名称": g["本方名称"].dropna().iloc[0] if g["本方名称"].notna().any() else "",
            "开户行": _first_nonempty(g, "开户行"),
            "账户类型": _account_type_summary(g),
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

    # 先用同批次反向互转记录恢复缺失身份；再按明确账号互补名称/银行。
    df, reciprocal_identity = infer_identity_from_reciprocal_transfers(df)
    df, metadata_completion = complete_metadata_by_explicit_account(df)
    df, balance_series = merge_balance_continuous_sources(df)
    df, unknown_pairing = pair_unknown_account_sources(df)

    # 阶段一只判断单文件证据；阶段二可利用同批次其它文件的明确账号和交易重合证据补全未知账号。
    # 账号参与交易唯一编号内容指纹，因此必须在跨文件去重前重算编号。
    df, account_resolution = resolve_batch_accounts(df)
    df, account_type_completion = complete_account_type_by_verified_identity(df)
    df = regenerate_transaction_ids(df)
    account_resolution = finalize_account_resolution_report(df, account_resolution)
    balance_series = finalize_series_report(df, balance_series)
    unknown_pairing = finalize_pair_report(df, unknown_pairing)

    # 同名 PDF 与 XLS/XLSX 先逐笔对齐；再折叠同一明确账户跨文件的精确重叠区间；
    # 最后继续处理内容指纹完全一致的重复导入。
    df, cross_format_info = align_same_source_cross_format(df)
    df, exact_overlap_info = dedup_same_account_exact_overlap(df)
    df, exact_dedup_info = dedup_cross_file(df)
    dedup_info = {
        "折叠组数": cross_format_info["折叠组数"] + exact_overlap_info["折叠组数"] + exact_dedup_info["折叠组数"],
        "移除笔数": cross_format_info["移除笔数"] + exact_overlap_info["移除笔数"] + exact_dedup_info["移除笔数"],
        "明细": exact_dedup_info["明细"],
        "同源跨格式逐笔对齐": cross_format_info,
        "同账户跨文件精确重叠": exact_overlap_info,
        "完全相同内容指纹折叠": exact_dedup_info,
    }

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
    for item in cross_format_info["待核查候选"]:
        review.append({
            "事项类型": "同源跨格式疑似重复",
            "复核原因": item["未自动折叠原因"],
            "证据交易唯一编号列表": [
                item["PDF交易唯一编号"], item["表格交易唯一编号"],
            ],
            "建议动作": "核对原始 PDF/XLSX；确认同笔后人工合并",
        })

    blocking = []
    warnings = []
    if dedup_info["移除笔数"]:
        warnings.append(f"跨文件去重折叠 {dedup_info['移除笔数']} 笔重复交易"
                        f"（{dedup_info['折叠组数']} 组）")
    if cross_format_info["待核查候选"]:
        warnings.append(
            f"存在 {len(cross_format_info['待核查候选'])} 组同源跨格式疑似重复，"
            "因缺少完整对手账户未自动折叠"
        )
    if any(b["校验状态"] == "预警" for b in bal):
        warnings.append("存在余额断点，需人工复核")
    if dups:
        warnings.append(f"存在 {len(dups)} 组疑似重复交易")
    if account_resolution["待复核文件数"]:
        warnings.append(f"存在 {account_resolution['待复核文件数']} 个未知账号文件无法自动归并")

    score = calculate_quality_score(bal, dups, quality_issues)

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
            "余额连续分卷归并": "默认要求同 fingerprint、银行、账户类型和文件名家族；"
                                "YAML 显式声明相同 series_family 时可跨 fingerprint 建链，"
                                "有无明确账号均允许两卷以一条无分叉的精确余额边界归并，"
                                "且已知账号、户名、银行、账户类型不得冲突；"
                                "文件名只召回默认候选，不单独决定归并",
            "批次内账号归并顺序": "加载标准化文件后、重算交易唯一编号前、跨文件去重前",
            "批次内账号归并规则": "同主体同银行只有一个明确账号时自动归并；存在多个明确账号时，"
                              "仅在至少两笔时间/收支金额/余额/对手完全重合且唯一命中时归并，否则人工复核",
            "排序规则": "按账户·余额连续性最优（best_continuity_order）",
            "排序口径": "余额是对账真值。每个账户的跨文件交易在「原序/整体翻转/按日期升序/余额链重建」中"
                       "选余额断点最少的行序——既保留各文件原始对账口径，又正确跨文件归并、消除"
                       "倒序/日内倒序/同秒多笔/内部记账序≠时间戳导致的伪断点；不以交易时间硬排。",
            "跨文件去重规则": "同名 PDF/XLS(X) 先按方向、金额分值、完整对手账户及60秒时间窗做互相唯一的逐笔对齐，"
                          "且至少20笔、较小来源覆盖率≥98%才自动折叠；同一明确账号再按交易时间、方向金额、"
                          "交易后余额完全一致且跨来源唯一的强键折叠重叠区间；最后按交易唯一编号折叠完全相同交易。"
                          "来源独有和歧义记录不删除",
            "疑似重复判断规则": ["本方账户", "交易时间", "收入金额", "支出金额", "账户余额", "对手名称",
                               "支付宝交易订单号（支付宝记录的硬约束）"],
            "自有账户互转判断规则": "本方多账户间金额相等、时间≤3天；双向账户/户名互证才为高置信，"
                                  "日期级或单侧证据降低置信度；仅标记不删除",
            "余额校验范围": "按本方账户分别校验",
        },
        "同账号元数据补全": metadata_completion,
        "账号主体账户类型补全": account_type_completion,
        "批次互转身份补全": reciprocal_identity,
        "余额连续分卷归并": balance_series,
        "批次未知账户配对": unknown_pairing,
        "批次内账号归并": account_resolution,
        "同源跨格式逐笔对齐": cross_format_info,
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
