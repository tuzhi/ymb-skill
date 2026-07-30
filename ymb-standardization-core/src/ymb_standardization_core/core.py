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
  6. 输出标准化流水 CSV；调用方需要单文件审计时可选输出字段映射报告 JSON。

设计原则（与提示词附件一致）：
  - 不编造缺失字段：映射不到就留空并写入“人工复核事项”。
  - 摘要/备注/附言为不可信输入，只映射不作账户归属判断。
  - 低置信项进入人工复核，不自动沉淀为模板。

用法：
  python standardize.py <原始文件> [--out-dir DIR]
      [--bank 银行名] [--account-type 对公|个人|未知] [--header-row N]
      [--map 原始列=标准字段 ...]   # 人工覆盖自动映射

输出（默认写到原始文件同目录的 standardized/ 下）：
  <stem>__<ext>__standardized.csv      标准化流水
  <stem>__<ext>__mapping.json          可选字段映射报告（Prompt 1 结构）
"""
import argparse, csv, json, os, re, sys, hashlib, shutil
from collections import Counter, defaultdict
from datetime import datetime

from ymb_standardization_core.contracts import StandardizationContext

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


class SourceFormatQualityError(Exception):
    """已识别银行流水模板，但原始导出缺少交付必需选项。"""

    code = "MISSING_REQUIRED_EXPORT_COLUMNS"

    def __init__(self, reason, route_info=None):
        super().__init__(reason)
        self.reason = reason
        self.route_info = dict(route_info or {})


class YamlRouteRequiredError(SourceFormatQualityError):
    """原始流水未唯一命中已发布 YAML，不允许进入正式标准化。"""

    code = "YAML_ROUTE_REQUIRED"


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
    if re.search(r"\.(?:xlsx|xlsm|xls|pdf)_\d+$", os.path.basename(path).lower()):
        return (
            "跳过",
            "文件已用 _数字伪后缀标记为转换文件或非原始文件，不作为原始流水接收",
        )
    if ext in KNOWN_NONSTATEMENT_EXT:
        return "跳过", f"非流水格式（{ext}）：本技能仅支持 Excel/CSV/文本/非图片PDF；图片或扫描件请先 OCR 转文本"
    return "忽略", ""


def screen_files(paths):
    """把一批路径分成 (候选文件列表, 跳过清单[(文件名,原因)])。无关文件静默忽略。
    内容层面的『图片型PDF / 非流水表格』在 standardize() 里进一步判定并抛 NotABankStatement。"""
    from ymb_standardization_core.readers.input_router import pdf_to_wps_rejection_reason

    candidates, skipped = [], []
    for f in paths:
        if not os.path.isfile(f) or is_pipeline_product(f):
            continue
        kind, reason = classify_ext(f)
        if kind == "候选":
            reason = pdf_to_wps_rejection_reason(f)
            if reason:
                skipped.append((os.path.basename(f), reason))
            else:
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
    "银行备注", "账户方附言", "交易渠道", "来源文件名", "来源行号", "__对手开户行",
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

_BANK_ALIAS_RULES = None


def load_bank_alias_rules():
    """从 routing YAML 加载银行规范名及别名，较长别名优先匹配。"""
    global _BANK_ALIAS_RULES
    if _BANK_ALIAS_RULES is not None:
        return _BANK_ALIAS_RULES
    path = os.path.join(_routing_config_dir(), "bank_aliases.yaml")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    aliases = data.get("bank_aliases") if isinstance(data, dict) else None
    if not isinstance(aliases, dict):
        raise ValueError(f"invalid bank alias config: {path}")
    rules = []
    for canonical, values in aliases.items():
        if not canonical or not isinstance(values, list):
            raise ValueError(f"invalid bank alias entry: {canonical!r}")
        for alias in values:
            normalized = _norm(alias)
            if normalized:
                rules.append((normalized, str(canonical).strip()))
    _BANK_ALIAS_RULES = sorted(rules, key=lambda item: len(item[0]), reverse=True)
    return _BANK_ALIAS_RULES


def infer_bank(*texts):
    """使用 YAML 别名配置规范化银行名；命中不了返回 ""。"""
    text = _norm(" ".join(str(t) for t in texts if t))
    if not text:
        return ""
    for alias, canonical in load_bank_alias_rules():
        if alias in text:
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
# 以下函数以「四元组行」或带批次键的「五元组行」为输入，与 dict/DataFrame 解耦，供
# standardize（单文件）、integrate（账户级合并/去重后）及后续余额报告复用：
# rows = [(余额|None, 收入|None, 支出|None, 交易时间字符串[, 内存批次键]), ...]


def _continuity_row_parts(row):
    """兼容原有四元组，以及仅供余额校验使用的五元组。"""
    bal, inc, exp, transaction_time = row[:4]
    batch_key = row[4] if len(row) > 4 else None
    return bal, inc, exp, transaction_time, batch_key


def _continuity_units(rows):
    """生成余额校验单元：普通交易逐笔校验，连续批量交易汇总后只校验一次。

    银行批量代发可能把一个批次展开成多名收款人的明细，但每行重复展示同一个批次后余额。
    这里的汇总只存在于内存，不删除、不合并、不改写最终输出的逐笔交易。
    """
    units = []
    for index, row in enumerate(rows):
        bal, inc, exp, transaction_time, batch_key = _continuity_row_parts(row)
        if batch_key and units and units[-1]["batch_key"] == batch_key:
            unit = units[-1]
        else:
            unit = {
                "batch_key": batch_key,
                "indices": [],
                "balance": bal,
                "income": 0.0,
                "expense": 0.0,
                "time": transaction_time,
            }
            units.append(unit)
        unit["indices"].append(index)
        unit["income"] += inc or 0
        unit["expense"] += exp or 0
    return units


def balance_break_indices(rows):
    """返回余额断点对应的明细行索引；共享余额批次按批次净额只校验一次。"""
    breaks, prev = [], None
    for unit in _continuity_units(rows):
        bal = unit["balance"]
        if prev is not None and bal is not None:
            expected = prev + unit["income"] - unit["expense"]
            if abs(bal - expected) >= 0.01:
                # 批次异常定位到批次末行，便于报告展示完整批次边界。
                breaks.append(unit["indices"][-1])
        if bal is not None:
            prev = bal
    return breaks


def continuity_unit_count(rows):
    """返回实际余额校验单元数，批次内多笔只计一个单元。"""
    return len(_continuity_units(rows))

def _balance_breaks(rows):
    """按给定顺序统计余额断点数；批量交易按共享余额批次计算。"""
    return len(balance_break_indices(rows))


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
    have_bal = sum(1 for row in rows if _continuity_row_parts(row)[0] is not None)
    if have_bal < max(2, n // 2):       # 余额不足以判定，退化为时间趋势
        times = [
            _continuity_row_parts(row)[3]
            for row in rows
            if _continuity_row_parts(row)[3]
        ]
        dec = sum(1 for a, b in zip(times, times[1:]) if b < a)
        inc = sum(1 for a, b in zip(times, times[1:]) if b > a)
        return (idx[::-1], "时间倒序翻正") if dec > inc else (idx, "原序")
    daykey = lambda i: (_continuity_row_parts(rows[i])[3] or "")[:10]
    candidates = [
        ("原序", idx),
        ("整体翻转", idx[::-1]),
        ("按日期升序·日内原序", sorted(idx, key=lambda i: (daykey(i), i))),
        ("按日期升序·日内翻转", sorted(idx, key=lambda i: (daykey(i), -i))),
    ]
    precise_time = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?$")
    if all(precise_time.match(str(_continuity_row_parts(rows[i])[3] or "").strip()) for i in idx):
        # 仅秒级及以上时间才允许按完整时间重排；日期级/分钟级流水不能据此臆定日内顺序。
        candidates.append(("按完整时间升序", sorted(
            idx, key=lambda i: (_continuity_row_parts(rows[i])[3], i)
        )))
    has_batch = any(_continuity_row_parts(row)[4] for row in rows)
    if not has_batch:
        # 普通流水继续使用原有逐笔余额链。
        chain_rows = [_continuity_row_parts(row)[:4] for row in rows]
        chain = _chain_order(chain_rows)
        if chain is not None:
            candidates.append(("余额链重建", chain))
    best = None
    for name, order in candidates:
        br = _balance_breaks([rows[i] for i in order])
        if best is None or br < best[2]:
            best = (order, name, br)
    return best[0], best[1]


def continuity_rows(records):
    """把标准记录转换为余额校验行，并识别连续共享余额批次。

    识别条件必须同时满足：同来源文件、记录连续、交易时间相同、余额相同、收支方向一致、
    账户类型为对公，且备注或附言至少命中“批量、代发、工资”之一。同余额是必要条件，
    防止把同一秒内正常逐笔记账的工资交易误当成共享余额批次。
    """
    rows, evidence = [], []
    for record in records:
        bal = parse_amount(record.get("账户余额"))
        inc = parse_amount(record.get("收入金额"))
        exp = parse_amount(record.get("支出金额"))
        transaction_time = str(record.get("交易时间") or "").strip()
        rows.append([bal, inc, exp, transaction_time, None])
        business_text = _norm(
            f"{record.get('银行备注') or ''} {record.get('账户方附言') or ''}"
        )
        if inc is not None and inc > 0 and not (exp is not None and exp > 0):
            direction = "收入"
        elif exp is not None and exp > 0 and not (inc is not None and inc > 0):
            direction = "支出"
        else:
            direction = ""
        evidence.append({
            "source": str(record.get("来源文件名") or "").strip(),
            "time": transaction_time,
            "balance": round(bal, 2) if bal is not None else None,
            "direction": direction,
            "corporate": str(record.get("账户类型") or "").strip() == "对公",
            "keyword": any(word in business_text for word in ("批量", "代发", "工资")),
        })

    start = 0
    while start < len(rows):
        first = evidence[start]
        signature = (first["source"], first["time"], first["balance"])
        end = start + 1
        # 只向后吸收连续且“同文件、同时间、同余额”的记录，绝不跨过普通交易拼批次。
        while end < len(rows):
            current = evidence[end]
            if (current["source"], current["time"], current["balance"]) != signature:
                break
            end += 1
        group = evidence[start:end]
        directions = {item["direction"] for item in group}
        if (
            end - start >= 2
            and first["source"] and first["time"] and first["balance"] is not None
            and all(item["corporate"] for item in group)
            and len(directions) == 1 and "" not in directions
            and any(item["keyword"] for item in group)
        ):
            # 批次键只在内存存在；标准 CSV 和最终交付物仍保持原有字段及逐笔明细。
            batch_key = ("shared_balance_batch", start, end, *signature)
            for index in range(start, end):
                rows[index][4] = batch_key
        start = end
    return [tuple(row) for row in rows]


def _rows_from_records(records):
    """兼容标准化流程原有内部调用名称。"""
    return continuity_rows(records)


def parse_datetime(date_part, time_part, date_order=""):
    """合并日期列与时间列，同时保留原始时间精度。

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

    # 日期 token：YYYY-MM-DD / YYYY/MM/DD / YYYYMMDD；两位年份仅在 YAML 明确声明顺序时解析。
    md = re.search(r"(\d{4})[-/]?(\d{1,2})[-/]?(\d{1,2})", raw)
    short_md = None
    date_order = str(date_order or "").strip().lower()
    if not md and date_order in {"dmy", "mdy", "ymd"}:
        short_md = re.search(r"(?<!\d)(\d{2})[-/](\d{2})[-/](\d{2})(?!\d)", raw)
    # 时间 token：HH:MM:SS / HH:MM / 紧跟在日期后的 6 位 HHMMSS
    mt = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", raw)
    if not md and not short_md:
        return raw
    if short_md:
        parts = {key: int(value) for key, value in zip(date_order, short_md.groups())}
        short_year = parts["y"]
        y = str(2000 + short_year if short_year <= 68 else 1900 + short_year)
        mo, day = str(parts["m"]).zfill(2), str(parts["d"]).zfill(2)
        date_token_end = short_md.end()
    else:
        y, mo, day = md.group(1), md.group(2).zfill(2), md.group(3).zfill(2)
        date_token_end = md.end()
    date_str = f"{y}-{mo}-{day}"

    hh = mm = ss = "00"
    precision = "date"
    if mt:
        hh, mm = mt.group(1).zfill(2), mt.group(2)
        ss = mt.group(3) or "00"
        precision = "second" if mt.group(3) is not None else "minute"
    else:
        # 没有带冒号的时间：尝试日期后面紧跟的 6 位/4 位数字（HHMMSS / HHMM）
        tail = raw[date_token_end:]
        m6 = re.search(r"(\d{6})", tail) or re.search(r"(\d{6})", t)
        if m6:
            v = m6.group(1)
            hh, mm, ss = v[0:2], v[2:4], v[4:6]
            precision = "second"
        else:
            m4 = re.search(r"\b(\d{4})\b", tail)
            if m4:
                v = m4.group(1)
                hh, mm = v[0:2], v[2:4]
                precision = "minute"
    if not (1990 <= int(y) <= 2100):
        return ""   # 年份越界，多半是把卡号/编号误当日期，判为无效
    try:
        dt = datetime(int(y), int(mo), int(day), int(hh), int(mm), int(ss))
        if precision == "date":
            return dt.strftime("%Y-%m-%d")
        if precision == "minute":
            return dt.strftime("%Y-%m-%d %H:%M")
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        # 时分秒越界等异常，至少保住日期
        return date_str


