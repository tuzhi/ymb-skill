from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Dict

from .detectors import (
    ConstantDetector,
    DetectionContext,
    HanlpPersonNameReviewer,
    IdNumberDetector,
    PersonNameDetector,
    PhoneDetector,
    Span,
)

DEFAULT_ENABLED_LABELS = [
    "subject_name",
    "subject_account",
    "counterparty_name",
    "counterparty_account",
    "counterparty_person",
    "source_file",
    "person",
    "phone",
    "id_number",
    "account",
    "email",
    "secret",
    "address",
]

# 标准化后的字段策略来自《可逆 Token 化与 Token Vault 前置脱敏方案》。
# 这里处理的是“标准化成功后的结构化文件”，不是原始 PDF/Excel 版式。
# 强制 Token 化字段按整格替换，保留字段不动，自由文本字段只替换可识别实体。
# 第一版先固化 ymb-skill 标准字段，后续再沉淀为外部配置。
STRUCTURED_COLUMN_LABELS = {
    "本方名称": "subject_name",
    "本方账户": "subject_account",
    "本方账号": "subject_account",
    "对手名称": "counterparty_name",
    "对手账户": "counterparty_account",
    "对手账号": "counterparty_account",
    "来源文件名": "source_file",
    "客户姓名": "person",
    "姓名": "person",
    "户名": "person",
    "对方户名": "counterparty_name",
    "收款人": "counterparty_name",
    "付款人": "counterparty_name",
    "手机号": "phone",
    "手机号码": "phone",
    "联系电话": "phone",
    "身份证号": "id_number",
    "证件号码": "id_number",
    "证件号": "id_number",
    "账号": "account",
    "卡号": "account",
    "银行卡号": "account",
    "交易账号": "account",
    "邮箱": "email",
    "电子邮箱": "email",
    "地址": "address",
    "联系地址": "address",
}

FREE_TEXT_COLUMNS = {
    "银行备注",
    "账户方附言",
    "摘要",
    "用途",
    "备注",
    "附言",
    "交易说明",
}

# 前端仍允许用户选择通用类别，例如“人名/账号”。
# 结构化流水里需要进一步区分本方和对手，所以这里把通用类别展开成业务角色类别。
LABEL_ALIASES = {
    "person": {"subject_name", "counterparty_name"},
    "account": {"subject_account", "counterparty_account"},
    "counterparty_name": {"counterparty_person"},
}


@dataclass
class TokenizationResult:
    """Token 化处理结果。

    文件级流程只把 Token 化文件、Token Vault 和非敏感摘要打包返回。
    `mapping` 中含原文，只能给调用方本地自持，不能写入审计日志。
    """

    mapping: dict[str, dict[str, str]]
    span_count: int
    by_label: dict[str, int]
    pages: list[dict[str, object]] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)

    def to_summary_dict(self) -> dict[str, object]:
        return {
            "span_count": self.span_count,
            "by_label": dict(self.by_label),
        }


MappingValue = Dict[str, str]
MappingDict = Dict[str, MappingValue]


