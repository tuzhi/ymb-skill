from __future__ import annotations

from token_vault_service.detectors import HanlpPersonNameReviewer, PersonNameDetector
from token_vault_service.tokenization import (
    MappingTokenStore,
    RuleDetector,
    TokenVault,
    detokenize_text,
    tokenize_pages,
    tokenize_standardized_rows,
)
from token_vault_service.vault_service import TokenVaultService


def test_rule_detector_detects_high_confidence_entities():
    spans = RuleDetector().detect(
        "客户姓名：张三，手机号13800138000，证件号码11010519491231002X，邮箱a@example.com"
    )

    by_label = {span.label: span.text for span in spans}

    assert by_label["person"] == "张三"
    assert by_label["phone"] == "13800138000"
    assert by_label["id_number"] == "11010519491231002X"
    assert by_label["email"] == "a@example.com"


def test_rule_detector_detects_context_address():
    spans = RuleDetector().detect("联系地址：湖南省长沙市岳麓区梅溪湖街道金茂小区1栋101室。")

    by_label = {span.label: span.text for span in spans}

    assert by_label["address"] == "湖南省长沙市岳麓区梅溪湖街道金茂小区1栋101室"


def test_rule_detector_detects_address_in_continuous_text_by_region_anchors():
    spans = RuleDetector().detect("张小二住湖南省长沙市岳麓区梅溪湖街道金茂小区1栋101室电话13800138000")

    by_label = {span.label: span.text for span in spans}

    assert by_label["address"] == "湖南省长沙市岳麓区梅溪湖街道金茂小区1栋101室"


def test_rule_detector_detects_leading_name_in_continuous_text():
    spans = RuleDetector().detect("张小二阿萨大大撒旦as")

    by_label = {span.label: span.text for span in spans}

    assert by_label["person"] == "张小二"


def test_rule_detector_detects_bounded_five_character_person_name():
    spans = RuleDetector().detect("联系人 张小二阿萨，手机号13800138000")

    by_label = {span.label: span.text for span in spans}

    assert by_label["person"] == "张小二阿萨"


def test_tokenize_pages_reuses_mapping_across_pages():
    result = tokenize_pages(
        [
            {"page_no": 1, "text": "户名：张三，手机号13800138000"},
            {"page_no": 2, "text": "张三再次提交材料，手机号13800138000"},
        ],
        enabled_labels=["person", "phone"],
        detector=RuleDetector(),
    )

    assert result.pages[0]["text"] == "户名：张某001，手机号手机号001"
    assert "张某001再次提交材料" in result.pages[1]["text"]
    assert "手机号001" in result.pages[1]["text"]
    assert result.mapping["张某001"] == {"label": "person", "original": "张三"}
    assert result.mapping["手机号001"] == {
        "label": "phone",
        "original": "13800138000",
    }


def test_tokenize_standardized_rows_uses_columns_and_summary_text():
    result = tokenize_standardized_rows(
        columns=["客户姓名", "手机号", "交易金额", "摘要"],
        rows=[["张三", "13800138000", "1000.00", "张三还款"]],
        enabled_labels=["person", "phone"],
        detector=RuleDetector(),
    )

    assert result.rows == [["张某001", "手机号001", "1000.00", "张某001还款"]]
    assert result.mapping["张某001"]["original"] == "张三"
    assert result.mapping["手机号001"]["original"] == "13800138000"


def test_tokenize_standardized_rows_uses_business_role_labels():
    result = tokenize_standardized_rows(
        columns=[
            "交易时间",
            "本方名称",
            "本方账户",
            "对手名称",
            "对手账户",
            "交易金额",
            "银行备注",
            "账户方附言",
            "来源文件名",
        ],
        rows=[
            [
                "2026-05-27 15:28:47",
                "张三",
                "6217000000000000000",
                "李四",
                "--",
                "1000.00",
                "货款",
                "李四货款转入张三",
                "张三流水.pdf",
            ]
        ],
        detector=RuleDetector(),
    )

    assert result.rows == [
        [
            "2026-05-27 15:28:47",
            "主体001",
            "本方账号001",
            "对手人名001",
            "--",
            "1000.00",
            "货款",
            "对手人名001货款转入主体001",
            "主体001流水.pdf",
        ]
    ]
    assert result.mapping["主体001"] == {
        "label": "subject_name",
        "original": "张三",
        "source_column": "本方名称",
    }
    assert result.mapping["本方账号001"]["source_column"] == "本方账户"
    assert result.mapping["对手人名001"]["label"] == "counterparty_person"
    assert all(value["original"] != "--" for value in result.mapping.values())
    assert "对手账号001" not in result.mapping


def test_counterparty_company_name_is_not_tokenized_as_person():
    result = tokenize_standardized_rows(
        columns=["对手名称", "账户方附言"],
        rows=[
            ["其他对手", "广东坚朗建材销售有限公司货款"],
            ["广东坚朗建材销售", "货款"],
        ],
        detector=RuleDetector(),
    )

    assert result.rows == [
        ["其他对手", "广东坚朗建材销售有限公司货款"],
        ["广东坚朗建材销售", "货款"],
    ]
    assert result.mapping == {}


