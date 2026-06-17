"""生成 testdata 支持矩阵和当前标准化基准。

输出文件默认写入 testdata 目录：
- support_matrix.xlsx：唯一维护的支持矩阵事实源
- baseline_summary.json：仅在 --write-baseline 时生成，用作逐文件标准化基准摘要
"""

import argparse
import csv
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
CORE_PACKAGE = REPO_ROOT / "packages" / "ymb_standardization_core"
if str(CORE_PACKAGE) not in sys.path:
    sys.path.insert(0, str(CORE_PACKAGE))

from ymb_standardization_core import core as standardize_core  # noqa: E402


SUPPORTED_EXTENSIONS = {".xlsx", ".xlsm", ".xls", ".csv", ".txt", ".tsv", ".pdf"}
GENERATED_NAME_MARKERS = (
    "__standardized",
    "__整合流水",
    "__打标流水",
    "__组合日余额",
    "__经营流水BI分析报告",
    "已清洗_待分析",
)
MATRIX_COLUMNS = [
    "银行",
    "格式",
    "版本",
    "文件路径",
    "router类",
    "测试类",
    "测试日期",
    "测试结果",
    "期望行数",
    "本方户名",
    "本方账号",
    "备注",
]


def iter_statement_files(root):
    """枚举原始流水候选文件，跳过管线产物和 Office 锁文件。"""
    root = Path(root)
    files = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        lower = name.lower()
        if "_support_matrix_work" in path.parts:
            continue
        if lower in {"support_matrix.xlsx", "support_matrix.csv", "support_matrix.md", "baseline_summary.json"}:
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if name.startswith("~$"):
            continue
        if any(marker.lower() in lower for marker in GENERATED_NAME_MARKERS):
            continue
        files.append(path)
    return sorted(files, key=lambda p: str(p.relative_to(root)).lower())


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def git_sha():
    try:
        return subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            text=True,
            encoding="utf-8",
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return ""


def test_class_for_parser(parser):
    mapping = {
        "jiangxi_rural_commercial_pdf_text": "test_jxrcb_pdf_route.py",
        "kasikorn_pdf_text": "test_kasikorn_pdf_route.py",
        "zhejiang_qyrcb_pdf_text": "test_zhejiang_qyrcb_pdf_route.py",
    }
    return mapping.get(parser or "", "")


def template_from_mapping(image):
    parser = image.get("parser") or image.get("命中模板") or ""
    evidence = image.get("route_evidence") or {}
    marker = evidence.get("bank_marker") or image.get("命中模板") or ""
    if parser:
        return marker or parser
    return marker


def normalize_bank_name(bank, parser="", template=""):
    """将映射结果里的简称或模板标记统一成支持矩阵里的银行全称。"""
    text = " ".join(str(part or "") for part in (bank, parser, template))
    rules = [
        ("zhejiang_qyrcb_pdf_text", "浙江庆元农商银行"),
        ("庆元农商银行", "浙江庆元农商银行"),
        ("jiangxi_rural_commercial_pdf_text", "江西农商银行"),
        ("江西·农商银行", "江西农商银行"),
        ("江西农商", "江西农商银行"),
        ("kasikorn_pdf_text", "开泰银行（Kasikorn Bank）"),
        ("Kasikorn", "开泰银行（Kasikorn Bank）"),
        ("农业银行", "中国农业银行"),
        ("农行", "中国农业银行"),
        ("工商银行", "中国工商银行"),
        ("工行", "中国工商银行"),
        ("建设银行", "中国建设银行"),
        ("建行", "中国建设银行"),
        ("邮储银行", "中国邮政储蓄银行"),
        ("邮政储蓄", "中国邮政储蓄银行"),
        ("招商银行", "招商银行"),
        ("浦发银行", "上海浦东发展银行"),
        ("长沙银行", "长沙银行"),
        ("三湘银行", "湖南三湘银行"),
        ("上饶银行", "上饶银行"),
        ("微信支付", "微信支付"),
        ("支付宝", "支付宝"),
    ]
    for needle, full_name in rules:
        if needle in text:
            return full_name
    return str(bank or "").strip() or "未识别"


def support_matrix_bank_name(image, parser="", template=""):
    source = image.get("开户行识别来源") or ""
    if source == "文件名":
        return "未识别"
    return normalize_bank_name(image.get("确认银行") or image.get("开户行") or "", parser, template)


def read_csv_summary(csv_path):
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    income_count = sum(1 for row in rows if str(row.get("收入金额", "")).strip())
    expense_count = sum(1 for row in rows if str(row.get("支出金额", "")).strip())
    first_time = rows[0].get("交易时间", "") if rows else ""
    last_time = rows[-1].get("交易时间", "") if rows else ""
    return {
        "row_count": len(rows),
        "income_count": income_count,
        "expense_count": expense_count,
        "first_time": first_time,
        "last_time": last_time,
        "columns": list(rows[0].keys()) if rows else [],
    }


