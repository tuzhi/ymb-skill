from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
from contextlib import contextmanager
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from token_vault_service.app import create_app
from token_vault_service.config import Settings
from token_vault_service.standardization_adapter import StandardizationResult


class FakeStandardizer:
    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok

    def standardize(self, input_path: Path, work_dir: Path) -> StandardizationResult:
        if not self.ok:
            return StandardizationResult(
                ok=False,
                error="standardization_failed",
                summary={"stage": "stage_1_standardize"},
            )
        output_path = work_dir / "standardized.csv"
        output_path.write_text(
            "客户姓名,手机号,交易金额,摘要\n张三,13800138000,1000.00,张三还款\n",
            encoding="utf-8-sig",
        )
        return StandardizationResult(ok=True, standardized_path=output_path)


class NamedBankStatementStandardizer(FakeStandardizer):
    def standardize(self, input_path: Path, work_dir: Path) -> StandardizationResult:
        output_path = work_dir / "standardized.csv"
        output_path.write_text(
            "\ufeff交易唯一编号,交易时间,本方名称,本方账户,开户行,账户类型,对手名称,对手账户,"
            "收入金额,支出金额,交易金额,账户余额,银行备注,账户方附言,交易渠道,来源文件名,来源行号\n"
            "TX-001,2026-06-11 10:00:00,江西省鹏达石业有限公司,622200,中国建设银行,对公,"
            "张三,622201,100.00,,100.00,1000.00,转账,,网银,raw.csv,2\n",
            encoding="utf-8",
        )
        return StandardizationResult(
            ok=True,
            standardized_path=output_path,
            summary={"rows": 1},
        )


class MultiNamedBankStatementStandardizer(FakeStandardizer):
    def standardize(self, input_path: Path, work_dir: Path) -> StandardizationResult:
        output_path = work_dir / "standardized.csv"
        output_path.write_text(
            "\ufeff交易唯一编号,交易时间,本方名称,本方账号,开户行,账户类型,对手名称,对手账号,"
            "收入金额,支出金额,交易金额,账户余额,银行备注,账户方附言,交易渠道,来源文件名,来源行号\n"
            "TX-001,2026-06-11 10:00:00,江西省鹏达石业有限公司,622200,中国建设银行,对公,"
            "张三,622201,100.00,,100.00,1000.00,转账,,网银,raw.csv,2\n"
            "TX-002,2026-06-11 11:00:00,长沙示例贸易有限公司,622300,中国建设银行,对公,"
            "李四,622301,200.00,,200.00,1200.00,转账,,网银,raw.csv,3\n",
            encoding="utf-8",
        )
        return StandardizationResult(
            ok=True,
            standardized_path=output_path,
            summary={"rows": 2},
        )


class LockedInputStandardizer(FakeStandardizer):
    def __init__(self) -> None:
        super().__init__()
        self.handles = []

    def standardize(self, input_path: Path, work_dir: Path) -> StandardizationResult:
        self.handles.append(input_path.open("rb"))
        return super().standardize(input_path, work_dir)

    def close(self) -> None:
        for handle in self.handles:
            handle.close()
        self.handles.clear()


@contextmanager
def capture_service_logs():
    logger = logging.getLogger("token_vault_service")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    try:
        yield stream
    finally:
        logger.removeHandler(handler)


def test_module_exports_asgi_app():
    from token_vault_service.app import app

    assert hasattr(app, "routes")


def test_health_and_runtime(tmp_path: Path):
    app = create_app(
        settings=Settings(log_path=tmp_path / "audit.jsonl"),
        standardizer=FakeStandardizer(),
    )
    client = TestClient(app)

    assert client.get("/health").json() == {"ok": True}
    runtime = client.get("/api/runtime").json()
    assert runtime["ok"] is True
    assert runtime["max_chars"] == 20000
    assert runtime["standardization_module"] == "ymb_standardization_core"


