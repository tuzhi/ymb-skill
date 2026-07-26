#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""流水线 QC 规则注册、检查点执行与结果持久化。"""

import json
import os
import tempfile

import pandas as pd


FILE = "FILE"
CUSTOMER = "CUSTOMER"
HARD = "HARD"
SOFT = "SOFT"

BEFORE_STAGE_1 = "BEFORE_STAGE_1"
AFTER_STAGE_1 = "AFTER_STAGE_1"
AFTER_STAGE_2 = "AFTER_STAGE_2"
AFTER_STAGE_2B = "AFTER_STAGE_2B"
AFTER_STAGE_3 = "AFTER_STAGE_3"
AFTER_STAGE_4 = "AFTER_STAGE_4"

RUNNING = "RUNNING"
BLOCKED = "BLOCKED"
PASS = "PASS"
PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"


def empty_results():
    return {"status": RUNNING, "files": {}, "customer": {}}


def load_results(path):
    if not os.path.isfile(path):
        return empty_results()
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise RuntimeError(f"QC 结果结构无效：{path}")
    data.setdefault("status", RUNNING)
    data.setdefault("files", {})
    data.setdefault("customer", {})
    return data


def atomic_write_json(path, data):
    """在目标目录原子替换 JSON，避免中断留下半个结果文件。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.",
        suffix=".tmp",
        dir=os.path.dirname(os.path.abspath(path)),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def check_file_openable(context):
    path = str(context.get("path") or "")
    if not path or not os.path.isfile(path):
        return {"passed": False, "message": f"文件不存在：{path}"}
    try:
        with open(path, "rb") as f:
            f.read(1)
    except OSError as exc:
        return {"passed": False, "message": f"文件无法读取：{exc}"}
    return {"passed": True, "message": ""}


def check_source_format_quality(context):
    message = str(context.get("source_format_error") or "").strip()
    return {"passed": not bool(message), "message": message}


def check_customer_coverage_two_years(context):
    paths = [str(path) for path in context.get("standardized_paths") or []]
    earliest = None
    latest = None
    unreadable = []
    for path in paths:
        try:
            chunks = pd.read_csv(
                path,
                dtype=str,
                usecols=["交易时间"],
                chunksize=100_000,
            )
            for frame in chunks:
                values = pd.to_datetime(frame["交易时间"], errors="coerce").dropna()
                if values.empty:
                    continue
                chunk_min = values.min()
                chunk_max = values.max()
                earliest = chunk_min if earliest is None else min(earliest, chunk_min)
                latest = chunk_max if latest is None else max(latest, chunk_max)
        except Exception as exc:
            unreadable.append(f"{os.path.basename(path)}：{exc}")
    if unreadable:
        return {
            "passed": False,
            "message": "覆盖周期无法完整计算：" + "；".join(unreadable),
        }
    if earliest is None or latest is None:
        return {"passed": False, "message": "全部有效文件没有可解析的交易时间"}
    passed = latest >= earliest + pd.DateOffset(years=2)
    return {
        "passed": bool(passed),
        "message": "" if passed else (
            f"全部有效文件覆盖不足两年：{earliest.date()} 至 {latest.date()}"
        ),
    }


QC_RULES = {
    "file.openable": {
        "scope": FILE,
        "checkpoint": BEFORE_STAGE_1,
        "level": HARD,
        "handler": check_file_openable,
    },
    "file.source_format_quality": {
        "scope": FILE,
        "checkpoint": AFTER_STAGE_1,
        "level": HARD,
        "handler": check_source_format_quality,
    },
    "customer.coverage_two_years": {
        "scope": CUSTOMER,
        "checkpoint": AFTER_STAGE_1,
        "level": SOFT,
        "handler": check_customer_coverage_two_years,
    },
}


def execute_checkpoint(results, scope, checkpoint, context, file_id=None, registry=None):
    """执行一个检查点的全部适用规则；异常转为失败结果，不中断其它规则。"""
    registry = QC_RULES if registry is None else registry
    if scope == FILE:
        if not file_id:
            raise RuntimeError("文件级 QC 缺少 file_id")
        bucket = results.setdefault("files", {}).setdefault(file_id, {})
    elif scope == CUSTOMER:
        bucket = results.setdefault("customer", {})
    else:
        raise RuntimeError(f"未知 QC scope：{scope}")

    for rule_id, spec in registry.items():
        if spec.get("scope") != scope or spec.get("checkpoint") != checkpoint:
            continue
        try:
            raw = spec["handler"](context) or {}
            passed = bool(raw.get("passed"))
            message = str(raw.get("message") or "")
        except Exception as exc:
            passed = False
            message = f"规则执行异常：{exc}"
        bucket[rule_id] = {
            "level": spec["level"],
            "passed": passed,
            "message": message,
        }
    return bucket


def iter_rule_results(results):
    for rules in (results.get("files") or {}).values():
        for value in (rules or {}).values():
            yield value
    for value in (results.get("customer") or {}).values():
        yield value


def has_hard_failure(results, file_id=None):
    if file_id is not None:
        values = (results.get("files") or {}).get(file_id, {}).values()
    else:
        values = iter_rule_results(results)
    return any(
        value.get("level") == HARD and not value.get("passed")
        for value in values
    )


def update_status(results, final=False):
    values = list(iter_rule_results(results))
    if any(value.get("level") == HARD and not value.get("passed") for value in values):
        results["status"] = BLOCKED
    elif final:
        results["status"] = (
            PASS_WITH_WARNINGS
            if any(value.get("level") == SOFT and not value.get("passed") for value in values)
            else PASS
        )
    else:
        results["status"] = RUNNING
    return results["status"]
