"""统一 Router YAML 的草稿、测试、发布与回滚服务。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable
import json
import os
import tempfile
from threading import RLock
import uuid

# 导入运行时适配层以定位仓库/分发包中的 core，并完成 Excel reader 注册。
from runtime import standardize as _standardize  # noqa: F401
from ymb_standardization_core.readers.input_router import read_rows
from ymb_standardization_core.readers.routing.rule_loader import (
    activate_routing_rules_snapshot,
    build_routing_rules_snapshot,
    routing_rules_path,
    routing_rules_version,
)

from .models import ArtifactStream, RuleDraft, RuleTestResult, RuleVersion


_DRAFT_LOCK = RLock()
DRAFT = "DRAFT"
TESTED = "TESTED"


class YamlRuleService:
    def __init__(
        self,
        run_root: str | os.PathLike[str],
        storage_root: str | os.PathLike[str],
        production_rules_path: str | os.PathLike[str] | None = None,
    ) -> None:
        self.run_root = Path(run_root).resolve()
        self.storage_root = Path(storage_root).resolve()
        self.production_path = Path(production_rules_path or routing_rules_path()).resolve()
        self.draft_dir = self.storage_root / "drafts"
        self.version_dir = self.storage_root / "versions"
        self.draft_path = self.draft_dir / "routing_rules.yaml"
        self.draft_meta_path = self.draft_dir / "metadata.json"
        self.draft_dir.mkdir(parents=True, exist_ok=True)
        self.version_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_version(self.production_path.read_text(encoding="utf-8"))

    def download_rules(self, version: str | None = None) -> ArtifactStream:
        if version:
            path = self._version_path(version)
            artifact_id = f"routing_rules-{version}.yaml"
        else:
            path = self.production_path
            artifact_id = "routing_rules.yaml"
        if not path.is_file():
            raise FileNotFoundError(f"规则版本不存在：{version}")
        return ArtifactStream(
            artifact_id=artifact_id,
            filename=artifact_id,
            content_type="application/yaml",
            size=path.stat().st_size,
            _path=path,
        )

    def create_draft(self, base_version: str | None = None) -> RuleDraft:
        with _DRAFT_LOCK:
            if self.draft_path.exists():
                raise RuntimeError("当前已经存在草稿")
            if base_version:
                source = self._version_path(base_version)
                if not source.is_file():
                    raise FileNotFoundError(f"规则版本不存在：{base_version}")
                content = source.read_text(encoding="utf-8")
            else:
                content = self.production_path.read_text(encoding="utf-8")
            self._atomic_write(self.draft_path, content)
            self._write_meta({"status": DRAFT, "test": None})
            return self._draft()

    def save_draft(self, content: str) -> RuleDraft:
        with _DRAFT_LOCK:
            self._require_draft()
            self._atomic_write(self.draft_path, content)
            self._write_meta({"status": DRAFT, "test": None})
            return self._draft()

    def test_draft(
        self,
        run_id: str,
        file_ids: Iterable[str] | None = None,
    ) -> RuleTestResult:
        with _DRAFT_LOCK:
            content = self._require_draft().read_text(encoding="utf-8")
            try:
                snapshot = build_routing_rules_snapshot(content)
            except ValueError as exc:
                self._write_meta({
                    "status": DRAFT,
                    "test": {"run_id": run_id, "error": str(exc)},
                })
                return RuleTestResult(
                    run_id=run_id,
                    passed=False,
                    files=[],
                    error=str(exc),
                )
            input_dir = self._run_input(run_id)
            selected = {
                value if str(value).startswith("md5:") else f"md5:{value}"
                for value in (str(item).strip() for item in (file_ids or []))
                if value
            }
            results = []
            seen = set()
            passed = True
            error = None
            tested_supported = 0
            for path in sorted(input_dir.rglob("*")):
                if not path.is_file():
                    continue
                file_id = self._file_md5(path)
                if selected and file_id not in selected:
                    continue
                seen.add(file_id)
                file_type = "pdf" if path.suffix.lower() == ".pdf" else "excel"
                if path.suffix.lower() not in {".pdf", ".xlsx", ".xlsm", ".xls"}:
                    results.append({
                        "file_id": file_id,
                        "name": path.name,
                        "passed": True,
                        "skipped": True,
                        "reason": "非 Router 支持格式",
                    })
                    continue
                tested_supported += 1
                try:
                    route_rules = (
                        snapshot.pdf_rules
                        if file_type == "pdf"
                        else snapshot.excel_rules
                    )
                    result = read_rows(str(path), route_rules=route_rules)
                    route = dict(result.route_info)
                    item_passed = route.get("decision") not in {"unmatched", "ambiguous"}
                    results.append({
                        "file_id": file_id,
                        "name": path.name,
                        "passed": item_passed,
                        "decision": route.get("decision", ""),
                        "fingerprint_id": route.get("fingerprint_id", ""),
                        "reader_id": route.get("reader_id", ""),
                    })
                    passed = passed and item_passed
                except Exception as exc:
                    passed = False
                    results.append({
                        "file_id": file_id,
                        "name": path.name,
                        "passed": False,
                        "error": str(exc),
                    })
            missing = selected - seen
            if missing:
                passed = False
                error = f"file_ids 不存在：{sorted(missing)}"
            if not results:
                passed = False
                error = error or "没有可测试的文件"
            elif not tested_supported:
                passed = False
                error = error or "没有 Router 支持格式的测试文件"

            self._write_meta({
                "status": TESTED if passed else DRAFT,
                "test": {"run_id": run_id, "file_ids": sorted(selected), "results": results},
            })
            return RuleTestResult(
                run_id=run_id,
                passed=passed,
                files=results,
                error=error,
            )

    def publish_draft(self) -> RuleVersion:
        with _DRAFT_LOCK:
            self._require_draft()
            if self._meta().get("status") != TESTED:
                raise RuntimeError("草稿必须先通过真实文件测试")
            content = self.draft_path.read_text(encoding="utf-8")
            snapshot = build_routing_rules_snapshot(content)
            previous = self._ensure_version(self.production_path.read_text(encoding="utf-8"))
            version = self._ensure_version(content)
            self._atomic_write(self.production_path, content)
            if self.production_path == routing_rules_path().resolve():
                activate_routing_rules_snapshot(snapshot)
            self.draft_path.unlink()
            self.draft_meta_path.unlink(missing_ok=True)
            return RuleVersion(version=version, based_on=previous)

    def rollback(self, target_version: str) -> RuleVersion:
        with _DRAFT_LOCK:
            target = self._version_path(target_version)
            if not target.is_file():
                raise FileNotFoundError(f"规则版本不存在：{target_version}")
            content = target.read_text(encoding="utf-8")
            previous = self._ensure_version(self.production_path.read_text(encoding="utf-8"))
            rollback_content = (
                f"# rolled_back_from: {target_version}\n"
                f"# rollback_id: {uuid.uuid4().hex}\n"
                f"{content}"
            )
            snapshot = build_routing_rules_snapshot(rollback_content)
            version = self._ensure_version(rollback_content)
            self._atomic_write(self.production_path, rollback_content)
            if self.production_path == routing_rules_path().resolve():
                activate_routing_rules_snapshot(snapshot)
            return RuleVersion(version=version, based_on=previous)

    def _run_input(self, run_id: str) -> Path:
        if not run_id or Path(run_id).name != run_id:
            raise ValueError("非法 run_id")
        run_dir = (self.run_root / run_id).resolve()
        input_dir = run_dir / "input"
        if self.run_root not in run_dir.parents or not input_dir.is_dir():
            raise FileNotFoundError(f"Run 输入快照不存在：{run_id}")
        return input_dir

    def _draft(self) -> RuleDraft:
        path = self._require_draft()
        meta = self._meta()
        return RuleDraft(
            content=path.read_text(encoding="utf-8"),
            tested=meta.get("status") == TESTED,
        )

    def _require_draft(self) -> Path:
        if not self.draft_path.is_file():
            raise FileNotFoundError("当前不存在草稿")
        return self.draft_path

    def _meta(self) -> dict[str, Any]:
        if not self.draft_meta_path.is_file():
            raise FileNotFoundError("草稿元数据不存在")
        return json.loads(self.draft_meta_path.read_text(encoding="utf-8"))

    def _write_meta(self, data: dict[str, Any]) -> None:
        self._atomic_write(
            self.draft_meta_path,
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        )

    def _version_path(self, version: str) -> Path:
        if not version.startswith("sha256-") or not version[7:].isalnum():
            raise ValueError("非法规则版本")
        return self.version_dir / f"{version}.yaml"

    def _ensure_version(self, content: str) -> str:
        version = routing_rules_version(content)
        path = self._version_path(version)
        if not path.exists():
            self._atomic_write(path, content)
        return version

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _file_md5(path: Path) -> str:
        import hashlib

        digest = hashlib.md5()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return "md5:" + digest.hexdigest()
