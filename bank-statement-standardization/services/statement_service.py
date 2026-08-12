"""Run 维度的流水标准化应用服务。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Iterable
import hashlib
import os
import shutil
import tempfile

from runtime.failure_policy import DEFAULT_RETRY_POLICY
from runtime import result_store as RS
from runtime.models import PipelineExecutionResult
from runtime.runner import Runner, load_parent_run_context

from .models import (
    InputFile,
    StandardizationRequest,
    StandardizationResult,
)
from .result_mapper import build_standardization_result
from ymb_standardization_core.readers.routing.rule_loader import RoutingRulesSnapshot


def _md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "md5:" + digest.hexdigest()


class StatementService:
    """使用显式规则快照同步执行标准化或草稿规则测试。"""

    def __init__(self, run_root: str | os.PathLike[str]) -> None:
        self.run_root = Path(run_root).resolve()
        self.run_root.mkdir(parents=True, exist_ok=True)

    def _create_runner(
        self,
        request: StandardizationRequest,
        routing_rules_snapshot: RoutingRulesSnapshot | None = None,
        *,
        repair_result_snapshot: str | os.PathLike[str] | None = None,
        repair_result_sha256: str | None = None,
    ) -> Runner:
        """把公开请求转换为一次确定性 Runner；密码只保留在内存中。"""
        if not isinstance(request, StandardizationRequest):
            raise TypeError("request 必须是 StandardizationRequest")
        if routing_rules_snapshot is not None and not isinstance(
            routing_rules_snapshot,
            RoutingRulesSnapshot,
        ):
            raise TypeError("rules 必须是 RoutingRulesSnapshot")
        self._validate_input_files(request.files)
        uploads = list(request.files)
        removed = list(request.remove_file_ids)
        parent_run_id = request.parent_run_id
        if not parent_run_id and (
            not str(request.client_name or "").strip() or not uploads
        ):
            raise ValueError("首次运行必须提供 client_name 和至少一个文件")
        if not parent_run_id and removed:
            raise ValueError("remove_file_ids 只能用于增量运行")
        if parent_run_id and request.client_name:
            raise ValueError("增量运行的 client_name 必须从父 Run 继承")
        has_open_passwords = any(item.open_password is not None for item in uploads)
        if repair_result_snapshot and not parent_run_id:
            raise ValueError("Repair snapshot 只能用于显式 Child Run")

        staging = Path(tempfile.mkdtemp(prefix="statement-input-"))
        try:
            if parent_run_id:
                parent = load_parent_run_context(str(self.run_root), parent_run_id)
                parent_result = RS.load_pipeline_result(parent["parent_run_dir"])
                if parent_result.get("status") == "RUNNING":
                    raise RuntimeError("RUNNING 状态的 Run 不能作为父运行")
                if (
                    has_open_passwords
                    and int(parent.get("password_attempt") or 0)
                    >= DEFAULT_RETRY_POLICY.max_password_attempts
                ):
                    raise RuntimeError("密码尝试次数已达上限")
                parent_input = Path(parent["parent_run_dir"]) / "input"
                if not parent_input.is_dir():
                    raise RuntimeError(f"父 Run 缺少输入快照：{parent_run_id}")
                shutil.copytree(parent_input, staging, dirs_exist_ok=True)
                self._remove_files(staging, removed)

            changed = self._copy_uploads(staging, uploads)
            file_passwords = self._file_passwords_from_inputs(staging, uploads)
            password_retry = bool(file_passwords)
            if not any(path.is_file() for path in staging.rglob("*")):
                raise ValueError("当前有效文件集合不能为空")

            args = SimpleNamespace(
                run_root=str(self.run_root),
                folder=str(staging),
                client=str(request.client_name or ""),
                client_arg_provided=bool(request.client_name),
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
                file_passwords=file_passwords,
            )
            return Runner(args)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _execute_pipeline(
        self,
        request: StandardizationRequest,
        routing_rules_snapshot: RoutingRulesSnapshot | None = None,
        *,
        repair_result_snapshot: str | os.PathLike[str] | None = None,
        repair_result_sha256: str | None = None,
    ) -> PipelineExecutionResult:
        runner = self._create_runner(
            request,
            routing_rules_snapshot,
            repair_result_snapshot=repair_result_snapshot,
            repair_result_sha256=repair_result_sha256,
        )
        execution = runner.execute()
        if not isinstance(execution, PipelineExecutionResult):
            raise TypeError("Runner 必须返回 PipelineExecutionResult")
        return execution

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
        if not isinstance(rules, RoutingRulesSnapshot):
            raise TypeError("rules 必须是 RoutingRulesSnapshot")
        execution = self._execute_pipeline(request, rules)
        return build_standardization_result(execution, rules.version)

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
    def _file_passwords_from_inputs(
        staging: Path,
        files: Iterable[InputFile],
    ) -> dict[str, str]:
        """把文件级打开密码转换为 Reader 使用的相对路径映射。"""
        normalized = {}
        for item in files:
            if item.open_password is None:
                continue
            relative = Path(Path(item.file_name).name)
            password = str(item.open_password)
            if not password:
                raise ValueError(f"文件打开密码不能为空：{item.file_name}")
            target = (staging / relative).resolve()
            if staging.resolve() not in target.parents or not target.is_file():
                raise FileNotFoundError(f"密码对应文件不存在：{relative.as_posix()}")
            normalized[relative.as_posix()] = password
        return normalized

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