def audit_one_file(path, root, output_work_dir, today):
    relative_path = path.relative_to(root).as_posix()
    record = {
        "银行": "",
        "格式": path.suffix.lower().lstrip("."),
        "版本": "",
        "文件路径": relative_path,
        "router类": "",
        "测试类": "",
        "测试日期": today,
        "测试结果": "FAIL",
        "期望行数": "",
        "本方户名": "",
        "本方账号": "",
        "备注": "",
    }
    baseline = {
        "file_path": relative_path,
        "sha256": file_sha256(path),
        "status": "FAIL",
        "error": "",
        "mapping": {},
        "csv_summary": {},
    }
    work = output_work_dir / hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:16]
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)

    try:
        csv_path, json_path, _report = standardize_core.standardize(str(path), out_dir=str(work))
        with open(json_path, encoding="utf-8") as f:
            mapping = json.load(f)
        image = mapping.get("文件画像", {})
        summary = read_csv_summary(csv_path)

        parser = image.get("parser") or image.get("命中模板") or ""
        template = template_from_mapping(image)
        bank_name = support_matrix_bank_name(image, parser, template)
        record.update({
            "银行": bank_name,
            "版本": template,
            "router类": parser,
            "测试类": test_class_for_parser(parser),
            "测试结果": "PASS",
            "期望行数": str(summary["row_count"]),
            "本方户名": image.get("本方名称") or "",
            "本方账号": image.get("本方账户") or "",
            "备注": "开户行仅由文件名推断，支持矩阵未采信" if image.get("开户行识别来源") == "文件名" else "",
        })
        baseline.update({
            "status": "PASS",
            "mapping": {
                "bank": record["银行"],
                "parser": parser,
                "template": record["版本"],
                "account_name": record["本方户名"],
                "account_no": record["本方账号"],
            },
            "csv_summary": summary,
        })
    except Exception as exc:
        msg = str(exc).strip() or exc.__class__.__name__
        record["备注"] = msg
        baseline["error"] = msg
        baseline["traceback"] = traceback.format_exc()
    return record, baseline


def write_xlsx(path, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "support_matrix"
    ws.append(MATRIX_COLUMNS)
    for row in rows:
        ws.append([row.get(col, "") for col in MATRIX_COLUMNS])

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    header_fill = PatternFill("solid", fgColor="D9EAF7")
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")

    widths = {
        "银行": 22,
        "格式": 10,
        "版本": 28,
        "文件路径": 72,
        "router类": 32,
        "测试类": 34,
        "测试日期": 14,
        "测试结果": 12,
        "期望行数": 12,
        "本方户名": 26,
        "本方账号": 26,
        "备注": 60,
    }
    for idx, col in enumerate(MATRIX_COLUMNS, 1):
        ws.column_dimensions[get_column_letter(idx)].width = widths.get(col, 16)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def write_json(path, payload):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def build_outputs(testdata_root, output_dir, write_baseline=False):
    testdata_root = Path(testdata_root)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    run_id = datetime.now().strftime("%Y%m%dT%H%M%S")
    work_root = output_dir / "_support_matrix_work" / run_id
    work_root.mkdir(parents=True, exist_ok=True)

    records = []
    baselines = []
    for path in iter_statement_files(testdata_root):
        record, baseline = audit_one_file(path, testdata_root, work_root, today)
        records.append(record)
        baselines.append(baseline)

    support_xlsx = output_dir / "support_matrix.xlsx"

    write_xlsx(support_xlsx, records)
    baseline_json = None
    if write_baseline:
        baseline_json = output_dir / "baseline_summary.json"
        write_json(baseline_json, {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "git_sha": git_sha(),
            "python": sys.version,
            "platform": platform.platform(),
            "testdata_root": str(testdata_root),
            "file_count": len(records),
            "pass_count": sum(1 for row in records if row["测试结果"] == "PASS"),
            "fail_count": sum(1 for row in records if row["测试结果"] != "PASS"),
            "records": baselines,
        })
    return support_xlsx, baseline_json


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成 testdata 支持矩阵")
    parser.add_argument("--testdata-root", default=str(ROOT / "testdata"))
    parser.add_argument("--output-dir", default=str(ROOT / "testdata"))
    parser.add_argument("--write-baseline", action="store_true", help="同时生成 baseline_summary.json 回归基准")
    args = parser.parse_args(argv)
    support_xlsx, baseline_json = build_outputs(args.testdata_root, args.output_dir, write_baseline=args.write_baseline)
    print(f"support_matrix_xlsx={support_xlsx}")
    if baseline_json:
        print(f"baseline_summary_json={baseline_json}")


if __name__ == "__main__":
    main()