class TokenVault:
    """本次任务内的可逆 Token Vault。

    Token Vault 只在内存中生成，并随响应返回给调用方自持。
    服务端不落库、不写日志；同一 `label + original` 在一次任务内必须稳定复用。
    """

    def __init__(self, mapping: MappingDict | None = None) -> None:
        self._original_to_token: dict[tuple[str, str], str] = {}
        self._mapping: dict[str, dict[str, str]] = {}
        self._counters: Counter[str] = Counter()
        if mapping:
            self._load_mapping(mapping)

    @property
    def mapping(self) -> dict[str, dict[str, str]]:
        return {token: dict(value) for token, value in self._mapping.items()}

    def export(self) -> dict[str, dict[str, str]]:
        return self.mapping

    def get_or_create(
        self,
        label: str,
        original: str,
        *,
        source_column: str | None = None,
    ) -> str:
        key = (label, original)
        existing = self._original_to_token.get(key)
        if existing is not None:
            return existing

        self._counters[label] += 1
        token = self._build_token(label, original, self._counters[label])
        self._original_to_token[key] = token
        self._mapping[token] = {"label": label, "original": original}
        if source_column:
            self._mapping[token]["source_column"] = source_column
        return token

    def known_spans(self, text: str, enabled_labels: set[str]) -> list[Span]:
        """在自由文本中复用已有映射。

        标准化字段会先建立 Vault，再处理备注/附言等自由文本。
        因此“对手名称”如果出现在后续或前序附言里，也应替换为同一个 token。
        """

        spans: list[Span] = []
        for (label, original), _token in self._original_to_token.items():
            if label not in enabled_labels or not original:
                continue
            start = 0
            while True:
                index = text.find(original, start)
                if index < 0:
                    break
                spans.append(
                    Span(
                        label=label,
                        start=index,
                        end=index + len(original),
                        text=original,
                        confidence=1.0,
                        source="mapping",
                        rule_id="known_mapping",
                    )
                )
                start = index + len(original)
        return spans

    @staticmethod
    def _build_token(label: str, original: str, index: int) -> str:
        suffix = f"{index:03d}"
        business_prefixes = {
            "subject_name": "主体",
            "subject_account": "本方账号",
            "counterparty_name": "对手",
            "counterparty_account": "对手账号",
            "counterparty_person": "对手人名",
            "source_file": "来源文件",
            "transaction_id": "交易编号",
        }
        if label in business_prefixes:
            return f"{business_prefixes[label]}{suffix}"
        if label == "person":
            if re.fullmatch(r"[\u4e00-\u9fff]{2,6}", original):
                return f"{original[0]}某{suffix}"
            return f"人名{suffix}"
        prefixes = {
            "phone": "手机号",
            "id_number": "身份证",
            "account": "账号",
            "email": "邮箱",
            "address": "地址",
            "date": "日期",
            "url": "URL",
            "secret": "密钥",
        }
        return f"{prefixes.get(label, label)}{suffix}"

    def _load_mapping(self, mapping: MappingDict) -> None:
        for token, value in mapping.items():
            if not isinstance(token, str) or not isinstance(value, dict):
                continue
            label = value.get("label")
            original = value.get("original")
            if not isinstance(label, str) or not isinstance(original, str):
                continue
            item = {"label": label, "original": original}
            source_column = value.get("source_column")
            if isinstance(source_column, str):
                item["source_column"] = source_column
            self._mapping[token] = item
            self._original_to_token[(label, original)] = token
            self._counters[label] = max(self._counters[label], _token_index(token))


MappingTokenStore = TokenVault


