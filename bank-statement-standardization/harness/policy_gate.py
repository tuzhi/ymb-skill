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

from .protocols import render_protocol


REQUIRED_RULE_FIELDS = {
    "file_type",
    "bank",
    "account_type",
    "fingerprint",
    "reader_id",
}
ALLOWED_ACCOUNT_TYPES = {"个人", "对公", "未知"}
ALLOWED_FINGERPRINT_FIELDS = {
    "identity",
    "date_format",
    "columns",
    "metadata",
    "style",
}
ALLOWED_STYLE_FIELDS = {
    "text",
    "font",
    "size_min",
    "size_max",
    "bold",
    "row_max",
    "col_max",
    "top_max",
    "centered",
    "center_tolerance",
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


def _only_fields(value: Mapping[str, Any], allowed: set[str], name: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{name} 包含不支持字段：{unknown}")


def _validate_string_list(value: object, name: str) -> None:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip()
        for item in value
    ):
        raise ValueError(f"{name} 必须是非空字符串数组")


def _validate_mapping_keys(value: Mapping[str, Any], name: str) -> None:
    if any(not str(key).strip() for key in value):
        raise ValueError(f"{name} 不能包含空字段名")


def _validate_fingerprint(value: object) -> None:
    if not isinstance(value, Mapping) or not value:
        raise ValueError("routing rule.fingerprint 必须是非空 object")
    _only_fields(value, ALLOWED_FINGERPRINT_FIELDS, "fingerprint")

    identity = value.get("identity") or {}
    if not isinstance(identity, Mapping):
        raise ValueError("fingerprint.identity 必须是 object")
    _only_fields(identity, {"any"}, "fingerprint.identity")
    _validate_string_list(identity.get("any") or [], "fingerprint.identity.any")

    date_format = value.get("date_format") or {}
    if not isinstance(date_format, Mapping):
        raise ValueError("fingerprint.date_format 必须是 object")
    _only_fields(date_format, {"any"}, "fingerprint.date_format")
    _validate_string_list(date_format.get("any") or [], "fingerprint.date_format.any")

    columns = value.get("columns") or {}
    if not isinstance(columns, Mapping):
        raise ValueError("fingerprint.columns 必须是 object")
    _only_fields(columns, {"all", "optional"}, "fingerprint.columns")
    for key in ("all", "optional"):
        if not isinstance(columns.get(key) or {}, Mapping):
            raise ValueError(f"fingerprint.columns.{key} 必须是 object")
        _validate_mapping_keys(columns.get(key) or {}, f"fingerprint.columns.{key}")

    metadata = value.get("metadata") or {}
    if not isinstance(metadata, Mapping):
        raise ValueError("fingerprint.metadata 必须是 object")
    _only_fields(metadata, {"all"}, "fingerprint.metadata")
    if not isinstance(metadata.get("all") or {}, Mapping):
        raise ValueError("fingerprint.metadata.all 必须是 object")
    _validate_mapping_keys(metadata.get("all") or {}, "fingerprint.metadata.all")

    style = value.get("style") or {}
    if not isinstance(style, Mapping):
        raise ValueError("fingerprint.style 必须是 object")
    _only_fields(style, {"all"}, "fingerprint.style")
    style_rules = style.get("all") or []
    if not isinstance(style_rules, list) or any(not isinstance(item, Mapping) for item in style_rules):
        raise ValueError("fingerprint.style.all 必须是 object 数组")
    for item in style_rules:
        if not item:
            raise ValueError("fingerprint.style.all[] 不能为空")
        _only_fields(item, ALLOWED_STYLE_FIELDS, "fingerprint.style.all[]")

    has_matcher = bool(
        identity.get("any")
        or date_format.get("any")
        or columns.get("all")
        or metadata.get("all")
        or style_rules
    )
    if not has_matcher:
        raise ValueError("routing rule.fingerprint 至少需要一个稳定匹配条件")


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
    if rule.get("account_type") not in ALLOWED_ACCOUNT_TYPES:
        raise ValueError("routing rule.account_type 只允许个人、对公或未知")
    if not str(rule.get("bank") or "").strip():
        raise ValueError("routing rule.bank 不能为空")
    _validate_fingerprint(rule.get("fingerprint"))
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
    output = render_protocol("policy-gate", {
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
    })
    atomic_write_json(attempt_root / "policy_gate.json", output)
    return output
