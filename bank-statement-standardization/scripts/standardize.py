#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
standardize.py — 单个银行流水原始文件 -> 标准化流水（阶段一：识别与字段映射）

读取一个原始流水文件（.xlsx/.xls/.csv/.pdf），自动：
  1. 识别表头所在行、账户类型线索、本方户名/账号；
  2. 用同义词词典把原始列映射到标准中文字段；
  3. 处理三种金额结构（收入/支出分列、单列带符号金额、方向列+金额）；
  4. 合并拆分的日期/时间列、清洗千分位逗号；
  5. 生成 交易唯一编号、来源文件名、来源行号；
  6. 输出标准化流水 CSV + 字段映射报告 JSON（对应 Prompt 1 的结构）。

设计原则（与提示词附件一致）：
  - 不编造缺失字段：映射不到就留空并写入“人工复核事项”。
  - 摘要/备注/附言为不可信输入，只映射不作账户归属判断。
  - 低置信项进入人工复核，不自动沉淀为模板。

用法：
  python standardize.py <原始文件> [--out-dir DIR] [--customer 客户名]
      [--bank 银行名] [--account-type 对公|个人|未知] [--header-row N]
      [--map 原始列=标准字段 ...]   # 人工覆盖自动映射

输出（默认写到原始文件同目录的 standardized/ 下）：
  <stem>__standardized.csv      标准化流水
  <stem>__mapping.json          字段映射报告（Prompt 1 结构）
