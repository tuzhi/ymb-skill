"""ROUTING_RULE_DRAFT 的确定性授权门禁。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
import hashlib
import json

import yaml

from runtime.run_result import atomic_write_json
from services.yaml_rule_service import YamlRuleService
from ymb_standardization_core.readers.routing.rule_loader import fingerprint_md5, routing_rules_path


REQUIRED_RULE_FIELDS = {
    "file_type",
    "bank",
    "account_type",
    "fingerprint",
    "reader_id",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _merge_rule(production_content: str, payload: Mapping[str, Any]) -> str:
    rules = yaml.safe_load(production_content) or []
    if not isinstance(rules, list) or any(not isinstance(item, dict) for item in rules):
        raise ValueError("生产 routing_rules.yaml 顶层必须是规则数组")
    operation = str(payload.get("operation") or "append")
    rule = payload.get("rule")
    if not isinstance(rule, Mapping):
        raise ValueError("repair_payload.rule 必须是 object")
    rule = dict(rule)
    missing = sorted(REQUIRED_RULE_FIELDS - set(rule))
    if missing:
        raise ValueError(f"routing rule 缺少字段：{missing}")
    if rule.get("file_type") not in {"pdf", "excel"}:
        raise ValueError("routing rule.file_type 只允许 pdf 或 excel")
    expected_rule_id = fingerprint_md5(rule.get("fingerprint") or {})
    declared_rule_id = str(rule.get("id") or "")
    if declared_rule_id and declared_rule_id != expected_rule_id:
        raise ValueError("routing rule.id 必须为空或等于程序计算的 fingerprint MD5")
    rule["id"] = expected_rule_id
    rule_id = expected_rule_id
    indexes = [index for index, item in enumerate(rules) if str(item.get("id") or "") == rule_id]
    if operation == "append":
        if indexes:
            raise ValueError("append 不允许覆盖已有 rule id")
        rules.append(rule)
    elif operation == "replace":
        target_rule_id = str(payload.get("target_rule_id") or "")
        target_indexes = [
            index for index, item in enumerate(rules)
            if str(item.get("id") or "") == target_rule_id
        ]
        if len(target_indexes) != 1:
            raise ValueError("replace 必须唯一命中已有 rule id")
        if indexes and indexes != target_indexes:
            raise ValueError("replace 生成的新 rule id 与其他规则冲突")
        rules[target_indexes[0]] = rule
    else:
        raise ValueError("operation 只允许 append 或 replace")
    return yaml.safe_dump(rules, allow_unicode=True, sort_keys=False, width=120)


def evaluate_routing_draft(
    *,
    run_dir: str | Path,
    attempt_root: str | Path,
    fallback_request: Mapping[str, Any],
    fallback_result: Mapping[str, Any],
) -> dict[str, Any]:
    run_dir = Path(run_dir).resolve()
    attempt_root = Path(attempt_root).resolve()
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    request_ids = {
        str(item.get("file_id") or "")
        for item in fallback_request.get("files", [])
        if isinstance(item, Mapping) and item.get("file_id")
    }
    affected_ids = {
        str(item)
        for item in fallback_result.get("affected_file_ids", [])
        if str(item)
    }
    check("identity", fallback_result.get("run_id") == run_dir.name, "修复必须属于当前父 Run")
    check(
        "scope",
        bool(affected_ids) and affected_ids <= request_ids,
        "修复只能覆盖 fallback request 中的失败文件",
    )
    check(
        "repair_type",
        fallback_result.get("repair_type") == "ROUTING_RULE_DRAFT",
        "客户 Run 只接受 routing 草稿",
    )

    snapshot_path = attempt_root / "repair" / "routing_rules.yaml"
    test_result: dict[str, Any] = {}
    try:
        production_path = Path(routing_rules_path()).resolve()
        merged = _merge_rule(
            production_path.read_text(encoding="utf-8"),
            fallback_result.get("repair_payload") or {},
        )
        storage_root = attempt_root / "routing-gate"
        service = YamlRuleService(
            run_root=run_dir.parent,
            storage_root=storage_root,
            production_rules_path=production_path,
        )
        service.create_draft()
        service.save_draft(merged)
        result = service.test_draft(run_dir.name)
        affected_test_files = [
            item for item in result.files
            if str(item.get("file_id") or "") in affected_ids
        ]
        test_result = {
            "test_id": result.test_id,
            "passed": result.passed,
            "summary": result.summary,
            "tested_file_count": len(result.files),
            "affected_files": affected_test_files,
            "error": result.error,
        }
        check("syntax_and_target", result.passed, result.error or "草稿语法和 Run 输入路由通过")

        stage_results = _read_json(run_dir / "stage_1_results.json", {"files": {}})
        existing_files = stage_results.get("files") or {}
        changed_unaffected = []
        for item in result.files:
            file_id = str(item.get("file_id") or "")
            if not file_id or file_id in affected_ids or item.get("skipped"):
                continue
            previous = existing_files.get(file_id) or {}
            previous_route = previous.get("route") or {}
            previous_fingerprint = str(previous_route.get("fingerprint_id") or "")
            current_fingerprint = str(item.get("fingerprint_id") or "")
            if previous.get("status") == "DONE" and previous_fingerprint != current_fingerprint:
                changed_unaffected.append(file_id)
        check(
            "unaffected_regression",
            not changed_unaffected,
            "未影响其他已成功文件" if not changed_unaffected else f"误改文件：{changed_unaffected}",
        )
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(merged, encoding="utf-8")
    except Exception as exc:
        check("syntax_and_target", False, str(exc))

    accepted = all(item["passed"] for item in checks)
    output = {
        "contract_version": 1,
        "run_id": run_dir.name,
        "stage_id": "stage_1_standardize",
        "status": "ACCEPTED" if accepted else "REJECTED",
        "checks": checks,
        "routing_test": test_result,
        "snapshot_ref": (
            snapshot_path.relative_to(run_dir).as_posix()
            if accepted else ""
        ),
        "snapshot_sha256": _sha256(snapshot_path) if accepted else "",
    }
    atomic_write_json(attempt_root / "policy_gate.json", output)
    return output