def normalized_transaction_time_precision(value):
    """从标准化时间文本本身判断精度，不再依赖文件级 YAML 结论。"""
    text = "" if value is None else str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return "date"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", text):
        return "minute"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", text):
        return "second"
    return "unknown"


def infer_transaction_time_precision(data_rows, date_cols, time_cols, route_info=None):
    """汇总逐行交易时间精度；同一文件允许返回 mixed。"""
    route_info = route_info or {}
    seen = set()
    for row in data_rows:
        date_value = " ".join(
            str(row[index]).strip()
            for index in date_cols
            if index < len(row) and str(row[index] or "").strip()
        )
        time_value = " ".join(
            str(row[index]).strip()
            for index in time_cols
            if index < len(row) and str(row[index] or "").strip()
        )
        parsed = parse_datetime(
            date_value or time_value,
            time_value if date_cols else "",
            route_info.get("date_order", ""),
        )
        precision = normalized_transaction_time_precision(parsed)
        if precision != "unknown":
            seen.add(precision)
    if len(seen) > 1:
        return "mixed"
    if seen:
        return next(iter(seen))

    evidence = [
        str(value).strip().lower()
        for value in route_info.get("date_format_evidence", [])
        if str(value).strip()
    ]
    if any("ss" in value and "hh" in value for value in evidence):
        return "second"
    if any("hh" in value for value in evidence):
        return "minute"
    if evidence or date_cols:
        return "date"
    return "unknown"


