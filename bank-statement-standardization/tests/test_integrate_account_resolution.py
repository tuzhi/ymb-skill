import importlib.util
import tempfile
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("integrate_account_resolution", ROOT / "scripts" / "integrate.py")
integrate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(integrate)


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


if __name__ == "__main__":
    unittest.main()