class RuleDetector:
    """规则兜底检测器。

    主要用于自由文本字段中的强模式信息，例如手机号、身份证号、账号、邮箱、密钥。
    标准化后的结构化字段优先按列策略处理，不依赖这里猜测。
    """

    def __init__(self, extra_detector: Callable[[str], Iterable[Span]] | None = None) -> None:
        self._extra_detector = extra_detector
        self._phone_detector = PhoneDetector()
        self._id_number_detector = IdNumberDetector()
        self._person_name_detector = PersonNameDetector()

    def detect(self, text: str) -> list[Span]:
        spans: list[Span] = []
        spans.extend(self._detect_context_people(text))
        spans.extend(self._person_name_detector.detect_continuous_text(text))
        context = DetectionContext(mode="free_text")
        spans.extend(self._phone_detector.detect(text, context))
        spans.extend(self._id_number_detector.detect(text, context))
        spans.extend(self._detect_context_addresses(text))
        spans.extend(
            self._detect_regex(
                text,
                "email",
                r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z0-9._%+-])",
            )
        )
        spans.extend(self._detect_context_accounts(text))
        spans.extend(self._detect_secrets(text))
        if self._extra_detector is not None:
            spans.extend(self._extra_detector(text))
        return merge_spans(spans)

    @staticmethod
    def _detect_regex(text: str, label: str, pattern: str) -> list[Span]:
        return [
            Span(label=label, start=match.start(), end=match.end(), text=match.group(0))
            for match in re.finditer(pattern, text)
        ]

    @staticmethod
    def _detect_context_people(text: str) -> list[Span]:
        pattern = re.compile(
            r"(?:客户姓名|姓名|户名|对方户名|收款人|付款人|交易对手)\s*[:：]\s*"
            r"([\u4e00-\u9fff]{2,6})"
        )
        return [
            Span(
                label="person",
                start=match.start(1),
                end=match.end(1),
                text=match.group(1),
                rule_id="cn_person_after_label",
            )
            for match in pattern.finditer(text)
        ]

    @staticmethod
    def _detect_context_accounts(text: str) -> list[Span]:
        pattern = re.compile(
            r"(?:账号|卡号|银行卡号|交易账号|对方账号|本方账号)\s*[:：]?\s*([0-9 ]{12,32})"
        )
        spans: list[Span] = []
        for match in pattern.finditer(text):
            value = match.group(1).strip()
            normalized = value.replace(" ", "")
            if 12 <= len(normalized) <= 32 and normalized.isdigit():
                start = match.start(1) + len(match.group(1)) - len(match.group(1).lstrip())
                spans.append(
                    Span(
                        label="account",
                        start=start,
                        end=start + len(value),
                        text=value,
                        rule_id="account_after_label",
                    )
                )
        return spans

    @staticmethod
    def _detect_context_addresses(text: str) -> list[Span]:
        context_pattern = re.compile(
            r"(?:联系地址|居住地址|通讯地址|住址|地址)\s*[:：]?\s*"
            r"([^\s,，;；。]{6,80}(?:省|市|县|区|乡|镇|街道|路|号|小区|村|组|栋|单元|室)[^\s,，;；。]{0,40})"
        )
        spans = [
            Span(
                label="address",
                start=match.start(1),
                end=match.end(1),
                text=match.group(1),
                rule_id="address_after_label",
            )
            for match in context_pattern.finditer(text)
        ]
        spans.extend(RuleDetector._detect_anchor_addresses(text))
        return merge_spans(spans)

    @staticmethod
    def _detect_anchor_addresses(text: str) -> list[Span]:
        pattern = re.compile(
            r"[\u4e00-\u9fff0-9A-Za-z]{2,120}?"
            r"(?:省|市|县|区)"
            r"[\u4e00-\u9fff0-9A-Za-z]{0,80}?"
            r"(?:省|市|县|区|乡|镇|街道|路|号|小区|村|组|栋|单元|室)"
            r"[\u4e00-\u9fff0-9A-Za-z]{0,40}"
        )
        spans: list[Span] = []
        for match in pattern.finditer(text):
            value = _trim_address_candidate(match.group(0))
            if value is None:
                continue
            start = match.start() + match.group(0).find(value)
            spans.append(
                Span(
                    label="address",
                    start=start,
                    end=start + len(value),
                    text=value,
                    confidence=0.8,
                    source="heuristic",
                    rule_id="address_region_anchors",
                )
            )
        return spans

    @staticmethod
    def _detect_secrets(text: str) -> list[Span]:
        pattern = re.compile(
            r"(?i)(?:api[_-]?key|secret|token)\s*[:=]\s*([A-Za-z0-9_\-]{16,})"
        )
        return [
            Span(
                label="secret",
                start=match.start(1),
                end=match.end(1),
                text=match.group(1),
                rule_id="secret_after_label",
            )
            for match in pattern.finditer(text)
        ]


