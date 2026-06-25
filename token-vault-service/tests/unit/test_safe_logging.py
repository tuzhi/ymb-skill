from __future__ import annotations

import json
from pathlib import Path

from token_vault_service.safe_logging import SafeAuditLogger


def test_audit_log_contains_only_metadata(tmp_path: Path):
    log_path = tmp_path / "audit.jsonl"
    logger = SafeAuditLogger(log_path)

    logger.log_event(
        request_id="req-1",
        input_chars=37,
        output_chars=51,
        latency_ms=12,
        span_count=2,
        by_label={"private_person": 1, "private_date": 1},
        ok=True,
        status_code=200,
        error_type=None,
    )

    raw = log_path.read_text(encoding="utf-8")
    event = json.loads(raw)

    assert set(event) == {
        "timestamp",
        "request_id",
        "input_chars",
        "output_chars",
        "latency_ms",
        "span_count",
        "by_label",
        "ok",
        "status_code",
        "error_type",
    }
    assert event["request_id"] == "req-1"
    assert event["input_chars"] == 37
    assert event["by_label"] == {"private_person": 1, "private_date": 1}
    for forbidden in (
        "Alice",
        "<PRIVATE_PERSON>",
        "token_vault",
        "original",
        "tokenized_text",
        "text",
        "start",
        "end",
        "start_char",
        "end_char",
        "span_text",
        "span_position",
    ):
        assert forbidden not in raw


def test_audit_log_creates_nested_directory_appends_and_writes_parseable_jsonl(tmp_path: Path):
    log_path = tmp_path / "nested" / "audit.jsonl"
    logger = SafeAuditLogger(log_path)

    logger.log_event(
        request_id="req-1",
        input_chars=37,
        output_chars=51,
        latency_ms=12,
        span_count=2,
        by_label={"private_person": 1, "private_date": 1},
        ok=True,
        status_code=200,
        error_type=None,
    )
    logger.log_event(
        request_id="req-2",
        input_chars=20,
        output_chars=28,
        latency_ms=8,
        span_count=1,
        by_label={"private_person": 1},
        ok=True,
        status_code=200,
        error_type=None,
    )

    lines = log_path.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 2
    events = [json.loads(line) for line in lines]
    assert [event["request_id"] for event in events] == ["req-1", "req-2"]


def test_stage_log_keeps_only_safe_detail_metadata(tmp_path: Path):
    log_path = tmp_path / "audit.jsonl"
    logger = SafeAuditLogger(log_path)

    logger.log_stage(
        request_id="req-stage",
        stage="standardization",
        latency_ms=30,
        ok=True,
        detail={
            "input_size": 1024,
            "standardized_rows": 306,
            "filename": "张三流水.pdf",
            "text": "张三",
            "token_vault": {"张某001": {"original": "张三"}},
            "output_path": "D:/tmp/张三.csv",
        },
    )

    raw = log_path.read_text(encoding="utf-8")
    event = json.loads(raw)

    assert event["event"] == "stage"
    assert event["stage"] == "standardization"
    assert event["detail"] == {"input_size": 1024, "standardized_rows": 306}
    for forbidden in ("张三", "filename", "text", "token_vault", "original", "output_path"):
        assert forbidden not in raw