def test_tokenize_text_api_uses_token_vault_and_does_not_log_it(tmp_path: Path):
    log_path = tmp_path / "audit.jsonl"
    app = create_app(settings=Settings(log_path=log_path), standardizer=FakeStandardizer())
    client = TestClient(app)

    response = client.post(
        "/api/tokenize/text",
        json={
            "pages": [{"page_no": 1, "text": "户名：张三，户名：李四"}],
            "enabled_labels": ["person"],
            "token_vault": {"张某001": {"label": "person", "original": "张三"}},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["pages"][0]["text"] == "户名：张某001，户名：李某002"
    assert payload["token_vault"]["李某002"]["original"] == "李四"

    log_text = log_path.read_text(encoding="utf-8")
    assert "张三" not in log_text
    assert "李四" not in log_text
    assert "张某001" not in log_text
    assert "token_vault" not in log_text


def test_tokenize_text_api_does_not_return_file_sha256(tmp_path: Path):
    log_path = tmp_path / "audit.jsonl"
    app = create_app(settings=Settings(log_path=log_path), standardizer=FakeStandardizer())
    client = TestClient(app)

    response = client.post(
        "/api/tokenize/text",
        json={
            "pages": [{"page_no": 1, "text": "客户姓名：张三，手机号：13800138000"}],
            "enabled_labels": ["person", "phone"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert "file_sha256" not in payload
    tokenized_text = payload["pages"][0]["text"]
    assert "张三" not in tokenized_text
    assert "13800138000" not in tokenized_text

    log_text = log_path.read_text(encoding="utf-8")
    assert "张三" not in log_text
    assert "13800138000" not in log_text


def test_text_file_tokenize_uses_uploaded_file_sha256_and_returns_zip(tmp_path: Path):
    log_path = tmp_path / "audit.jsonl"
    app = create_app(
        settings=Settings(
            log_path=log_path,
            vault_cache_path=tmp_path / "cache.sqlite3",
        ),
        standardizer=FakeStandardizer(),
    )
    client = TestClient(app)
    raw = "客户姓名：张三，手机号：13800138000".encode("utf-8")

    response = client.post(
        "/api/text-files/tokenize",
        files={"files": ("input.txt", raw, "text/plain")},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = archive.namelist()
    assert "tokenized_text_bundle/001_input__tokenized.txt" in names
    assert "tokenized_text_bundle/summary/001_input_token_vault_ref.json" in names
    tokenized_text = archive.read("tokenized_text_bundle/001_input__tokenized.txt").decode("utf-8")
    ref = json.loads(
        archive.read("tokenized_text_bundle/summary/001_input_token_vault_ref.json").decode("utf-8")
    )
    assert ref["file_sha256"] == hashlib.sha256(raw).hexdigest()
    assert "张三" not in tokenized_text
    assert "13800138000" not in tokenized_text

    detokenize_response = client.post(
        "/api/detokenize",
        json={"text": tokenized_text, "file_sha256": ref["file_sha256"]},
    )

    assert detokenize_response.status_code == 200
    assert detokenize_response.json()["text"] == "客户姓名：张三，手机号：13800138000"


def test_detokenize_api_uses_token_vault_without_logging_it(tmp_path: Path):
    log_path = tmp_path / "audit.jsonl"
    app = create_app(settings=Settings(log_path=log_path), standardizer=FakeStandardizer())
    client = TestClient(app)

    response = client.post(
        "/api/detokenize",
        json={
            "text": "张某001的流水稳定",
            "token_vault": {"张某001": {"label": "person", "original": "张三"}},
        },
    )

    assert response.status_code == 200
    assert response.json()["text"] == "张三的流水稳定"
    log_text = log_path.read_text(encoding="utf-8")
    assert "张三" not in log_text
    assert "张某001" not in log_text


def test_file_tokenize_returns_zip_with_tokenized_csv_and_vault(tmp_path: Path):
    log_path = tmp_path / "audit.jsonl"
    app = create_app(settings=Settings(log_path=log_path), standardizer=FakeStandardizer())
    client = TestClient(app)

    response = client.post(
        "/api/files/tokenize",
        files={"file": ("raw.csv", b"placeholder", "text/csv")},
        data={"enabled_labels": "person,phone", "mode": "auto"},
    )

    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = archive.namelist()
    assert any(name.endswith("_tokenized.csv") for name in names)
    assert any(name.endswith("_token_vault.json") for name in names)
    assert any(name.endswith("_token_vault_ref.json") for name in names)
    tokenized_name = next(name for name in names if name.endswith("_tokenized.csv"))
    vault_name = next(name for name in names if name.endswith("_token_vault.json"))
    ref_name = next(name for name in names if name.endswith("_token_vault_ref.json"))
    tokenized_rows = list(
        csv.reader(
            io.StringIO(archive.read(tokenized_name).decode("utf-8-sig"))
        )
    )
    assert tokenized_rows == [
        ["客户姓名", "手机号", "交易金额", "摘要"],
        ["张某001", "手机号001", "1000.00", "张某001还款"],
    ]
    vault = json.loads(archive.read(vault_name).decode("utf-8"))
    ref = json.loads(archive.read(ref_name).decode("utf-8"))
    assert ref["file_sha256"] == hashlib.sha256(b"placeholder").hexdigest()
    assert ref["vault_cache_policy"] == "sqlite_lru_200"
    assert ref["vault_cache_scope"] == "local_machine_persistent_file"
    assert "张三" not in json.dumps(ref, ensure_ascii=False)

    token, mapping = next(iter(vault.items()))
    detokenize_response = client.post(
        "/api/detokenize",
        json={"text": f"{token}的分析结论", "file_sha256": ref["file_sha256"]},
    )
    assert detokenize_response.status_code == 200
    assert mapping["original"] in detokenize_response.json()["text"]

    log_text = log_path.read_text(encoding="utf-8")
    assert "张三" not in log_text
    assert "13800138000" not in log_text
    assert "张某001" not in log_text


def test_console_log_includes_tokenized_filename_but_audit_log_does_not(tmp_path: Path):
    log_path = tmp_path / "audit.jsonl"
    app = create_app(settings=Settings(log_path=log_path), standardizer=FakeStandardizer())
    client = TestClient(app)

    with capture_service_logs() as console_log:
        response = client.post(
            "/api/files/tokenize",
            files={"file": ("客户A流水.xlsx", b"placeholder", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"enabled_labels": "person,phone", "mode": "auto"},
        )

    assert response.status_code == 200
    assert "standardized_tokenized.csv" in console_log.getvalue()
    audit_text = log_path.read_text(encoding="utf-8")
    assert "客户A流水.xlsx" not in audit_text
    assert "standardized_tokenized.csv" not in audit_text
    assert "filename" not in audit_text


def test_file_tokenize_sha256_vault_survives_new_app_instance(tmp_path: Path):
    cache_path = tmp_path / "vault-cache.sqlite3"
    settings = Settings(
        log_path=tmp_path / "audit.jsonl",
        vault_cache_path=cache_path,
    )
    first_app = create_app(settings=settings, standardizer=FakeStandardizer())
    first_client = TestClient(first_app)

    response = first_client.post(
        "/api/files/tokenize",
        files={"file": ("raw.csv", b"persistent", "text/csv")},
        data={"enabled_labels": "person,phone", "mode": "auto"},
    )

    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    vault_name = next(name for name in archive.namelist() if name.endswith("_token_vault.json"))
    ref_name = next(name for name in archive.namelist() if name.endswith("_token_vault_ref.json"))
    vault = json.loads(archive.read(vault_name).decode("utf-8"))
    ref = json.loads(archive.read(ref_name).decode("utf-8"))
    token, mapping = next(iter(vault.items()))

    second_app = create_app(settings=settings, standardizer=FakeStandardizer())
    second_client = TestClient(second_app)
    detokenize_response = second_client.post(
        "/api/detokenize",
        json={"text": f"{token} analysis", "file_sha256": ref["file_sha256"]},
    )

    assert detokenize_response.status_code == 200
    assert mapping["original"] in detokenize_response.json()["text"]


def test_file_tokenize_batch_returns_combined_zip(tmp_path: Path):
    log_path = tmp_path / "audit.jsonl"
    app = create_app(
        settings=Settings(
            log_path=log_path,
            vault_cache_path=tmp_path / "cache.sqlite3",
        ),
        standardizer=FakeStandardizer(),
    )
    client = TestClient(app)

    response = client.post(
        "/api/files/tokenize/batch",
        files=[
            ("files", ("raw-a.csv", b"a", "text/csv")),
            ("files", ("raw-b.csv", b"b", "text/csv")),
        ],
        data={"enabled_labels": "person,phone", "mode": "auto"},
    )

    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = archive.namelist()
    ref_names = [name for name in names if name.endswith("_token_vault_ref.json")]
    vault_names = [name for name in names if name.endswith("_token_vault.json")]
    standardized_names = [name for name in names if name.endswith("__standardized.csv")]
    summary_names = [name for name in names if name.endswith("_summary.json")]
    assert len(ref_names) == 1
    assert vault_names == []
    assert len(standardized_names) == 2
    assert len(summary_names) == 2
    assert all(name.startswith("tokenized_batch_bundle/") for name in names)
    bundle_prefix = "tokenized_batch_bundle/"
    assert all(
        (
            "/" not in name[len(bundle_prefix):]
            or name.startswith("tokenized_batch_bundle/summary/")
        )
        for name in names
    )
    assert "tokenized_batch_bundle/summary/manifest.json" in names
    assert any(name.startswith("tokenized_batch_bundle/001_raw-a_") for name in names)
    assert any(name.startswith("tokenized_batch_bundle/002_raw-b_") for name in names)
    assert all(name.startswith("tokenized_batch_bundle/summary/") for name in ref_names)
    assert all(name.startswith("tokenized_batch_bundle/summary/") for name in summary_names)
    assert "tokenized_batch_bundle/001_raw-a__standardized.csv" in names
    assert "tokenized_batch_bundle/002_raw-b__standardized.csv" in names
    assert "tokenized_batch_bundle/summary/token_vault_manifest.json" not in names
    assert not any("__standardized_tokenized" in name for name in standardized_names)
    for ref_name in ref_names:
        ref = json.loads(archive.read(ref_name).decode("utf-8"))
        assert ref["vault_cache_policy"] == "sqlite_lru_200"
        assert ref["file_count"] == 2
    manifest = json.loads(archive.read("tokenized_batch_bundle/summary/manifest.json").decode("utf-8"))
    assert manifest["schema_version"] == "bank-statement-standardization.manifest/v1"
    assert manifest["producer"] == "token_vault_service"
    assert "archive_name" not in manifest
    assert manifest["archive_id"]
    assert manifest["client_alias"] == "张某001"
    assert manifest["stage_1_standardize"] == {
        "status": "DONE",
        "outputs": [
            "../001_raw-a__standardized.csv",
            "../002_raw-b__standardized.csv",
        ],
    }
    batch_sha256 = json.loads(archive.read(ref_names[0]).decode("utf-8"))["file_sha256"]
    assert manifest["archive_id"] == batch_sha256
    tokenized_rows = list(
        csv.reader(
            io.StringIO(archive.read("tokenized_batch_bundle/001_raw-a__standardized.csv").decode("utf-8-sig"))
        )
    )
    token = tokenized_rows[1][0]
    detokenize_response = client.post(
        "/api/detokenize",
        json={"text": f"{token} batch analysis", "file_sha256": batch_sha256},
    )
    assert detokenize_response.status_code == 200
    assert token not in detokenize_response.json()["text"]
    log_events = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    batch_events = [
        event
        for event in log_events
        if event.get("stage") == "batch_token_vault"
    ]
    assert len(batch_events) == 1
    assert batch_events[0]["detail"]["batch_sha256"] == batch_sha256
    assert batch_events[0]["detail"]["file_count"] == 2


def test_batch_tokenize_manifest_uses_tokenized_alias_not_archive_client_name(tmp_path: Path):
    app = create_app(
        settings=Settings(
            log_path=tmp_path / "audit.jsonl",
            vault_cache_path=tmp_path / "cache.sqlite3",
        ),
        standardizer=NamedBankStatementStandardizer(),
    )
    client = TestClient(app)

    response = client.post(
        "/api/files/tokenize/batch",
        files=[("files", ("raw-a.csv", b"a", "text/csv"))],
        data={"enabled_labels": "subject_name,subject_account", "mode": "auto"},
    )

    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    manifest = json.loads(
        archive.read("tokenized_batch_bundle/summary/manifest.json").decode("utf-8")
    )
    manifest_text = json.dumps(manifest, ensure_ascii=False)
    assert "archive_name" not in manifest
    assert "archive_id" in manifest
    assert manifest["client_alias"] == "主体001"
    assert "tokenized_batch_bundle" not in manifest["client_alias"]
    assert manifest["client_alias"] in manifest_text


def test_batch_tokenize_manifest_joins_multiple_tokenized_client_aliases(tmp_path: Path):
    app = create_app(
        settings=Settings(
            log_path=tmp_path / "audit.jsonl",
            vault_cache_path=tmp_path / "cache.sqlite3",
        ),
        standardizer=MultiNamedBankStatementStandardizer(),
    )
    client = TestClient(app)

    response = client.post(
        "/api/files/tokenize/batch",
        files=[("files", ("raw-a.csv", b"a", "text/csv"))],
        data={"enabled_labels": "subject_name,subject_account", "mode": "auto"},
    )

    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    manifest = json.loads(
        archive.read("tokenized_batch_bundle/summary/manifest.json").decode("utf-8")
    )
    manifest_text = json.dumps(manifest, ensure_ascii=False)
    assert manifest["client_alias"] == "主体001_主体002"
    assert "江西省鹏达石业有限公司" not in manifest_text
    assert "长沙示例贸易有限公司" not in manifest_text


def test_batch_console_log_includes_uploaded_filenames_but_audit_log_does_not(tmp_path: Path):
    log_path = tmp_path / "audit.jsonl"
    filenames = [
        "26052715284707352363.pdf",
        "hqmx_20260527152443(1).pdf",
        "微信支付账单流水文件(20250531-20260524)_20260602170137(1).xlsx",
        "微信支付账单流水文件(20250701-20260531)_20260602165440(1).xlsx",
    ]
    app = create_app(
        settings=Settings(
            log_path=log_path,
            vault_cache_path=tmp_path / "cache.sqlite3",
        ),
        standardizer=FakeStandardizer(),
    )
    client = TestClient(app)

    with capture_service_logs() as console_log:
        response = client.post(
            "/api/files/tokenize/batch",
            files=[
                ("files", (filename, b"placeholder", "application/octet-stream"))
                for filename in filenames
            ],
            data={"enabled_labels": "person,phone", "mode": "auto"},
        )

    assert response.status_code == 200
    console_text = console_log.getvalue()
    for filename in filenames:
        assert filename in console_text
    audit_text = log_path.read_text(encoding="utf-8")
    for filename in filenames:
        assert filename not in audit_text


def test_file_tokenize_batch_ignores_locked_temp_input_cleanup(tmp_path: Path):
    standardizer = LockedInputStandardizer()
    app = create_app(
        settings=Settings(
            log_path=tmp_path / "audit.jsonl",
            vault_cache_path=tmp_path / "cache.sqlite3",
        ),
        standardizer=standardizer,
    )
    client = TestClient(app)

    try:
        response = client.post(
            "/api/files/tokenize/batch",
            files=[
                ("files", ("raw-a.xlsx", b"a", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
                ("files", ("raw-b.xlsx", b"b", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
            ],
            data={"enabled_labels": "person,phone", "mode": "auto"},
        )
    finally:
        standardizer.close()

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"


def test_file_tokenize_ignores_locked_temp_input_cleanup(tmp_path: Path):
    standardizer = LockedInputStandardizer()
    app = create_app(
        settings=Settings(
            log_path=tmp_path / "audit.jsonl",
            vault_cache_path=tmp_path / "cache.sqlite3",
        ),
        standardizer=standardizer,
    )
    client = TestClient(app)

    try:
        response = client.post(
            "/api/files/tokenize",
            files={
                "file": (
                    "raw.xlsx",
                    b"a",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            data={"enabled_labels": "person,phone", "mode": "auto"},
        )
    finally:
        standardizer.close()

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"


def test_file_tokenize_standardization_failure_exits_without_vault(tmp_path: Path):
    log_path = tmp_path / "audit.jsonl"
    app = create_app(
        settings=Settings(log_path=log_path),
        standardizer=FakeStandardizer(ok=False),
    )
    client = TestClient(app)

    response = client.post(
        "/api/files/tokenize",
        files={"file": ("raw.csv", b"placeholder", "text/csv")},
        data={"mode": "auto"},
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error"] == "standardization_failed"
    assert payload["failed_summary"] == {"stage": "stage_1_standardize"}
    assert "token_vault" not in json.dumps(payload)


def test_detokenize_with_uploaded_token_vault_file(tmp_path: Path):
    log_path = tmp_path / "audit.jsonl"
    app = create_app(settings=Settings(log_path=log_path), standardizer=FakeStandardizer())
    client = TestClient(app)
    vault = {"张某001": {"label": "person", "original": "张三"}}

    response = client.post(
        "/api/files/detokenize",
        files={
            "analysis_file": ("analysis.txt", "张某001 的流水稳定".encode("utf-8"), "text/plain"),
            "token_vault_file": (
                "vault.json",
                json.dumps(vault, ensure_ascii=False).encode("utf-8"),
                "application/json",
            ),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["text"] == "张三 的流水稳定"
    assert payload["summary"]["replacement_count"] == 1

    log_text = log_path.read_text(encoding="utf-8")
    assert "张三" not in log_text
    assert "张某001" not in log_text


def test_detokenize_with_sha256_cache_miss_returns_clear_error(tmp_path: Path):
    log_path = tmp_path / "audit.jsonl"
    app = create_app(settings=Settings(log_path=log_path), standardizer=FakeStandardizer())
    client = TestClient(app)

    response = client.post(
        "/api/detokenize",
        json={"text": "张某001 的流水稳定", "file_sha256": "0" * 64},
    )

    assert response.status_code == 404
    assert response.json()["error"] == "vault_cache_miss"


def test_index_page_supports_batch_upload_and_hides_token_vault_inputs(tmp_path: Path):
    app = create_app(settings=Settings(log_path=tmp_path / "audit.jsonl"), standardizer=FakeStandardizer())
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert 'id="file"' in html
    assert "multiple" in html
    assert "文本脱敏" in html
    assert 'id="text-tokenize"' in html
    assert 'id="text-file"' in html
    assert 'id="text-input"' not in html
    assert 'aria-label="脱敏类别"' not in html
    assert 'name="label"' not in html
    assert 'id="token-vault"' not in html
    assert 'id="token-vault-file"' not in html
