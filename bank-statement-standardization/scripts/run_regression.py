#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run small bank-statement regression suites and compare stable metrics."""

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

import pandas as pd
import yaml


SKILL_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SKILL_ROOT.parent
CORE_PACKAGE = REPO_ROOT / "ymb-standardization-core"
if str(CORE_PACKAGE) not in sys.path:
    sys.path.insert(0, str(CORE_PACKAGE))

from ymb_standardization_core import core  # noqa: E402


DEFAULT_CONFIG = SKILL_ROOT / "regression" / "regression_cases.yaml"
DEFAULT_TESTDATA_ROOT = SKILL_ROOT / "testdata"
DEFAULT_RESULTS_ROOT = DEFAULT_TESTDATA_ROOT / "regression"

COMPARE_METRICS = [
    "parser",
    "decision",
    "bank",
    "account_type",
    "standardized_rows",
    "unique_transactions",
    "accounts",
    "names",
    "banks",
    "account_types",
    "income_total",
    "expense_total",
    "net_total",
    "balance_breaks",
]


def load_suite(config_path, suite):
    config_path = Path(config_path)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    suites = data.get("suites") or {}
    if suite not in suites:
        raise ValueError(f"unknown regression suite: {suite}")
    cases = suites[suite] or []
    for case in cases:
        if not case.get("id") or not case.get("file"):
            raise ValueError(f"regression case must include id and file: {case}")
    return cases


def compare_case(actual, expected):
    diffs = []
    actual_metrics = actual.get("metrics") or {}
    expected_metrics = expected.get("metrics") or {}
    for metric in COMPARE_METRICS:
        actual_value = actual_metrics.get(metric)
        expected_value = expected_metrics.get(metric)
        if actual_value != expected_value:
            diffs.append({
                "metric": metric,
                "expected": expected_value,
                "actual": actual_value,
            })
    if actual.get("status") != expected.get("status"):
        diffs.append({
            "metric": "status",
            "expected": expected.get("status"),
            "actual": actual.get("status"),
        })
    return diffs