def tokenize_pages(
    pages: Sequence[dict[str, Any]],
    *,
    enabled_labels: Sequence[str] | None = None,
    detector: RuleDetector | None = None,
    token_vault: MappingDict | None = None,
) -> TokenizationResult:
    labels = _enabled_label_set(enabled_labels)
    store = TokenVault(token_vault)
    detector = detector or RuleDetector()
    output_pages: list[dict[str, object]] = []
    counter: Counter[str] = Counter()
    span_count = 0

    for page in pages:
        page_no = page.get("page_no")
        text = str(page.get("text", ""))
        spans = store.known_spans(text, labels) + detector.detect(text)
        tokenized, used_spans = _tokenize_text_with_store(text, spans, labels, store)
        span_count += len(used_spans)
        counter.update(span.label for span in used_spans)
        output_pages.append({"page_no": page_no, "text": tokenized})

    return TokenizationResult(
        pages=output_pages,
        mapping=store.mapping,
        span_count=span_count,
        by_label=dict(counter),
    )


def tokenize_standardized_rows(
    *,
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    enabled_labels: Sequence[str] | None = None,
    detector: RuleDetector | None = None,
    person_name_detector: PersonNameDetector | None = None,
    token_vault: MappingDict | None = None,
) -> TokenizationResult:
    labels = _enabled_label_set(enabled_labels)
    store = TokenVault(token_vault)
    detector = detector or RuleDetector()
    person_name_detector = person_name_detector or PersonNameDetector()
    constant_detector = ConstantDetector()
    output_rows: list[list[str]] = []
    counter: Counter[str] = Counter()
    span_count = 0

    column_labels = [_column_label(column) for column in columns]
    # 第一遍只建立结构化字段映射，不输出行。
    # 这样即使某个对手名称先出现在早期附言里、后面才出现在“对手名称”列，
    # 第二遍处理自由文本时也能复用同一个 Token Vault 映射，避免漏脱敏。
    for row in rows:
        for index, value in enumerate(row):
            text = "" if value is None else str(value)
            column = columns[index] if index < len(columns) else ""
            label = column_labels[index] if index < len(column_labels) else None
            constant_spans = _constant_spans(text, column, labels, constant_detector)
            if constant_spans:
                for span in constant_spans:
                    store.get_or_create(span.label, span.text, source_column=column)
            elif label == "counterparty_name":
                _prime_counterparty_person_mapping(
                    text,
                    column,
                    labels,
                    store,
                    person_name_detector,
                )
            elif label == "counterparty_account":
                _prime_counterparty_account_mapping(
                    text,
                    column,
                    labels,
                    store,
                    person_name_detector,
                )
            elif label == "source_file":
                continue
            elif label and label in labels and _should_tokenize_structured_text(text):
                store.get_or_create(label, text, source_column=column)

    for row in rows:
        output_row: list[str] = []
        for index, value in enumerate(row):
            text = "" if value is None else str(value)
            column = columns[index] if index < len(columns) else ""
            label = column_labels[index] if index < len(column_labels) else None
            constant_spans = _constant_spans(text, column, labels, constant_detector)
            if constant_spans:
                span = constant_spans[0]
                token = store.get_or_create(span.label, span.text, source_column=column)
                output_row.append(token)
                counter[span.label] += 1
                span_count += 1
            elif label == "counterparty_name":
                token = _counterparty_person_token(
                    text,
                    column,
                    labels,
                    store,
                    person_name_detector,
                )
                if token is None:
                    output_row.append(text)
                    continue
                output_row.append(token)
                counter["counterparty_person"] += 1
                span_count += 1
            elif label == "counterparty_account":
                tokenized, used_spans = _tokenize_counterparty_account(
                    text,
                    column,
                    labels,
                    store,
                    person_name_detector,
                )
                if not used_spans:
                    output_row.append(text)
                else:
                    output_row.append(tokenized)
                    span_count += len(used_spans)
                    counter.update(span.label for span in used_spans)
            elif label == "source_file":
                tokenized, used_spans = _tokenize_text_with_store(
                    text,
                    store.known_spans(text, labels) + detector.detect(text),
                    labels,
                    store,
                )
                output_row.append(tokenized)
                span_count += len(used_spans)
                counter.update(span.label for span in used_spans)
            elif label and label in labels:
                if not _should_tokenize_structured_text(text):
                    output_row.append(text)
                    continue
                # 结构化敏感字段按整格替换，保持行列结构不变。
                token = store.get_or_create(label, text, source_column=column)
                output_row.append(token)
                counter[label] += 1
                span_count += 1
            elif column in FREE_TEXT_COLUMNS:
                # 自由文本不整格替换，只替换已有映射和强规则命中的敏感实体。
                # 这样保留“货款/工资/结息/还款”等业务语义，供 WorkBuddy 分析。
                spans = store.known_spans(text, labels) + detector.detect(text)
                tokenized, used_spans = _tokenize_text_with_store(text, spans, labels, store)
                output_row.append(tokenized)
                span_count += len(used_spans)
                counter.update(span.label for span in used_spans)
            else:
                output_row.append(text)
        output_rows.append(output_row)

    return TokenizationResult(
        columns=list(columns),
        rows=output_rows,
        mapping=store.mapping,
        span_count=span_count,
        by_label=dict(counter),
    )


