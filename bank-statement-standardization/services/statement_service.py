"""Run 维度的流水标准化应用服务。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable
import hashlib
import json
import mimetypes
import os
import shutil
import tempfile

from scripts.orchestrator import Runner, load_parent_run_context

from .models import ArtifactStream, RunDetail, RunReference


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
    """封装 Runner，只向调用方暴露 Run、详情、产物和清理。"""

    def __init__(
        self,
        run_root: str | os.PathLike[str],
        submit: Callable[[Callable[[], int]], Any] | None = None,
    ) -> None:
        self.run_root = Path(run_root).resolve()
        self.run_root.mkdir(parents=True, exist_ok=True)
        self._submit = submit or _EXECUTOR.submit
        self._active_runs: dict[str, Any] = {}

    def start_run(
        self,
        client_name: str | None,
        files: Iterable[Any],
        parent_run_id: str | None = None,
        remove_file_ids: Iterable[str] | None = None,
    ) -> RunReference:
        uploads = list(files or [])
        removed = list(remove_file_ids or [])
        if not parent_run_id and (not str(client_name or "").strip() or not uploads):
            raise ValueError("首次运行必须提供 client_name 和至少一个文件")
        if not parent_run_id and removed:
            raise ValueError("remove_file_ids 只能用于增量运行")
        if parent_run_id and client_name:
            raise ValueError("增量运行的 client_name 必须从父 Run 继承")

        staging = Path(tempfile.mkdtemp(prefix="statement-input-"))
        try:
            if parent_run_id:
                if self.get_run(parent_run_id).status == "RUNNING":
                    raise RuntimeError("RUNNING 状态的 Run 不能作为父运行")
                parent = load_parent_run_context(str(self.run_root), parent_run_id)
                parent_input = Path(parent["parent_run_dir"]) / "input"
                if not parent_input.is_dir():
                    raise RuntimeError(f"父 Run 缺少输入快照：{parent_run_id}")
                shutil.copytree(parent_input, staging, dirs_exist_ok=True)
                self._remove_files(staging, removed)

            changed = self._copy_uploads(staging, uploads)
            if not any(path.is_file() for path in staging.rglob("*")):
                raise ValueError("当前有效文件集合不能为空")

            args = SimpleNamespace(
                run_root=str(self.run_root),
                folder=str(staging),
                client=str(client_name or ""),
                client_arg_provided=bool(client_name),
                error_bundle_mode="full",
                parent_run_id=parent_run_id,
                rerun_reason=self._rerun_reason(parent_run_id, changed, removed),
                account_type=None,
                file_sleep_seconds=0,
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

    def get_run(self, run_id: str) -> RunDetail:
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

        fallback = _read_json(
            run_dir / "fallback" / "stage_1_standardize" / "fallback_request.json",
            {},
        )
        error = self._error_summary(run_dir, fallback)
        public_fallback = self._public_value(fallback, run_dir)
        return RunDetail(
            run_id=run_id,
            parent_run_id=str(manifest.get("parent_run_id") or ""),
            client_name=str(manifest.get("client") or ""),
            status=self._status(run_id, stages),
            files=files,
            stages=stages,
            stage_1_results=stage_1_results,
            qc=qc,
            analysis=self._analysis(run_dir),
            artifacts=self._artifact_entries(run_dir),
            fallback=public_fallback,
            error=error,
        )

    def get_artifact(self, run_id: str, artifact_id: str) -> ArtifactStream:
        run_dir = self._run_dir(run_id)
        entries = {item["artifact_id"]: item for item in self._artifact_entries(run_dir)}
        item = entries.get(artifact_id)
        if not item:
            raise FileNotFoundError(f"产物不存在：{artifact_id}")
        path = (run_dir / artifact_id).resolve()
        if run_dir not in path.parents or not path.is_file():
            raise FileNotFoundError(f"产物不存在：{artifact_id}")
        return ArtifactStream(
            artifact_id=artifact_id,
            filename=path.name,
            content_type=item["content_type"],
            size=path.stat().st_size,
            _path=path,
        )

    def delete_run(self, run_id: str) -> None:
        run_dir = self._run_dir(run_id)
        if self.get_run(run_id).status == "RUNNING":
            raise RuntimeError("RUNNING 状态的 Run 不允许删除")
        for child_dir in self.run_root.iterdir():
            if not child_dir.is_dir() or child_dir == run_dir:
                continue
            child_manifest = _read_json(child_dir / "manifest.json", {})
            if child_manifest.get("parent_run_id") == run_id:
                raise RuntimeError(f"Run 仍被子运行引用：{child_dir.name}")
        shutil.rmtree(run_dir)

    def _run_dir(self, run_id: str) -> Path:
        if not run_id or Path(run_id).name != run_id:
            raise ValueError("非法 run_id")
        path = (self.run_root / run_id).resolve()
        if self.run_root not in path.parents or not path.is_dir():
            raise FileNotFoundError(f"Run 不存在：{run_id}")
        return path

    @staticmethod
    def _copy_uploads(staging: Path, uploads: list[Any]) -> bool:
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
            filename, source, temporary = StatementService._upload_source(upload)
            try:
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
            finally:
                if temporary:
                    source.unlink(missing_ok=True)
        return changed

    @staticmethod
    def _upload_source(upload: Any) -> tuple[str, Path, bool]:
        if isinstance(upload, (str, os.PathLike)):
            source = Path(upload).resolve()
            filename = source.name
            temporary = False
        else:
            filename = Path(str(getattr(upload, "filename", "") or "")).name
            source_value = getattr(upload, "source_path", None)
            if source_value:
                source = Path(source_value).resolve()
                temporary = False
            else:
                file_object = getattr(upload, "file", None)
                if not filename or file_object is None:
                    source = None
                    temporary = False
                else:
                    suffix = Path(filename).suffix
                    descriptor, name = tempfile.mkstemp(prefix="statement-upload-", suffix=suffix)
                    try:
                        with os.fdopen(descriptor, "wb") as target:
                            try:
                                file_object.seek(0)
                            except (AttributeError, OSError):
                                pass
                            shutil.copyfileobj(file_object, target)
                        source = Path(name)
                        temporary = True
                    except Exception:
                        Path(name).unlink(missing_ok=True)
                        raise
        if not filename or source is None or not source.is_file():
            raise ValueError("上传文件必须是本地路径，或包含 filename/source_path/file")
        return filename, source, temporary

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
    ) -> str:
        if not parent_run_id:
            return ""
        if changed or list(remove_file_ids or []):
            return "incremental_input_changed"
        return "resume_parent_run"

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
    def _analysis(run_dir: Path) -> dict[str, Any]:
        receipts = {}
        receipt_dir = run_dir / "receipts"
        if receipt_dir.is_dir():
            for path in sorted(receipt_dir.glob("*.json")):
                data = _read_json(path, {})
                stage = str(data.get("stage") or "")
                if stage in {
                    "stage_2_integrate",
                    "stage_2b_portfolio_balance",
                    "stage_3_tag",
                    "stage_4_package",
                    "validate_final",
                }:
                    receipts[stage] = StatementService._public_value(
                        data.get("details", data),
                        run_dir,
                    )
        return receipts

    @staticmethod
    def _public_value(value: Any, run_dir: Path) -> Any:
        if isinstance(value, dict):
            return {
                key: StatementService._public_value(item, run_dir)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [StatementService._public_value(item, run_dir) for item in value]
        if isinstance(value, tuple):
            return [StatementService._public_value(item, run_dir) for item in value]
        if isinstance(value, str) and os.path.isabs(value):
            path = Path(value).resolve()
            if run_dir in path.parents:
                return path.relative_to(run_dir).as_posix()
            return path.name
        return value

    @staticmethod
    def _artifact_entries(run_dir: Path) -> list[dict[str, Any]]:
        candidates = []
        artifact_dir = run_dir / "artifacts"
        if artifact_dir.is_dir():
            candidates.extend(path for path in artifact_dir.rglob("*") if path.is_file())
        for name in ("stage_1_results.json", "qc_results.json"):
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
    def _error_summary(run_dir: Path, fallback: dict[str, Any]) -> str | None:
        if fallback.get("error"):
            return str(fallback["error"]).replace(str(run_dir), "<run>")
        traceback_path = run_dir / "traceback.txt"
        if traceback_path.is_file():
            lines = [line.strip() for line in traceback_path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines() if line.strip()]
            return lines[-1].replace(str(run_dir), "<run>") if lines else "运行失败"
        return None
