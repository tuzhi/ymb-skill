from ymb_standardization_core.readers.pdf.text_lines import _extract_pdf_text_table_rows


def test_regex_records_append_wrapped_counterparty():
    config = {
        "captures": {"交易时间": "trade_time", "对方户名/账号": "counterparty"},
        "record_patterns": [
            r"^卡\s+(?P<voucher_no>\d+)\s+(?P<trade_time>20\d{2}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2})\s+(?P<summary>.*?)\s+(?P<amount>[+-]?\d[\d,]*\.\d{2})\s+(?P<balance>[+-]?\d[\d,]*\.\d{2})\s+(?P<counterparty>.*)$",
        ],
        "continuation_patterns": [{
            "pattern": r"^(?P<voucher_suffix>\d{4,})(?:\s+(?P<counterparty_suffix>.*))?$",
            "append": {
                "对方户名/账号": "counterparty_suffix",
            },
        }],
    }
    rows = _extract_pdf_text_table_rows(
        "\n".join([
            "个人账户对账单",
            "卡 6216917800 2025/05/31 10:31:30 快捷支付 -24.00 33.32 浙江长拓自动化有限公司",
            "007827 /2088821492945482",
        ]),
        config,
    )

    assert rows[1] == [
        "2025/05/31 10:31:30",
        "浙江长拓自动化有限公司 /2088821492945482",
    ]


def test_pipe_records_merge_wrapped_cells_without_inserting_spaces():
    config = {
        "captures": {"序号": "sequence", "用途": "details", "备注": "notes"},
        "record_patterns": [
            r"^\|\s*(?P<sequence>\d+)\s*\|\s*(?P<details>[^|]*)\|\s*(?P<notes>[^|]*)\|$",
        ],
        "continuation_patterns": [{
            "pattern": r"^\|\s*\|\s*(?P<details>[^|]*)\|\s*(?P<notes>[^|]*)\|$",
            "append": {"用途": "details", "备注": "notes"},
            "joiner": "",
        }],
    }
    rows = _extract_pdf_text_table_rows(
        "| 2 |购买 |中国农业银行股 |\n| |纺织线 |份有限公司 |",
        config,
    )

    assert rows[1] == ["2", "购买纺织线", "中国农业银行股份有限公司"]