# ---- 原始文件读取（统一成 list[list]） ----------------------------------------
def read_rows_excel(path, open_password=None, all_sheets_same_layout=False):
    """返回 (sheet名, rows:list[list])；按路由配置可合并同表头的多个 sheet。"""
    from ymb_standardization_core.readers.input_router import _maybe_decrypted_office_file

    with _maybe_decrypted_office_file(path, open_password=open_password) as source:
        try:
            return _read_rows_excel_source(
                source,
                all_sheets_same_layout=all_sheets_same_layout,
            )
        except ValueError as exc:
            repaired = _repair_xlsx_invalid_numeric_literals(source, exc)
            if not repaired:
                raise
            try:
                return _read_rows_excel_source(
                    repaired,
                    all_sheets_same_layout=all_sheets_same_layout,
                )
            finally:
                try:
                    os.unlink(repaired)
                except OSError:
                    pass


def _read_rows_excel_source(source, all_sheets_same_layout=False):
    """读取首个非空 sheet；显式开启时合并表头完全一致的同构 sheet。"""
    with pd.ExcelFile(source) as xl:
        populated = []
        for sheet in xl.sheet_names:
            df = xl.parse(sheet, header=None, dtype=str)
            if df.dropna(how="all").shape[0] >= 2:
                rows = df.where(pd.notnull(df), None).values.tolist()
                rows = _sanitize_nan_strings(rows)
                populated.append((sheet, rows))
        if populated:
            first_sheet, first_rows = populated[0]
            if not all_sheets_same_layout or len(populated) == 1:
                return first_sheet, first_rows

            first_header_idx, _ = find_header_row(first_rows)
            if first_header_idx is None:
                return first_sheet, first_rows
            first_signature = tuple(_norm(value) for value in first_rows[first_header_idx])
            compatible = []
            for sheet, rows in populated:
                header_idx, _ = find_header_row(rows)
                if header_idx is None:
                    continue
                signature = tuple(_norm(value) for value in rows[header_idx])
                if signature == first_signature:
                    compatible.append((sheet, rows, header_idx))
            if len(compatible) <= 1:
                return first_sheet, first_rows

            source_marker = "__原始工作表行号"
            combined = [list(row) + [None] for row in first_rows[:first_header_idx]]
            combined.append(list(first_rows[first_header_idx]) + [source_marker])
            for sheet, rows, header_idx in compatible:
                for row_number, row in enumerate(rows[header_idx + 1:], start=header_idx + 2):
                    combined.append(list(row) + [f"{sheet}!{row_number}"])
            return "；".join(sheet for sheet, _, _ in compatible), combined
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