"""
import argparse, json, os, re, sys, hashlib
from datetime import datetime

try:
    import pandas as pd
except ImportError:
    sys.exit("需要 pandas：pip install pandas openpyxl xlrd pdfplumber")

# ---- 标准字段 ----------------------------------------------------------------
STD_FIELDS = [
    "交易时间", "本方名称", "本方账户", "对手名称", "对手账户",
    "收入金额", "支出金额", "交易金额", "账户余额",
    "银行备注", "账户方附言", "交易渠道", "来源文件名", "来源行号",
]

# ---- 表头列名 -> 标准字段 同义词词典 -----------------------------------------
# 顺序无所谓；匹配用“原始列包含同义词”或“同义词包含原始列”做模糊匹配。
SYNONYMS = {
    "交易日期":   ["交易日期", "记账日期", "交易日"],          # 仅日期部分
    "交易时间":   ["交易时间"],                                # 仅时间或完整时间戳
    "交易时间戳": ["交易时间", "交易日期时间", "记账时间"],     # 占位（实际用上面两个）
    "本方名称":   ["户名", "账户名称", "账户名", "本方户名", "客户名称", "本方名称"],
    "本方账户":   ["账号", "账户号", "卡号", "本方账户", "账户号码"],
    "对手名称":   ["对方户名", "对手信息", "交易对手名称", "对方账户名", "对手户名",
                  "对方名称", "对手名称", "对方账户名称", "对手账户名"],
    "对手账户":   ["对方账号", "对方账户", "交易对手账号", "对手账户", "对方账户号",
                  "对方卡号", "交易对手账号"],
    "收入金额":   ["收入金额", "收入", "贷方发生额", "存入金额", "贷方金额", "贷方",
                  "贷方发生额(收入)", "贷方发生额（收入）"],
    "支出金额":   ["支出金额", "支出", "借方发生额", "取出金额", "借方金额", "借方",
                  "借方发生额(支取)", "借方发生额（支取）"],
    "交易金额":   ["交易金额", "发生额", "金额"],
    "账户余额":   ["账户余额", "余额", "本次余额", "当前余额"],
    "收支方向":   ["收支方向", "收支", "借贷标志", "借贷", "方向", "借贷方向"],
    "银行备注":   ["交易摘要", "摘要", "用途", "交易类型", "交易备注", "相关信息",
                  "备注", "交易种类", "业务摘要", "附言摘要"],
    "账户方附言": ["交易附言", "附言", "留言", "客户附言"],
    "交易渠道":   ["交易渠道", "渠道", "交易方式", "记账渠道"],
}

# 不可信字段（仅作辅助证据，不决定账户归属）
UNTRUSTED = {"银行备注", "账户方附言"}

# 分页导出时夹在数据中的小计/页眉页脚噪声行关键词。
# 只保留几乎不可能出现在交易备注/附言里的强标记，避免误删真实交易
# （例如 ETC 备注含「共计消费X元」，故不能用「共计/合计」这类宽泛词）。
NOISE_KEYWORDS = ["本页", "算术合计", "交易笔数", "承上页", "接下页", "转下页",
                  "页次", "续表", "本期合计", "本页小计", "打印时间："]

# 表头识别用的关键词（一行命中>=2个即可能是表头）
HEADER_HINTS = set()
for vs in SYNONYMS.values():
    HEADER_HINTS.update(vs)


def _norm(s):
    """归一化列名：去空白、全角括号统一，便于模糊匹配。"""
    if s is None:
        return ""
    s = str(s).strip()
    s = re.sub(r"\s+", "", s)
    s = s.replace("（", "(").replace("）", ")")
    return s


def match_field(col):
    """把一个原始列名匹配到标准字段名（返回标准字段 或 None）。"""
    c = _norm(col)
    if not c or c.lower() == "nan":
        return None
    best = None
    for field, syns in SYNONYMS.items():
        for syn in syns:
            sn = _norm(syn)
            if c == sn:
                return field            # 完全相等，最高优先级
            if sn and (sn in c or c in sn):
                best = best or field
    return best


# ---- 金额/日期解析 -----------------------------------------------------------
def parse_amount(v):
    """'46,800.00' / '-1,026' / '（空）' / '' -> float 或 None"""
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "nan", "None", "（空）", "(空)", "-", "—"):
        return None
    s = s.replace(",", "").replace("，", "").replace(" ", "")
    s = s.replace("¥", "").replace("￥", "").replace("元", "")
    try:
        return float(s)
    except ValueError:
        return None


def parse_datetime(date_part, time_part):
    """把日期列 + 时间列合并成标准 'YYYY-MM-DD HH:MM:SS'。

    用正则从拼接串里分别抽取「日期 token」和「时间 token」，因此对以下脏数据都鲁棒：
      20250421 152103              （日期列+6位时间列）
      2025-06-04 16:06:27.014000   （带毫秒）
      2024/01/19  22:57:48         （斜杠+多空格）
      2025032100:10:53             （PDF 抽表时日期时间被粘连）
      2026-03-1009:54:35           （PDF 抽表时连字符日期+时间粘连）
    取首个日期 token 与首个时间 token；无法识别则原样返回，交给后续校验/人工复核。
    """
    d = "" if date_part is None else str(date_part).strip()
    t = "" if time_part is None else str(time_part).strip()
    for junk in ("nan", "None", "（空）"):
        d = "" if d == junk else d
        t = "" if t == junk else t
    raw = (d + " " + t).strip()
    if not raw:
        return ""

    # 日期 token：YYYY-MM-DD / YYYY/MM/DD / YYYYMMDD
    md = re.search(r"(\d{4})[-/]?(\d{1,2})[-/]?(\d{1,2})", raw)
    # 时间 token：HH:MM:SS / HH:MM / 紧跟在日期后的 6 位 HHMMSS
    mt = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", raw)
    if not md:
        return raw
    y, mo, day = md.group(1), md.group(2).zfill(2), md.group(3).zfill(2)
    date_str = f"{y}-{mo}-{day}"

    hh = mm = ss = "00"
    if mt:
        hh, mm = mt.group(1).zfill(2), mt.group(2)
        ss = mt.group(3) or "00"
    else:
        # 没有带冒号的时间：尝试日期后面紧跟的 6 位/4 位数字（HHMMSS / HHMM）
        tail = raw[md.end():]
        m6 = re.search(r"(\d{6})", tail) or re.search(r"(\d{6})", t)
        if m6:
            v = m6.group(1)
            hh, mm, ss = v[0:2], v[2:4], v[4:6]
        else:
            m4 = re.search(r"\b(\d{4})\b", tail)
            if m4:
                v = m4.group(1)
                hh, mm = v[0:2], v[2:4]
    if not (1990 <= int(y) <= 2100):
        return ""   # 年份越界，多半是把卡号/编号误当日期，判为无效
    try:
        dt = datetime(int(y), int(mo), int(day), int(hh), int(mm), int(ss))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        # 时分秒越界等异常，至少保住日期
        return f"{date_str} 00:00:00"


# ---- 原始文件读取（统一成 list[list]） ----------------------------------------
def read_rows_excel(path):
    """返回 (sheet名, rows:list[list])。取第一个有数据的 sheet。"""
    xl = pd.ExcelFile(path)
    for sheet in xl.sheet_names:
        df = xl.parse(sheet, header=None, dtype=str)
        if df.dropna(how="all").shape[0] >= 2:
            rows = df.where(pd.notnull(df), None).values.tolist()
            return sheet, rows
    sheet = xl.sheet_names[0]
    df = xl.parse(sheet, header=None, dtype=str)
    return sheet, df.where(pd.notnull(df), None).values.tolist()


def read_rows_csv(path):
    """跳过以 # 开头的说明行和空行，返回 (preamble, rows)。自动探测编码。
    preamble 保留被跳过的说明行文本（常含账号/户名等抬头信息）。"""
    text = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030", "utf-16"):
        try:
            with open(path, encoding=enc) as f:
                text = f.read()
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    if text is None:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    import csv, io
    rows = []
    preamble = []
    for line in text.splitlines():
        if line.strip().startswith("#"):
            preamble.append(line.lstrip("#").strip())
            continue
        if not line.strip():
            continue
        rows.append(line)
    reader = csv.reader(io.StringIO("\n".join(rows)))
    out = []
    for r in reader:
        out.append([c.strip().strip("\t") if c is not None else None for c in r])
    return "\n".join(preamble), out


def read_rows_pdf(path):
    """用 pdfplumber 抽表；逐页拼接，跳过重复表头行；多行单元格内换行去掉。"""
    import pdfplumber
    all_rows = []
    header_sig = None
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for tbl in page.extract_tables():
                for r in tbl:
                    cells = [(c or "").replace("\n", "").strip() for c in r]
                    if not any(cells):
                        continue
                    sig = "|".join(cells)
                    if header_sig is None:
                        header_sig = sig
                        all_rows.append(cells)
                    elif sig == header_sig:
                        continue  # 跳过每页重复表头
                    else:
                        all_rows.append(cells)
    return None, all_rows


def read_rows(path):
    """返回 (kind, preamble, rows)。preamble 为表格之外的抬头文本（可为空）。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm", ".xls"):
        sheet, rows = read_rows_excel(path)
        return ("excel", "", rows)
    if ext in (".csv", ".txt", ".tsv"):
        pre, rows = read_rows_csv(path)
        return ("csv", pre, rows)
    if ext == ".pdf":
        _, rows = read_rows_pdf(path)
        return ("pdf", "", rows)
    raise ValueError(f"暂不支持的文件类型：{ext}")


