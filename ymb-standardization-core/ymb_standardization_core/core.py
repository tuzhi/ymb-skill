#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ymb_standardization_core.core — 单个银行流水原始文件 -> 标准化流水（阶段一：识别与字段映射）

读取一个原始流水文件（.xlsx/.xls/.pdf），自动：
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
  python standardize.py <原始文件> [--out-dir DIR] [--customer 户名兜底值]
      [--bank 银行名] [--account-type 对公|个人|未知] [--header-row N]
      [--map 原始列=标准字段 ...]   # 人工覆盖自动映射

输出（默认写到原始文件同目录的 standardized/ 下）：
  <stem>__standardized.csv      标准化流水
  <stem>__mapping.json          字段映射报告（Prompt 1 结构）
"""
import argparse, csv, json, os, re, sys, hashlib, shutil
from collections import Counter, defaultdict
from datetime import datetime

try:
    import pandas as pd
    import yaml
except ImportError:
    sys.exit("需要 pandas/PyYAML：pip install pandas openpyxl xlrd pdfplumber pyyaml")

# ---- 支持的格式 / 非流水文件识别（用户常把图片、发票、名册等杂糅进来，需自动排除） --------
SUPPORTED_EXT = (".xlsx", ".xlsm", ".xls", ".pdf")
# 已知的非流水格式（图片/扫描件、Word/PPT、压缩包等）：报告给用户「已跳过」，不静默吞掉
KNOWN_NONSTATEMENT_EXT = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic", ".heif",
    ".doc", ".docx", ".ppt", ".pptx", ".key", ".pages", ".numbers", ".zip", ".rar", ".7z",
}
# 本技能自身的下游产物后缀；扫描原始流水时跳过，避免把整合/打标/交付物重复摄入。
# <stem>__standardized.csv 是阶段一标准产物，也允许作为输入直接透传。
PRODUCT_SUFFIXES = ("__整合流水.csv", "__打标流水.csv",
                    "__组合日余额.csv", "__多客户底表.csv", "_已清洗_待分析.xlsx")


class NotABankStatement(Exception):
    """文件无法识别/解析为银行流水（图片型PDF、非流水表格、空文件、不支持格式等）。
    reason 给出可读原因，供编排脚本汇总到「已跳过文件」清单。"""
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def is_pipeline_product(path):
    base = os.path.basename(path)
    return any(base.endswith(s) for s in PRODUCT_SUFFIXES)


def classify_ext(path):
    """按扩展名初筛：返回 ('候选'|'跳过'|'忽略', 原因)。
    候选=可尝试解析；跳过=已知非流水格式(报告给用户)；忽略=无关文件(.DS_Store 等，静默)。"""
    ext = os.path.splitext(path)[1].lower()
    if os.path.basename(path).lower().endswith("__standardized.csv"):
        return "候选", ""
    if ext in SUPPORTED_EXT:
        return "候选", ""
    if ext in KNOWN_NONSTATEMENT_EXT:
        return "跳过", f"非流水格式（{ext}）：本技能仅支持 Excel/CSV/文本/非图片PDF；图片或扫描件请先 OCR 转文本"
    return "忽略", ""


def screen_files(paths):
    """把一批路径分成 (候选文件列表, 跳过清单[(文件名,原因)])。无关文件静默忽略。
    内容层面的『图片型PDF / 非流水表格』在 standardize() 里进一步判定并抛 NotABankStatement。"""
    candidates, skipped = [], []
    for f in paths:
        if not os.path.isfile(f) or is_pipeline_product(f):
            continue
        kind, reason = classify_ext(f)
        if kind == "候选":
            candidates.append(f)
        elif kind == "跳过":
            skipped.append((os.path.basename(f), reason))
    return candidates, skipped


# ---- 标准字段 ----------------------------------------------------------------
STD_FIELDS = [
    "交易时间", "本方名称", "本方账户", "对手名称", "对手账户",
    "收入金额", "支出金额", "交易金额", "账户余额",
    "银行备注", "账户方附言", "交易渠道", "来源文件名", "来源行号",
]

OUTPUT_FIELDS = [
    "交易唯一编号", "交易时间", "本方名称", "本方账户", "开户行", "账户类型",
    "对手名称", "对手账户", "收入金额", "支出金额", "交易金额", "账户余额",
    "银行备注", "账户方附言", "交易渠道", "来源文件名", "来源行号",
]

# ---- 表头列名 -> 标准字段 同义词词典 -----------------------------------------
# 顺序无所谓；匹配用“原始列包含同义词”或“同义词包含原始列”做模糊匹配。
SYNONYMS = {
    # 仅日期部分（与下面「交易时间」拆两列时，二者合并成完整时间戳）
    "交易日期":   ["交易日期", "入账日期", "记账日期", "记账日", "交易日", "入账日", "会计日期"],
    # 仅时间或完整时间戳（含「入账时间/记账时间」等拆列时间，以及粘连在一起的完整时间戳列）
    "交易时间":   ["交易时间", "入账时间", "记账时间", "交易日期时间", "交易时分"],
    "本方名称":   ["户名", "账户名称", "账户名", "本方户名", "客户名称", "本方名称"],
    "本方账户":   ["账号", "账户号", "卡号", "本方账户", "账户号码", "本方账号"],
    "对手名称":   ["对方户名", "对手信息", "交易对手名称", "对方账户名", "对手户名",
                  "对方名称", "对手名称", "对方账户名称", "对手账户名",
                  "对方单位", "对方单位名称", "对方账户名", "对方客户名", "交易对方"],
    "对手账户":   ["对方账号", "对方账户", "交易对手账号", "对手账户", "对方账户号",
                  "对方卡号", "交易对手账号"],
    "收入金额":   ["收入金额", "收入", "贷方发生额", "存入金额", "贷方金额", "贷方",
                  "转入金额", "转入", "贷方发生额(收入)", "贷方发生额（收入）"],
    "支出金额":   ["支出金额", "支出", "借方发生额", "取出金额", "借方金额", "借方",
                  "转出金额", "转出", "借方发生额(支取)", "借方发生额（支取）"],
    "交易金额":   ["交易金额", "发生额", "金额"],
    "账户余额":   ["账户余额", "余额", "本次余额", "当前余额", "交易后余额"],
    "收支方向":   ["收支方向", "收支", "收/支/其他", "借贷标志", "借贷", "方向", "借贷方向"],
    "银行备注":   ["交易摘要", "摘要", "用途", "交易类型", "交易名称", "交易备注", "相关信息",
                  "备注", "交易种类", "业务摘要", "附言摘要"],
    "账户方附言": ["交易附言", "附言", "留言", "客户附言"],
    "交易渠道":   ["交易渠道", "渠道", "交易方式", "记账渠道"],
}

# 不可信字段（仅作辅助证据，不决定账户归属）
UNTRUSTED = {"银行备注", "账户方附言"}

# ---- 开户行推断词典 -----------------------------------------------------------
# (规范行名, [同义/简称...])。按列表顺序匹配，命中即返回，故把更具体/更易混淆的放前面：
# 区域行（三湘/长沙/上饶/江西…）和「农商/农信/邮储」先于工农中建交等大行简称，避免「农行/中行」误命中。
BANK_PATTERNS = [
    ("湖南三湘银行", ["三湘银行", "三湘"]),
    ("长沙银行", ["长沙银行"]),
    ("上饶银行", ["上饶银行"]),
    ("江西银行", ["江西银行"]),
    ("中国邮政储蓄银行", ["邮政储蓄", "邮储银行", "邮储"]),
    ("农村商业银行", ["农村商业银行", "农商银行", "农商行"]),
    ("农村信用社", ["农村信用", "农信社", "信用社", "农村合作银行"]),
    ("村镇银行", ["村镇银行"]),
    ("上海浦东发展银行", ["浦东发展银行", "浦发银行", "浦发"]),
    ("中国工商银行", ["工商银行", "工行"]),
    ("中国农业银行", ["农业银行", "农行"]),
    ("中国建设银行", ["建设银行", "建行"]),
    ("交通银行", ["交通银行", "交行"]),
    ("中国银行", ["中国银行", "中行"]),
    ("招商银行", ["招商银行", "招行"]),
    ("中信银行", ["中信银行", "中信"]),
    ("中国民生银行", ["民生银行", "民生"]),
    ("兴业银行", ["兴业银行", "兴业"]),
    ("中国光大银行", ["光大银行", "光大"]),
    ("平安银行", ["平安银行"]),
    ("广发银行", ["广发银行", "广发"]),
    ("华夏银行", ["华夏银行", "华夏"]),
    ("北京银行", ["北京银行"]),
    ("上海银行", ["上海银行"]),
    ("南京银行", ["南京银行"]),
    ("宁波银行", ["宁波银行"]),
    ("杭州银行", ["杭州银行"]),
]


def infer_bank(*texts):
    """从文件名/抬头/表头等文本里推断开户行规范名。命中不了返回 ""。"""
    text = _norm(" ".join(str(t) for t in texts if t))
    if not text:
        return ""
    for canonical, syns in BANK_PATTERNS:
        for s in syns:
            if s in text:
                return canonical
    return ""

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
    # 列名里出现「对方/对手/交易对手」时，即使包含「户名/账号」，也不能映射成本方字段。
    # 例如建行列「对方账号与户名」包含“户名”，但它描述的是交易对手，不是本方客户名称。
    is_counterparty_col = any(k in c for k in ("对方", "对手", "交易对手"))
    best = None
    for field, syns in SYNONYMS.items():
        if is_counterparty_col and field in ("本方名称", "本方账户"):
            continue
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


# ---- 行序整理：余额连续性优先（保留原始对账口径，消除排序导致的伪断点） -------------
# 以下函数都以「四元组行」为输入，与 dict/DataFrame 解耦，供 standardize（单文件）与
# integrate（账户级合并/去重后）复用：rows = [(余额|None, 收入|None, 支出|None, 交易时间字符串), ...]

def _balance_breaks(rows):
    """按给定顺序统计余额断点数：账户余额 != 上一笔余额 + 收入 − 支出。余额是对账真值。"""
    breaks, prev = 0, None
    for bal, inc, exp, _t in rows:
        inc = inc or 0
        exp = exp or 0
        if prev is not None and bal is not None and abs(bal - (prev + inc - exp)) >= 0.01:
            breaks += 1
        if bal is not None:
            prev = bal
    return breaks


def _chain_order(rows):
    """用余额连续性重建行序：每行紧跟在「余额 == 本行余额 − 本行净额」的那一行之后。
    专治同秒/同毫秒多笔、以及银行内部记账序与时间戳不一致——时间无法区分、但余额唯一确定先后。
    仅当所有行余额齐全时启用；返回覆盖全部行的顺序索引，拼不出则返回 None。"""
    n = len(rows)
    if n < 3:
        return None
    bals, nets = [], []
    for bal, inc, exp, _t in rows:
        if bal is None:
            return None
        bals.append(round(bal, 2))
        nets.append(round((inc or 0) - (exp or 0), 2))
    pred = [round(bals[i] - nets[i], 2) for i in range(n)]   # 行 i 的前驱余额
    waiting = defaultdict(list)        # 前驱余额值 -> 等待接在其后的行（按原序）
    for i in range(n):
        waiting[pred[i]].append(i)
    endset = set(bals)
    starts = sorted(i for i in range(n) if pred[i] not in endset) or [0]   # 开账行：前驱余额无人匹配
    used, order = [False] * n, []
    for s in starts:
        cur = s
        while cur is not None and not used[cur]:
            used[cur] = True
            order.append(cur)
            nxt = None
            q = waiting.get(bals[cur], [])
            while q:
                c = q.pop(0)
                if not used[c]:
                    nxt = c
                    break
            cur = nxt
    for i in range(n):                 # 未接入链的行按原序补在末尾
        if not used[i]:
            order.append(i)
    return order if len(order) == n else None


def best_continuity_order(rows):
    """在几个候选行序里选「余额断点最少」者，返回 (order: list[int], 策略名)。

    各行/各账户的导出排序五花八门：整体倒序、按日期升序但日内倒序、同秒多笔、内部记账序≠时间戳。
    余额是对账真值，故据余额断点择优；余额数据不足时退化为时间趋势。候选均为块重排或按余额链重建，
    都保留同时刻多笔的合理相对顺序。standardize 用它整理单文件，integrate 用它整理「账户跨文件合并去重后」的序。"""
    n = len(rows)
    idx = list(range(n))
    if n < 3:
        return idx, "原序"
    have_bal = sum(1 for bal, *_ in rows if bal is not None)
    if have_bal < max(2, n // 2):       # 余额不足以判定，退化为时间趋势
        times = [t for *_, t in rows if t]
        dec = sum(1 for a, b in zip(times, times[1:]) if b < a)
        inc = sum(1 for a, b in zip(times, times[1:]) if b > a)
        return (idx[::-1], "时间倒序翻正") if dec > inc else (idx, "原序")
    daykey = lambda i: (rows[i][3] or "")[:10]
    candidates = [
        ("原序", idx),
        ("整体翻转", idx[::-1]),
        ("按日期升序·日内原序", sorted(idx, key=lambda i: (daykey(i), i))),
        ("按日期升序·日内翻转", sorted(idx, key=lambda i: (daykey(i), -i))),
    ]
    chain = _chain_order(rows)         # 余额链重建（治同秒多笔/内部序≠时间），仅当严格更优才胜出
    if chain is not None:
        candidates.append(("余额链重建", chain))
    best = None
    for name, order in candidates:
        br = _balance_breaks([rows[i] for i in order])
        if best is None or br < best[2]:
            best = (order, name, br)
    return best[0], best[1]


def _rows_from_records(records):
    return [(parse_amount(r.get("账户余额")), parse_amount(r.get("收入金额")),
             parse_amount(r.get("支出金额")), r.get("交易时间") or "") for r in records]


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

    # 0) 紧凑完整时间戳 YYYYMMDDHHMMSS（含毫秒/流水号后缀），如农行「交易时间戳」20230201195010370199。
    #    优先处理：否则下游会把日期段误当成时分秒（如 202302 -> 20:23:02），精度被毁、同时刻大量碰撞。
    for m in re.finditer(r"\d{14,}", raw):
        s = m.group(0)
        y, mo, day = int(s[0:4]), int(s[4:6]), int(s[6:8])
        hh, mm, ss = int(s[8:10]), int(s[10:12]), int(s[12:14])
        if 1990 <= y <= 2100 and 1 <= mo <= 12 and 1 <= day <= 31 and hh < 24 and mm < 60 and ss < 60:
            return f"{y:04d}-{mo:02d}-{day:02d} {hh:02d}:{mm:02d}:{ss:02d}"

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
def read_rows_excel(path, open_password=None):
    """返回 (sheet名, rows:list[list])。取第一个有数据的 sheet。"""
    from ymb_standardization_core.readers.input_router import _maybe_decrypted_office_file

    with _maybe_decrypted_office_file(path, open_password=open_password) as source:
        try:
            return _read_rows_excel_source(source)
        except ValueError as exc:
            repaired = _repair_xlsx_invalid_numeric_literals(source, exc)
            if not repaired:
                raise
            try:
                return _read_rows_excel_source(repaired)
            finally:
                try:
                    os.unlink(repaired)
                except OSError:
                    pass


def _read_rows_excel_source(source):
    """Read the first non-empty worksheet from a pandas-compatible Excel source."""
    with pd.ExcelFile(source) as xl:
        for sheet in xl.sheet_names:
            df = xl.parse(sheet, header=None, dtype=str)
            if df.dropna(how="all").shape[0] >= 2:
                rows = df.where(pd.notnull(df), None).values.tolist()
                rows = _sanitize_nan_strings(rows)
                return sheet, rows
        sheet = xl.sheet_names[0]
        df = xl.parse(sheet, header=None, dtype=str)
        rows = df.where(pd.notnull(df), None).values.tolist()
        return sheet, _sanitize_nan_strings(rows)


def _repair_xlsx_invalid_numeric_literals(source, exc):
    """Return a temp xlsx with invalid numeric '.' cells marked as strings, or None."""
    if "could not convert string to float: '.'" not in str(exc):
        return None
    import tempfile
    import zipfile

    try:
        zin = zipfile.ZipFile(source)
    except Exception:
        return None

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
    tmp.close()
    changed = False
    try:
        with zin, zipfile.ZipFile(tmp.name, "w") as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename.startswith("xl/worksheets/") and info.filename.endswith(".xml"):
                    text = data.decode("utf-8")
                    fixed = re.sub(
                        r'(<c\b(?=[^>]*>\s*<v>\.</v>)(?![^>]*\bt=)([^>]*)>)',
                        lambda m: f'<c{m.group(2)} t="str">',
                        text,
                    )
                    if fixed != text:
                        changed = True
                        data = fixed.encode("utf-8")
                zout.writestr(info, data)
    except Exception:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        return None
    if not changed:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        return None
    return tmp.name


def _sanitize_nan_strings(rows):
    """修复 pandas/xlrd 读取 .xls 时空单元格变为 float('nan') 或字符串 'nan' 的问题。
    统一转为 None，保持与下游空值检查（None/""/ "nan"）的一致性。"""
    import math
    sanitized = []
    for row in rows:
        new_row = []
        for v in row:
            if isinstance(v, float) and math.isnan(v):
                new_row.append(None)
            elif isinstance(v, str) and v.strip().lower() == "nan":
                new_row.append(None)
            else:
                new_row.append(v)
        sanitized.append(new_row)
    return sanitized


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
    """用标准化输入路由读取 PDF，命中专属模板时交给对应 reader。"""
    from ymb_standardization_core.readers.router import read_pdf_rows

    return read_pdf_rows(path)


def read_rows(path):
    """返回 (kind, preamble, rows, route_info)。preamble 为表格之外的抬头文本（可为空）。"""
    from ymb_standardization_core.file_hints import load_file_hints_for_path
    from ymb_standardization_core.readers import input_router

    file_hints = load_file_hints_for_path(path)
    hints = file_hints.for_file(path)
    input_router.configure_readers(read_rows_excel, read_rows_csv, NotABankStatement)
    result = input_router.read_rows(path, hints=hints)
    route_info = dict(result.route_info or {})
    hints_audit = file_hints.audit_for_file(path)
    if hints_audit:
        route_info["file_hints"] = hints_audit
    result.route_info.clear()
    result.route_info.update(route_info)
    return (result.kind, result.preamble, result.rows, result.route_info)


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
    # 注意：这里是“抬头/元数据”识别，和 SYNONYMS 里的“数据列名映射”是两套逻辑。
    # 例如建行 PDF 的「客户名称:张三」通常在表格上方抬头里，不在明细表头列里，
    # 因此必须在这里显式匹配，不能只依赖 SYNONYMS["本方名称"]。
    upper = rows[:header_idx] if header_idx is not None else rows[:6]
    meta = preamble + " " + " ".join(
        str(c) for r in upper for c in r if c
    )
    # 英文 PDF 抬头常见空格姓名，如“户名：HUAHUA JIANG 账号：...”，需保留完整姓名。
    m = re.search(
        r"(?:户名|账户名称|账户名|客户名称|客户姓名|企业名称)[:：]?\s*"
        r"([A-Za-z][A-Za-z .'\-]*[A-Za-z])"
        r"(?=\s+(?:账\s*号|卡\s*号|Reference|Account Number)|\s*$)",
        meta,
    )
    if not m:
        m = re.search(r"(?:户名|账户名称|账户名|客户名称|客户姓名|企业名称)[:：]?\s*([^\s,，:：\-]+)", meta)
    if not m:
        m = re.search(r"AccountMR\.\s*(.+?)\s+Reference", meta)
    if not m:
        m = re.search(r"兹证明[:：]?\s*([^（(\s,，:：\-]+)", meta)
    if m:
        info["本方名称"] = m.group(1)
    # 账号：优先带「账号/卡号/账户」标签的；支持掩码 6226****4806
    acct = None
    m = re.search(r"(?:账\s*号|卡\s*号|账\s*户)[^0-9]{0,6}(\d[\d*\-]{5,}\d)", meta)
    if m:
        acct = m.group(1)
        # 户名后接「-账号」结构，如 公司名称-800091876502013
        m = re.search(r"[一-龥)）]-(\d{8,})", meta)
        if m:
            acct = m.group(1)
    if not acct:
        m = re.search(r"Account Number\s+(\d[\d\-]{5,}\d)", meta)
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


_CARD_BIN_RULES = None
_CARD_BIN_BANK_NAMES = None


def _routing_dir():
    return os.path.join(os.path.dirname(__file__), "readers", "routing")


def load_card_bin_bank_names():
    """读取银行卡 BIN 银行代码到中文标准名的映射。"""
    global _CARD_BIN_BANK_NAMES
    if _CARD_BIN_BANK_NAMES is not None:
        return _CARD_BIN_BANK_NAMES
    csv_path = os.path.join(_routing_dir(), "card_bin_banks.csv")
    names = {}
    if os.path.exists(csv_path):
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                bank = str(row.get("bank", "")).strip()
                bank_name = str(row.get("bank_name", "")).strip()
                if bank and bank_name:
                    names[bank] = bank_name
    _CARD_BIN_BANK_NAMES = names
    return _CARD_BIN_BANK_NAMES


def load_card_bin_rules():
    """读取银行卡 BIN 配置。命中只证明“这是卡号段”，不用于反推对公。"""
    global _CARD_BIN_RULES
    if _CARD_BIN_RULES is not None:
        return _CARD_BIN_RULES
    routing_dir = _routing_dir()
    csv_path = os.path.join(routing_dir, "card_bins.csv")
    yaml_path = os.path.join(routing_dir, "card_bins.yaml")
    rules = []
    if os.path.exists(csv_path):
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                prefix = str(row.get("bin", "")).strip()
                if not prefix:
                    continue
                rules.append({
                    "prefix": prefix,
                    "bank": str(row.get("bank", "")).strip(),
                    "card_type": str(row.get("type", "")).strip(),
                    "card_length": str(row.get("length", "")).strip(),
                })
    elif os.path.exists(yaml_path):
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        rules = data.get("card_bins", []) if isinstance(data, dict) else []
    _CARD_BIN_RULES = sorted(
        [r for r in rules if str(r.get("prefix", "")).isdigit()],
        key=lambda r: len(str(r.get("prefix", ""))),
        reverse=True,
    )
    return _CARD_BIN_RULES


def match_card_bin(account):
    """按配置匹配本方账号的银行卡 BIN。掩码账号只使用星号前的可见前缀。"""
    raw = str(account or "").strip()
    if not raw or raw.startswith("未识别账户#"):
        return None
    visible_prefix = raw.split("*", 1)[0]
    digits = re.sub(r"\D", "", visible_prefix)
    if len(digits) < 6:
        return None
    for rule in load_card_bin_rules():
        prefix = str(rule.get("prefix", ""))
        if digits.startswith(prefix):
            return rule
    return None


def bank_name_from_card_bin(card_bin):
    if not card_bin:
        return ""
    bank_code = str(card_bin.get("bank") or "").strip()
    return load_card_bin_bank_names().get(bank_code, bank_code)


KNOWN_ACCOUNT_TYPES = {"个人", "对公"}
INFERRED_ACCOUNT_TYPES = {"拟对公"}

ENTERPRISE_COUNTERPARTY_KEYWORDS = [
    "有限公司", "有限责任公司", "股份有限公司", "集团", "公司",
    "合作社", "个体工商户", "商行", "经营部", "门市部", "中心",
    "工厂", "厂", "银行", "支付", "财付通", "银联", "税务", "国库",
]


def is_enterprise_counterparty(name):
    """按对手户名结构识别企业/机构名称；不使用摘要、附言等不可信字段。"""
    text = _norm(name)
    if not text:
        return False
    return any(keyword in text for keyword in ENTERPRISE_COUNTERPARTY_KEYWORDS)


def infer_account_type_from_counterparties(records, min_rows=20, min_ratio=0.3):
    """企业对手方达到一定比例时，只给出“拟对公”线索，不直接等同硬证据。"""
    valid_names = [str(row.get("对手名称") or "").strip() for row in records if str(row.get("对手名称") or "").strip()]
    total = len(valid_names)
    enterprise_count = sum(1 for name in valid_names if is_enterprise_counterparty(name))
    ratio = round(enterprise_count / total, 4) if total else 0
    stats = {
        "valid_counterparty_count": total,
        "enterprise_counterparty_count": enterprise_count,
        "enterprise_counterparty_ratio": ratio,
        "min_rows": min_rows,
        "min_ratio": min_ratio,
    }
    if total >= min_rows and ratio >= min_ratio:
        return "拟对公", stats
    return "", stats


def resolve_account_type(manual_type, account, metadata_hint, route_type, counterparty_hint=""):
    """账户类型统一口径：人工参数优先；卡 BIN 命中判个人；交易画像只给“拟对公”。"""
    if manual_type:
        return manual_type, "manual", None
    card_bin = match_card_bin(account)
    if card_bin:
        return "个人", "card_bin", card_bin
    if metadata_hint in KNOWN_ACCOUNT_TYPES:
        return metadata_hint, "metadata", None
    if route_type in KNOWN_ACCOUNT_TYPES:
        return route_type, "route", None
    if counterparty_hint in INFERRED_ACCOUNT_TYPES:
        return counterparty_hint, "counterparty_profile", None
    return "未知", "unknown", None


# ---- 主流程 ------------------------------------------------------------------
def _fmt_amt(x):
    """金额归一化为定点字符串，让 PDF「1,026.00」与 Excel「1026」在指纹里一致。"""
    if x is None or x == "":
        return ""
    try:
        return f"{float(x):.2f}"
    except (TypeError, ValueError):
        return str(x).strip()


def build_fingerprint(self_name, account, time_str, opp_name, opp_acct,
                      income, expense, balance):
    """交易内容指纹：本方名称/账户 + 时间 + 对手名称/账户 + 收入/支出 + 余额。
    不含来源文件/行号，使同一笔交易在不同文件（PDF 与 Excel 同源、整份文件重复提交）里
    得到相同指纹，作为跨文件去重依据（对应附件A「交易唯一编号可由账户/时间/金额组合生成」）。"""
    def n(s):
        return _norm(s) if s not in (None, "") else ""
    raw = "|".join([
        n(self_name), n(account), time_str or "",
        n(opp_name), n(opp_acct),
        _fmt_amt(income), _fmt_amt(expense), _fmt_amt(balance),
    ])
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def build_unique_id(fingerprint, occurrence):
    """由内容指纹生成交易唯一编号。同一文件内出现的真实重复笔（指纹相同）追加序号保证唯一；
    首笔不加后缀，使其与其它文件里同一笔交易的编号一致，便于整合阶段按编号精确去重。"""
    base = f"TX-{fingerprint}"
    return base if occurrence == 0 else f"{base}-{occurrence + 1}"


def infer_account_from_records(records):
    """从已生成的标准化明细反推报告层本方户名/账号，避免元数据停留在抬头嗅探结果。"""
    candidates = {}
    for row in records:
        account = str(row.get("本方账户") or "").strip()
        name = str(row.get("本方名称") or "").strip()
        if not account or account.startswith("未识别账户#"):
            continue
        key = (account, name)
        stats = candidates.setdefault(key, {"rows": 0, "balance_rows": 0, "sources": set()})
        stats["rows"] += 1
        if row.get("账户余额") not in (None, ""):
            stats["balance_rows"] += 1
        source = str(row.get("来源文件名") or "").strip()
        if source:
            stats["sources"].add(source)

    if not candidates:
        return {}

    (account, name), _stats = max(
        candidates.items(),
        key=lambda item: (item[1]["balance_rows"], item[1]["rows"], len(item[1]["sources"]), item[0][1], item[0][0]),
    )
    return {"本方账户": account, "本方名称": name}


def is_standardized_input_name(path):
    """文件名为 <stem>__standardized.csv 时，直接视为已完成阶段一标准化。"""
    return os.path.basename(path).lower().endswith("__standardized.csv")


def _standardized_output_stem(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    return stem[:-len("__standardized")] if stem.endswith("__standardized") else stem


def adopt_standardized_input(path, out_dir=None):
    """接收已完成阶段一标准化的 CSV：复制到工作区，并生成最小 mapping 报告供状态机验收。"""
    fname = os.path.basename(path)
    if not is_standardized_input_name(path):
        raise NotABankStatement("文件名不是 <stem>__standardized.csv，不能按已标准化输入接收")
    if out_dir is None:
        out_dir = os.path.join(os.path.dirname(path), "standardized")
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, fname)
    if os.path.abspath(path) != os.path.abspath(csv_path):
        shutil.copy2(path, csv_path)

    df = pd.read_csv(csv_path, dtype=str, encoding="utf-8-sig")
    stem = _standardized_output_stem(path)
    json_path = os.path.join(out_dir, f"{stem}__mapping.json")
    report = {
        "文件画像": {
            "确认银行": "已标准化输入",
            "开户行": "",
            "账户类型": "",
            "文件类型": "csv",
            "命中模板": "文件名已标准化输入",
            "整体置信度": 1.0,
            "本方名称": "",
            "本方账户": "",
        },
        "预处理方案": [
            {"步骤": "文件名识别", "处理动作": "按 <stem>__standardized.csv 判定为已标准化输入",
             "处理原因": "用户或前置系统已完成阶段一标准化", "影响范围": "阶段一跳过解析"},
            {"步骤": "文件接收", "处理动作": "复制标准化 CSV 到阶段工作区",
             "处理原因": "让 manifest、receipt、validate_stage.py 按正常阶段一产物验收", "影响范围": "文件路径"},
        ],
        "表头识别": {"表头行号": 0, "表头字段": list(df.columns), "置信度": 1.0},
        "字段映射": {},
        "校验预期": {
            "日期可解析": "交易时间" in df.columns,
            "金额可解析": bool({"收入金额", "支出金额", "交易金额"}.intersection(df.columns)),
            "余额可校验": "账户余额" in df.columns,
            "缺失关键字段": [],
            "可能冲突字段": [],
        },
        "人工复核事项": [],
        "标准化统计": {"交易笔数": len(df), "金额结构": "已标准化",
                    "丢弃噪声行": 0, "行序整理策略": "保持已标准化输入原样"},
        "判断依据": "文件名匹配 <stem>__standardized.csv，阶段一视为已完成标准化。",
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return csv_path, json_path, report


def standardize(path, out_dir=None, customer=None, bank=None,
                account_type=None, header_row=None, overrides=None,
                force_customer=False):
    fname = os.path.basename(path)
    stem = os.path.splitext(fname)[0]
    manual_account_type = account_type
    if is_standardized_input_name(path):
        return adopt_standardized_input(path, out_dir=out_dir)

    file_kind, preamble, rows, route_info = read_rows(path)
    route_info = dict(route_info or {})

    # 空文件 / PDF 无可解析记录：区分“完全无文本”和“有文本但没有命中结构化/专属解析器”。
    if not rows or not any(any(c not in (None, "", "nan") for c in (r or [])) for r in rows):
        if file_kind == "pdf" and preamble:
            raise NotABankStatement("PDF 可抽取文本，但未识别到结构化流水表格或已支持的专属文本模板")
        raise NotABankStatement(
            "图片型/扫描件 PDF，未抽取到文本（需先 OCR 转文本）" if file_kind == "pdf"
            else "空文件或无可解析内容")

    if header_row is None:
        header_idx, hits = find_header_row(rows)
        route_header_keys = {
            _norm(src)
            for src in (route_info.get("column_mapping") or {})
            if str(src or "").strip()
        }
        if route_header_keys:
            best_route_idx, best_route_hits = None, -1
            for i, row in enumerate(rows[:30]):
                route_hits = 0
                for value in row:
                    key = _norm(value)
                    if key and (key in route_header_keys or match_field(key)):
                        route_hits += 1
                if route_hits > best_route_hits:
                    best_route_idx, best_route_hits = i, route_hits
            if best_route_idx is not None and best_route_hits > (hits or 0):
                header_idx, hits = best_route_idx, best_route_hits
    else:
        header_idx, hits = header_row, None
    if header_idx is None:
        raise NotABankStatement(
            "未识别到银行流水表头" +
            ("（疑似图片型PDF或非流水文件）" if file_kind == "pdf" else "（疑似非流水文件）"))

    header = [(_norm(c) if c is not None else "") for c in rows[header_idx]]
    data_rows = rows[header_idx + 1:]

    acct = sniff_account_info(rows, header_idx, preamble)
    if customer and (force_customer or not acct["本方名称"]):
        acct["本方名称"] = customer
    account_type, account_type_source, account_type_card_bin = resolve_account_type(
        manual_account_type,
        acct["本方账户"],
        acct["账户类型线索"],
        route_info.get("account_type", ""),
    )

    # 开户行：优先 --bank（仍做规范化），否则从内容证据推断；文件名仅作兼容兜底，并记录来源。
    upper_text = " ".join(str(c) for r in rows[:header_idx + 1] for c in (r or []) if c)
    bank_name = ""
    bank_infer_source = "未知"
    if bank:
        bank_name = infer_bank(bank) or bank
        bank_infer_source = "参数"
    else:
        bank_name = infer_bank(preamble, upper_text)
        if bank_name:
            bank_infer_source = "内容"
        else:
            bank_name = infer_bank(fname)
            if bank_name:
                bank_infer_source = "文件名"
    if not bank_name and route_info.get("fingerprint_id") == "md5:f25d1960686525515cc3c5d3eb69ad59":
        bank_name = "农村商业银行"
        bank_infer_source = "router"
    if not bank_name:
        route_bank = str(route_info.get("bank") or "").strip()
        if route_bank and "银行" in route_bank and route_bank not in {"微信支付", "支付宝"}:
            bank_name = route_bank
            bank_infer_source = "router"

    # ---- 列 -> 标准字段 映射 ----
    overrides = overrides or {}
    route_column_mapping = {
        _norm(src): dst
        for src, dst in (route_info.get("column_mapping") or {}).items()
        if src and dst
    }
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
        elif _norm(col) in route_column_mapping:
            field = route_column_mapping[_norm(col)]
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

    # 非流水文件自动排除（仅在全自动识别时启用；--header-row/--map 视为用户已确认，不拦截）：
    # 一张真正的流水至少要「有金额或余额」且「有时间或余额」可定位；据此把发票/名册/其它表筛掉。
    if header_row is None and not overrides:
        has_amount = bool(field_to_cols.get("收入金额") or field_to_cols.get("支出金额")
                          or field_to_cols.get("交易金额"))
        has_balance = bool(field_to_cols.get("账户余额"))
        has_time = bool(date_cols or time_cols)
        if (hits is not None and hits < 2) or not (has_amount or has_balance) \
                or not (has_time or has_balance):
            missing = []
            if not (has_amount or has_balance):
                missing.append("金额/余额列")
            if not (has_time or has_balance):
                missing.append("交易时间列")
            why = "、".join(missing) or f"仅命中 {hits} 个标准列"
            raise NotABankStatement(f"未识别为银行流水（{why}），疑似发票/名册等非流水文件")

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
    fp_seen = Counter()   # 同一文件内的内容指纹计数，给真实重复笔加序号保证编号唯一
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

        bank_memo = cell(field_to_cols.get("银行备注", []))
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
                if "冲正" in bank_memo and txn < 0:
                    txn = abs(txn)
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
            is_income = ("收" in direction) or ("贷" in direction) or ("入" in direction)
            is_expense = ("支" in direction) or ("出" in direction) or ("借" in direction)
            if amt is not None:
                if is_income and not is_expense:
                    income = abs(amt)
                elif is_expense and not is_income:
                    expense = abs(amt)
                elif not direction and amt >= 0:
                    income = amt
                elif not direction:
                    expense = abs(amt)

        balance = parse_amount(cell(field_to_cols.get("账户余额", [])))
        opp_name = cell(field_to_cols.get("对手名称", []))
        opp_acct = cell(field_to_cols.get("对手账户", []))
        cust_memo = cell(field_to_cols.get("账户方附言", []))
        channel = cell(field_to_cols.get("交易渠道", []))

        # 解析后仍全空（无时间、无任何金额、无余额）的行视为残留噪声丢弃
        if (not t) and income is None and expense is None and txn is None and balance is None:
            dropped_noise += 1
            continue

        # 汇总/合计页脚行（如农行页脚「总收入笔数/总支出金额」标签行及其数值行）：交易时间无有效日期、
        # 且无对手/摘要/附言，只剩金额合计——这是统计行不是真实交易，丢弃（否则翻正后顶在最前，打断余额校验）。
        if not re.match(r"\d{4}-\d{2}-\d{2}", t or "") and not (opp_name or opp_acct or bank_memo or cust_memo):
            dropped_noise += 1
            continue

        # 本方账户/本方名称有两类来源：
        # 1) 数据列来源：表格明细中存在「本方名称/客户名称/账户名称」等列，已通过 SYNONYMS 映射到标准字段；
        # 2) 抬头来源：表格上方元数据中存在「户名/客户名称/账号」等信息，由 sniff_account_info() 抽成常量。
        # 若行内数据列有值，优先使用行内值；否则使用抬头常量。这样既支持单账户抬头式流水，
        # 也支持同一文件中多账户/多主体混在明细列里的情况。
        row_self_acct = cell(field_to_cols.get("本方账户", []))
        row_self_name = cell(field_to_cols.get("本方名称", []))
        # 抬头和数据列都没有账号时，才在行级写入文件隔离占位账号，避免跨文件错误合并。
        self_acct = row_self_acct or acct["本方账户"] or f"未识别账户#{stem}"
        cust = customer if force_customer and customer else (row_self_name or acct["本方名称"] or customer)

        # 交易金额 = 收入金额(空→0) − 支出金额(空→0)，带符号（流入为正、流出为负）；
        # 两者皆空（本行未解析出金额）时留空，不臆造 0。
        if income is None and expense is None:
            txn = None
        else:
            txn = round((income or 0) - (expense or 0), 2)

        # 交易唯一编号：基于内容指纹（账户名+时间+对手+收支+余额），同文件内重复笔加序号
        fp = build_fingerprint(cust, self_acct, t, opp_name, opp_acct,
                               income, expense, balance)
        occ = fp_seen[fp]
        fp_seen[fp] += 1
        uid = build_unique_id(fp, occ)

        std_records.append({
            "交易唯一编号": uid,
            "交易时间": t,
            "本方名称": cust,
            "本方账户": self_acct,
            "开户行": bank_name,
            "账户类型": account_type,
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

    # ---- 整理行序，使余额连续性最佳（保留原始对账口径） ----
    # 块重排/按余额链重建、保留同时刻多笔的相对顺序、来源行号不变可追溯。使输出为可对账的正序，
    # 让下游严格按「文件内原始顺序」做余额校验，避免按时间排序打乱同秒/同日多笔产生伪断点。
    _order, order_strategy = best_continuity_order(_rows_from_records(std_records))
    std_records = [std_records[i] for i in _order]

    record_account = infer_account_from_records(std_records)
    if record_account.get("本方账户"):
        acct["本方账户"] = record_account["本方账户"]
    if record_account.get("本方名称") and not (force_customer and customer):
        acct["本方名称"] = record_account["本方名称"]
    counterparty_account_type, counterparty_account_type_stats = infer_account_type_from_counterparties(std_records)
    account_type, account_type_source, account_type_card_bin = resolve_account_type(
        manual_account_type,
        acct["本方账户"],
        acct["账户类型线索"],
        route_info.get("account_type", ""),
        counterparty_account_type,
    )
    card_bin_bank_name = bank_name_from_card_bin(account_type_card_bin)
    if card_bin_bank_name and (not bank_name or bank_infer_source in {"未知", "文件名"}):
        bank_name = card_bin_bank_name
        bank_infer_source = "card_bin"
    for record in std_records:
        record["账户类型"] = account_type
        record["开户行"] = bank_name

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
    # 交易时间为必填字段：列缺失、或解析后仍有空值，都升级为人工复核
    empty_time = sum(1 for r in std_records if not r["交易时间"])
    if not (date_cols or time_cols):
        key_missing.append("交易时间")
        review_items.append({
            "字段": "交易时间", "问题": "未识别到日期/时间列，但交易时间为必填字段不可空缺",
            "证据": f"表头={header}",
            "建议动作": "用 --map 指定列（如 入账日期=交易日期 入账时间=交易时间），拆两列会自动合并"})
    elif empty_time:
        review_items.append({
            "字段": "交易时间", "问题": f"{empty_time} 行交易时间解析为空（必填字段不应空缺）",
            "证据": f"日期列={[header[i] for i in date_cols]} 时间列={[header[i] for i in time_cols]}",
            "建议动作": "人工核对这些行的原始日期/时间格式"})
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
            "确认银行": bank_name or "未知",
            "开户行": bank_name,
            "开户行识别来源": bank_infer_source,
            "账户类型": account_type,
            "account_type_source": account_type_source,
            "card_bin_match": account_type_card_bin or {},
            "counterparty_profile": counterparty_account_type_stats,
            "文件类型": file_kind,
            "命中模板": "自动同义词映射",
            "整体置信度": overall_conf,
            "本方名称": acct["本方名称"],
            "本方账户": acct["本方账户"],
            "reader_id": route_info.get("reader_id", ""),
            "decision": route_info.get("decision", ""),
            "fingerprint_id": route_info.get("fingerprint_id", route_info.get("id", "")),
            "file_type": route_info.get("file_type", file_kind),
            "bank": route_info.get("bank", ""),
            "account_type": route_info.get("account_type", ""),
            "column_mapping": route_info.get("column_mapping", {}),
            "identity_evidence": route_info.get("identity_evidence", []),
            "columns_evidence": route_info.get("columns_evidence", []),
            "ocr_supported": False,
            "ocr_used": False,
        },
        "预处理方案": [
            {"步骤": "输入路由", "处理动作": (
                f"使用 reader_id={route_info.get('reader_id', 'unknown')}"
            ),
             "处理原因": "按文件类型、fingerprint 和抽取模式选择确定性 rows 读取策略",
             "影响范围": "文件读取与字段初始结构"},
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
                    "丢弃噪声行": dropped_noise, "行序整理策略": order_strategy},
        "判断依据": f"路由 fingerprint_id={route_info.get('fingerprint_id', '')}；"
                    f"reader_id={route_info.get('reader_id', 'unknown')}；"
                    f"基于表头同义词匹配命中 {hits} 列；金额结构判为「{amount_mode}」；"
                    f"账户类型线索：{account_type}。摘要/附言按不可信输入仅作辅助。",
    }

    # ---- 落盘 ----
    if out_dir is None:
        out_dir = os.path.join(os.path.dirname(path), "standardized")
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(fname)[0]
    csv_path = os.path.join(out_dir, f"{stem}__standardized.csv")
    json_path = os.path.join(out_dir, f"{stem}__mapping.json")

    pd.DataFrame(std_records, columns=OUTPUT_FIELDS).to_csv(csv_path, index=False, encoding="utf-8-sig")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    return csv_path, json_path, report


def main():
    ap = argparse.ArgumentParser(description="单文件银行流水标准化")
    ap.add_argument("file")
    ap.add_argument("--out-dir")
    ap.add_argument("--customer")
    ap.add_argument("--force-customer", action="store_true",
                    help="强制用 --customer 覆盖原始文件识别出的本方名称；默认 --customer 仅作缺失兜底")
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

    try:
        csv_path, json_path, report = standardize(
            args.file, out_dir=args.out_dir, customer=args.customer, bank=args.bank,
            account_type=args.account_type, header_row=args.header_row, overrides=overrides,
            force_customer=args.force_customer)
    except NotABankStatement as e:
        sys.exit(f"[SKIP] {os.path.basename(args.file)}：{e.reason}")

    print(f"[OK] {os.path.basename(args.file)}")
    print(f"  标准化流水 -> {csv_path}（{report['标准化统计']['交易笔数']} 笔，"
          f"金额结构「{report['标准化统计']['金额结构']}」）")
    print(f"  映射报告   -> {json_path}")
    if report["人工复核事项"]:
        print(f"  [WARNING] 人工复核 {len(report['人工复核事项'])} 项：" +
              "; ".join(r["问题"] for r in report["人工复核事项"]))


if __name__ == "__main__":
    main()
