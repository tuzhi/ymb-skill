from __future__ import annotations

from token_vault_service.detectors import (
    ConstantDetector,
    DetectionContext,
    HanlpPersonNameReviewer,
    IdNumberDetector,
    PersonNameDetector,
    PhoneDetector,
)


def test_constant_detector_builds_subject_spans_from_structured_columns():
    detector = ConstantDetector()

    subject = detector.detect(
        "江西省鹏达石业有限公司",
        DetectionContext(column="本方名称", mode="structured_cell"),
    )
    account = detector.detect(
        "622200",
        DetectionContext(column="本方账号", mode="structured_cell"),
    )

    assert [(span.label, span.text) for span in subject] == [
        ("subject_name", "江西省鹏达石业有限公司")
    ]
    assert [(span.label, span.text) for span in account] == [
        ("subject_account", "622200")
    ]


def test_phone_detector_detects_full_cell_and_free_text_phone_numbers():
    detector = PhoneDetector()

    full_cell = detector.detect(
        "13800138000",
        DetectionContext(column="对手账户", mode="structured_cell"),
    )
    free_text = detector.detect(
        "手机号13800138000付款",
        DetectionContext(column="账户方附言", mode="free_text"),
    )

    assert [(span.label, span.text) for span in full_cell] == [
        ("phone", "13800138000")
    ]
    assert [(span.label, span.text) for span in free_text] == [
        ("phone", "13800138000")
    ]


def test_id_number_detector_validates_checksum_before_emitting_span():
    detector = IdNumberDetector()

    valid = detector.detect(
        "证件号码11010519491231002X",
        DetectionContext(column="账户方附言", mode="free_text"),
    )
    invalid = detector.detect(
        "证件号码110105194912310021",
        DetectionContext(column="账户方附言", mode="free_text"),
    )

    assert [(span.label, span.text) for span in valid] == [
        ("id_number", "11010519491231002X")
    ]
    assert invalid == []


def test_person_name_detector_prefilters_and_can_use_hanlp_reviewer():
    class FakeHanlp:
        def __call__(self, inputs):
            if inputs == ["李四"]:
                return [{"ner": [("李四", "PERSON", 0, 2)]}]
            if inputs == ["张三"]:
                return [{"ner": []}]
            raise AssertionError(inputs)

    detector = PersonNameDetector(reviewer=HanlpPersonNameReviewer(FakeHanlp()))

    accepted = detector.detect(
        "李四",
        DetectionContext(column="对手名称", mode="structured_cell"),
    )
    rejected_by_hanlp = detector.detect(
        "张三",
        DetectionContext(column="对手名称", mode="structured_cell"),
    )
    rejected_by_rule = detector.detect(
        "广东坚朗建材销售",
        DetectionContext(column="对手名称", mode="structured_cell"),
    )

    assert [(span.label, span.text) for span in accepted] == [
        ("counterparty_person", "李四")
    ]
    assert rejected_by_hanlp == []
    assert rejected_by_rule == []


def test_person_name_detector_finds_name_inside_counterparty_account():
    detector = PersonNameDetector()

    spans = detector.detect(
        "6217002020095156865/陈荣武",
        DetectionContext(column="对手账户", mode="structured_cell"),
    )

    assert [(span.label, span.text, span.start, span.end) for span in spans] == [
        ("counterparty_person", "陈荣武", 20, 23)
    ]


def test_person_name_detector_recognizes_uncommon_surnames():
    detector = PersonNameDetector()

    assert [
        (span.label, span.text)
        for span in detector.detect(
            "帅天梓",
            DetectionContext(column="对手名称", mode="structured_cell"),
        )
    ] == [("counterparty_person", "帅天梓")]
    assert [
        (span.label, span.text)
        for span in detector.detect(
            "占茶花",
            DetectionContext(column="对手名称", mode="structured_cell"),
        )
    ] == [("counterparty_person", "占茶花")]
