from __future__ import annotations

import hashlib
import json
import logging
import shutil
import tempfile
import zipfile
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter
from typing import Any, Iterator
from uuid import uuid4

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

from .config import Settings, get_settings
from .file_tokenizer import StandardizedFileTokenizer
from .safe_logging import SafeAuditLogger
from .schemas import (
    DetokenizeRequest,
    DetokenizeResponse,
    ErrorResponse,
    RuntimeResponse,
    TokenizeStandardizedRequest,
    TokenizeStandardizedResponse,
    TokenizeTextRequest,
    TokenizeTextResponse,
)
from .standardization_adapter import BundledStandardizationAdapter
from .tokenization import count_detokenize_replacements
from .vault_cache import TokenVaultCache
from .vault_service import TokenVaultService


logger = logging.getLogger("token_vault_service")
if not logger.handlers:
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logger.addHandler(console_handler)
logger.setLevel(logging.INFO)
logger.propagate = False
STRATEGY_VERSION = "v1"


def create_app(
    *,
    settings: Settings | None = None,
    token_service: TokenVaultService | None = None,
    standardizer: Any | None = None,
    vault_cache: TokenVaultCache | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    resolved_token_service = token_service or TokenVaultService()
    resolved_standardizer = standardizer or BundledStandardizationAdapter()
    resolved_vault_cache = vault_cache or TokenVaultCache(
        db_path=resolved_settings.vault_cache_path,
        max_size=resolved_settings.vault_cache_size,
    )
    file_tokenizer = StandardizedFileTokenizer(resolved_token_service)
    audit_logger = SafeAuditLogger(resolved_settings.log_path)

    app = FastAPI()

    def log_event(
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
        audit_logger.log_event(
            request_id=request_id,
            input_chars=input_chars,
            output_chars=output_chars,
            latency_ms=latency_ms,
            span_count=span_count,
            by_label=by_label,
            ok=ok,
            status_code=status_code,
            error_type=error_type,
        )

    def log_stage(
        *,
        request_id: str,
        stage: str,
        stage_started_at: float,
        ok: bool,
        detail: dict[str, Any] | None = None,
        error_type: str | None = None,
    ) -> None:
        audit_logger.log_stage(
            request_id=request_id,
            stage=stage,
            latency_ms=int((perf_counter() - stage_started_at) * 1000),
            ok=ok,
            detail=detail,
            error_type=error_type,
        )

    def error_response(
        *,
        request_id: str,
        error: str,
        status_code: int,
        input_chars: int,
        started_at: float,
    ) -> JSONResponse:
        latency_ms = int((perf_counter() - started_at) * 1000)
        log_event(
            request_id=request_id,
            input_chars=input_chars,
            output_chars=0,
            latency_ms=latency_ms,
            span_count=0,
            by_label={},
            ok=False,
            status_code=status_code,
            error_type=error,
        )
        payload = ErrorResponse(request_id=request_id, error=error)
        return JSONResponse(status_code=status_code, content=payload.model_dump())

    @app.exception_handler(RequestValidationError)
    def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        del request, exc
        return error_response(
            request_id=str(uuid4()),
            error="invalid_request",
            status_code=422,
            input_chars=0,
            started_at=perf_counter(),
        )

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(Path(__file__).parent / "static" / "index.html")

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/runtime", response_model=RuntimeResponse)
    def runtime() -> RuntimeResponse:
        return RuntimeResponse(
            ok=True,
            max_chars=resolved_settings.max_chars,
            standardization_module=resolved_settings.standardization_module,
        )

    async def _tokenize_upload(
        *,
        file: UploadFile,
        work_dir: Path,
        labels: list[str] | None,
        vault: dict[str, dict[str, str]] | None,
        request_id: str,
        started_at: float,
        include_vault_ref: bool = True,
        cache_result: bool = True,
    ) -> tuple[Any, int, int, str] | JSONResponse:
        input_path = work_dir / _safe_upload_name(file.filename or "input")
        with input_path.open("wb") as handle:
            shutil.copyfileobj(file.file, handle)
        input_size = input_path.stat().st_size
        file_sha256 = _sha256_file(input_path)
        logger.info(
            "文件脱敏开始 request_id=%s 文件名=%s sha256=%s 输入大小=%s",
            request_id,
            file.filename or "input",
            file_sha256,
            input_size,
        )

        standardization_started_at = perf_counter()
        standardization = resolved_standardizer.standardize(input_path, work_dir)
        if not standardization.ok or standardization.standardized_path is None:
            log_stage(
                request_id=request_id,
                stage="standardization",
                stage_started_at=standardization_started_at,
                ok=False,
                detail={"input_size": input_size},
                error_type=standardization.error or "standardization_failed",
            )
            logger.warning(
                "标准化失败 request_id=%s sha256=%s error=%s",
                request_id,
                file_sha256,
                standardization.error or "standardization_failed",
            )
            latency_ms = int((perf_counter() - started_at) * 1000)
            log_event(
                request_id=request_id,
                input_chars=input_size,
                output_chars=0,
                latency_ms=latency_ms,
                span_count=0,
                by_label={},
                ok=False,
                status_code=422,
                error_type=standardization.error or "standardization_failed",
            )
            return JSONResponse(
                status_code=422,
                content={
                    "ok": False,
                    "request_id": request_id,
                    "error": standardization.error or "standardization_failed",
                    "failed_summary": standardization.summary,
                },
            )

        log_stage(
            request_id=request_id,
            stage="standardization",
            stage_started_at=standardization_started_at,
            ok=True,
            detail={
                "input_size": input_size,
                "file_sha256": file_sha256,
                "standardized_size": standardization.standardized_path.stat().st_size,
                "standardized_rows": standardization.summary.get("rows"),
            },
        )
        logger.info(
            "标准化完成 request_id=%s sha256=%s 标准化大小=%s 标准化行数=%s",
            request_id,
            file_sha256,
            standardization.standardized_path.stat().st_size,
            standardization.summary.get("rows"),
        )

        token_vault_started_at = perf_counter()
        try:
            output = file_tokenizer.tokenize_file(
                standardization.standardized_path,
                work_dir,
                enabled_labels=labels,
                token_vault=vault,
                vault_ref=(
                    _build_vault_ref(
                        file_sha256=file_sha256,
                        file_size=input_size,
                        enabled_labels=labels,
                    )
                    if include_vault_ref
                    else None
                ),
            )
        except Exception:
            log_stage(
                request_id=request_id,
                stage="token_vault",
                stage_started_at=token_vault_started_at,
                ok=False,
                detail={"standardized_size": standardization.standardized_path.stat().st_size},
                error_type="file_tokenization_failed",
            )
            logger.exception("Token Vault 化失败 request_id=%s sha256=%s", request_id, file_sha256)
            return error_response(
                request_id=request_id,
                error="file_tokenization_failed",
                status_code=500,
                input_chars=input_size,
                started_at=started_at,
            )

        archive_size = output.archive_path.stat().st_size
        log_stage(
            request_id=request_id,
            stage="token_vault",
            stage_started_at=token_vault_started_at,
            ok=True,
            detail={
                "archive_size": archive_size,
                "file_sha256": file_sha256,
                "span_count": output.span_count,
                "by_label": output.by_label,
            },
        )
        if cache_result:
            resolved_vault_cache.put(
                file_sha256=file_sha256,
                file_size=input_size,
                strategy_version=STRATEGY_VERSION,
                enabled_labels=labels,
                token_vault=output.token_vault,
            )
        logger.info(
            "Token Vault 化完成 request_id=%s sha256=%s 输出文件=%s 命中数量=%s 输出大小=%s",
            request_id,
            file_sha256,
            output.tokenized_path.name,
            output.span_count,
            archive_size,
        )
        latency_ms = int((perf_counter() - started_at) * 1000)
        log_event(
            request_id=request_id,
            input_chars=input_size,
            output_chars=archive_size,
            latency_ms=latency_ms,
            span_count=output.span_count,
            by_label=output.by_label,
            ok=True,
            status_code=200,
            error_type=None,
        )
        return output, input_size, archive_size, file_sha256

    @app.post("/api/files/tokenize", response_model=None)
    async def tokenize_file(
        file: UploadFile = File(...),
        enabled_labels: str | None = Form(default=None),
        mode: str = Form(default="auto"),
        token_vault: str | None = Form(default=None),
    ) -> FileResponse | JSONResponse:
        # 文件级主流程：上传原始流水 -> ymb-standardization-core 标准化 -> Token Vault。
        # 标准化失败时直接退出，不做全文兜底脱敏，也不提交 WorkBuddy / AI。
        started_at = perf_counter()
        request_id = str(uuid4())
        if mode not in {"auto", "standardized"}:
            return error_response(
                request_id=request_id,
                error="unsupported_mode",
                status_code=400,
                input_chars=0,
                started_at=started_at,
            )

        labels = _parse_labels(enabled_labels)
        try:
            vault = _parse_token_vault(token_vault)
        except ValueError:
            return error_response(
                request_id=request_id,
                error="invalid_token_vault",
                status_code=400,
                input_chars=0,
                started_at=started_at,
            )

        with _temporary_directory(prefix="token-vault-") as temp_name:
            temp_dir = Path(temp_name)
            result = await _tokenize_upload(
                file=file,
                work_dir=temp_dir,
                labels=labels,
                vault=vault,
                request_id=request_id,
                started_at=started_at,
            )
            if isinstance(result, JSONResponse):
                return result
            output, _input_size, _archive_size, _file_sha256 = result
            persistent_archive = Path(tempfile.gettempdir()) / f"{request_id}.zip"
            shutil.copy2(output.archive_path, persistent_archive)
            return FileResponse(
                persistent_archive,
                media_type="application/zip",
                filename=f"{Path(file.filename or 'tokenized').stem}_tokenized_bundle.zip",
                background=BackgroundTask(
                    lambda: persistent_archive.unlink(missing_ok=True)
                ),
            )

    @app.post("/api/files/tokenize/batch", response_model=None)
    async def tokenize_files_batch(
        files: list[UploadFile] = File(...),
        enabled_labels: str | None = Form(default=None),
        mode: str = Form(default="auto"),
        token_vault: str | None = Form(default=None),
    ) -> FileResponse | JSONResponse:
        started_at = perf_counter()
        request_id = str(uuid4())
        if mode not in {"auto", "standardized"}:
            return error_response(
                request_id=request_id,
                error="unsupported_mode",
                status_code=400,
                input_chars=0,
                started_at=started_at,
            )
        if not files:
            return error_response(
                request_id=request_id,
                error="empty_files",
                status_code=400,
                input_chars=0,
                started_at=started_at,
            )

        labels = _parse_labels(enabled_labels)
        try:
            vault = _parse_token_vault(token_vault)
        except ValueError:
            return error_response(
                request_id=request_id,
                error="invalid_token_vault",
                status_code=400,
                input_chars=0,
                started_at=started_at,
            )

        with _temporary_directory(prefix="token-vault-batch-") as temp_name:
            temp_dir = Path(temp_name)
            combined_archive = temp_dir / "tokenized_batch_output.zip"
            bundle_root = "tokenized_batch_bundle"
            summary_root = f"{bundle_root}/summary"
            manifest_items: list[dict[str, Any]] = []
            batch_records: list[dict[str, Any]] = []
            tokenized_members: list[str] = []
            batch_vault = vault
            aggregate_counter: Counter[str] = Counter()
            total_input_size = 0
            total_archive_size = 0
            total_span_count = 0
            client_alias_counts: Counter[str] = Counter()
            with zipfile.ZipFile(combined_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for index, upload in enumerate(files, start=1):
                    item_started_at = perf_counter()
                    item_dir = temp_dir / f"item-{index:03d}"
                    item_dir.mkdir()
                    item_request_id = f"{request_id}-{index:03d}"
                    prefix = _safe_archive_prefix(index, upload.filename or f"input-{index}")
                    result = await _tokenize_upload(
                        file=upload,
                        work_dir=item_dir,
                        labels=labels,
                        vault=batch_vault,
                        request_id=item_request_id,
                        started_at=item_started_at,
                        include_vault_ref=False,
                        cache_result=False,
                    )
                    if isinstance(result, JSONResponse):
                        payload = json.loads(result.body.decode("utf-8"))
                        failed_name = f"{summary_root}/{prefix}_failed_summary.json"
                        archive.writestr(
                            failed_name,
                            json.dumps(payload, ensure_ascii=False, indent=2),
                        )
                        batch_records.append(
                            {
                                "index": index,
                                "ok": False,
                                "file_sha256": None,
                                "file_size": 0,
                            }
                        )
                        manifest_items.append(
                            {
                                "index": index,
                                "filename": upload.filename,
                                "ok": False,
                                "error": payload.get("error"),
                                "failed_summary": failed_name,
                            }
                        )
                        continue

                    output, input_size, archive_size, file_sha256 = result
                    batch_vault = output.token_vault
                    total_input_size += input_size
                    total_archive_size += archive_size
                    total_span_count += output.span_count
                    aggregate_counter.update(output.by_label)
                    client_alias_counts.update(output.client_alias_counts)
                    batch_records.append(
                        {
                            "index": index,
                            "ok": True,
                            "file_sha256": file_sha256,
                            "file_size": input_size,
                        }
                    )
                    with zipfile.ZipFile(output.archive_path) as item_archive:
                        for member in item_archive.namelist():
                            if member.endswith(("_token_vault.json", "_token_vault_ref.json")):
                                continue
                            target_dir = summary_root if member.endswith("_summary.json") else bundle_root
                            target_name = (
                                f"{target_dir}/{prefix}_{Path(member).name}"
                                if target_dir == summary_root
                                else _batch_standardized_member_name(
                                    bundle_root=bundle_root,
                                    prefix=prefix,
                                    member=member,
                                )
                            )
                            archive.writestr(
                                target_name,
                                item_archive.read(member),
                            )
                            if target_dir == bundle_root:
                                tokenized_members.append(target_name)
                    manifest_items.append(
                        {
                            "index": index,
                            "filename": upload.filename,
                            "ok": True,
                            "file_sha256": file_sha256,
                            "input_size": input_size,
                            "archive_size": archive_size,
                        }
                    )
                batch_sha256 = _sha256_batch(batch_records)
                batch_ref = _build_batch_vault_ref(
                    batch_sha256=batch_sha256,
                    file_count=len(files),
                    total_input_size=total_input_size,
                    enabled_labels=labels,
                )
                archive.writestr(
                    f"{summary_root}/tokenized_batch_bundle_token_vault_ref.json",
                    json.dumps(batch_ref, ensure_ascii=False, indent=2),
                )
                archive.writestr(
                    f"{summary_root}/manifest.json",
                    json.dumps(
                        {
                            "schema_version": "bank-statement-standardization.manifest/v1",
                            "producer": "token_vault_service",
                            "archive_id": batch_sha256,
                            "client_alias": _build_batch_client_alias(
                                client_alias_counts,
                                batch_sha256,
                            ),
                            "stage_1_standardize": {
                                "status": "DONE",
                                "outputs": [
                                    f"../{Path(member).name}"
                                    for member in tokenized_members
                                ],
                            },
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
            resolved_vault_cache.put(
                file_sha256=batch_sha256,
                file_size=total_input_size,
                strategy_version=STRATEGY_VERSION,
                enabled_labels=labels,
                token_vault=batch_vault or {},
            )
            log_stage(
                request_id=request_id,
                stage="batch_token_vault",
                stage_started_at=started_at,
                ok=True,
                detail={
                    "batch_sha256": batch_sha256,
                    "file_count": len(files),
                    "input_size": total_input_size,
                    "archive_size": total_archive_size,
                    "span_count": total_span_count,
                    "by_label": dict(aggregate_counter),
                },
            )
            logger.info(
                "批量 Token Vault 化完成 request_id=%s batch_sha256=%s 输出文件=%s 文件数=%s 命中数量=%s 输出大小=%s",
                request_id,
                batch_sha256,
                ",".join(tokenized_members),
                len(files),
                total_span_count,
                total_archive_size,
            )

            persistent_archive = Path(tempfile.gettempdir()) / f"{request_id}.zip"
            shutil.copy2(combined_archive, persistent_archive)
            return FileResponse(
                persistent_archive,
                media_type="application/zip",
                filename="tokenized_batch_bundle.zip",
                background=BackgroundTask(
                    lambda: persistent_archive.unlink(missing_ok=True)
                ),
            )

    @app.post("/api/text-files/tokenize", response_model=None)
    async def tokenize_text_files(
        files: list[UploadFile] = File(...),
        enabled_labels: str | None = Form(default=None),
    ) -> FileResponse | JSONResponse:
        started_at = perf_counter()
        request_id = str(uuid4())
        labels = _parse_labels(enabled_labels)
        if not files:
            return error_response(
                request_id=request_id,
                error="empty_file",
                status_code=400,
                input_chars=0,
                started_at=started_at,
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            archive_path = work_dir / "tokenized_text_bundle.zip"
            total_input_chars = 0
            total_output_chars = 0
            total_span_count = 0
            aggregate_counter: Counter[str] = Counter()

            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for index, upload in enumerate(files, start=1):
                    raw = await upload.read()
                    if not raw:
                        return error_response(
                            request_id=request_id,
                            error="empty_file",
                            status_code=400,
                            input_chars=total_input_chars,
                            started_at=started_at,
                        )
                    input_path = work_dir / f"text-{index}{Path(upload.filename or 'input.txt').suffix or '.txt'}"
                    input_path.write_bytes(raw)
                    file_sha256 = _sha256_file(input_path)
                    try:
                        text = _decode_text(raw)
                    except ValueError:
                        return error_response(
                            request_id=request_id,
                            error="unsupported_text_encoding",
                            status_code=400,
                            input_chars=total_input_chars,
                            started_at=started_at,
                        )
                    if total_input_chars + len(text) > resolved_settings.max_chars:
                        return error_response(
                            request_id=request_id,
                            error="text_too_large",
                            status_code=413,
                            input_chars=total_input_chars + len(text),
                            started_at=started_at,
                        )

                    output = resolved_token_service.tokenize_text_pages(
                        [{"page_no": 1, "text": text}],
                        labels,
                        None,
                    )
                    resolved_vault_cache.put(
                        file_sha256=file_sha256,
                        file_size=len(raw),
                        strategy_version=STRATEGY_VERSION,
                        enabled_labels=labels,
                        token_vault=output.mapping,
                    )

                    tokenized_text = str(output.pages[0].get("text", "")) if output.pages else ""
                    prefix = _safe_archive_prefix(index, upload.filename or f"text-{index}.txt")
                    archive.writestr(
                        f"tokenized_text_bundle/{prefix}__tokenized.txt",
                        tokenized_text,
                    )
                    archive.writestr(
                        f"tokenized_text_bundle/summary/{prefix}_token_vault_ref.json",
                        json.dumps(
                            _build_vault_ref(
                                file_sha256=file_sha256,
                                file_size=len(raw),
                                enabled_labels=labels,
                            ),
                            ensure_ascii=False,
                            indent=2,
                        ),
                    )
                    archive.writestr(
                        f"tokenized_text_bundle/summary/{prefix}_summary.json",
                        json.dumps(output.to_summary_dict(), ensure_ascii=False, indent=2),
                    )

                    total_input_chars += len(text)
                    total_output_chars += len(tokenized_text)
                    total_span_count += output.span_count
                    aggregate_counter.update(output.by_label)

            latency_ms = int((perf_counter() - started_at) * 1000)
            log_event(
                request_id=request_id,
                input_chars=total_input_chars,
                output_chars=total_output_chars,
                latency_ms=latency_ms,
                span_count=total_span_count,
                by_label=dict(aggregate_counter),
                ok=True,
                status_code=200,
                error_type=None,
            )
            persistent_archive = Path(tempfile.gettempdir()) / f"{request_id}_text.zip"
            shutil.copy2(archive_path, persistent_archive)
            return FileResponse(
                persistent_archive,
                media_type="application/zip",
                filename="tokenized_text_bundle.zip",
                background=BackgroundTask(
                    lambda: persistent_archive.unlink(missing_ok=True)
                ),
            )

    @app.post(
        "/api/tokenize/text",
        response_model=TokenizeTextResponse,
        responses={400: {"model": ErrorResponse}, 413: {"model": ErrorResponse}},
    )
    def tokenize_text(request: TokenizeTextRequest) -> TokenizeTextResponse | JSONResponse:
        started_at = perf_counter()
        request_id = str(uuid4())
        input_chars = sum(len(page.text) for page in request.pages)
        if not request.pages or all(not page.text.strip() for page in request.pages):
            return error_response(
                request_id=request_id,
                error="empty_text",
                status_code=400,
                input_chars=input_chars,
                started_at=started_at,
            )
        if input_chars > resolved_settings.max_chars:
            return error_response(
                request_id=request_id,
                error="text_too_large",
                status_code=413,
                input_chars=input_chars,
                started_at=started_at,
            )
        vault = _model_vault_to_dict(request.token_vault)
        output = resolved_token_service.tokenize_text_pages(
            [page.model_dump() for page in request.pages],
            request.enabled_labels,
            vault,
        )
        latency_ms = int((perf_counter() - started_at) * 1000)
        output_chars = sum(len(str(page.get("text", ""))) for page in output.pages)
        log_event(
            request_id=request_id,
            input_chars=input_chars,
            output_chars=output_chars,
            latency_ms=latency_ms,
            span_count=output.span_count,
            by_label=dict(output.by_label),
            ok=True,
            status_code=200,
            error_type=None,
        )
        return TokenizeTextResponse(
            request_id=request_id,
            latency_ms=latency_ms,
            pages=output.pages,
            token_vault=output.mapping,
            summary=output.to_summary_dict(),
        )

    @app.post("/api/tokenize/standardized", response_model=TokenizeStandardizedResponse)
    def tokenize_standardized(
        request: TokenizeStandardizedRequest,
    ) -> TokenizeStandardizedResponse | JSONResponse:
        started_at = perf_counter()
        request_id = str(uuid4())
        input_chars = sum(len(column) for column in request.columns) + sum(
            len(value) for row in request.rows for value in row
        )
        if not request.columns or not request.rows:
            return error_response(
                request_id=request_id,
                error="empty_table",
                status_code=400,
                input_chars=input_chars,
                started_at=started_at,
            )
        if any(len(row) != len(request.columns) for row in request.rows):
            return error_response(
                request_id=request_id,
                error="invalid_table_shape",
                status_code=400,
                input_chars=input_chars,
                started_at=started_at,
            )
        output = resolved_token_service.tokenize_standardized(
            request.columns,
            request.rows,
            request.enabled_labels,
            _model_vault_to_dict(request.token_vault),
        )
        latency_ms = int((perf_counter() - started_at) * 1000)
        output_chars = sum(len(value) for row in output.rows for value in row)
        log_event(
            request_id=request_id,
            input_chars=input_chars,
            output_chars=output_chars,
            latency_ms=latency_ms,
            span_count=output.span_count,
            by_label=dict(output.by_label),
            ok=True,
            status_code=200,
            error_type=None,
        )
        return TokenizeStandardizedResponse(
            request_id=request_id,
            latency_ms=latency_ms,
            columns=output.columns,
            rows=output.rows,
            token_vault=output.mapping,
            summary=output.to_summary_dict(),
        )

    @app.post("/api/detokenize", response_model=DetokenizeResponse)
    def detokenize(request: DetokenizeRequest) -> DetokenizeResponse | JSONResponse:
        # 逆脱敏只在本地服务执行，WorkBuddy / AI 不接触 Token Vault。
        started_at = perf_counter()
        request_id = str(uuid4())
        input_chars = len(request.text)
        if not request.text.strip():
            return error_response(
                request_id=request_id,
                error="empty_text",
                status_code=400,
                input_chars=input_chars,
                started_at=started_at,
            )
        token_vault, vault_error = _resolve_detokenize_vault(
            token_vault=_model_vault_to_dict(request.token_vault),
            file_sha256=request.file_sha256,
            vault_cache=resolved_vault_cache,
        )
        if vault_error is not None or token_vault is None:
            return error_response(
                request_id=request_id,
                error=vault_error or "invalid_token_vault",
                status_code=404 if vault_error == "vault_cache_miss" else 400,
                input_chars=input_chars,
                started_at=started_at,
            )
        replacement_count = count_detokenize_replacements(request.text, token_vault)
        output_text = resolved_token_service.detokenize(request.text, token_vault)
        latency_ms = int((perf_counter() - started_at) * 1000)
        logger.info(
            "逆脱敏完成 request_id=%s 来源=%s 替换数量=%s",
            request_id,
            "sha256缓存" if request.file_sha256 and request.token_vault is None else "Token Vault",
            replacement_count,
        )
        log_event(
            request_id=request_id,
            input_chars=input_chars,
            output_chars=len(output_text),
            latency_ms=latency_ms,
            span_count=replacement_count,
            by_label={},
            ok=True,
            status_code=200,
            error_type=None,
        )
        return DetokenizeResponse(
            request_id=request_id,
            latency_ms=latency_ms,
            text=output_text,
            summary={"replacement_count": replacement_count},
        )

    @app.post("/api/files/detokenize", response_model=DetokenizeResponse)
    async def detokenize_file(
        analysis_file: UploadFile = File(...),
        token_vault_file: UploadFile | None = File(default=None),
        file_sha256: str | None = Form(default=None),
    ) -> DetokenizeResponse | JSONResponse:
        started_at = perf_counter()
        request_id = str(uuid4())
        raw_text = await analysis_file.read()
        try:
            text = _decode_text(raw_text)
        except ValueError:
            return error_response(
                request_id=request_id,
                error="unsupported_text_encoding",
                status_code=400,
                input_chars=0,
                started_at=started_at,
            )
        input_chars = len(text)
        if not text.strip():
            return error_response(
                request_id=request_id,
                error="empty_text",
                status_code=400,
                input_chars=input_chars,
                started_at=started_at,
            )

        uploaded_vault = None
        if token_vault_file is not None:
            try:
                uploaded_vault = _parse_token_vault_bytes(await token_vault_file.read())
            except ValueError:
                return error_response(
                    request_id=request_id,
                    error="invalid_token_vault",
                    status_code=400,
                    input_chars=input_chars,
                    started_at=started_at,
                )

        token_vault, vault_error = _resolve_detokenize_vault(
            token_vault=uploaded_vault,
            file_sha256=file_sha256,
            vault_cache=resolved_vault_cache,
        )
        if vault_error is not None or token_vault is None:
            return error_response(
                request_id=request_id,
                error=vault_error or "invalid_token_vault",
                status_code=404 if vault_error == "vault_cache_miss" else 400,
                input_chars=input_chars,
                started_at=started_at,
            )

        replacement_count = count_detokenize_replacements(text, token_vault)
        output_text = resolved_token_service.detokenize(text, token_vault)
        latency_ms = int((perf_counter() - started_at) * 1000)
        logger.info(
            "文件逆脱敏完成 request_id=%s 来源=%s 替换数量=%s",
            request_id,
            "sha256缓存" if file_sha256 and uploaded_vault is None else "Token Vault文件",
            replacement_count,
        )
        log_event(
            request_id=request_id,
            input_chars=input_chars,
            output_chars=len(output_text),
            latency_ms=latency_ms,
            span_count=replacement_count,
            by_label={},
            ok=True,
            status_code=200,
            error_type=None,
        )
        return DetokenizeResponse(
            request_id=request_id,
            latency_ms=latency_ms,
            text=output_text,
            summary={"replacement_count": replacement_count},
        )

    return app


def _safe_upload_name(filename: str) -> str:
    suffix = Path(filename).suffix
    return f"input{suffix.lower()}"


def _safe_archive_prefix(index: int, filename: str) -> str:
    stem = Path(filename).stem or "input"
    cleaned = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "_"
        for char in stem
    ).strip("._")
    return f"{index:03d}_{cleaned or 'input'}"


def _batch_standardized_member_name(*, bundle_root: str, prefix: str, member: str) -> str:
    suffix = Path(member).suffix.lower() or ".csv"
    return f"{bundle_root}/{prefix}__standardized{suffix}"


def _build_batch_client_alias(
    client_alias_counts: Counter[str],
    batch_sha256: str,
) -> str:
    aliases = [
        alias
        for alias, _count in sorted(
            client_alias_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]
    if aliases:
        return "_".join(aliases)
    return f"tokenized_{batch_sha256[:8]}"


def _parse_labels(raw: str | None) -> list[str] | None:
    if raw is None or not raw.strip():
        return None
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_token_vault(raw: str | None) -> dict[str, dict[str, str]] | None:
    if raw is None or not raw.strip():
        return None
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("invalid_token_vault")
    vault: dict[str, dict[str, str]] = {}
    for token, value in parsed.items():
        if not isinstance(value, dict) or "label" not in value or "original" not in value:
            continue
        item = {"label": str(value["label"]), "original": str(value["original"])}
        if "source_column" in value and value["source_column"] is not None:
            item["source_column"] = str(value["source_column"])
        vault[str(token)] = item
    return vault


def _parse_token_vault_bytes(raw: bytes) -> dict[str, dict[str, str]]:
    try:
        return _parse_token_vault(raw.decode("utf-8-sig")) or {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_token_vault") from exc


def _model_vault_to_dict(
    vault: Any | None,
) -> dict[str, dict[str, str]] | None:
    if not vault:
        return None
    return {token: value.model_dump() for token, value in vault.items()}


def _resolve_detokenize_vault(
    *,
    token_vault: dict[str, dict[str, str]] | None,
    file_sha256: str | None,
    vault_cache: TokenVaultCache,
) -> tuple[dict[str, dict[str, str]] | None, str | None]:
    if token_vault:
        return token_vault, None
    if file_sha256 and file_sha256.strip():
        cached = vault_cache.get(file_sha256.strip())
        if cached:
            return cached, None
        return None, "vault_cache_miss"
    return None, "invalid_token_vault"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def _temporary_directory(prefix: str) -> Iterator[str]:
    """创建临时目录，并在清理失败时保持流程可继续。"""

    temp_name = tempfile.mkdtemp(prefix=prefix)
    try:
        yield temp_name
    finally:
        shutil.rmtree(temp_name, ignore_errors=True)


def _sha256_batch(records: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for record in records:
        digest.update(str(record.get("index", "")).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(record.get("ok", "")).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(record.get("file_size", "")).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(record.get("file_sha256") or "").encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _build_vault_ref(
    *,
    file_sha256: str,
    file_size: int,
    enabled_labels: list[str] | None,
) -> dict[str, Any]:
    return {
        "file_sha256": file_sha256,
        "file_size": file_size,
        "strategy_version": STRATEGY_VERSION,
        "enabled_labels": enabled_labels or [],
        "vault_cache_policy": "sqlite_lru_200",
        "vault_cache_scope": "local_machine_persistent_file",
        "vault_cache_path_hint": "data/token-vault-cache.sqlite3",
    }


def _build_batch_vault_ref(
    *,
    batch_sha256: str,
    file_count: int,
    total_input_size: int,
    enabled_labels: list[str] | None,
) -> dict[str, Any]:
    return {
        "file_sha256": batch_sha256,
        "file_count": file_count,
        "total_input_size": total_input_size,
        "strategy_version": STRATEGY_VERSION,
        "enabled_labels": enabled_labels or [],
        "vault_cache_policy": "sqlite_lru_200",
        "vault_cache_scope": "local_machine_persistent_file",
        "vault_cache_path_hint": "data/token-vault-cache.sqlite3",
        "scope": "batch",
    }


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("unsupported_text_encoding")


app = create_app()