# ---- 表头识别 ----------------------------------------------------------------
def find_header_row(rows, max_scan=30):
    """扫描前若干行，命中表头同义词最多的一行作为表头。返回 (行号, 命中数)。"""
    best_idx, best_hits = None, 0
    for i, row in enumerate(rows[:max_scan]):
        hits = 0
        for cell in row:
            if match_field(cell):
                hits += 1
        # 至少要有时间/金额/余额其中之一相关，且命中>=2列
        if hits > best_hits:
            best_hits, best_idx = hits, i
    return best_idx, best_hits


def sniff_account_info(rows, header_idx, preamble=""):
    """从抬头元数据（表头以上的行 + CSV 说明行）里抓 本方户名 / 本方账户 / 账户类型线索。"""
    info = {"本方名称": "", "本方账户": "", "账户类型线索": ""}
    # 只取表头以上的抬头行；header_idx 可能为 0（CSV 表头即首行），此时仅用 preamble，
    # 不能误取数据行——否则交易备注里的「…有限公司」会把个人账户错判成对公。
    upper = rows[:header_idx] if header_idx is not None else rows[:6]
    meta = preamble + " " + " ".join(
        str(c) for r in upper for c in r if c
    )
    m = re.search(r"(?:户名|账户名称|账户名)[:：]?\s*([^\s,，:：\-]+)", meta)
    if m:
        info["本方名称"] = m.group(1)
    # 账号：优先带「账号/卡号/账户」标签的；支持掩码 6226****4806
    acct = None
    m = re.search(r"(?:账\s*号|卡\s*号|账\s*户)[^0-9]{0,6}(\d[\d*]{5,}\d)", meta)
    if m:
        acct = m.group(1)
    if not acct:
        # 户名后接「-账号」结构，如 公司名称-800091876502013
        m = re.search(r"[一-龥)）]-(\d{8,})", meta)
        if m:
            acct = m.group(1)
    if not acct:
        # 兜底：抬头里最长的数字/掩码串（>=8 位）
        cands = re.findall(r"\d[\d*]{7,}", meta)
        if cands:
            acct = max(cands, key=len)
    if acct:
        info["本方账户"] = acct
    if re.search(r"对公|公司|企业|有限|厂|合作社|个体", meta):
        info["账户类型线索"] = "对公"
    elif re.search(r"借记卡|一卡通|储蓄|个人|活期一本通|储种", meta):
        info["账户类型线索"] = "个人"
    return info