def sniff_account_info(rows, header_idx, preamble="", preamble_mapping=None,
                       preamble_extractors=None, column_mapping=None):
    """按路由配置从表头以上元数据提取本方户名、账号和账户类型线索。"""
    info = {"本方名称": "", "本方账户": "", "账户类型线索": ""}
    # 只取表头以上的元数据；header_idx 为 0 时仅使用 reader 提供的 preamble。
    upper = rows[:header_idx] if header_idx is not None else rows[:6]
    meta = preamble + " " + " ".join(
        str(c) for r in upper for c in r if c
    )
    metadata_lines = preamble + "\n" + "\n".join(
        str(c) for r in upper for c in r if c
    )
    mappings = dict(column_mapping or {})
    mappings.update(preamble_mapping or {})
    for label, field in mappings.items():
        if field not in info:
            continue
        clean_label = str(label).strip().rstrip(":：").strip()
        if not clean_label:
            continue
        value = ""
        for row in upper:
            for index, cell in enumerate(row):
                text = str(cell or "").strip()
                match = re.match(
                    rf"^{re.escape(clean_label)}(?=\s|[:：]|$)\s*[:：]?\s*(.*)$",
                    text,
                )
                if not match:
                    continue
                value = match.group(1).strip()
                if not value:
                    value = next(
                        (str(item).strip() for item in row[index + 1:] if str(item or "").strip()),
                        "",
                    )
                if value:
                    break
            if value:
                break
        m = re.search(
            rf"(?:^|\n)\s*{re.escape(clean_label)}(?=\s|[:：]|$)\s*[:：]?\s*(.+?)"
            rf"(?=\s+[^\s:：]{{1,20}}\s*[:：]|\s*(?:\r?\n|$))",
            metadata_lines,
        )
        if m:
            value = m.group(1).strip()
        if value:
            info[field] = value
    for extractor in preamble_extractors or []:
        field = str(extractor.get("field") or "").strip()
        pattern = str(extractor.get("pattern") or "").strip()
        if field not in info or not pattern:
            continue
        match = re.search(pattern, meta)
        if not match:
            continue
        value = match.group(1).strip()
        template = str(extractor.get("template") or "").strip()
        info[field] = template.format(value=value) if template else value
    return info


def apply_conditional_mapping(row, header, standard_values, rules):
    """比较原始列与标准字段；条件成立时按 YAML 将原始列写入标准字段。"""
    raw_values = {
        _norm(name): (
            "" if row[index] is None else str(row[index]).strip()
        )
        for index, name in enumerate(header)
        if name and index < len(row)
    }
    resolved = {}
    current = dict(standard_values or {})
    for rule in rules or []:
        source, target = next(iter(rule["if"].items()))
        left = raw_values.get(_norm(source), "")
        if isinstance(target, dict):
            literal = target.get("equals")
            expected = "" if literal is None else str(literal).strip()
            if not left or _norm(left) != _norm(expected):
                continue
        elif target == "__nonzero__":
            amount = parse_amount(left)
            if amount in (None, 0):
                continue
        else:
            right = str(current.get(target) or "").strip()
            if not left or _norm(left) != _norm(right):
                continue
        for raw_field, standard_field in rule["map"].items():
            value = raw_values.get(_norm(raw_field), "")
            if value:
                resolved[standard_field] = value
                current[standard_field] = value
    return resolved


def apply_extract_mapping(row, header, rules):
    """按 YAML 正则从一个原始列提取一个标准字段。"""
    raw_values = {
        _norm(name): str(row[index] or "").strip()
        for index, name in enumerate(header)
        if name and index < len(row)
    }
    resolved = {}
    for rule in rules or []:
        source = _norm(rule.get("source"))
        pattern = str(rule.get("pattern") or "")
        value = raw_values.get(source, "")
        if not value:
            candidates = [
                (len(raw_source), raw_value)
                for raw_source, raw_value in raw_values.items()
                if source in raw_source
            ]
            if candidates:
                value = max(candidates)[1]
        if not source or not pattern or not value:
            continue
        match = re.search(pattern, value)
        if not match:
            continue
        field = str(rule.get("field") or "").strip()
        replacement = str(rule.get("replacement", r"\1"))
        extracted = re.sub(pattern, replacement, value, count=1)
        extracted = re.sub(r"\s+", " ", extracted).strip(" /|,，;；:：-")
        resolved[field] = extracted
    return resolved


_CARD_BIN_RULES = None
_CARD_BIN_BANK_NAMES = None


def _routing_config_dir():
    return os.path.join(os.path.dirname(__file__), "config", "routing")


def load_card_bin_bank_names():
    """读取银行卡 BIN 银行代码到中文标准名的映射。"""
    global _CARD_BIN_BANK_NAMES
    if _CARD_BIN_BANK_NAMES is not None:
        return _CARD_BIN_BANK_NAMES
    csv_path = os.path.join(_routing_config_dir(), "card_bin_banks.csv")
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
    routing_dir = _routing_config_dir()
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


