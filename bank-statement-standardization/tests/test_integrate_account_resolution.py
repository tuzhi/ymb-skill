import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("integrate_account_resolution", ROOT / "scripts" / "integrate.py")
integrate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(integrate)
PACKAGE_SPEC = importlib.util.spec_from_file_location("package_deliverable_account_resolution", ROOT / "scripts" / "package_deliverable.py")
package_deliverable = importlib.util.module_from_spec(PACKAGE_SPEC)
PACKAGE_SPEC.loader.exec_module(package_deliverable)


NAME = "斑马（南昌）商业有限公司"


def transaction(source, account, time, balance, opponent="测试对手", opponent_account="90001"):
    income = "100.00" if balance in {"1100.00", "2100.00", "3100.00"} else ""
    expense = "" if income else "50.00"
    return {
        "交易唯一编号": f"old-{source}-{time}",
        "交易时间": time,
        "本方名称": NAME,
        "本方账户": account,
        "开户行": "招商银行青山湖支行" if source.endswith("xlsx") else "招商银行",
        "账户类型": "对公",
        "对手名称": opponent,
        "对手账户": opponent_account,
        "收入金额": income,
        "支出金额": expense,
        "交易金额": income or expense,
        "账户余额": balance,
        "银行备注": "",
        "账户方附言": "",
        "交易渠道": "",
        "来源文件名": source,
        "来源行号": "1",
        "__源标准化文件": f"{Path(source).stem}__standardized.csv",
        "__fileseq": 0,
    }


