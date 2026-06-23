from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from .base import DetectionContext, Span


COMMON_CN_SURNAMES = frozenset(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐"
    "费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟"
    "平黄和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋庞熊纪舒屈项"
    "祝董梁杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡"
    "田胡凌霍虞万支柯昝管卢莫经房裘缪干解应宗丁宣邓郁单杭洪包诸左"
    "石崔吉龚程邢裴陆荣翁荀羊於惠甄曲家封芮羿储靳汲邴糜松井段富巫"
    "乌焦巴弓牧隗山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘斜厉戎祖武"
    "符刘景詹束龙叶幸司韶郜黎蓟薄印宿白怀蒲台从鄂索咸籍赖卓蔺屠蒙"
    "池乔阴胥能苍双闻莘党翟谭贡劳逄姬申扶堵冉宰郦雍郤璩桑桂濮牛寿"
    "通边扈燕冀郏浦尚农温别庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈"
    "廖庾终暨居衡步都耿满弘匡国文寇广禄阙东殴殳沃利蔚越夔隆师巩厍"
    "聂晁勾敖融冷訾辛阚那简饶空曾毋沙乜养鞠须丰巢关蒯相查后荆红游"
    "竺权逯盖益桓公付"
)

# 真实流水中出现的低频姓氏补充。单独维护，避免改动百家姓主体表时难以追溯来源。
EXTRA_CN_SURNAMES = frozenset("帅占")

COMPOUND_CN_SURNAMES = (
    "欧阳",
    "太史",
    "端木",
    "上官",
    "司马",
    "东方",
    "独孤",
    "南宫",
    "万俟",
    "闻人",
    "夏侯",
    "诸葛",
    "尉迟",
    "公羊",
    "赫连",
    "澹台",
    "皇甫",
    "宗政",
    "濮阳",
    "公冶",
    "太叔",
    "申屠",
    "公孙",
    "慕容",
    "仲孙",
    "钟离",
    "长孙",
    "宇文",
    "司徒",
    "鲜于",
    "司空",
    "闾丘",
    "子车",
    "亓官",
    "司寇",
    "巫马",
    "公西",
    "颛孙",
    "壤驷",
    "公良",
    "漆雕",
    "乐正",
    "宰父",
    "谷梁",
    "拓跋",
    "夹谷",
    "轩辕",
    "令狐",
    "段干",
    "百里",
    "呼延",
    "东郭",
    "南门",
    "羊舌",
    "微生",
    "公户",
    "公玉",
    "公仪",
    "梁丘",
    "公仲",
    "公上",
    "公门",
    "公山",
    "公坚",
    "左丘",
    "公伯",
    "西门",
    "公祖",
    "第五",
    "公乘",
    "贯丘",
    "公皙",
    "南荣",
    "东里",
    "东宫",
    "仲长",
    "子书",
    "子桑",
    "即墨",
    "达奚",
    "褚师",
)

ORGANIZATION_NAME_MARKERS = (
    "公司",
    "有限",
    "集团",
    "银行",
    "支行",
    "分行",
    "个体",
    "商行",
    "经营部",
    "工作室",
    "店",
    "超市",
    "平台",
    "中心",
    "合作社",
    "委员会",
    "学校",
    "医院",
)

BUSINESS_TEXT_MARKERS = (
    "货款",
    "工资",
    "还款",
    "转账",
    "利息",
    "手续费",
    "租金",
    "报销",
    "借款",
    "贷款",
    "付款",
    "收款",
)


class PersonNameDetector:
    """一般人名候选检测器。

    第一版只在强语境字段中使用：先用姓氏、长度、组织词/业务词过滤候选，
    再可选交给 HanLP reviewer 复核，避免把企业名、商户名误识别成人名。
    """

    def __init__(self, reviewer: "PersonNameReviewer | None" = None) -> None:
        self._reviewer = reviewer

    def detect(self, text: str, context: DetectionContext | None = None) -> list[Span]:
        value = text.strip()
        if not value:
            return []
        if _looks_like_person_name(value):
            return self._review_spans(
                [
                    Span(
                        label="counterparty_person",
                        start=text.index(value),
                        end=text.index(value) + len(value),
                        text=value,
                        rule_id="counterparty_person_name",
                    )
                ]
            )
        if context is not None and context.column in {"对手账户", "对手账号"}:
            return self._review_spans(_counterparty_account_name_spans(text))
        return []

    def detect_continuous_text(self, text: str) -> list[Span]:
        spans: list[Span] = []
        for match in re.finditer(r"[\u4e00-\u9fff]{2,}", text):
            segment = match.group(0)
            candidate = _continuous_person_candidate(segment)
            if candidate is None:
                continue
            spans.append(
                Span(
                    label="person",
                    start=match.start(),
                    end=match.start() + len(candidate),
                    text=candidate,
                    confidence=0.65,
                    source="heuristic",
                    rule_id="continuous_person_name",
                )
            )
        return self._review_spans(spans)

    @staticmethod
    def is_counterparty_person_name(text: str) -> bool:
        return bool(PersonNameDetector().detect(text))

    def _review_spans(self, spans: list[Span]) -> list[Span]:
        if self._reviewer is None:
            return spans
        return [
            span for span in spans
            if self._reviewer.is_person_name(span.text)
        ]


