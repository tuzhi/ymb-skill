#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""银行流水标准化的薄命令行入口。"""

from __future__ import annotations

import argparse
import json
import os
import sys


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
if SKILL_DIR not in sys.path:
    sys.path.insert(0, SKILL_DIR)

from runtime.models import run_result as _run_result  # noqa: E402
from runtime import execution_plan as _execution_plan  # noqa: E402
from runtime import runner as _runner_runtime  # noqa: E402


def main(argv=None):
    _runner_runtime.configure_console()
    parser = argparse.ArgumentParser(description="银行流水标准化正式生产编排器")
    parser.add_argument("command", choices=["run"], help="正式执行流水线")
    parser.add_argument("--client", help="客户名称兼交付物归档名；未传时使用原始输入文件夹名称")
    parser.add_argument("--folder", required=True)
    parser.add_argument("--run-root", help="每次运行的独立归档目录，默认 ./runs")
    parser.add_argument("--account-type", choices=["对公", "个人", "未知"])
    parser.add_argument(
        "--file-sleep-seconds",
        type=float,
        default=0,
        help="阶段一相邻原始文件之间的暂停秒数；默认不暂停",
    )
    parser.add_argument("--parent-run-id", help="可选：AI 兜底修复后重跑时，记录关联的上一轮失败 run_id")
    parser.add_argument("--rerun-reason", help="可选：重跑原因，例如 ai_repair_after_stage_1_failure")
    parser.add_argument(
        "--error-bundle-mode",
        choices=["none", "full", "safe"],
        default="none",
        help="诊断包模式；默认 none 不生成，full 包含原始输入，safe 仅包含诊断信息",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="诊断模式：输出阶段事件并持久化 events/receipts",
    )
    parser.add_argument("--password-attempt-increment", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--ai-repair-attempt-increment", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--routing-rules-snapshot", help=argparse.SUPPRESS)
    parser.add_argument("--routing-rules-sha256", help=argparse.SUPPRESS)
    parser.add_argument("--run-id", help=argparse.SUPPRESS)
    parser.add_argument("--execution-plan-key", help=argparse.SUPPRESS)
    parser.add_argument("--attach-timeout-seconds", type=float, default=600, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    source, input_result = _runner_runtime.validate_input_source(args.folder)
    if input_result:
        print(json.dumps(input_result, ensure_ascii=False, separators=(",", ":")))
        return 0
    args.folder = source
    args.client_arg_provided = bool(args.client)
    if not args.client:
        args.client = os.path.basename(os.path.abspath(args.folder).rstrip(os.sep)) or "未命名客户"
    if not args.run_id:
        try:
            args.run_id, args.execution_plan_key = _execution_plan.load_or_create_execution_plan(
                args.folder,
                args.run_root,
            )
        except RuntimeError as exc:
            result = _runner_runtime.entry_result(
                _run_result.RunDecision(
                    _run_result.NextAction.REPORT_ERROR,
                    _run_result.ReasonCode.EXECUTION_PLAN_INVALID,
                    str(exc),
                ),
            )
            print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
            return 0
    run_dir, claimed = _execution_plan.claim_planned_run(args.run_root, args.run_id)
    if not claimed:
        result = _execution_plan.wait_for_run_result(run_dir, args.attach_timeout_seconds)
        if not result:
            result = {
                "run_id": args.run_id,
                "status": "RUNNING",
                "next_action": _run_result.NextAction.REPORT_ERROR,
                "reason_code": _run_result.ReasonCode.PIPELINE_ALREADY_RUNNING,
                "artifact_refs": [],
                "context_ref": "",
                "message": "同一执行计划仍在运行；未创建重复 Run",
                "contract_version": _run_result.CONTRACT_VERSION,
            }
        public = _runner_runtime.public_result(result, run_dir)
        print(json.dumps(public, ensure_ascii=False, separators=(",", ":")))
        return _runner_runtime.protocol_exit_status(public)

    runner = _runner_runtime.Runner(args)
    execution = runner.execute()
    result = execution.to_summary_dict()
    _execution_plan.release_execution_plan(
        args.run_root,
        args.execution_plan_key,
        runner.run_id,
    )
    result_run_dir = getattr(runner, "run_dir", None)
    if not result_run_dir:
        result_run_dir = os.path.dirname(runner.pipeline_result_path)
    public = _runner_runtime.public_result(
        result,
        result_run_dir,
        stage_results=execution.file_results,
        attempts=(getattr(runner, "pipeline_state", {}) or {}).get("attempts") or {},
    )
    print(json.dumps(public, ensure_ascii=False, separators=(",", ":")))
    return _runner_runtime.protocol_exit_status(public, execution.exit_code)


if __name__ == "__main__":
    sys.exit(main())
