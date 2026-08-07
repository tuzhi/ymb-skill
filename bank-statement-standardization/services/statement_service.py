"""Run 维度的流水标准化应用服务。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Mapping
import hashlib
import json
import mimetypes
import os
import shutil
import tempfile
import yaml

from runtime.failure_policy import MAX_PASSWORD_ATTEMPTS
from runtime import standardize as S
from runtime.models import PipelineExecutionResult
from runtime.runner import Runner, load_parent_run_context

from .models import (
    InputFile,
    RunDetail,
    RunReference,
    ServiceError,
    StandardizationRequest,
    StandardizationResult,
)
from ymb_standardization_core.readers.routing.rule_loader import RoutingRulesSnapshot


_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="statement-run")


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def _md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "md5:" + digest.hexdigest()


class StatementService:
    """使用显式规则快照同步执行标准化或草稿规则测试。"""

    def __init__(
        self,
        run_root: str | os.PathLike[str],
        submit: Callable[[Callable[[], PipelineExecutionResult]], Any] | None = None,
    ) -> None:
        self.run_root = Path(run_root).resolve()
        self.run_root.mkdir(parents=True, exist_ok=True)
        self._submit = submit or _EXECUTOR.submit
        self._active_runs: dict[str, Any] = {}

    def _start_run(
        self,
        client_name: str | None,
        files: Iterable[InputFile],
        parent_run_id: str | None = None,
        remove_file_ids: Iterable[str] | None = None,
        file_passwords: Mapping[str, str] | None = None,
        repair_result_snapshot: str | os.PathLike[str] | None = None,
        repair_result_sha256: str | None = None,
        routing_rules_snapshot: RoutingRulesSnapshot | None = None,
    ) -> RunReference:
        uploads = list(files or [])
        removed = list(remove_file_ids or [])
        if not parent_run_id and (not str(client_name or "").strip() or not uploads):
            raise ValueError("首次运行必须提供 client_name 和至少一个文件")
        if not parent_run_id and removed:
            raise ValueError("remove_file_ids 只能用于增量运行")
        if parent_run_id and client_name:
            raise ValueError("增量运行的 client_name 必须从父 Run 继承")
        if (file_passwords or repair_result_snapshot) and not parent_run_id:
            raise ValueError("密码或 Repair snapshot 只能用于显式 Child Run")

        staging = Path(tempfile.mkdtemp(prefix="statement-input-"))
        try:
            if parent_run_id:
                if self._get_run(parent_run_id).status == "RUNNING":
                    raise RuntimeError("RUNNING 状态的 Run 不能作为父运行")
                parent = load_parent_run_context(str(self.run_root), parent_run_id)
                if (
                    file_passwords
                    and int(parent.get("password_attempt") or 0) >= MAX_PASSWORD_ATTEMPTS
                ):
                    raise RuntimeError("密码尝试次数已达上限")
                parent_input = Path(parent["parent_run_dir"]) / "input"
                if not parent_input.is_dir():
                    raise RuntimeError(f"父 Run 缺少输入快照：{parent_run_id}")
                shutil.copytree(parent_input, staging, dirs_exist_ok=True)
                self._remove_files(staging, removed)

            changed = self._copy_uploads(staging, uploads)
            password_retry = self._write_file_passwords(staging, file_passwords or {})
            if not any(path.is_file() for path in staging.rglob("*")):
                raise ValueError("当前有效文件集合不能为空")

            args = SimpleNamespace(
                run_root=str(self.run_root),
                folder=str(staging),
                client=str(client_name or ""),
                client_arg_provided=bool(client_name),
                error_bundle_mode="full",
                parent_run_id=parent_run_id,
                rerun_reason=self._rerun_reason(
                    parent_run_id,
                    changed,
                    removed,
                    password_retry=password_retry,
                    ai_repair=bool(repair_result_snapshot),
                ),
                account_type=None,
                file_sleep_seconds=0,
                verbose=False,
                password_attempt_increment=1 if password_retry else 0,
                ai_repair_attempt_increment=1 if repair_result_snapshot else 0,
                repair_result_snapshot=str(repair_result_snapshot or ""),
                repair_result_sha256=str(repair_result_sha256 or ""),
                routing_rules_snapshot=routing_rules_snapshot,
            )
            runner = Runner(args)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        submitted = self._submit(runner.execute)
        if hasattr(submitted, "done"):
            self._active_runs[runner.run_id] = submitted
        return RunReference(
            run_id=runner.run_id,
            parent_run_id=parent_run_id or "",
        )

    def execute_standardization(
        self,
        request: StandardizationRequest,
        rules: RoutingRulesSnapshot,
    ) -> StandardizationResult:
        """使用指定规则快照，同步执行完整流水标准化。

        参数：
            request: 客户、输入文件、父 Run 和密码提示等任务参数。
            rules: 本次 Run 独占使用的不可变 Router 规则快照。

        返回：
            可由上层直接入库的 ``StandardizationResult``，包含文件结果、
            Stage 状态、QC、分析摘要、产物引用和结构化错误。

        异常：
            TypeError: 请求或规则快照类型不正确。
            FileNotFoundError: 输入文件或父 Run 不存在。
            ValueError: 参数、文件 MD5 或增量文件快照不合法。

        方法会创建独立 Run 目录并同步等待 Stage 1～4、QC 和 Validator
        完成。Run 启动后固定使用 ``rules``，不会在文件之间重新加载 YAML。
        """
        if not isinstance(request, StandardizationRequest):
            raise TypeError("request 必须是 StandardizationRequest")
        if not isinstance(rules, RoutingRulesSnapshot):
            raise TypeError("rules 必须是 RoutingRulesSnapshot")
        self._validate_input_files(request.files)
        reference = self._start_run(
            request.client_name,
            request.files,
            parent_run_id=request.parent_run_id,
            remove_file_ids=request.remove_file_ids,
            file_passwords=request.file_passwords,
            routing_rules_snapshot=rules,
        )
        active = self._active_runs.pop(reference.run_id, None)
        if active is None:
            raise RuntimeError("Runner 未返回可等待的执行句柄")
        execution = active.result()
        if not isinstance(execution, PipelineExecutionResult):
            raise TypeError("Runner 必须返回 PipelineExecutionResult")
        return StandardizationResult(
            run_id=execution.run_id,
            parent_run_id=execution.parent_run_id,
            client_name=execution.client_name,
            status=execution.status,
            rules_version=rules.version,
            file_results=self._execution_file_results(execution.file_results),
            stages=dict(execution.stages),
            qc=dict(execution.qc),
            stage_summaries=dict(execution.stage_summaries),
            artifacts=[dict(item) for item in execution.artifacts],
            run_result=dict(execution.run_result),
            error=self._execution_error(execution),
        )

    def _get_run(self, run_id: str) -> RunDetail:
        run_dir = self._run_dir(run_id)
        manifest = _read_json(run_dir / "manifest.json", {})
        stage_1_results = _read_json(run_dir / "stage_1_results.json", {"files": {}})
        qc = _read_json(run_dir / "qc_results.json", {})
        stages = {
            key: value
            for key, value in manifest.items()
            if str(key).startswith("stage_") and isinstance(value, dict)
        }
        files = []
        for path in sorted((run_dir / "input").rglob("*")):
            if not path.is_file():
                continue
            file_id = _md5(path)
            files.append({
                "file_id": file_id,
                "name": path.name,
                "relative_path": path.relative_to(run_dir / "input").as_posix(),
                "size": path.stat().st_size,
                "stage_1": (stage_1_results.get("files") or {}).get(file_id, {}),
            })

        error = self._error_summary(run_dir)
        return RunDetail(
            run_id=run_id,
            parent_run_id=str(manifest.get("parent_run_id") or ""),
            client_name=str(manifest.get("client") or ""),
            status=self._status(run_id, stages),
            files=files,
            stages=stages,
            stage_1_results=stage_1_results,
            qc=qc,
            artifacts=self._artifact_entries(run_dir),
            run_result=_read_json(run_dir / "run_result.json", {}),
            error=error,
        )

    def _run_dir(self, run_id: str) -> Path:
        if not run_id or Path(run_id).name != run_id:
            raise ValueError("非法 run_id")
        path = (self.run_root / run_id).resolve()
        if self.run_root not in path.parents or not path.is_dir():
            raise FileNotFoundError(f"Run 不存在：{run_id}")
        return path

    @staticmethod
    def _sha256_path(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _copy_uploads(staging: Path, uploads: list[InputFile]) -> bool:
        changed = False
        hash_to_path = {}
        for path in sorted(staging.rglob("*")):
            if not path.is_file():
                continue
            file_id = _md5(path)
            if file_id in hash_to_path:
                path.unlink()
            else:
                hash_to_path[file_id] = path
        for upload in uploads:
            if not isinstance(upload, InputFile):
                raise TypeError("files 必须包含 InputFile")
            filename = Path(upload.file_name).name
            source = Path(upload.file_path).resolve()
            if not filename or not source.is_file():
                raise ValueError("InputFile 必须包含有效的 file_name 和 file_path")
            target = staging / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            incoming_hash = _md5(source)
            same_content = hash_to_path.get(incoming_hash)
            if same_content is not None and same_content != target and not target.is_file():
                continue
            if target.is_file():
                previous_hash = _md5(target)
                if hash_to_path.get(previous_hash) == target:
                    hash_to_path.pop(previous_hash, None)
            if same_content is not None and same_content != target:
                same_content.unlink()
            shutil.copy2(source, target)
            hash_to_path[incoming_hash] = target
            changed = True
        return changed

    @staticmethod
    def _remove_files(staging: Path, file_ids: Iterable[str]) -> None:
        targets = {
            value if str(value).startswith("md5:") else f"md5:{value}"
            for value in (str(item).strip() for item in file_ids)
            if value
        }
        if not targets:
            return
        found = set()
        for path in staging.rglob("*"):
            if path.is_file() and _md5(path) in targets:
                found.add(_md5(path))
                path.unlink()
        missing = targets - found
        if missing:
            raise ValueError(f"remove_file_ids 不存在：{sorted(missing)}")

    @staticmethod
    def _rerun_reason(
        parent_run_id: str | None,
        changed: bool,
        remove_file_ids: Iterable[str] | None,
        *,
        password_retry: bool = False,
        ai_repair: bool = False,
    ) -> str:
        if not parent_run_id:
            return ""
        if password_retry:
            return "password_retry"
        if ai_repair:
            return "ai_repair_after_stage_1_failure"
        if changed or list(remove_file_ids or []):
            return "incremental_input_changed"
        return "resume_parent_run"

    @staticmethod
    def _write_file_passwords(staging: Path, passwords: Mapping[str, str]) -> bool:
        if not passwords:
            return False
        hints_path = staging / "_file_hints.yaml"
        payload: dict[str, Any] = {"file_info": {}}
        if hints_path.is_file():
            loaded = yaml.safe_load(hints_path.read_text(encoding="utf-8")) or {}
            if not isinstance(loaded, dict) or not isinstance(loaded.get("file_info", {}), dict):
                raise ValueError("现有 _file_hints.yaml 结构无效")
            payload = loaded
            payload.setdefault("file_info", {})
        for raw_relative, raw_password in passwords.items():
            relative = Path(str(raw_relative).replace("\\", "/"))
            password = str(raw_password)
            if relative.is_absolute() or ".." in relative.parts or not password:
                raise ValueError("密码提示必须使用有效的客户目录内相对路径和非空密码")
            target = (staging / relative).resolve()
            if staging.resolve() not in target.parents or not target.is_file():
                raise FileNotFoundError(f"密码对应文件不存在：{relative.as_posix()}")
            current = payload["file_info"].get(relative.as_posix()) or {}
            if not isinstance(current, dict):
                raise ValueError(f"文件 hints 结构无效：{relative.as_posix()}")
            payload["file_info"][relative.as_posix()] = {
                **current,
                "open_password": password,
            }
        descriptor, temporary = tempfile.mkstemp(prefix="._file_hints.", dir=staging)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                yaml.safe_dump(payload, stream, allow_unicode=True, sort_keys=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, hints_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return True

    def _status(self, run_id: str, stages: dict[str, Any]) -> str:
        active = self._active_runs.get(run_id)
        if active is not None and not active.done():
            return "RUNNING"
        statuses = [str(spec.get("status") or "") for spec in stages.values()]
        if "ERROR" in statuses:
            return "ERROR"
        if statuses and all(status == "DONE" for status in statuses):
            return "DONE"
        return "RUNNING"

    @staticmethod
    def _validate_input_files(files: Iterable[InputFile]) -> None:
        for item in files:
            if not isinstance(item, InputFile):
                raise TypeError("files 必须包含 InputFile")
            path = Path(item.file_path).resolve()
            if not path.is_file():
                raise FileNotFoundError(f"输入文件不存在：{path}")
            expected = str(item.file_md5 or "").strip()
            if expected:
                expected = expected if expected.startswith("md5:") else f"md5:{expected}"
                if _md5(path) != expected:
                    raise ValueError(f"输入文件 MD5 不一致：{item.file_name}")

    @staticmethod
    def _execution_file_results(
        stage_1_results: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        files = stage_1_results.get("files") or {}
        if not isinstance(files, Mapping):
            return []
        return [
            {
                "file_id": str(file_id),
                "name": str(record.get("name") or ""),
                "relative_path": str(record.get("relative_path") or ""),
                "stage_1": dict(record),
            }
            for file_id, record in sorted(files.items())
            if isinstance(record, Mapping)
        ]

    @staticmethod
    def _execution_error(
        execution: PipelineExecutionResult,
    ) -> ServiceError | None:
        if execution.status == "DONE" and not execution.error:
            return None
        run_result = execution.run_result or {}
        return ServiceError(
            code=str(run_result.get("reason_code") or "STANDARDIZATION_FAILED"),
            message=str(
                execution.error
                or run_result.get("message")
                or "流水标准化执行失败"
            ),
        )

    @staticmethod
    def _artifact_entries(run_dir: Path) -> list[dict[str, Any]]:
        candidates = []
        artifact_dir = run_dir / "artifacts"
        if artifact_dir.is_dir():
            candidates.extend(path for path in artifact_dir.rglob("*") if path.is_file())
        for name in (
            "run_result.json",
            "stage_1_results.json",
            "qc_results.json",
            "token_usage.json",
        ):
            path = run_dir / name
            if path.is_file():
                candidates.append(path)
        candidates.extend(path for path in run_dir.glob("*.zip") if path.is_file())
        entries = []
        for path in sorted(set(candidates)):
            artifact_id = path.relative_to(run_dir).as_posix()
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            entries.append({
                "artifact_id": artifact_id,
                "filename": path.name,
                "content_type": content_type,
                "size": path.stat().st_size,
            })
        return entries

    @staticmethod
    def _error_summary(run_dir: Path) -> str | None:
        traceback_path = run_dir / "traceback.txt"
        if traceback_path.is_file():
            lines = [line.strip() for line in traceback_path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines() if line.strip()]
            return lines[-1].replace(str(run_dir), "<run>") if lines else "运行失败"
        return None