class PersonNameReviewer:
    """人名候选复核接口。"""

    def is_person_name(self, text: str) -> bool:
        raise NotImplementedError


class HanlpPersonNameReviewer(PersonNameReviewer):
    """HanLP 人名复核包装器。

    调用方传入已加载的 HanLP callable，本类只复用模型结果，不负责模型加载。
    """

    _PERSON_LABELS = {"PERSON", "PER", "Nh", "nr", "人名"}

    def __init__(self, recognizer: Callable[[list[str]], Any] | Callable[[str], Any]) -> None:
        self._recognizer = recognizer

    def is_person_name(self, text: str) -> bool:
        result = self._recognizer([text])
        first_result = result[0] if isinstance(result, list) and result else result
        return _hanlp_result_has_person(first_result, text, self._PERSON_LABELS)


def _looks_like_person_name(text: str) -> bool:
    value = re.sub(r"\s+", "", text.strip())
    if not re.fullmatch(r"[\u4e00-\u9fff]{2,5}", value):
        return False
    if any(marker in value for marker in ORGANIZATION_NAME_MARKERS):
        return False
    if any(marker in value for marker in BUSINESS_TEXT_MARKERS):
        return False
    return _has_chinese_surname(value)


def _continuous_person_candidate(segment: str) -> str | None:
    if any(marker in segment for marker in ORGANIZATION_NAME_MARKERS):
        return None
    if any(marker in segment for marker in BUSINESS_TEXT_MARKERS):
        return None
    if 2 <= len(segment) <= 5:
        return segment if _looks_like_person_name(segment) else None
    lengths = [3, 2, 4, 5]
    if any(segment.startswith(surname) for surname in COMPOUND_CN_SURNAMES):
        lengths = [4, 3, 5, 2]
    for length in lengths:
        candidate = segment[:length]
        if _looks_like_person_name(candidate):
            return candidate
    return None


def _counterparty_account_name_spans(text: str) -> list[Span]:
    spans: list[Span] = []
    for match in re.finditer(r"[\u4e00-\u9fff]{2,5}", text):
        value = match.group(0)
        if not _looks_like_person_name(value):
            continue
        spans.append(
            Span(
                label="counterparty_person",
                start=match.start(),
                end=match.end(),
                text=value,
                rule_id="counterparty_account_person_name",
            )
        )
    return spans


def _has_chinese_surname(value: str) -> bool:
    if any(value.startswith(surname) for surname in COMPOUND_CN_SURNAMES):
        return len(value) >= 3
    return value[0] in COMMON_CN_SURNAMES or value[0] in EXTRA_CN_SURNAMES


def _hanlp_result_has_person(
    result: Any,
    expected_text: str,
    person_labels: set[str],
) -> bool:
    entities = _extract_hanlp_entities(result)
    for entity_text, entity_label in entities:
        if entity_text == expected_text and entity_label in person_labels:
            return True
    return False


def _extract_hanlp_entities(result: Any) -> list[tuple[str, str]]:
    if isinstance(result, dict):
        for key in ("ner", "ner/msra", "ner/pku", "ner/ontonotes"):
            if key in result:
                return _extract_hanlp_entities(result[key])
        return []
    if not isinstance(result, list):
        return []
    entities: list[tuple[str, str]] = []
    for item in result:
        if isinstance(item, dict):
            text = item.get("text") or item.get("span") or item.get("word")
            label = item.get("label") or item.get("type") or item.get("ner")
            if isinstance(text, str) and isinstance(label, str):
                entities.append((text, label))
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            text, label = item[0], item[1]
            if isinstance(text, str) and isinstance(label, str):
                entities.append((text, label))
    return entities
