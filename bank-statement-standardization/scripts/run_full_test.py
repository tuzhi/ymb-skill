"""运行 testdata 全量测试，并把所有产物写入 testoutput/<timestamp>/。

P2 全量 test 目录规范：
- testdata/ 只作为输入样本目录。
- testoutput/YYYYMMDDHHMMSS/ 保存 support_matrix、baseline、客户交付物、日志和汇总。
- 产品级 *_已清洗_待分析.xlsx 也只进入本次 run 目录，不写回 testdata。
"""

import argparse
import csv
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import audit_testdata_support as audit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TESTDATA = ROOT / "testdata"
PACKAGE_SCRIPT = ROOT / "scripts" / "package_deliverable.py"

SKIP_CLIENT_DIRS = {
    ".git",
    "testoutput",
}


def timestamp():
    return datetime.now().strftime("%Y%m%d%H%M%S")


def create_run_dir(testdata_root, run_id=None, output_root=None):
    testdata_root = Path(testdata_root)
    base = Path(output_root) if output_root else testdata_root.parent / "testoutput"
    run_dir = base / (run_id or timestamp())
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def iter_client_dirs(testdata_root):
    testdata_root = Path(testdata_root)
    clients = []
    for path in testdata_root.iterdir():
        if not path.is_dir():
            continue
        name = path.name
        if name in SKIP_CLIENT_DIRS:
            continue
        if name.startswith("_"):
            continue
        if "已清洗_待分析" in name:
            continue
        clients.append(path)
    return sorted(clients, key=lambda p: p.name)


def run_support_matrix(testdata_root, run_dir):
    return audit.build_outputs(testdata_root, run_dir, write_baseline=True)


def _copy_deliverables(work_dir, run_dir, index):
    """把产品级交付物归档到本次 testoutput run 目录。"""
    copied = []
    for item in sorted(work_dir.glob("*_已清洗_待分析.xlsx")):
        target = run_dir / item.name
        if target.exists():
            target = run_dir / f"{index:03d}_{item.name}"
        shutil.copy2(item, target)
        copied.append(target.name)
    return copied


def package_one_client(index, client_dir, run_dir, temp_root):
    client = client_dir.name
    logs_dir = run_dir / "_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{index:03d}_{client}.log"
    work_dir = temp_root / f"{index:03d}_{client}"
    work_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        str(PACKAGE_SCRIPT),
        "--client",
        client,
        "--folder",
        str(client_dir),
        "--out-dir",
        str(work_dir),
    ]
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    log_path.write_text(proc.stdout, encoding="utf-8")

    copied = _copy_deliverables(work_dir, run_dir, index) if proc.returncode == 0 else []
    status = "PASS" if proc.returncode == 0 and copied else "FAIL"
    return {
        "client": client,
        "status": status,
        "file_count": len(copied),
        "files": ";".join(copied),
        "log": log_path.name,
        "returncode": "" if proc.returncode == 0 else proc.returncode,
    }


def write_summary_csv(run_dir, rows):
    summary = Path(run_dir) / "_summary.csv"
    fieldnames = ["client", "status", "file_count", "files", "log", "returncode"]
    with summary.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return summary


def run_package_deliverables(testdata_root, run_dir, temp_root=None):
    temp_root = Path(temp_root) if temp_root else run_dir / "_package_work"
    temp_root.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, client_dir in enumerate(iter_client_dirs(testdata_root), 1):
        row = package_one_client(index, client_dir, run_dir, temp_root)
        rows.append(row)
        print(f"{index:03d} {row['client']} {row['status']} {row['file_count']}", flush=True)
    return write_summary_csv(run_dir, rows), rows


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="运行 testdata 全量测试，support_matrix/baseline/已清洗_待分析等产物统一写入 testoutput/<timestamp>/"
    )
    parser.add_argument("--testdata-root", default=str(DEFAULT_TESTDATA))
    parser.add_argument("--output-root", help="默认是 testdata 同级 testoutput")
    parser.add_argument("--run-id", help="默认使用 YYYYMMDDHHMMSS")
    parser.add_argument("--skip-package", action="store_true", help="只生成 support_matrix 与 baseline，不生成 *_已清洗_待分析.xlsx")
    args = parser.parse_args(argv)

    testdata_root = Path(args.testdata_root)
    run_dir = create_run_dir(testdata_root, run_id=args.run_id, output_root=args.output_root)
    support_xlsx, baseline_json = run_support_matrix(testdata_root, run_dir)
    print(f"run_dir={run_dir}")
    print(f"support_matrix_xlsx={support_xlsx}")
    print(f"baseline_summary_json={baseline_json}")

    if not args.skip_package:
        summary_csv, rows = run_package_deliverables(testdata_root, run_dir)
        print(f"summary_csv={summary_csv}")
        print(f"package_pass_count={sum(1 for row in rows if row['status'] == 'PASS')}")
        print(f"package_fail_count={sum(1 for row in rows if row['status'] != 'PASS')}")


if __name__ == "__main__":
    main()