def test_counterparty_phone_account_is_tokenized_as_phone():
    result = tokenize_standardized_rows(
        columns=["对手账户", "账户方附言"],
        rows=[["13800138000", "手机号13800138000付款"]],
        detector=RuleDetector(),
    )

    assert result.rows == [["手机号001", "手机号手机号001付款"]]
    assert result.mapping["手机号001"] == {
        "label": "phone",
        "original": "13800138000",
        "source_column": "对手账户",
    }


def test_counterparty_account_partially_tokenizes_person_name():
    result = tokenize_standardized_rows(
        columns=["对手账户", "账户方附言"],
        rows=[["6217002020095156865/陈荣武", "陈荣武付款"]],
        detector=RuleDetector(),
    )

    assert result.rows == [["6217002020095156865/对手人名001", "对手人名001付款"]]
    assert result.mapping["对手人名001"] == {
        "label": "counterparty_person",
        "original": "陈荣武",
        "source_column": "对手账户",
    }


def test_source_filename_partially_reuses_subject_and_phone_tokens():
    result = tokenize_standardized_rows(
        columns=["本方名称", "本方账号", "来源文件名"],
        rows=[["张三", "622200", "张三_招商银行_13800138000_2025流水.xlsx"]],
        detector=RuleDetector(),
    )

    assert result.rows == [["主体001", "本方账号001", "主体001_招商银行_手机号001_2025流水.xlsx"]]
    assert result.mapping["手机号001"]["original"] == "13800138000"


def test_person_name_detector_prefilters_counterparty_person_candidates():
    detector = PersonNameDetector()

    spans = detector.detect("李四")
    assert [(span.label, span.text) for span in spans] == [("counterparty_person", "李四")]
    assert detector.detect("广东坚朗建材销售") == []
    assert detector.detect("张三货款") == []


def test_counterparty_person_detector_can_reject_rule_candidate():
    class RejectAllReviewer:
        def is_person_name(self, text: str) -> bool:
            assert text == "李四"
            return False

    result = tokenize_standardized_rows(
        columns=["对手名称"],
        rows=[["李四"]],
        detector=RuleDetector(),
        person_name_detector=PersonNameDetector(RejectAllReviewer()),
    )

    assert result.rows == [["李四"]]
    assert result.mapping == {}


def test_token_vault_service_accepts_person_name_detector():
    class RejectAllReviewer:
        def is_person_name(self, text: str) -> bool:
            assert text == "李四"
            return False

    service = TokenVaultService(
        person_name_detector=PersonNameDetector(RejectAllReviewer())
    )

    result = service.tokenize_standardized(["对手名称"], [["李四"]])

    assert result.rows == [["李四"]]
    assert result.mapping == {}


def test_hanlp_person_name_reviewer_accepts_only_person_entities():
    class FakeHanlp:
        def __call__(self, inputs):
            if inputs == ["李四"]:
                return [{"ner": [("李四", "PERSON", 0, 2)]}]
            if inputs == ["广东坚朗"]:
                return [{"ner": []}]
            raise AssertionError(inputs)

    reviewer = HanlpPersonNameReviewer(FakeHanlp())

    assert reviewer.is_person_name("李四") is True
    assert reviewer.is_person_name("广东坚朗") is False


def test_disabled_label_is_not_tokenized():
    result = tokenize_pages(
        [{"page_no": 1, "text": "户名：张三，手机号13800138000"}],
        enabled_labels=["phone"],
        detector=RuleDetector(),
    )

    assert result.pages[0]["text"] == "户名：张三，手机号手机号001"
    assert "张某001" not in result.mapping


def test_detokenize_replaces_longer_tokens_first():
    text = detokenize_text(
        "张某0010 与 张某001 有交易",
        {
            "张某001": {"label": "person", "original": "张三"},
            "张某0010": {"label": "person", "original": "张三丰"},
        },
    )

    assert text == "张三丰 与 张三 有交易"


def test_mapping_token_store_does_not_persist_between_instances():
    first = MappingTokenStore()
    second = MappingTokenStore()

    assert first.get_or_create("person", "张三") == "张某001"
    assert second.get_or_create("person", "张三") == "张某001"


def test_token_vault_imports_existing_mapping_and_continues_counters():
    vault = TokenVault(
        {
            "张某001": {"label": "person", "original": "张三"},
            "手机号001": {"label": "phone", "original": "13800138000"},
        }
    )

    assert vault.get_or_create("person", "张三") == "张某001"
    assert vault.get_or_create("person", "李四") == "李某002"
    assert vault.get_or_create("phone", "13900139000") == "手机号002"
    assert vault.export()["张某001"] == {"label": "person", "original": "张三"}


def test_tokenize_pages_can_continue_from_existing_vault():
    result = tokenize_pages(
        [{"page_no": 1, "text": "户名：张三，户名：李四"}],
        enabled_labels=["person"],
        token_vault={
            "张某001": {"label": "person", "original": "张三"},
        },
        detector=RuleDetector(),
    )

    assert result.pages[0]["text"] == "户名：张某001，户名：李某002"
    assert result.mapping["张某001"]["original"] == "张三"
    assert result.mapping["李某002"]["original"] == "李四"