# ---- 主流程 ------------------------------------------------------------------
def build_unique_id(customer, account, time_str, amount, src_file, src_row):
    raw = f"{customer}|{account}|{time_str}|{amount}|{src_file}|{src_row}"
    h = hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]
    return f"TX-{h}"


def standardize(path, out_dir=None, customer=None, bank=None,
                account_type=None, header_row=None, overrides=None):
    fname = os.path.basename(path)
    stem = os.path.splitext(fname)[0]
    file_kind, preamble, rows = read_rows(path)

    if header_row is None:
        header_idx, hits = find_header_row(rows)
    else:
        header_idx, hits = header_row, None
    if header_idx is None:
        raise ValueError(f"未能识别表头：{fname}")

    header = [(_norm(c) if c is not None else "") for c in rows[header_idx]]
    data_rows = rows[header_idx + 1:]

    acct = sniff_account_info(rows, header_idx, preamble)
    if customer:
        acct["本方名称"] = customer
    if not account_type:
        account_type = acct["账户类型线索"] or "未知"
    # 账号识别失败时，用「按文件隔离」的占位账号，避免不同文件错误合并到同一空账户做余额校验
    if not acct["本方账户"]:
        acct["本方账户"] = f"未识别账户#{stem}"
        acct["_账号未识别"] = True

    # ---- 列 -> 标准字段 映射 ----
    overrides = overrides or {}
    col_to_field = {}      # 列索引 -> 标准字段
    field_to_cols = {}     # 标准字段 -> [列索引...]
    mapping_detail = {}    # 标准字段 -> {原始字段, 置信度, 说明}
    review_items = []      # 人工复核事项

    for idx, col in enumerate(header):
        if not col:
            continue
        # 人工覆盖优先
        if col in overrides:
            field = overrides[col]
        elif _norm(idx) in overrides:
            field = overrides[_norm(idx)]
        else:
            field = match_field(col)
        if not field:
            continue
        col_to_field[idx] = field
        field_to_cols.setdefault(field, []).append(idx)

    # 时间列：可能 交易日期 + 交易时间 拆分
    date_cols = field_to_cols.get("交易日期", [])
    time_cols = field_to_cols.get("交易时间", [])
    has_split_dt = bool(date_cols)

    # 金额结构判定
    has_income = "收入金额" in field_to_cols
    has_expense = "支出金额" in field_to_cols
    has_amount = "交易金额" in field_to_cols
    has_direction = "收支方向" in field_to_cols

    # 探测「交易金额」列是否带符号（含负数）。带符号时符号比文字方向更可信
    amount_is_signed = False
    if has_amount:
        for row in data_rows[:500]:
            for ci in field_to_cols["交易金额"]:
                if ci < len(row):
                    v = parse_amount(row[ci])
                    if v is not None and v < 0:
                        amount_is_signed = True
                        break
            if amount_is_signed:
                break

    if has_amount and amount_is_signed:
        amount_mode = "单列带符号"          # 优先用符号（最可靠）
    elif has_income and has_expense:
        amount_mode = "分列"
    elif has_direction and (has_amount or has_income or has_expense):
        amount_mode = "方向+金额"           # 金额无符号，方向取自文字列
    elif has_amount:
        amount_mode = "单列带符号"
    elif has_income or has_expense:
        amount_mode = "分列"
    else:
        amount_mode = "未知"

    # ---- 逐行生成标准化记录 ----
    std_records = []
    header_set = set(h for h in header if h)
    dropped_noise = 0
    raw_offset = header_idx + 2  # 人类可读行号：表头下一行从 1-based 文件行算
    for ri, row in enumerate(data_rows):
        if not any(c not in (None, "", "nan") for c in row):
            continue

        # 过滤分页导出夹带的小计/页眉页脚行
        joined = " ".join(str(c) for c in row if c not in (None, "nan"))
        if any(k in joined for k in NOISE_KEYWORDS):
            dropped_noise += 1
            continue
        # 过滤每页重复的表头行（多列与表头完全一致）
        hdr_match = sum(1 for c in row if c is not None and _norm(c) in header_set and _norm(c))
        if hdr_match >= 3:
            dropped_noise += 1
            continue

        def cell(field_idx_list, joiner=" "):
            vals = []
            for ci in field_idx_list:
                if ci < len(row) and row[ci] not in (None, "nan"):
                    v = str(row[ci]).strip()
                    if v and v.lower() != "nan":
                        vals.append(v)
            return joiner.join(vals)

        # 时间
        if has_split_dt:
            t = parse_datetime(cell(date_cols), cell(time_cols))
        elif time_cols:
            t = parse_datetime(cell(time_cols), "")
        else:
            t = ""

        income = expense = txn = None
        if amount_mode == "分列":
            raw_inc = parse_amount(cell(field_to_cols.get("收入金额", [])))
            raw_exp = parse_amount(cell(field_to_cols.get("支出金额", [])))
            if raw_inc is None and raw_exp is None:
                pass
            else:
                # 合并为净流入：负数（冲正/退回）自动归到正确方向，保证收/支互斥
                net_in = (raw_inc or 0) - (raw_exp or 0)
                if net_in > 0:
                    income = net_in
                elif net_in < 0:
                    expense = -net_in
                else:
                    income = 0  # 净额为 0，保留一笔 0 收入占位
        elif amount_mode == "单列带符号":
            txn = parse_amount(cell(field_to_cols.get("交易金额", [])))
            if txn is not None:
                if txn >= 0:
                    income = txn
                else:
                    expense = abs(txn)
        elif amount_mode == "方向+金额":
            direction = cell(field_to_cols.get("收支方向", []))
            amt = parse_amount(cell(field_to_cols.get("交易金额", [])
                                    or field_to_cols.get("收入金额", [])
                                    or field_to_cols.get("支出金额", [])))
            txn = amt
            is_income = ("收" in direction) or ("贷" in direction)
            is_expense = ("支" in direction) or ("出" in direction) or ("借" in direction)
            if amt is not None:
                if is_income and not is_expense:
                    income = abs(amt)
                elif is_expense and not is_income:
                    expense = abs(amt)
                elif amt >= 0:
                    income = amt
                else:
                    expense = abs(amt)

        balance = parse_amount(cell(field_to_cols.get("账户余额", [])))
        opp_name = cell(field_to_cols.get("对手名称", []))
        opp_acct = cell(field_to_cols.get("对手账户", []))
        bank_memo = cell(field_to_cols.get("银行备注", []))
        cust_memo = cell(field_to_cols.get("账户方附言", []))
        channel = cell(field_to_cols.get("交易渠道", []))

        # 解析后仍全空（无时间、无任何金额、无余额）的行视为残留噪声丢弃
        if (not t) and income is None and expense is None and txn is None and balance is None:
            dropped_noise += 1
            continue

        # 本方账户/本方名称：若文件把它们放在每行的数据列里（如建行账号列），优先用行内值；
        # 否则用从抬头元数据嗅探到的常量。这样既支持单账户抬头式，也支持多账户混在一文件的情况。
        row_self_acct = cell(field_to_cols.get("本方账户", []))
        row_self_name = cell(field_to_cols.get("本方名称", []))
        self_acct = row_self_acct or acct["本方账户"]
        cust = customer or row_self_name or acct["本方名称"]
        amt_for_id = income if income is not None else (expense if expense is not None else txn)
        uid = build_unique_id(cust, self_acct, t, amt_for_id, fname, raw_offset + ri)

        std_records.append({
            "交易唯一编号": uid,
            "交易时间": t,
            "本方名称": cust,
            "本方账户": self_acct,
            "对手名称": opp_name,
            "对手账户": opp_acct,
            "收入金额": income if income is not None else "",
            "支出金额": expense if expense is not None else "",
            "交易金额": txn if txn is not None else "",
            "账户余额": balance if balance is not None else "",
            "银行备注": bank_memo,
            "账户方附言": cust_memo,
            "交易渠道": channel,
            "来源文件名": fname,
            "来源行号": raw_offset + ri,
        })

    # ---- 映射报告 ----
    for field in STD_FIELDS:
        cols = field_to_cols.get(field, [])
        if field == "交易时间" and (date_cols or time_cols):
            raw_names = [header[i] for i in (date_cols + time_cols)]
            mapping_detail[field] = {"原始字段": "+".join(raw_names),
                                     "置信度": 0.9, "说明": "由日期列与时间列合并" if date_cols and time_cols else "单一时间列"}
        elif cols:
            conf = 0.95 if any(_norm(header[i]) == _norm(field) for i in cols) else 0.8
            note = "不可信字段，仅作辅助证据" if field in UNTRUSTED else ""
            mapping_detail[field] = {"原始字段": "+".join(header[i] for i in cols),
                                     "置信度": conf, "说明": note}
        else:
            mapping_detail[field] = {"原始字段": "", "置信度": 0.0, "说明": ""}

    # 缺失关键字段 -> 人工复核
    key_missing = []
    if not (field_to_cols.get("收入金额") or field_to_cols.get("支出金额")
            or field_to_cols.get("交易金额")):
        key_missing.append("金额")
    if not (date_cols or time_cols):
        key_missing.append("交易时间")
    if not field_to_cols.get("对手名称"):
        review_items.append({"字段": "对手名称", "问题": "未识别到对手名称列，可能嵌在备注中",
                             "证据": f"表头={header}", "建议动作": "人工确认对手名称来源"})
    if not field_to_cols.get("账户余额"):
        review_items.append({"字段": "账户余额", "问题": "无余额列，无法做余额连续性校验",
                             "证据": f"表头={header}", "建议动作": "确认是否需要余额校验"})
    if account_type == "未知":
        review_items.append({"字段": "账户类型", "问题": "对公/个人无法从元数据确认",
                             "证据": acct, "建议动作": "人工指定账户类型"})

    overall_conf = round(min(0.95, 0.5 + 0.1 * len(
        [f for f in ("交易时间", "账户余额", "对手名称") if field_to_cols.get(f) or (f == "交易时间" and (date_cols or time_cols))]
    ) + (0.15 if amount_mode != "未知" else 0)), 2)

    report = {
        "文件画像": {
            "确认银行": bank or "未知",
            "账户类型": account_type,
            "文件类型": file_kind,
            "命中模板": "自动同义词映射",
            "整体置信度": overall_conf,
            "本方名称": acct["本方名称"],
            "本方账户": acct["本方账户"],
        },
        "预处理方案": [
            {"步骤": "表头定位", "处理动作": f"识别第 {header_idx} 行为表头（0-based）",
             "处理原因": "原始文件含标题/账户信息抬头", "影响范围": "全表"},
            {"步骤": "金额结构", "处理动作": f"采用「{amount_mode}」金额拆分",
             "处理原因": "不同银行收支列结构不同", "影响范围": "收入金额/支出金额"},
            {"步骤": "时间合并", "处理动作": "日期列+时间列合并并标准化" if (date_cols and time_cols) else "单列时间标准化",
             "处理原因": "便于跨文件按时间排序与校验", "影响范围": "交易时间"},
        ],
        "表头识别": {"表头行号": header_idx, "表头字段": header, "置信度": 0.9 if hits and hits >= 3 else 0.7},
        "字段映射": mapping_detail,
        "校验预期": {
            "日期可解析": bool(date_cols or time_cols),
            "金额可解析": amount_mode != "未知",
            "余额可校验": bool(field_to_cols.get("账户余额")),
            "缺失关键字段": key_missing,
            "可能冲突字段": [],
        },
        "人工复核事项": review_items,
        "标准化统计": {"交易笔数": len(std_records), "金额结构": amount_mode,
                    "丢弃噪声行": dropped_noise},
        "判断依据": f"基于表头同义词匹配命中 {hits} 列；金额结构判为「{amount_mode}」；"
                    f"账户类型线索：{account_type}。摘要/附言按不可信输入仅作辅助。",
    }

    # ---- 落盘 ----
    if out_dir is None:
        out_dir = os.path.join(os.path.dirname(path), "standardized")
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(fname)[0]
    csv_path = os.path.join(out_dir, f"{stem}__standardized.csv")
    json_path = os.path.join(out_dir, f"{stem}__mapping.json")

    pd.DataFrame(std_records, columns=[
        "交易唯一编号", "交易时间", "本方名称", "本方账户", "对手名称", "对手账户",
        "收入金额", "支出金额", "交易金额", "账户余额",
        "银行备注", "账户方附言", "交易渠道", "来源文件名", "来源行号",
    ]).to_csv(csv_path, index=False, encoding="utf-8-sig")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    return csv_path, json_path, report