def _prime_counterparty_person_mapping(
    text: str,
    column: str,
    labels: set[str],
    store: TokenVault,
    person_name_detector: PersonNameDetector,
) -> None:
    if "counterparty_person" not in labels or not _should_tokenize_structured_text(text):
        return
    if person_name_detector.detect(text):
        store.get_or_create("counterparty_person", text.strip(), source_column=column)


def _constant_spans(
    text: str,
    column: str,
    labels: set[str],
    detector: ConstantDetector,
) -> list[Span]:
    context = DetectionContext(column=column, mode="structured_cell")
    return [span for span in detector.detect(text, context) if span.label in labels]


def _prime_counterparty_account_mapping(
    text: str,
    column: str,
    labels: set[str],
    store: TokenVault,
    person_name_detector: PersonNameDetector,
) -> None:
    if not _should_tokenize_structured_text(text):
        return
    context = DetectionContext(column=column, mode="structured_cell")
    spans: list[Span] = []
    if "phone" in labels:
        spans.extend(PhoneDetector().detect(text, context))
    if "counterparty_person" in labels:
        spans.extend(person_name_detector.detect(text, context))
    for span in merge_spans(spans):
        if span.label in labels:
            store.get_or_create(span.label, span.text, source_column=column)


def _counterparty_person_token(
    text: str,
    column: str,
    labels: set[str],
    store: TokenVault,
    person_name_detector: PersonNameDetector,
) -> str | None:
    if "counterparty_person" not in labels or not _should_tokenize_structured_text(text):
        return None
    value = text.strip()
    if not person_name_detector.detect(value):
        return None
    return store.get_or_create("counterparty_person", value, source_column=column)


def _tokenize_counterparty_account(
    text: str,
    column: str,
    labels: set[str],
    store: TokenVault,
    person_name_detector: PersonNameDetector,
) -> tuple[str, list[Span]]:
    if not _should_tokenize_structured_text(text):
        return text, []
    context = DetectionContext(column=column, mode="structured_cell")
    spans: list[Span] = store.known_spans(text, labels)
    if "phone" in labels:
        spans.extend(PhoneDetector().detect(text, context))
    if "counterparty_person" in labels:
        spans.extend(person_name_detector.detect(text, context))
    return _tokenize_text_with_store(text, spans, labels, store)


def detokenize_text(text: str, mapping: dict[str, dict[str, str]]) -> str:
    result = text
    for token in sorted(mapping, key=len, reverse=True):
        original = mapping[token].get("original")
        if isinstance(original, str):
            result = result.replace(token, original)
    return result


def count_detokenize_replacements(text: str, mapping: dict[str, dict[str, str]]) -> int:
    return sum(text.count(token) for token in mapping)