def write_baseline(path, suite, cases):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "suite": suite,
        "generated_at": _now_utc(),
        "case_count": len(cases),
        "cases": cases,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_baseline(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_suite(config_path, suite, testdata_root, work_root=None):
    cases = load_suite(config_path, suite)
    testdata_root = Path(testdata_root)
    owns_work_root = work_root is None
    if owns_work_root:
        work_root = Path(tempfile.mkdtemp(prefix=f"regression-{suite}-"))
    else:
        work_root = Path(work_root)
        work_root.mkdir(parents=True, exist_ok=True)

    results = []
    try:
        for case in cases:
            results.append(run_case(case, testdata_root, work_root))
    finally:
        if owns_work_root:
            shutil.rmtree(work_root, ignore_errors=True)
    return results


def run_case(case, testdata_root, work_root):
    case_id = case["id"]
    source = Path(testdata_root) / case["file"]
    if not source.exists():
        return {
            "case_id": case_id,
            "file": case["file"],
            "status": "ERROR",
            "error": f"file not found: {case['file']}",
            "metrics": {},
        }

    out_dir = Path(work_root) / _safe_name(case_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        _kind, _preamble, _rows, route_info = core.read_rows(str(source))
        _csv_path, _json_path, report = core.standardize(str(source), out_dir=str(out_dir))
        df = pd.read_csv(_csv_path, dtype=str).fillna("")
        return {
            "case_id": case_id,
            "file": case["file"],
            "reason": case.get("reason", ""),
            "tags": case.get("tags", []),
            "status": "PASS",
            "metrics": _metrics(df, route_info),
        }
    except Exception as exc:
        return {
            "case_id": case_id,
            "file": case["file"],
            "reason": case.get("reason", ""),
            "tags": case.get("tags", []),
            "status": "ERROR",
            "error": str(exc),
            "metrics": {},
        }


def compare_suite(actual_cases, baseline):
    expected_by_id = {case["case_id"]: case for case in baseline.get("cases", [])}
    results = []
    for actual in actual_cases:
        expected = expected_by_id.get(actual["case_id"])
        if expected is None:
            results.append({
                "case_id": actual["case_id"],
                "status": "NEW",
                "diffs": [{"metric": "case_id", "expected": None, "actual": actual["case_id"]}],
            })
            continue
        diffs = compare_case(actual, expected)
        results.append({
            "case_id": actual["case_id"],
            "status": "PASS" if not diffs else "FAIL",
            "diffs": diffs,
        })
    return results


def write_run_report(path, suite, cases, comparison=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    failed = [item for item in (comparison or []) if item["status"] != "PASS"]
    payload = {
        "suite": suite,
        "generated_at": _now_utc(),
        "case_count": len(cases),
        "status": "PASS" if not failed and all(c["status"] == "PASS" for c in cases) else "FAIL",
        "cases": cases,
    }
    if comparison is not None:
        payload["comparison"] = comparison
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def default_baseline_path(results_root, suite):
    return Path(results_root) / "baselines" / f"{suite}.json"


def default_report_path(results_root, suite):
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    return Path(results_root) / "runs" / f"{suite}_{stamp}.json"


def _metrics(df, route_info):
    route = route_info or {}
    return {
        "parser": route.get("parser", ""),
        "decision": route.get("decision", ""),
        "bank": route.get("bank", ""),
        "account_type": route.get("account_type", ""),
        "standardized_rows": int(len(df)),
        "unique_transactions": int(df["交易唯一编号"].nunique()) if "交易唯一编号" in df else 0,
        "accounts": _unique_values(df, "本方账户"),
        "names": _unique_values(df, "本方名称"),
        "banks": _unique_values(df, "开户行"),
        "account_types": _unique_values(df, "账户类型"),
        "income_total": _sum_amount(df, "收入金额"),
        "expense_total": _sum_amount(df, "支出金额"),
        "net_total": _sum_amount(df, "交易金额"),
        "balance_breaks": _balance_breaks(df),
    }


def _unique_values(df, column):
    if column not in df:
        return []
    values = sorted({str(v).strip() for v in df[column].tolist() if str(v).strip()})
    return values[:20]


def _sum_amount(df, column):
    if column not in df:
        return "0.00"
    total = Decimal("0")
    for value in df[column].tolist():
        try:
            total += Decimal(str(value).replace(",", "").strip() or "0")
        except InvalidOperation:
            continue
    return str(total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _balance_breaks(df):
    required = {"账户余额", "收入金额", "支出金额"}
    if not required.issubset(set(df.columns)):
        return None
    breaks = 0
    prev_by_account = {}
    for _, row in df.iterrows():
        account = str(row.get("本方账户", "")).strip()
        balance = _decimal_or_none(row.get("账户余额", ""))
        income = _decimal_or_zero(row.get("收入金额", ""))
        expense = _decimal_or_zero(row.get("支出金额", ""))
        if balance is None:
            continue
        previous = prev_by_account.get(account)
        if previous is not None and abs(balance - (previous + income - expense)) >= Decimal("0.01"):
            breaks += 1
        prev_by_account[account] = balance
    return breaks


def _decimal_or_none(value):
    try:
        text = str(value).replace(",", "").strip()
        if not text:
            return None
        return Decimal(text)
    except InvalidOperation:
        return None


def _decimal_or_zero(value):
    return _decimal_or_none(value) or Decimal("0")


def _safe_name(value):
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value)


def _now_utc():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def main():
    ap = argparse.ArgumentParser(description="运行银行流水标准化回归集合")
    ap.add_argument("--suite", default="p0_smoke")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--testdata-root", default=str(DEFAULT_TESTDATA_ROOT))
    ap.add_argument("--results-root", default=str(DEFAULT_RESULTS_ROOT))
    ap.add_argument("--baseline")
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--report")
    args = ap.parse_args()

    cases = run_suite(args.config, args.suite, args.testdata_root)
    baseline_path = Path(args.baseline) if args.baseline else default_baseline_path(args.results_root, args.suite)
    report_path = Path(args.report) if args.report else default_report_path(args.results_root, args.suite)

    comparison = None
    if args.update_baseline or not baseline_path.exists():
        write_baseline(baseline_path, args.suite, cases)
    else:
        comparison = compare_suite(cases, load_baseline(baseline_path))

    write_run_report(report_path, args.suite, cases, comparison=comparison)
    status = "PASS"
    if comparison and any(item["status"] != "PASS" for item in comparison):
        status = "FAIL"
    if any(case["status"] != "PASS" for case in cases):
        status = "FAIL"
    print(json.dumps({
        "status": status,
        "suite": args.suite,
        "case_count": len(cases),
        "baseline": str(baseline_path),
        "report": str(report_path),
    }, ensure_ascii=False, indent=2))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
