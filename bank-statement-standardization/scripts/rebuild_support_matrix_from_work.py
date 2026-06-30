"""从已完成的 _support_matrix_work 目录重建支持矩阵。

用于全量审计已经完成但最终写 support_matrix.xlsx 失败的场景，例如 Excel
正在占用目标文件。
"""

import argparse
import hashlib
import importlib.util
import json
import platform
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
CORE_PACKAGE = REPO_ROOT / "ymb-standardization-core"
if str(CORE_PACKAGE) not in sys.path:
    sys.path.insert(0, str(CORE_PACKAGE))

AUDIT_SCRIPT = ROOT / "scripts" / "audit_testdata_support.py"
spec = importlib.util.spec_from_file_location("audit_testdata_support", AUDIT_SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def _record_from_artifacts(path, root, mapping_path, csv_path, today):
    rel = path.relative_to(root).as_posix()
    metadata_check = audit.metadata_checks(path)
    with open(mapping_path, encoding="utf-8") as f:
        mapping = json.load(f)
    image = mapping.get("文件画像", {})
    summary = audit.read_csv_summary(csv_path)
    parser = image.get("parser") or image.get("命中模板") or ""
    fingerprint_id = audit.support_matrix_fingerprint_id(parser)
    template = audit.template_from_mapping(image)
    bank_name = audit.support_matrix_bank_name(image, parser, template)
    record = {
        "银行": bank_name,
        "账户类型(YAML)": audit.yaml_account_type(parser),
        "格式": path.suffix.lower().lstrip("."),
        "版本": template,
        "文件路径": rel,
        "router类": fingerprint_id,
        "YAML指纹": audit.yaml_fingerprint_summary(parser),
        "测试类": audit.test_class_for_parser(parser),
        "测试日期": today,
        "测试结果": "PASS",
        "期望行数": str(summary["row_count"]),
        "本方户名": image.get("本方名称") or "",
        "本方账号": image.get("本方账户") or "",
        "创建时间≈修改时间": metadata_check["创建时间≈修改时间"],
        "创建人=修改人": metadata_check["创建人=修改人"],
        "备注": "开户行仅由文件名推断，支持矩阵未采信" if image.get("开户行识别来源") == "文件名" else "",
    }
    baseline = {
        "file_path": rel,
        "sha256": audit.file_sha256(path),
        "status": "PASS",
        "error": "",
        "mapping": {
            "bank": record["银行"],
            "yaml_account_type": record["账户类型(YAML)"],
            "parser": parser,
            "fingerprint_id": fingerprint_id,
            "template": record["版本"],
            "yaml_fingerprint": record["YAML指纹"],
            "created_modified_check": record["创建时间≈修改时间"],
            "creator_modifier_check": record["创建人=修改人"],
            "account_name": record["本方户名"],
            "account_no": record["本方账号"],
        },
        "csv_summary": summary,
    }
    return record, baseline


def rebuild(testdata_root, work_root, output_dir):
    testdata_root = Path(testdata_root)
    work_root = Path(work_root)
    output_dir = Path(output_dir)
    today = datetime.now().strftime("%Y-%m-%d")
    records = []
    baselines = []
    rechecked = 0

    for path in audit.iter_statement_files(testdata_root):
        work = work_root / hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:16]
        mapping_files = list(work.glob("*__mapping.json"))
        csv_files = list(work.glob("*__standardized.csv"))
        if mapping_files and csv_files:
            record, baseline = _record_from_artifacts(path, testdata_root, mapping_files[0], csv_files[0], today)
        else:
            rechecked += 1
            record, baseline = audit.audit_one_file(path, testdata_root, work_root, today)
        records.append(record)
        baselines.append(baseline)

    support_xlsx = output_dir / "support_matrix.xlsx"
    try:
        audit.write_xlsx(support_xlsx, records)
        written_xlsx = support_xlsx
    except PermissionError:
        written_xlsx = output_dir / "support_matrix.generated.xlsx"
        audit.write_xlsx(written_xlsx, records)

    baseline_json = output_dir / "baseline_summary.json"
    audit.write_json(baseline_json, {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "git_sha": audit.git_sha(),
        "python": sys.version,
        "platform": platform.platform(),
        "testdata_root": str(testdata_root),
        "file_count": len(records),
        "pass_count": sum(1 for row in records if row["测试结果"] == "PASS"),
        "fail_count": sum(1 for row in records if row["测试结果"] != "PASS"),
        "records": baselines,
    })
    return written_xlsx, baseline_json, records, rechecked


def main(argv=None):
    parser = argparse.ArgumentParser(description="从 _support_matrix_work 重建 support_matrix.xlsx")
    parser.add_argument("--testdata-root", default=str(ROOT / "testdata"))
    parser.add_argument("--work-root", required=True)
    parser.add_argument("--output-dir", default=str(ROOT / "testdata"))
    args = parser.parse_args(argv)
    support_xlsx, baseline_json, records, rechecked = rebuild(args.testdata_root, args.work_root, args.output_dir)
    print(f"support_matrix_xlsx={support_xlsx}")
    print(f"baseline_summary_json={baseline_json}")
    print(f"file_count={len(records)}")
    print(f"pass_count={sum(1 for row in records if row['测试结果'] == 'PASS')}")
    print(f"fail_count={sum(1 for row in records if row['测试结果'] != 'PASS')}")
    print(f"rechecked_count={rechecked}")


if __name__ == "__main__":
    main()
