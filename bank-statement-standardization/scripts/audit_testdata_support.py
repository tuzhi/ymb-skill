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
import re
import shutil
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
import yaml


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
CORE_PACKAGE = REPO_ROOT / "packages" / "ymb_standardization_core"
if str(CORE_PACKAGE) not in sys.path:
    sys.path.insert(0, str(CORE_PACKAGE))

from ymb_standardization_core import core as standardize_core  # noqa: E402


SUPPORTED_EXTENSIONS = {".xlsx", ".xlsm", ".xls", ".pdf"}
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
    "YAML指纹",
    "测试类",
    "测试日期",
    "测试结果",
    "期望行数",
    "本方户名",
    "本方账号",
    "创建时间≈修改时间",
    "创建人=修改人",
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


def _format_delta(seconds):
    seconds = int(abs(seconds))
    if seconds < 60:
        return f"{seconds}秒"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}分{sec}秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}小时{minutes}分"


def _metadata_time_check(created, modified, tolerance_seconds=300):
    if not created or not modified:
        return "不适用（缺少创建/修改时间）"
    if created.tzinfo is not None:
        created = created.astimezone(timezone.utc).replace(tzinfo=None)
    if modified.tzinfo is not None:
        modified = modified.astimezone(timezone.utc).replace(tzinfo=None)
    delta = abs((modified - created).total_seconds())
    result = "是" if delta <= tolerance_seconds else "否"
    return f"{result}（差{_format_delta(delta)}）"


def _metadata_author_check(creator, modified_by):
    creator = str(creator or "").strip()
    modified_by = str(modified_by or "").strip()
    if not creator or not modified_by:
        return "是（缺少创建人/修改人，按一致处理）"
    result = "是" if creator == modified_by else "否"
    return f"{result}（创建人:{creator}；修改人:{modified_by}）"


def _parse_pdf_datetime(value):
    text = str(value or "").strip()
    if text.startswith("D:"):
        text = text[2:]
    match = re.match(r"(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?", text)
    if not match:
        return None
    parts = [int(part) if part else default for part, default in zip(match.groups(), [1, 1, 1, 0, 0, 0])]
    try:
        return datetime(*parts)
    except ValueError:
        return None


def metadata_checks(path):
    """读取文档元数据，输出支持矩阵用的两项一致性核验。"""
    path = Path(path)
    ext = path.suffix.lower()
    try:
        if ext in {".xlsx", ".xlsm"}:
            import openpyxl

            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            props = wb.properties
            return {
                "创建时间≈修改时间": _metadata_time_check(props.created, props.modified),
                "创建人=修改人": _metadata_author_check(props.creator, props.lastModifiedBy),
            }
        if ext == ".xls":
            import xlrd

            book = xlrd.open_workbook(path, on_demand=True)
            creator = getattr(book, "user_name", "") or ""
            return {
                "创建时间≈修改时间": "是（XLS 未提供创建/修改时间，按一致处理）",
                "创建人=修改人": "是（XLS 仅提供创建人:%s，按一致处理）" % creator if creator else "是（XLS 未提供创建人/修改人，按一致处理）",
            }
        if ext == ".pdf":
            import pdfplumber

            with pdfplumber.open(path) as pdf:
                metadata = pdf.metadata or {}
            created = _parse_pdf_datetime(metadata.get("CreationDate"))
            modified = _parse_pdf_datetime(metadata.get("ModDate"))
            return {
                "创建时间≈修改时间": _metadata_time_check(created, modified),
                "创建人=修改人": "是（PDF 未提供创建人/修改人，按一致处理）",
            }
    except Exception as exc:
        msg = exc.__class__.__name__
        return {
            "创建时间≈修改时间": f"核验失败（{msg}）",
            "创建人=修改人": f"核验失败（{msg}）",
        }
    return {
        "创建时间≈修改时间": "不适用（不支持的格式）",
        "创建人=修改人": "不适用（不支持的格式）",
    }


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


def _load_route_rule_index():
    rules = {}
    for name in ("excel_rules.yaml", "pdf_rules.yaml"):
        path = CORE_PACKAGE / "ymb_standardization_core" / "parsers" / "routing" / name
        try:
            items = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        except Exception:
            items = []
        for item in items:
            parser = item.get("parser")
            if parser:
                rules[parser] = item
    return rules


ROUTE_RULE_INDEX = _load_route_rule_index()


def yaml_fingerprint_summary(parser):
    """返回支持矩阵中可读的 YAML 指纹配置摘要。"""
    if not parser:
        return ""
    if parser.startswith("generic_") or parser == "ambiguous_router_match":
        return "无 YAML 指纹"
    rule = ROUTE_RULE_INDEX.get(parser)
    if not rule:
        return "未找到 YAML 规则"

    parts = []
    identity_any = rule.get("identity", {}).get("any") or []
    layout_all = rule.get("layout", {}).get("all") or []
    fingerprint = rule.get("fingerprint") or {}
    metadata_all = fingerprint.get("metadata", {}).get("all") or {}
    style_all = fingerprint.get("style", {}).get("all") or []
    data_all = fingerprint.get("data", {}).get("all") or []
    date_any = fingerprint.get("date_format", {}).get("any") or []

    if identity_any:
        parts.append(f"身份:{len(identity_any)}")
    if layout_all:
        parts.append(f"结构:{len(layout_all)}")
    if metadata_all:
        parts.append("元数据:" + ",".join(metadata_all.keys()))
    if style_all:
        style_labels = [str(item.get("text", "")).strip() for item in style_all if isinstance(item, dict)]
        parts.append("样式:" + ",".join([label for label in style_labels if label][:3]))
    if data_all:
        parts.append(f"数据:{len(data_all)}")
    if date_any:
        parts.append("日期:" + ",".join(date_any))
    return "；".join(parts) if parts else "YAML 规则未配置指纹"


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
    metadata_check = metadata_checks(path)
    record = {
        "银行": "",
        "格式": path.suffix.lower().lstrip("."),
        "版本": "",
        "文件路径": relative_path,
        "router类": "",
        "YAML指纹": "",
        "测试类": "",
        "测试日期": today,
        "测试结果": "FAIL",
        "期望行数": "",
        "本方户名": "",
        "本方账号": "",
        "创建时间≈修改时间": metadata_check["创建时间≈修改时间"],
        "创建人=修改人": metadata_check["创建人=修改人"],
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
            "YAML指纹": yaml_fingerprint_summary(parser),
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
                "yaml_fingerprint": record["YAML指纹"],
                "created_modified_check": record["创建时间≈修改时间"],
                "creator_modifier_check": record["创建人=修改人"],
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
        "YAML指纹": 48,
        "测试类": 34,
        "测试日期": 14,
        "测试结果": 12,
        "期望行数": 12,
        "本方户名": 26,
        "本方账号": 26,
        "创建时间≈修改时间": 28,
        "创建人=修改人": 40,
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