def infer_bank_from_structured_self_banks(values, min_rows=3, min_ratio=0.8):
    """从按模板方向明确选出的本方开户行字段确认银行，不反写 Router 身份。"""
    nonempty_values = [
        str(value).strip()
        for value in values
        if str(value or "").strip()
    ]
    banks = [infer_bank(value) for value in nonempty_values]
    banks = [bank for bank in banks if bank]
    counts = Counter(banks)
    top_bank, top_count = counts.most_common(1)[0] if counts else ("", 0)
    ratio = round(top_count / len(nonempty_values), 4) if nonempty_values else 0
    recognition_ratio = (
        round(len(banks) / len(nonempty_values), 4)
        if nonempty_values
        else 0
    )
    profile = {
        "nonempty_count": len(nonempty_values),
        "candidate_count": len(banks),
        "candidate_bank_counts": dict(sorted(counts.items())),
        "candidate_ratio": ratio,
        "recognition_ratio": recognition_ratio,
        "min_rows": min_rows,
        "min_ratio": min_ratio,
    }
    if top_count >= min_rows and ratio >= min_ratio:
        return top_bank, profile
    return "", profile


LOAN_DISBURSEMENT_KEYWORDS = ["贷款放款"]
LOAN_REPAYMENT_KEYWORDS = ["贷款扣款", "贷款还款", "贷款柜面还款"]


def infer_bank_from_internal_transactions(records, min_rows=3, min_ratio=0.8):
    """从明确行内业务的结构化对手开户行推测本方银行，不影响 Router 身份。"""
    candidates = []
    evidence = []
    category_counts = defaultdict(Counter)
    for row in records:
        business_text = _norm(
            " ".join(str(row.get(field) or "") for field in ("银行备注", "账户方附言"))
        )
        is_disbursement = any(keyword in business_text for keyword in LOAN_DISBURSEMENT_KEYWORDS)
        is_repayment = any(keyword in business_text for keyword in LOAN_REPAYMENT_KEYWORDS)
        if not (is_disbursement or is_repayment):
            continue
        bank = infer_bank(row.get("对手账户", ""))
        if not bank:
            continue
        candidates.append(bank)
        category = "loan_disbursement" if is_disbursement else "loan_repayment"
        category_counts[bank][category] += 1
        evidence.append({
            "交易唯一编号": str(row.get("交易唯一编号") or ""),
            "业务文本": business_text,
            "候选银行": bank,
            "业务类型": category,
        })

    counts = Counter(candidates)
    top_bank, top_count = counts.most_common(1)[0] if counts else ("", 0)
    ratio = round(top_count / len(candidates), 4) if candidates else 0
    profile = {
        "candidate_count": len(candidates),
        "candidate_bank_counts": dict(sorted(counts.items())),
        "candidate_ratio": ratio,
        "candidate_business_counts": dict(category_counts.get(top_bank, {})),
        "min_rows": min_rows,
        "min_ratio": min_ratio,
        "evidence_transaction_ids": [
            item["交易唯一编号"] for item in evidence if item["候选银行"] == top_bank
        ][:20],
        "evidence": [item for item in evidence if item["候选银行"] == top_bank][:20],
    }
    top_categories = category_counts.get(top_bank, {})
    has_lifecycle = (
        top_categories.get("loan_disbursement", 0) >= 1
        and top_categories.get("loan_repayment", 0) >= 1
    )
    profile["has_loan_lifecycle"] = has_lifecycle
    if top_count >= min_rows and ratio >= min_ratio and has_lifecycle:
        return top_bank, profile
    return "", profile


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


def complete_unique_file_identity(records):
    """同一文件只有一个明确本方账号时，用其补齐该文件的未知账号与空户名。"""
    accounts = {
        str(row.get("本方账户") or "").strip()
        for row in records
        if str(row.get("本方账户") or "").strip()
        and not str(row.get("本方账户") or "").startswith("未识别账户#")
    }
    if len(accounts) != 1:
        return False
    account = next(iter(accounts))
    names = {
        str(row.get("本方名称") or "").strip()
        for row in records
        if str(row.get("本方账户") or "").strip() == account
        and str(row.get("本方名称") or "").strip()
    }
    if not names:
        account_digits = "".join(char for char in account if char.isdigit())
        names = {
            str(row.get("对手名称") or "").strip()
            for row in records
            if account_digits
            and "".join(char for char in str(row.get("对手账户") or "") if char.isdigit()) == account_digits
            and str(row.get("对手名称") or "").strip()
        }
    name = next(iter(names)) if len(names) == 1 else ""
    changed = False
    for row in records:
        row_account = str(row.get("本方账户") or "").strip()
        if not row_account or row_account.startswith("未识别账户#"):
            row["本方账户"] = account
            changed = True
        if name and not str(row.get("本方名称") or "").strip():
            row["本方名称"] = name
            changed = True
    return changed


def refresh_unique_ids(records):
    """本方身份补齐后重算内容指纹，保证跨文件去重仍使用最终标准字段。"""
    seen = Counter()
    for row in records:
        fingerprint = build_fingerprint(
            row.get("本方名称"), row.get("本方账户"), row.get("交易时间"),
            row.get("对手名称"), row.get("对手账户"), row.get("收入金额"),
            row.get("支出金额"), row.get("账户余额"),
        )
        occurrence = seen[fingerprint]
        seen[fingerprint] += 1
        row["交易唯一编号"] = build_unique_id(fingerprint, occurrence)


def is_standardized_input_name(path):
    """文件名为 <stem>__standardized.csv 时，直接视为已完成阶段一标准化。"""
    return os.path.basename(path).lower().endswith("__standardized.csv")


