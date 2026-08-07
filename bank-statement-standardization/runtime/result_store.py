"""流水线整体结果及阶段结果引用的原子持久化。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
import json
import os
import tempfile

PIPELINE_RESULT_FILENAME = "pipeline_result.json"
LEGACY_MANIFEST_FILENAME = "manifest.json"
LEGACY_RUN_RESULT_FILENAME = "run_result.json"
LEGACY_RUN_CONTEXT_FILENAME = "run_manifest.json"

RUN_RESULT_FIELDS = (
    "run_id",
    "status",
    "next_action",
    "reason_code",
    "context_ref",
    "message",
    "action",
    "summary",
)


def atomic_write_json(path: str | os.PathLike[str], value: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(dict(value), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path: str | os.PathLike[str], default: Any = None) -> Any:
    target = Path(path)
    if not target.is_file():
        return default
    with target.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def run_result_from_pipeline(pipeline_result: Mapping[str, Any]) -> dict[str, Any]:
    """从精简整体结果恢复 stdout/Coordinator 使用的 RunResult。"""
    nested = pipeline_result.get("run_result")
    if isinstance(nested, Mapping):
        return dict(nested)

    result = {
        key: pipeline_result[key]
        for key in RUN_RESULT_FIELDS
        if key in pipeline_result
    }
    result["contract_version"] = int(
        pipeline_result.get("schema_version") or 1
    )
    if not result.get("action"):
        result.pop("action", None)
    if not result.get("summary"):
        result.pop("summary", None)
    deliverables = pipeline_result.get("deliverables")
    artifact_refs = pipeline_result.get("artifact_refs")
    if isinstance(deliverables, list):
        result["artifact_refs"] = list(deliverables)
    elif isinstance(artifact_refs, list):
        result["artifact_refs"] = list(artifact_refs)
    else:
        result["artifact_refs"] = []
    return result


def load_pipeline_result(run_dir: str | os.PathLike[str]) -> dict[str, Any]:
    """读取新整体结果；旧 Run 只读适配为同一结构。"""
    root = Path(run_dir)
    current = read_json(root / PIPELINE_RESULT_FILENAME, None)
    if current is not None:
        if not isinstance(current, dict):
            raise ValueError(f"{PIPELINE_RESULT_FILENAME} 必须是 object")
        return current

    manifest = read_json(root / LEGACY_MANIFEST_FILENAME, {}) or {}
    run_result = read_json(root / LEGACY_RUN_RESULT_FILENAME, {}) or {}
    run_context = read_json(root / LEGACY_RUN_CONTEXT_FILENAME, {}) or {}
    if not manifest and not run_result and not run_context:
        raise FileNotFoundError(f"缺少文件：{root / PIPELINE_RESULT_FILENAME}")
    stages = {
        key: value
        for key, value in manifest.items()
        if str(key).startswith("stage_") and isinstance(value, dict)
    }
    compact_stages = {
        stage_id: {
            key: value
            for key, value in spec.items()
            if key in {"status", "duration_seconds", "reason_code"}
        }
        for stage_id, spec in stages.items()
    }
    result = {
        "schema_version": 1,
        "skill_version": str((manifest.get("skill") or {}).get("version") or ""),
        "run_id": str(run_result.get("run_id") or root.name),
        "client_name": str(manifest.get("client") or run_context.get("client") or ""),
        "parent_run_id": str(
            manifest.get("parent_run_id") or run_context.get("parent_run_id") or ""
        ),
        "status": str(run_result.get("status") or "RUNNING"),
        "rules_version": str(manifest.get("routing_rules_version") or ""),
        "rerun_reason": str(
            manifest.get("rerun_reason") or run_context.get("rerun_reason") or ""
        ),
        "attempts": {
            "password": int(
                manifest.get("password_attempt")
                or run_context.get("password_attempt")
                or 0
            ),
            "ai_repair": int(
                manifest.get("ai_repair_attempt")
                or run_context.get("ai_repair_attempt")
                or 0
            ),
        },
        "stages": compact_stages,
        "deliverables": list(run_result.get("artifact_refs") or [])
        if run_result.get("next_action") == "DELIVER"
        else [],
        "refs": {
            "stage_1": "stage_1_results.json",
            "qc": "qc_results.json",
        },
        "skipped_inputs": list(manifest.get("skipped_inputs") or []),
        "error": None,
        "legacy_source": True,
    }
    for key in RUN_RESULT_FIELDS:
        if key in run_result:
            result[key] = run_result[key]
    if run_result.get("next_action") != "DELIVER" and run_result.get("artifact_refs"):
        result["artifact_refs"] = list(run_result["artifact_refs"])
    return result
