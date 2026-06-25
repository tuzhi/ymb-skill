from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SAFE_STAGE_DETAIL_KEYS = {
    "input_size",
    "batch_sha256",
    "file_count",
    "file_sha256",
    "standardized_size",
    "standardized_rows",
    "archive_size",
    "span_count",
    "by_label",
}


class SafeAuditLogger:
    """安全审计日志写入器。

    只允许记录请求编号、阶段、耗时、文件大小、命中类别计数和错误类型。
    禁止写入原始报文、Token 化正文、Token Vault、span 原文和文件名中的敏感信息。
    """

    def __init__(self, log_path: Path) -> None:
        self._log_path = log_path

    def log_event(
        self,
        *,
        request_id: str,
        input_chars: int,
        output_chars: int,
        latency_ms: int,
        span_count: int,
        by_label: dict[str, int],
        ok: bool,
        status_code: int,
        error_type: str | None,
    ) -> None:
        event: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": request_id,
            "input_chars": input_chars,
            "output_chars": output_chars,
            "latency_ms": latency_ms,
            "span_count": span_count,
            "by_label": dict(by_label),
            "ok": ok,
            "status_code": status_code,
            "error_type": error_type,
        }
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")

    def log_stage(
        self,
        *,
        request_id: str,
        stage: str,
        latency_ms: int,
        ok: bool,
        detail: dict[str, Any] | None = None,
        error_type: str | None = None,
    ) -> None:
        """记录核心阶段日志，只保留可审计的安全元数据。

        阶段日志用于观察“标准化”和“Token Vault 化”的耗时、规模和状态。
        detail 必须经过白名单过滤，避免误写原始文件名、原文片段、脱敏文件正文或映射文件。
        """

        safe_detail = {
            key: value
            for key, value in (detail or {}).items()
            if key in SAFE_STAGE_DETAIL_KEYS
        }
        event: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": request_id,
            "event": "stage",
            "stage": stage,
            "latency_ms": latency_ms,
            "ok": ok,
            "error_type": error_type,
            "detail": safe_detail,
        }
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