def _standardized_output_stem(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    return stem[:-len("__standardized")] if stem.endswith("__standardized") else stem


def adopt_standardized_input(path, out_dir=None, write_mapping=True):
    """接收已完成阶段一标准化的 CSV；mapping 仅在调用方需要审计报告时生成。"""
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
    if write_mapping:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    else:
        json_path = ""
    return csv_path, json_path, report


def standardize(path, out_dir=None, bank=None,
                account_type=None, header_row=None, overrides=None, write_mapping=True,
                strict_yaml_route=True):
    fname = os.path.basename(path)
    stem = os.path.splitext(fname)[0]
    manual_account_type = account_type
    if is_standardized_input_name(path):
        return adopt_standardized_input(path, out_dir=out_dir, write_mapping=write_mapping)

    file_kind, preamble, rows, route_info = read_rows(path)
    route_info = dict(route_info or {})

    if route_info.get("decision") == "matched_incomplete":
        missing = [
            str(value).strip()
            for value in route_info.get("missing_required_columns") or []
            if str(value).strip()
        ]
        hints = [
            str(value).strip()
            for value in route_info.get("missing_hints") or []
            if str(value).strip()
        ]
        bank_name = str(route_info.get("bank") or "已识别银行").strip()
        account_type_name = str(route_info.get("account_type") or "").strip()
        fingerprint_id = str(route_info.get("fingerprint_id") or "").strip()
        identity = "".join(part for part in (bank_name, account_type_name) if part)
        detail = "、".join(missing) or "未声明列"
        hint = "；".join(hints) or f"请重新导出流水，并勾选：{detail}"
        raise SourceFormatQualityError(
            f"已识别为{identity}流水（fingerprint={fingerprint_id}），"
            f"但原始导出缺少必需可选列：{detail}。{hint}",
            route_info=route_info,
        )

    # 空文件 / PDF 无可解析记录：区分“完全无文本”和“有文本但没有命中结构化/专属解析器”。
    if not rows or not any(any(c not in (None, "", "nan") for c in (r or [])) for r in rows):
        if file_kind == "pdf" and preamble:
            raise NotABankStatement("PDF 可抽取文本，但未识别到结构化流水表格或已支持的专属文本模板")
        raise NotABankStatement(
            "图片型/扫描件 PDF，未抽取到文本（需先 OCR 转文本）" if file_kind == "pdf"
            else "空文件或无可解析内容")

    route_decision = str(route_info.get("decision") or "").strip()
    fingerprint_id = str(
        route_info.get("fingerprint_id") or route_info.get("id") or ""
    ).strip()
    if strict_yaml_route and file_kind in {"excel", "pdf"} and (
        route_decision != "matched" or not fingerprint_id
    ):
        kind_name = "PDF" if file_kind == "pdf" else "Excel"
        if route_decision == "ambiguous":
            candidates = (
                route_info.get("candidate_fingerprints")
                or route_info.get("candidates")
                or []
            )
            candidate_ids = []
            for candidate in candidates:
                candidate_id = (
                    candidate.get("fingerprint_id") or candidate.get("id")
                    if isinstance(candidate, dict)
                    else candidate
                )
                candidate_id = str(candidate_id or "").strip()
                if candidate_id and candidate_id not in candidate_ids:
                    candidate_ids.append(candidate_id)
            detail = f"（候选：{'、'.join(candidate_ids)}）" if candidate_ids else ""
            reason = (
                f"原始{kind_name}命中多个已发布 YAML 指纹{detail}，"
                "禁止生成正式标准化产物；请收窄 YAML 规则并测试发布后重跑"
            )
        else:
            reason = (
                f"原始{kind_name}未唯一命中已发布 YAML 指纹，"
                "禁止使用通用 Reader 生成正式标准化产物；"
                "请创建或维护 YAML 草稿并测试发布后重跑"
            )
        raise YamlRouteRequiredError(reason, route_info=route_info)

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
    source_marker_index = next(
        (index for index, value in enumerate(header) if value == _norm("__原始工作表行号")),
        None,
    )

    acct = sniff_account_info(
        rows,
        header_idx,
        preamble,
        route_info.get("preamble_mapping"),
        route_info.get("preamble_extractors"),
        route_info.get("column_mapping"),
    )
    account_type, account_type_source, account_type_card_bin = resolve_account_type(
        manual_account_type,
        acct["本方账户"],
        acct["账户类型线索"],
        route_info.get("account_type", ""),
    )

    # 开户行：人工参数 > matched Router YAML > 表头前固定元数据。文件名和交易行内容均不参与。
    upper_text = " ".join(str(c) for r in rows[:header_idx + 1] for c in (r or []) if c)
    route_bank = str(route_info.get("bank") or "").strip()
    router_bank = route_bank if route_bank and route_bank not in {"未识别", "未知"} else ""
    bank_name = ""
    bank_infer_source = "未知"
    if bank:
        bank_name = infer_bank(bank) or bank
        bank_infer_source = "参数"
    elif route_info.get("decision") == "matched" and router_bank:
        bank_name = router_bank
        bank_infer_source = "router"
    if not bank_name:
        bank_name = infer_bank(preamble, upper_text)
        if bank_name:
            bank_infer_source = "metadata"

    # ---- 列 -> 标准字段 映射 ----
    overrides = overrides or {}
    route_column_mapping = {
        _norm(src): dst
        for src, dst in (route_info.get("column_mapping") or {}).items()
        if src
    }
    route_column_markers = sorted(
        (
            (source, field)
            for source, field in route_column_mapping.items()
            if field is not None and len(source) >= 8 and re.search(r"[A-Za-z]", source)
        ),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    conditional_source_columns = {
        _norm(source)
        for rule in (route_info.get("conditional_mapping") or [])
        for source in (*rule.get("if", {}).keys(), *rule.get("map", {}).keys())
    }
    extract_mapping_rules = route_info.get("extract_mapping") or []
    extract_source_columns = {
        _norm(rule.get("source"))
        for rule in extract_mapping_rules
        if str(rule.get("source") or "").strip()
    }
    derived_target_sources = {}
    for rule in extract_mapping_rules:
        source = str(rule.get("source") or "").strip()
        target = str(rule.get("field") or "").strip()
        if source and target:
            derived_target_sources.setdefault(target, []).append(source)
    for rule in route_info.get("conditional_mapping") or []:
        for source, target in (rule.get("map") or {}).items():
            source = str(source or "").strip()
            target = str(target or "").strip()
            if source and target:
                derived_target_sources.setdefault(target, []).append(source)
    col_to_field = {}      # 列索引 -> 标准字段
    field_to_cols = {}     # 标准字段 -> [列索引...]
    mapping_detail = {}    # 标准字段 -> {原始字段, 置信度, 说明}
    review_items = []      # 人工复核事项

    for idx, col in enumerate(header):
        if not col:
            continue
        normalized_col = _norm(col)
        route_matched = normalized_col in route_column_mapping
        route_field = route_column_mapping.get(normalized_col)
        if not route_matched:
            marker, route_field = next(
                (
                    (marker, field)
                    for marker, field in route_column_markers
                    if marker in normalized_col
                ),
                (None, None),
            )
            route_matched = marker is not None
        # 人工覆盖优先
        if col in overrides:
            field = overrides[col]
        elif _norm(idx) in overrides:
            field = overrides[_norm(idx)]
        elif route_matched:
            field = route_field
        elif _norm(col) in conditional_source_columns or _norm(col) in extract_source_columns:
            field = None
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
    transaction_time_precision = infer_transaction_time_precision(
        data_rows, date_cols, time_cols, route_info
    )

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
    structured_self_bank_values = []
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
            t = parse_datetime(cell(date_cols), cell(time_cols), route_info.get("date_order", ""))
        elif time_cols:
            t = parse_datetime(cell(time_cols), "", route_info.get("date_order", ""))
        else:
            t = ""

        bank_memo = cell(field_to_cols.get("银行备注", []))
        # extract_mapping 也可映射收支方向；先于金额拆分执行，避免原始方向为
        # “其他”等非标准值时丢失金额。具体业务词仍由各 reader YAML 配置。
        extract_values = apply_extract_mapping(row, header, extract_mapping_rules)
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
            direction = (extract_values.get("收支方向")
                         or cell(field_to_cols.get("收支方向", [])))
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

        conditional_values = apply_conditional_mapping(
            row,
            header,
            {"本方账户": acct["本方账户"], "本方名称": acct["本方名称"]},
            route_info.get("conditional_mapping") or [],
        )
        derived_values = {**extract_values, **conditional_values}
        structured_self_bank = str(
            derived_values.get("__本方开户行") or ""
        ).strip()
        if structured_self_bank:
            structured_self_bank_values.append(structured_self_bank)
        balance = parse_amount(cell(field_to_cols.get("账户余额", [])))
        opp_name = (derived_values["对手名称"] if "对手名称" in derived_values
                    else cell(field_to_cols.get("对手名称", [])))
        opp_acct = (derived_values["对手账户"] if "对手账户" in derived_values
                    else cell(field_to_cols.get("对手账户", [])))
        cust_memo = cell(field_to_cols.get("账户方附言", []))
        channel = cell(field_to_cols.get("交易渠道", []))
        counterparty_bank = (
            derived_values["__对手开户行"]
            if "__对手开户行" in derived_values
            else cell(field_to_cols.get("__对手开户行", []))
        )

        # 默认仅丢弃全空噪声；分段导出 Excel 可由 YAML 要求交易行必须含金额或余额。
        no_monetary_value = income is None and expense is None and txn is None and balance is None
        if no_monetary_value and (
            not t
            or (
                route_info.get("require_monetary_value", False)
                and not (opp_name or bank_memo or cust_memo or channel)
            )
        ):
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
        row_self_acct = (derived_values["本方账户"] if "本方账户" in derived_values
                         else cell(field_to_cols.get("本方账户", [])))
        row_self_name = (derived_values["本方名称"] if "本方名称" in derived_values
                         else cell(field_to_cols.get("本方名称", [])))
        # 抬头和数据列都没有账号时，才在行级写入文件隔离占位账号，避免跨文件错误合并。
        self_acct = row_self_acct or acct["本方账户"] or f"未识别账户#{stem}"
        cust = row_self_name or acct["本方名称"]

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
            "来源行号": (
                str(row[source_marker_index]).strip()
                if source_marker_index is not None
                and source_marker_index < len(row)
                and str(row[source_marker_index] or "").strip()
                else raw_offset + ri
            ),
            "__对手开户行": counterparty_bank,
        })

    if complete_unique_file_identity(std_records):
        refresh_unique_ids(std_records)

    # ---- 整理行序，使余额连续性最佳（保留原始对账口径） ----
    # 块重排/按余额链重建、保留同时刻多笔的相对顺序、来源行号不变可追溯。使输出为可对账的正序，
    # 让下游严格按「文件内原始顺序」做余额校验，避免按时间排序打乱同秒/同日多笔产生伪断点。
    source_order = str(route_info.get("source_order") or "").strip()
    if source_order == "descending":
        _order = list(range(len(std_records) - 1, -1, -1))
        order_strategy = "YAML配置：整体翻转"
    elif source_order == "ascending":
        _order = list(range(len(std_records)))
        order_strategy = "YAML配置：保持原序"
    else:
        _order, order_strategy = best_continuity_order(_rows_from_records(std_records))
    std_records = [std_records[i] for i in _order]

    record_account = infer_account_from_records(std_records)
    if record_account.get("本方账户"):
        acct["本方账户"] = record_account["本方账户"]
    if record_account.get("本方名称"):
        acct["本方名称"] = record_account["本方名称"]
    counterparty_account_type, counterparty_account_type_stats = infer_account_type_from_counterparties(std_records)
    structured_bank, structured_self_bank_profile = infer_bank_from_structured_self_banks(
        structured_self_bank_values
    )
    internal_bank, internal_transaction_profile = infer_bank_from_internal_transactions(std_records)
    inferred_bank = structured_bank or internal_bank
    inferred_source = (
        "structured_self_bank"
        if structured_bank
        else "internal_transaction_profile" if internal_bank else "未知"
    )
    bank_conflict = bool(
        inferred_bank and bank_name and _norm(inferred_bank) != _norm(bank_name)
    ) or bool(
        structured_bank
        and internal_bank
        and _norm(structured_bank) != _norm(internal_bank)
    )
    if inferred_bank and not bank_name:
        bank_name = inferred_bank
        bank_infer_source = inferred_source
    elif bank_conflict:
        review_items.append({
            "字段": "开户行",
            "问题": "Router/元数据银行与行内业务画像冲突",
            "证据": {"确认银行": bank_name, "画像银行": inferred_bank},
            "建议动作": "人工核对本方银行和对手开户行字段",
        })
    account_type, account_type_source, account_type_card_bin = resolve_account_type(
        manual_account_type,
        acct["本方账户"],
        acct["账户类型线索"],
        route_info.get("account_type", ""),
        counterparty_account_type,
    )
    card_bin_bank_name = bank_name_from_card_bin(account_type_card_bin)
    if card_bin_bank_name and (not bank_name or bank_infer_source == "未知"):
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
        elif derived_target_sources.get(field):
            mapping_detail[field] = {
                "原始字段": "+".join(dict.fromkeys(derived_target_sources[field])),
                "置信度": 0.9,
                "说明": "由复合列按路由 YAML 拆分",
            }
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
    if not (field_to_cols.get("对手名称") or derived_target_sources.get("对手名称")):
        review_items.append({"字段": "对手名称", "问题": "未识别到对手名称列，可能嵌在备注中",
                             "证据": f"表头={header}", "建议动作": "人工确认对手名称来源"})
    if not field_to_cols.get("账户余额"):
        review_items.append({"字段": "账户余额", "问题": "无余额列，无法做余额连续性校验",
                             "证据": f"表头={header}", "建议动作": "确认是否需要余额校验"})
    if account_type == "未知":
        review_items.append({"字段": "账户类型", "问题": "对公/个人无法从元数据确认",
                             "证据": acct, "建议动作": "人工指定账户类型"})

    overall_conf = round(min(0.95, 0.5 + 0.1 * len(
        [f for f in ("交易时间", "账户余额", "对手名称")
         if field_to_cols.get(f) or derived_target_sources.get(f)
         or (f == "交易时间" and (date_cols or time_cols))]
    ) + (0.15 if amount_mode != "未知" else 0)), 2)

    report = {
        "文件画像": {
            "确认银行": bank_name or "未知",
            "开户行": bank_name,
            "开户行识别来源": bank_infer_source,
            "router_bank": router_bank or "未识别",
            "inferred_bank": inferred_bank,
            "bank_status": (
                "conflict" if bank_conflict
                else "inferred" if bank_infer_source == "internal_transaction_profile"
                else "confirmed" if bank_name else "unknown"
            ),
            "bank_source": bank_infer_source,
            "bank_conflict": bank_conflict,
            "internal_transaction_profile": internal_transaction_profile,
            "structured_self_bank_profile": structured_self_bank_profile,
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
            "series_family": route_info.get("series_family", ""),
            "transaction_time_precision": transaction_time_precision,
            "date_format_evidence": route_info.get("date_format_evidence", []),
            "date_order": route_info.get("date_order", ""),
            "file_type": route_info.get("file_type", file_kind),
            "bank": route_info.get("bank", ""),
            "account_type": route_info.get("account_type", ""),
            "column_mapping": route_info.get("column_mapping", {}),
            "conditional_mapping": route_info.get("conditional_mapping", []),
            "extract_mapping": route_info.get("extract_mapping", []),
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
    stem, source_ext = os.path.splitext(fname)
    artifact_stem = f"{stem}__{source_ext.lower().lstrip('.') or 'file'}"
    csv_path = os.path.join(out_dir, f"{artifact_stem}__standardized.csv")
    json_path = os.path.join(out_dir, f"{artifact_stem}__mapping.json")

    pd.DataFrame(std_records, columns=OUTPUT_FIELDS).to_csv(csv_path, index=False, encoding="utf-8-sig")
    if write_mapping:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    else:
        json_path = ""

    return csv_path, json_path, report


def standardize_file(context: StandardizationContext):
    """按公开上下文执行单文件标准化。"""
    if not isinstance(context, StandardizationContext):
        raise TypeError("context must be StandardizationContext")
    return standardize(
        context.path,
        out_dir=context.out_dir,
        bank=context.bank,
        account_type=context.account_type,
        header_row=context.header_row,
        overrides=dict(context.overrides),
        write_mapping=context.write_mapping,
    )


def main():
    ap = argparse.ArgumentParser(description="单文件银行流水标准化")
    ap.add_argument("file")
    ap.add_argument("--out-dir")
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
        csv_path, json_path, report = standardize_file(StandardizationContext(
            path=args.file,
            out_dir=args.out_dir,
            bank=args.bank,
            account_type=args.account_type,
            header_row=args.header_row,
            overrides=overrides,
        ))
    except SourceFormatQualityError as e:
        sys.exit(f"[QC ERROR] {os.path.basename(args.file)}：{e.reason}")
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