def main():
    ap = argparse.ArgumentParser(description="单文件银行流水标准化")
    ap.add_argument("file")
    ap.add_argument("--out-dir")
    ap.add_argument("--customer")
    ap.add_argument("--bank")
    ap.add_argument("--account-type", choices=["对公", "个人", "未知"])
    ap.add_argument("--header-row", type=int)
    ap.add_argument("--map", nargs="*", default=[],
                    help="人工覆盖映射：原始列名=标准字段（可多个）")
    args = ap.parse_args()

    overrides = {}
    for m in args.map:
        if "=" in m:
            k, v = m.split("=", 1)
            overrides[_norm(k)] = v.strip()

    csv_path, json_path, report = standardize(
        args.file, out_dir=args.out_dir, customer=args.customer, bank=args.bank,
        account_type=args.account_type, header_row=args.header_row, overrides=overrides)

    print(f"[OK] {os.path.basename(args.file)}")
    print(f"  标准化流水 -> {csv_path}（{report['标准化统计']['交易笔数']} 笔，"
          f"金额结构「{report['标准化统计']['金额结构']}」）")
    print(f"  映射报告   -> {json_path}")
    if report["人工复核事项"]:
        print(f"  ⚠ 人工复核 {len(report['人工复核事项'])} 项：" +
              "; ".join(r["问题"] for r in report["人工复核事项"]))


if __name__ == "__main__":
    main()