class BatchAccountResolutionTests(unittest.TestCase):
    def test_same_explicit_account_completes_missing_name_and_bank(self):
        rows = [
            transaction("known.pdf", "237019600000017553", "2025-01-01 10:00:00", "1100.00"),
            transaction("missing.xlsx", "237019600000017553", "2025-01-01 10:00:00", "1100.00"),
        ]
        rows[0]["开户行"] = "九江银行"
        rows[0]["__router_bank"] = "九江银行"
        rows[1]["本方名称"] = ""
        rows[1]["开户行"] = ""
        rows[1]["__router_bank"] = "未识别"

        completed, report = integrate.complete_metadata_by_explicit_account(pd.DataFrame(rows))

        self.assertEqual(set(completed["本方名称"]), {NAME})
        self.assertEqual(set(completed["开户行"]), {"九江银行"})
        self.assertEqual(report["补全文件数"], 1)
        self.assertEqual(report["补全明细"][0]["本方账户"], "237019600000017553")

    def test_two_unknown_sources_with_high_overlap_share_virtual_account(self):
        rows = []
        for idx, balance in enumerate(("1100.00", "1050.00", "1150.00"), start=1):
            xls = transaction(
                "srbank.xls", "未识别账户#xls", f"2025-01-0{idx} 10:00:00", balance,
                f"对手{idx}", f"1000{idx} 上饶银行",
            )
            xls["本方名称"] = ""
            xls["开户行"] = "上饶银行"
            xls["__router_bank"] = "上饶银行"
            xls["__inferred_bank"] = ""
            pdf = transaction(
                "srbank.pdf", "未识别账户#pdf", f"2025-01-0{idx} 10:00:00", balance,
                f"对手{idx}", f"1000{idx}",
            )
            pdf["本方名称"] = ""
            pdf["开户行"] = "上饶银行"
            pdf["__router_bank"] = "未识别"
            pdf["__inferred_bank"] = "上饶银行"
            rows.extend([xls, pdf])

        paired, report = integrate.pair_unknown_account_sources(pd.DataFrame(rows))

        accounts = set(paired["本方账户"])
        self.assertEqual(len(accounts), 1)
        self.assertTrue(next(iter(accounts)).startswith("批次虚拟账户#上饶银行#PAIR-"))
        self.assertEqual(report["已配对组数"], 1)
        detail = report["配对明细"][0]
        self.assertEqual(detail["batch_pair"], "上饶银行")
        self.assertEqual(detail["核心交易重合数"], 3)
        self.assertEqual(detail["核心交易重合率"], 1.0)
        self.assertEqual(detail["来源判断"]["srbank.pdf"]["router_bank"], "未识别")
        self.assertEqual(detail["来源判断"]["srbank.pdf"]["inferred_bank"], "上饶银行")

    def test_unknown_pair_overlap_counts_duplicate_core_transactions(self):
        rows = []
        for source in ("same.xls", "same.pdf"):
            for row_no in range(3):
                row = transaction(
                    source, f"未识别账户#{source}", "2025-01-01 10:00:00", "1100.00",
                    "同一对手", "10001",
                )
                row["来源行号"] = str(row_no + 1)
                row["本方名称"] = ""
                row["开户行"] = "上饶银行"
                rows.append(row)

        _paired, report = integrate.pair_unknown_account_sources(pd.DataFrame(rows))

        self.assertEqual(report["已配对组数"], 1)
        self.assertEqual(report["配对明细"][0]["核心交易重合数"], 3)
        self.assertEqual(report["配对明细"][0]["核心交易重合率"], 1.0)

    def test_unknown_pair_requires_counterparty_enhancement(self):
        rows = []
        for idx, balance in enumerate(("1100.00", "1050.00", "1150.00"), start=1):
            left = transaction(
                "left.xls", "未识别账户#left", f"2025-01-0{idx} 10:00:00", balance,
                f"甲方{idx}", f"1000{idx}",
            )
            right = transaction(
                "right.pdf", "未识别账户#right", f"2025-01-0{idx} 10:00:00", balance,
                f"乙方{idx}", f"2000{idx}",
            )
            left["本方名称"] = right["本方名称"] = ""
            left["开户行"] = right["开户行"] = "上饶银行"
            rows.extend([left, right])

        paired, report = integrate.pair_unknown_account_sources(pd.DataFrame(rows))

        self.assertEqual(report["已配对组数"], 0)
        self.assertEqual(paired["本方账户"].nunique(), 2)

    def test_unknown_pair_rejects_small_subset_of_large_source(self):
        rows = []
        for idx in range(100):
            row = transaction(
                "large.xls", "未识别账户#large", f"2025-01-{idx % 28 + 1:02d} 10:{idx:02d}:00",
                str(2000 + idx), f"对手{idx}", f"1000{idx}",
            )
            row["本方名称"] = ""
            row["开户行"] = "上饶银行"
            rows.append(row)
            if idx < 3:
                copy = dict(row)
                copy["来源文件名"] = "small.pdf"
                copy["本方账户"] = "未识别账户#small"
                copy["__源标准化文件"] = "small__standardized.csv"
                rows.append(copy)

        paired, report = integrate.pair_unknown_account_sources(pd.DataFrame(rows))

        self.assertEqual(report["已配对组数"], 0)
        self.assertEqual(paired["本方账户"].nunique(), 2)

    def test_single_explicit_account_resolves_unknown_source(self):
        rows = [
            transaction("known.xlsx", "791912215110008", "2025-01-01 10:00:00", "1100.00"),
            transaction("unknown.pdf", "未识别账户#unknown", "2025-02-01 10:00:00", "2050.00"),
        ]

        resolved, report = integrate.resolve_batch_accounts(pd.DataFrame(rows))

        self.assertEqual(set(resolved["本方账户"]), {"791912215110008"})
        self.assertEqual(report["已归并文件数"], 1)
        self.assertEqual(report["归并明细"][0]["归并方式"], "同主体同银行唯一明确账号")
        self.assertEqual(report["待复核明细"], [])

    def test_high_overlap_resolves_unknown_when_bank_metadata_is_missing(self):
        rows = []
        for idx, balance in enumerate(("1100.00", "1050.00", "1150.00"), start=1):
            known = transaction(
                "known.xlsx", "791912215110008", f"2025-01-0{idx} 10:00:00", balance,
                f"对手{idx}", f"1000{idx}",
            )
            unknown = transaction(
                "unknown.pdf", "未识别账户#unknown", f"2025-01-0{idx} 10:00:00", balance,
                f"对手{idx}", f"1000{idx}",
            )
            unknown["开户行"] = ""
            unknown["__router_bank"] = "未识别"
            rows.extend([known, unknown])

        resolved, report = integrate.resolve_batch_accounts(pd.DataFrame(rows))

        self.assertEqual(set(resolved["本方账户"]), {"791912215110008"})
        pdf = resolved[resolved["来源文件名"] == "unknown.pdf"]
        self.assertEqual(set(pdf["开户行"]), {"招商银行青山湖支行"})
        self.assertEqual(report["已归并文件数"], 1)
        self.assertEqual(report["归并明细"][0]["归并方式"], "核心交易高重合跨元数据唯一命中")
        self.assertEqual(report["归并明细"][0]["batch_pair"], "招商银行青山湖支行")

    def test_cross_metadata_overlap_counts_duplicate_transactions(self):
        rows = []
        for source, account, bank in (
            ("known.xlsx", "791912215110008", "招商银行"),
            ("unknown.pdf", "未识别账户#unknown", ""),
        ):
            for row_no in range(3):
                row = transaction(
                    source, account, "2025-01-01 10:00:00", "1100.00", "同一对手", "10001"
                )
                row["来源行号"] = str(row_no + 1)
                row["开户行"] = bank
                rows.append(row)

        _resolved, report = integrate.resolve_batch_accounts(pd.DataFrame(rows))

        self.assertEqual(report["已归并文件数"], 1)
        self.assertEqual(report["归并明细"][0]["重合交易数"], 3)

    def test_same_identity_multi_account_overlap_counts_duplicate_transactions(self):
        rows = [
            transaction("a.xlsx", "6222000000000001", "2025-02-01 10:00:00", "2100.00", "甲", "10001"),
        ]
        for source, account in (
            ("b.xlsx", "6222000000000002"),
            ("unknown.pdf", "未识别账户#unknown"),
        ):
            for row_no in range(3):
                row = transaction(
                    source, account, "2025-01-01 10:00:00", "1100.00", "同一对手", "20001"
                )
                row["来源行号"] = str(row_no + 1)
                rows.append(row)

        resolved, report = integrate.resolve_batch_accounts(pd.DataFrame(rows))

        pdf_accounts = set(resolved.loc[resolved["来源文件名"] == "unknown.pdf", "本方账户"])
        self.assertEqual(pdf_accounts, {"6222000000000002"})
        self.assertEqual(report["归并明细"][0]["重合交易数"], 3)

    def test_multiple_accounts_without_overlap_remain_unresolved(self):
        rows = [
            transaction("a.xlsx", "6222000000000001", "2025-01-01 10:00:00", "1100.00"),
            transaction("b.xlsx", "6222000000000002", "2025-01-02 10:00:00", "2100.00"),
            transaction("unknown.pdf", "未识别账户#unknown", "2025-03-01 10:00:00", "3050.00"),
        ]

        resolved, report = integrate.resolve_batch_accounts(pd.DataFrame(rows))

        self.assertIn("未识别账户#unknown", set(resolved["本方账户"]))
        self.assertEqual(report["已归并文件数"], 0)
        self.assertEqual(report["待复核文件数"], 1)
        self.assertEqual(report["待复核明细"][0]["候选账号"], ["6222000000000001", "6222000000000002"])

    def test_unknown_bank_is_not_valid_grouping_evidence(self):
        rows = [
            transaction("known.xlsx", "A-001", "2025-01-01 10:00:00", "1100.00"),
            transaction("unknown.pdf", "未识别账户#unknown", "2025-02-01 10:00:00", "2050.00"),
        ]
        for row in rows:
            row["开户行"] = "未识别"

        resolved, report = integrate.resolve_batch_accounts(pd.DataFrame(rows))

        self.assertIn("未识别账户#unknown", set(resolved["本方账户"]))
        self.assertEqual(report["已归并文件数"], 0)

    def test_multiple_accounts_use_two_transaction_overlaps(self):
        rows = [
            transaction("a.xlsx", "6222000000000001", "2025-01-01 10:00:00", "1100.00", "甲", "10001"),
            transaction("a.xlsx", "6222000000000001", "2025-01-02 10:00:00", "1050.00", "乙", "10002"),
            transaction("b.xlsx", "6222000000000002", "2025-02-01 10:00:00", "2100.00", "丙", "20001"),
            transaction("b.xlsx", "6222000000000002", "2025-02-02 10:00:00", "2050.00", "丁", "20002"),
            transaction("unknown.pdf", "未识别账户#unknown", "2025-02-01 10:00:00", "2100.00", "丙", "20001"),
            transaction("unknown.pdf", "未识别账户#unknown", "2025-02-02 10:00:00", "2050.00", "丁", "20002"),
        ]

        resolved, report = integrate.resolve_batch_accounts(pd.DataFrame(rows))

        pdf_accounts = set(resolved.loc[resolved["来源文件名"] == "unknown.pdf", "本方账户"])
        self.assertEqual(pdf_accounts, {"6222000000000002"})
        self.assertEqual(report["归并明细"][0]["归并方式"], "多笔交易重合唯一命中")
        self.assertEqual(report["归并明细"][0]["重合交易数"], 2)
        regenerated = integrate.regenerate_transaction_ids(resolved)
        report = integrate.finalize_account_resolution_report(regenerated, report)
        evidence = report["归并明细"][0]["证据交易唯一编号列表"]
        self.assertEqual(len(evidence), 2)
        self.assertTrue(all(item.startswith("TX-") for item in evidence))

    def test_different_regional_rural_banks_do_not_share_candidates(self):
        rows = [
            transaction("known.xlsx", "6222000000000001", "2025-01-01 10:00:00", "1100.00"),
            transaction("unknown.pdf", "未识别账户#unknown", "2025-02-01 10:00:00", "2050.00"),
        ]
        rows[0]["开户行"] = "浙江庆元农村商业银行"
        rows[1]["开户行"] = "江西赣昌农村商业银行"

        resolved, report = integrate.resolve_batch_accounts(pd.DataFrame(rows))

        self.assertIn("未识别账户#unknown", set(resolved["本方账户"]))
        self.assertEqual(report["已归并文件数"], 0)
        self.assertEqual(report["待复核文件数"], 1)

    def test_non_numeric_synthetic_account_is_not_explicit_evidence(self):
        rows = [
            transaction("known.xlsx", "微信支付#张三", "2025-01-01 10:00:00", "1100.00"),
            transaction("unknown.pdf", "未识别账户#unknown", "2025-02-01 10:00:00", "2050.00"),
        ]

        resolved, report = integrate.resolve_batch_accounts(pd.DataFrame(rows))

        self.assertIn("未识别账户#unknown", set(resolved["本方账户"]))
        self.assertEqual(report["已归并文件数"], 0)
        self.assertEqual(report["待复核文件数"], 1)

    def test_missing_counterparty_is_not_strong_overlap(self):
        rows = [
            transaction("a.xlsx", "6222000000000001", "2025-01-01 10:00:00", "1100.00", "", ""),
            transaction("a.xlsx", "6222000000000001", "2025-01-02 10:00:00", "1050.00", "", ""),
            transaction("b.xlsx", "6222000000000002", "2025-02-01 10:00:00", "2100.00", "丙", "20001"),
            transaction("b.xlsx", "6222000000000002", "2025-02-02 10:00:00", "2050.00", "丁", "20002"),
            transaction("unknown.pdf", "未识别账户#unknown", "2025-01-01 10:00:00", "1100.00", "", ""),
            transaction("unknown.pdf", "未识别账户#unknown", "2025-01-02 10:00:00", "1050.00", "", ""),
        ]

        resolved, report = integrate.resolve_batch_accounts(pd.DataFrame(rows))

        self.assertIn("未识别账户#unknown", set(resolved["本方账户"]))
        self.assertEqual(report["已归并文件数"], 0)
        self.assertEqual(report["待复核文件数"], 1)

    def test_regenerated_ids_isolate_same_basename_from_different_paths(self):
        rows = [
            transaction("same.pdf", "6222000000000001", "2025-01-01 10:00:00", "1100.00"),
            transaction("same.pdf", "6222000000000001", "2025-01-01 10:00:00", "1100.00"),
        ]
        rows[0]["__源标准化文件路径"] = "/batch/a/same__standardized.csv"
        rows[1]["__源标准化文件路径"] = "/batch/b/same__standardized.csv"

        regenerated = integrate.regenerate_transaction_ids(pd.DataFrame(rows))

        self.assertEqual(regenerated["交易唯一编号"].nunique(), 1)

    def test_integrate_resolves_before_cross_file_deduplication(self):
        known = [
            transaction("known.xlsx", "791912215110008", "2025-01-01 10:00:00", "1100.00", "甲", "10001"),
            transaction("known.xlsx", "791912215110008", "2025-01-02 10:00:00", "1050.00", "乙", "10002"),
        ]
        unknown = [
            transaction("unknown.pdf", "未识别账户#unknown", "2025-01-01 10:00:00", "1100.00", "甲", "10001"),
            transaction("unknown.pdf", "未识别账户#unknown", "2025-01-02 10:00:00", "1050.00", "乙", "10002"),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            pd.DataFrame(known).drop(columns=["__源标准化文件", "__fileseq"]).to_csv(
                work / "known__standardized.csv", index=False, encoding="utf-8-sig"
            )
            pd.DataFrame(unknown).drop(columns=["__源标准化文件", "__fileseq"]).to_csv(
                work / "unknown__standardized.csv", index=False, encoding="utf-8-sig"
            )

            out_csv, _out_json, report = integrate.integrate("测试客户", [str(work)], out_dir=str(work / "out"))
            output = pd.read_csv(out_csv, dtype=str)

        self.assertEqual(set(output["本方账户"]), {"791912215110008"})
        self.assertEqual(len(output), 2)
        self.assertEqual(report["客户整合概览"]["整合账户数"], 1)
        self.assertEqual(report["客户整合概览"]["跨文件去重笔数"], 2)
        self.assertEqual(report["批次内账号归并"]["已归并文件数"], 1)

    def test_integrate_pairs_two_unknown_sources_before_deduplication(self):
        rows_by_source = {"srbank.xls": [], "srbank.pdf": []}
        for idx, balance in enumerate(("1100.00", "1050.00", "1150.00"), start=1):
            for source in rows_by_source:
                row = transaction(
                    source, f"未识别账户#{Path(source).suffix}",
                    f"2025-01-0{idx} 10:00:00", balance, f"对手{idx}", f"1000{idx}",
                )
                row["本方名称"] = ""
                row["开户行"] = "上饶银行"
                rows_by_source[source].append(row)

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            for source, rows in rows_by_source.items():
                pd.DataFrame(rows).drop(columns=["__源标准化文件", "__fileseq"]).to_csv(
                    work / f"{source}__standardized.csv", index=False, encoding="utf-8-sig"
                )
            out_csv, _out_json, report = integrate.integrate("测试客户", [str(work)], out_dir=str(work / "out"))
            output = pd.read_csv(out_csv, dtype=str)

        self.assertEqual(len(output), 3)
        self.assertEqual(output["本方账户"].nunique(), 1)
        self.assertEqual(report["批次未知账户配对"]["已配对组数"], 1)
        self.assertEqual(report["客户整合概览"]["跨文件去重笔数"], 3)
        self.assertTrue({"router_bank", "inferred_bank", "batch_pair", "bank_source"}.issubset(output.columns))

    def test_zebra_batch_finishes_with_expected_accounts_and_transactions(self):
        folder = ROOT / "testdata" / "斑马商业对公流水"
        if not folder.exists():
            self.skipTest("本地未提供斑马商业对公流水样本")

        with tempfile.TemporaryDirectory() as tmp:
            args = SimpleNamespace(
                client="斑马商业对公流水",
                folder=str(folder),
                subject=None,
                account_type="对公",
                out_dir=tmp,
                force_name=False,
                infer_client_name=False,
            )
            package_deliverable.run(args.client, args)
            report_path = Path(tmp) / "_工作区" / args.client / f"{args.client}__整合报告.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))

        overview = report["客户整合概览"]
        self.assertEqual(overview["原始交易数"], 11107)
        self.assertEqual(overview["跨文件去重笔数"], 5548)
        self.assertEqual(overview["整合交易数"], 5559)
        self.assertEqual(overview["整合账户数"], 11)
        self.assertEqual(report["同账号元数据补全"]["补全交易数"], 203)
        self.assertEqual(report["批次未知账户配对"]["配对明细"][0]["核心交易重合数"], 50)


if __name__ == "__main__":
    unittest.main()
