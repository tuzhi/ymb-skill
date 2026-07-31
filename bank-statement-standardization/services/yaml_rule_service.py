"""统一 Router YAML 的草稿、测试、发布与回滚服务。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
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
        self.test_dir = self.storage_root / "tests"
        self.version_dir = self.storage_root / "versions"
        self.draft_path = self.draft_dir / "routing_rules.yaml"
        self.draft_meta_path = self.draft_dir / "metadata.json"
        self.draft_dir.mkdir(parents=True, exist_ok=True)
        self.test_dir.mkdir(parents=True, exist_ok=True)
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

    def test_draft(self, run_id: str) -> RuleTestResult:
        with _DRAFT_LOCK:
            content = self._require_draft().read_text(encoding="utf-8")
            draft_version = routing_rules_version(content)
            test_id = f"rule-test-{uuid.uuid4().hex}"
            try:
                snapshot = build_routing_rules_snapshot(content)
            except ValueError as exc:
                result = RuleTestResult(
                    run_id=run_id,
                    passed=False,
                    files=[],
                    error=str(exc),
                    test_id=test_id,
                    draft_version=draft_version,
                    summary=self._summarize_test_results([]),
                )
                self._record_test_result(result)
                return result
            input_dir = self._run_input(run_id)
            results = []
            passed = True
            error = None
            tested_supported = 0
            for path in sorted(input_dir.rglob("*")):
                if not path.is_file():
                    continue
                file_id = self._file_md5(path)
                relative_path = path.relative_to(input_dir).as_posix()
                file_type = "pdf" if path.suffix.lower() == ".pdf" else "excel"
                if path.suffix.lower() not in {".pdf", ".xlsx", ".xlsm", ".xls"}:
                    results.append({
                        "file_id": file_id,
                        "name": path.name,
                        "relative_path": relative_path,
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
                    decision = str(route.get("decision") or "")
                    item_passed = decision == "matched"
                    results.append({
                        "file_id": file_id,
                        "name": path.name,
                        "relative_path": relative_path,
                        "passed": item_passed,
                        "decision": decision,
                        "fingerprint_id": route.get("fingerprint_id", ""),
                        "reader_id": route.get("reader_id", ""),
                    })
                    passed = passed and item_passed
                except Exception as exc:
                    passed = False
                    results.append({
                        "file_id": file_id,
                        "name": path.name,
                        "relative_path": relative_path,
                        "passed": False,
                        "error": str(exc),
                    })
            if not results:
                passed = False
                error = error or "没有可测试的文件"
            elif not tested_supported:
                passed = False
                error = error or "没有 Router 支持格式的测试文件"

            result = RuleTestResult(
                run_id=run_id,
                passed=passed,
                files=results,
                error=error,
                test_id=test_id,
                draft_version=draft_version,
                summary=self._summarize_test_results(results),
            )
            self._record_test_result(result)
            return result

    def publish_draft(self) -> RuleVersion:
        with _DRAFT_LOCK:
            self._require_draft()
            meta = self._meta()
            if meta.get("status") != TESTED:
                raise RuntimeError("草稿必须先通过真实文件测试")
            content = self.draft_path.read_text(encoding="utf-8")
            current_version = routing_rules_version(content)
            tested_version = str((meta.get("test") or {}).get("draft_version") or "")
            if tested_version != current_version:
                raise RuntimeError("当前草稿内容与已通过测试的版本不一致")
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

    def _record_test_result(self, result: RuleTestResult) -> None:
        payload = {
            "test_id": result.test_id,
            "source_run_id": result.run_id,
            "draft_version": result.draft_version,
            "passed": result.passed,
            "summary": result.summary,
            "files": result.files,
            "error": result.error,
        }
        result_path = self.test_dir / result.test_id / "result.json"
        self._atomic_write(
            result_path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )
        self._write_meta({
            "status": TESTED if result.passed else DRAFT,
            "test": {
                "test_id": result.test_id,
                "source_run_id": result.run_id,
                "draft_version": result.draft_version,
                "passed": result.passed,
                "result_path": result_path.relative_to(self.storage_root).as_posix(),
            },
        })

    @staticmethod
    def _summarize_test_results(results: list[dict[str, Any]]) -> dict[str, int]:
        supported = [item for item in results if not item.get("skipped")]
        decisions = [str(item.get("decision") or "") for item in supported]
        return {
            "total": len(results),
            "supported": len(supported),
            "matched": decisions.count("matched"),
            "unmatched": decisions.count("unmatched"),
            "ambiguous": decisions.count("ambiguous"),
            "incomplete": decisions.count("matched_incomplete"),
            "errors": sum(1 for item in supported if item.get("error")),
            "skipped": sum(1 for item in results if item.get("skipped")),
            "failed": sum(1 for item in supported if not item.get("passed")),
        }

    def _draft(self) -> RuleDraft:
        path = self._require_draft()
        meta = self._meta()
        content = path.read_text(encoding="utf-8")
        tested_version = str((meta.get("test") or {}).get("draft_version") or "")
        return RuleDraft(
            content=content,
            tested=(
                meta.get("status") == TESTED
                and tested_version == routing_rules_version(content)
            ),
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