def _tokenize_text_with_store(
    text: str,
    spans: Iterable[Span],
    labels: set[str],
    store: MappingTokenStore,
) -> tuple[str, list[Span]]:
    filtered = [
        span for span in merge_spans(spans)
        if span.label in labels and 0 <= span.start < span.end <= len(text)
    ]
    result = text
    for span in sorted(filtered, key=lambda item: item.start, reverse=True):
        token = store.get_or_create(span.label, span.text)
        result = result[: span.start] + token + result[span.end :]
    return result, filtered


def merge_spans(spans: Iterable[Span]) -> list[Span]:
    priority = {
        "subject_name": 95,
        "counterparty_name": 95,
        "id_number": 100,
        "phone": 90,
        "subject_account": 85,
        "counterparty_account": 85,
        "account": 80,
        "email": 70,
        "secret": 60,
        "person": 50,
        "source_file": 45,
        "address": 40,
        "date": 30,
        "url": 20,
    }
    ordered = sorted(
        spans,
        key=lambda span: (
            span.start,
            -(span.end - span.start),
            -priority.get(span.label, 0),
            -span.confidence,
        ),
    )
    selected: list[Span] = []
    seen: set[tuple[int, int, str]] = set()
    for span in ordered:
        key = (span.start, span.end, span.label)
        if key in seen:
            continue
        seen.add(key)
        overlap = [
            current
            for current in selected
            if not (span.end <= current.start or span.start >= current.end)
        ]
        if not overlap:
            selected.append(span)
            continue
        best = max(
            [span, *overlap],
            key=lambda item: (
                priority.get(item.label, 0),
                item.confidence,
                item.end - item.start,
            ),
        )
        if best is span:
            selected = [item for item in selected if item not in overlap]
            selected.append(span)
    return sorted(selected, key=lambda span: span.start)


def _column_label(column: str) -> str | None:
    return STRUCTURED_COLUMN_LABELS.get(column)


def _trim_address_candidate(value: str) -> str | None:
    start = _address_start_index(value)
    if start is None:
        return None
    candidate = value[start:]
    end = _address_end_index(candidate)
    if end is None:
        return None
    candidate = candidate[:end].strip()
    if len(candidate) < 6:
        return None
    anchors = sum(candidate.count(anchor) for anchor in ("省", "市", "县", "区"))
    if anchors < 2:
        return None
    return candidate


def _address_start_index(value: str) -> int | None:
    for anchor in ("省", "市", "县", "区"):
        index = value.find(anchor)
        if index < 0:
            continue
        start = max(0, index - 2)
        if start < index and re.fullmatch(r"[\u4e00-\u9fff]{2,}", value[start:index]):
            return start
    return None


def _address_end_index(value: str) -> int | None:
    endings = ("室", "号", "单元", "栋", "组", "村", "小区", "路", "街道", "镇", "乡", "区", "县", "市", "省")
    last_end = -1
    for ending in endings:
        index = value.rfind(ending)
        if index >= 0:
            last_end = max(last_end, index + len(ending))
    return last_end if last_end > 0 else None


def _enabled_label_set(enabled_labels: Sequence[str] | None) -> set[str]:
    labels = set(enabled_labels or DEFAULT_ENABLED_LABELS)
    expanded = set(labels)
    for label in labels:
        expanded.update(LABEL_ALIASES.get(label, set()))
    return expanded


def _should_tokenize_structured_text(text: str) -> bool:
    """过滤标准化脚本产生的空值或占位值。

    `--`、`未知` 等不是敏感实体，不能写进 Token Vault。
    """

    value = text.strip()
    if not value:
        return False
    return value.lower() not in {"-", "--", "无", "未知", "nan", "none", "null"}


def _token_index(token: str) -> int:
    match = re.search(r"(\d+)$", token)
    if match is None:
        return 0
    try:
        return int(match.group(1))
    except ValueError:
        return 0


