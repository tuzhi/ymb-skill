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
        "__time_precision": "second",
        "__fileseq": 0,
    }


class BatchAccountResolutionTests(unittest.TestCase):
    def test_load_inputs_uses_manifest_route_index_without_mapping_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            csv_path = work / "交易明细A__pdf__standardized.csv"
            row = transaction("交易明细A.pdf", "未识别账户#A", "2026-01-01 10:00:00", "100.00")
            pd.DataFrame([{key: value for key, value in row.items() if not key.startswith("__")}]).to_csv(
                csv_path, index=False, encoding="utf-8-sig")
            routes = {
                csv_path.name: {
                    "fingerprint_id": "md5:route",
                    "series_family": "family-v1",
                    "router_bank": "上饶银行",
                    "inferred_bank": "",
                    "yaml_match_status": "matched",
                }
            }

            loaded, files = integrate.load_inputs([str(work)], file_routes=routes)

        self.assertEqual(files, [str(csv_path)])
        self.assertEqual(loaded["__fingerprint_id"].iloc[0], "md5:route")
        self.assertEqual(loaded["__series_family"].iloc[0], "family-v1")
        self.assertEqual(loaded["__router_bank"].iloc[0], "上饶银行")

    def test_balance_continuous_volumes_merge_as_one_logical_account(self):
        rows = []
        specs = (
            ("交易明细111.pdf", "2025-06-16 11:20:42", "198.70", "", "30000.00"),
            ("交易明细+B.pdf", "2025-06-17 12:34:01", "978.70", "780.00", ""),
            ("交易明细+A.pdf", "2026-01-28 17:08:19", "1538.70", "560.00", ""),
        )
        for source, time, balance, income, expense in specs:
            row = transaction(source, f"未识别账户#{Path(source).stem}", time, balance)
            row["本方名称"] = ""
            row["开户行"] = "上饶银行"
            row["收入金额"] = income
            row["支出金额"] = expense
            row["收入金额_num"] = float(income) if income else float("nan")
            row["支出金额_num"] = float(expense) if expense else float("nan")
            row["账户余额_num"] = float(balance)
            row["__t"] = pd.Timestamp(time)
            row["__fingerprint_id"] = "md5:srbank"
            rows.append(row)

        resolved, report = integrate.merge_balance_continuous_sources(pd.DataFrame(rows))

        self.assertEqual(resolved["本方账户"].nunique(), 1)
        self.assertTrue(resolved["本方账户"].iloc[0].startswith("批次虚拟账户#上饶银行#SERIES-"))
        self.assertEqual(report["已归并组数"], 1)
        self.assertEqual(report["已归并文件数"], 3)
        self.assertEqual(len(report["归并明细"][0]["balance_links"]), 2)

    def test_filename_family_does_not_merge_when_balance_boundary_breaks(self):
        rows = []
        for source, time, balance, income in (
            ("交易明细+A.pdf", "2025-01-01 10:00:00", "100.00", "100.00"),
            ("交易明细+B.pdf", "2025-01-02 10:00:00", "999.00", "50.00"),
        ):
            row = transaction(source, f"未识别账户#{source}", time, balance)
            row["开户行"] = "上饶银行"
            row["收入金额"] = income
            row["支出金额"] = ""
            row["收入金额_num"] = float(income)
            row["支出金额_num"] = float("nan")
            row["账户余额_num"] = float(balance)
            row["__t"] = pd.Timestamp(time)
            row["__fingerprint_id"] = "md5:srbank"
            rows.append(row)

        resolved, report = integrate.merge_balance_continuous_sources(pd.DataFrame(rows))

        self.assertEqual(resolved["本方账户"].nunique(), 2)
        self.assertEqual(report["已归并组数"], 0)

    def test_yaml_series_family_merges_header_and_headerless_volumes(self):
        rows = []
        specs = (
            ("九江银行交易明细1.xlsx", "md5:with-header", "2025-06-30 16:54:49",
             "16529.88", "南昌广源木制品有限公司", "237019600000007422", "九江银行", "", "100.00"),
            ("九江银行交易明细2.xlsx", "md5:no-header", "2025-07-01 10:51:44",
             "40529.88", "", "未识别账户#九江银行交易明细2", "", "24000.00", ""),
            ("九江银行交易流水3.xlsx", "md5:no-header", "2025-10-09 14:29:44",
             "65520.35", "", "未识别账户#九江银行交易流水3", "", "60000.00", ""),
        )
        # 为第二卷补一笔期末交易，使第三卷首笔能够继续接上余额链。
        for source, fingerprint, time, balance, name, account, bank, income, expense in specs:
            row = transaction(source, account, time, balance)
            row["本方名称"] = name
            row["开户行"] = bank
            row["收入金额"] = income
            row["支出金额"] = expense
            row["收入金额_num"] = float(income) if income else float("nan")
            row["支出金额_num"] = float(expense) if expense else float("nan")
            row["账户余额_num"] = float(balance)
            row["__t"] = pd.Timestamp(time)
            row["__fingerprint_id"] = fingerprint
            row["__series_family"] = "jiujiang_corporate_detail_grid_v1"
            rows.append(row)
            if source == "九江银行交易明细2.xlsx":
                end = transaction(source, account, "2025-09-30 18:20:50", "5520.35")
                end["本方名称"] = ""
                end["开户行"] = ""
                end["收入金额"] = ""
                end["支出金额"] = "35009.53"
                end["收入金额_num"] = float("nan")
                end["支出金额_num"] = 35009.53
                end["账户余额_num"] = 5520.35
                end["__t"] = pd.Timestamp("2025-09-30 18:20:50")
                end["__fingerprint_id"] = fingerprint
                end["__series_family"] = "jiujiang_corporate_detail_grid_v1"
                end["__fileseq"] = 1
                rows.append(end)

        resolved, report = integrate.merge_balance_continuous_sources(pd.DataFrame(rows))

        self.assertEqual(set(resolved["本方账户"]), {"237019600000007422"})
        self.assertEqual(set(resolved["本方名称"]), {"南昌广源木制品有限公司"})
        self.assertEqual(set(resolved["开户行"]), {"九江银行"})
        self.assertEqual(report["已归并组数"], 1)
        self.assertEqual(report["归并明细"][0]["series_family"], "jiujiang_corporate_detail_grid_v1")
        self.assertEqual(len(report["归并明细"][0]["fingerprint_ids"]), 2)

    def test_explicit_account_family_allows_duplicate_anchor_exports(self):
        rows = []
        for source, time, balance, account, name, bank, income in (
            ("九江银行交易明细1.xlsx", "2025-01-01 10:00:00", "100.00",
             "237019600000007422", "南昌广源木制品有限公司", "九江银行", "100.00"),
            ("九江银行交易明细清单.xlsx", "2025-01-01 10:00:00", "100.00",
             "237019600000007422", "南昌广源木制品有限公司", "九江银行", "100.00"),
            ("九江银行交易明细2.xlsx", "2025-01-02 10:00:00", "150.00",
             "未识别账户#九江银行交易明细2", "", "", "50.00"),
        ):
            row = transaction(source, account, time, balance)
            row["本方名称"] = name
            row["开户行"] = bank
            row["收入金额"] = income
            row["支出金额"] = ""
            row["收入金额_num"] = float(income)
            row["支出金额_num"] = float("nan")
            row["账户余额_num"] = float(balance)
            row["__t"] = pd.Timestamp(time)
            row["__fingerprint_id"] = "md5:with-header" if account[0].isdigit() else "md5:no-header"
            row["__series_family"] = "jiujiang_corporate_detail_grid_v1"
            rows.append(row)

        resolved, report = integrate.merge_balance_continuous_sources(pd.DataFrame(rows))

        self.assertEqual(set(resolved["本方账户"]), {"237019600000007422"})
        self.assertEqual(report["已归并组数"], 1)
        self.assertEqual(report["归并明细"][0]["身份锚点"], "明确账号")

    def test_two_volume_series_family_without_identity_anchor_merges_on_exact_boundary(self):
        rows = []
        for source, fingerprint, time, balance, income in (
            ("九江银行交易明细2.xlsx", "md5:no-header-a", "2025-07-01 10:00:00", "100.00", "100.00"),
            ("九江银行交易流水3.xlsx", "md5:no-header-b", "2025-07-02 10:00:00", "150.00", "50.00"),
        ):
            row = transaction(source, f"未识别账户#{source}", time, balance)
            row["本方名称"] = ""
            row["开户行"] = "九江银行" if source.endswith("2.xlsx") else ""
            row["收入金额"] = income
            row["支出金额"] = ""
            row["收入金额_num"] = float(income)
            row["支出金额_num"] = float("nan")
            row["账户余额_num"] = float(balance)
            row["__t"] = pd.Timestamp(time)
            row["__fingerprint_id"] = fingerprint
            row["__series_family"] = "jiujiang_corporate_detail_grid_v1"
            rows.append(row)

        resolved, report = integrate.merge_balance_continuous_sources(pd.DataFrame(rows))

        self.assertEqual(resolved["本方账户"].nunique(), 1)
        self.assertTrue(
            resolved["本方账户"].iloc[0].startswith("批次虚拟账户#九江银行#SERIES-")
        )
        self.assertEqual(report["已归并组数"], 1)
        self.assertEqual(report["已归并文件数"], 2)
        self.assertEqual(len(report["归并明细"][0]["balance_links"]), 1)

    def test_three_balance_links_merge_unidentified_series_family(self):
        rows = []
        for source, fingerprint, time, balance, income, expense, account_type in (
            ("农商流水1.xls", "md5:plain", "2025-08-21 01:24:25", "100.00", "100.00", "", "未知"),
            ("农商流水2.xls", "md5:plain", "2025-09-19 11:27:55", "150.00", "50.00", "", "未知"),
            ("农商流水3.xls", "md5:plain", "2025-12-18 11:05:32", "130.00", "", "20.00", "拟对公"),
            ("农商流水4.xls", "md5:bold", "2026-03-19 15:33:21", "200.00", "70.00", "", "未知"),
        ):
            row = transaction(source, f"未识别账户#{Path(source).stem}", time, balance)
            row["本方名称"] = ""
            row["开户行"] = ""
            row["账户类型"] = account_type
            row["收入金额"] = income
            row["支出金额"] = expense
            row["收入金额_num"] = float(income) if income else float("nan")
            row["支出金额_num"] = float(expense) if expense else float("nan")
            row["账户余额_num"] = float(balance)
            row["__t"] = pd.Timestamp(time)
            row["__fingerprint_id"] = fingerprint
            row["__series_family"] = "rural_account_detail_query_biff_v1"
            rows.append(row)

        resolved, report = integrate.merge_balance_continuous_sources(pd.DataFrame(rows))

        self.assertEqual(resolved["本方账户"].nunique(), 1)
        self.assertTrue(
            resolved["本方账户"].iloc[0].startswith("批次虚拟账户#未识别#SERIES-")
        )
        self.assertEqual(set(resolved["本方名称"]), {""})
        self.assertEqual(set(resolved["开户行"]), {""})
        self.assertEqual(set(resolved["账户类型"]), {"拟对公"})
        self.assertEqual(report["已归并组数"], 1)
        self.assertEqual(report["已归并文件数"], 4)
        self.assertEqual(len(report["归并明细"][0]["balance_links"]), 3)
        self.assertEqual(report["归并明细"][0]["身份锚点"], "无身份锚点强余额链")

    def test_unidentified_series_family_with_branching_balance_candidates_does_not_merge(self):
        rows = []
        for source, time, balance, income in (
            ("分卷1.xls", "2025-01-01 10:00:00", "100.00", "100.00"),
            ("分卷2.xls", "2025-01-02 10:00:00", "150.00", "50.00"),
            ("分卷3.xls", "2025-01-03 10:00:00", "150.00", "50.00"),
        ):
            row = transaction(source, f"未识别账户#{Path(source).stem}", time, balance)
            row["本方名称"] = ""
            row["开户行"] = ""
            row["账户类型"] = "未知"
            row["收入金额"] = income
            row["支出金额"] = ""
            row["收入金额_num"] = float(income)
            row["支出金额_num"] = float("nan")
            row["账户余额_num"] = float(balance)
            row["__t"] = pd.Timestamp(time)
            row["__fingerprint_id"] = "md5:rural"
            row["__series_family"] = "rural_account_detail_query_biff_v1"
            rows.append(row)

        resolved, report = integrate.merge_balance_continuous_sources(pd.DataFrame(rows))

        self.assertEqual(resolved["本方账户"].nunique(), 3)
        self.assertEqual(report["已归并组数"], 0)

    def test_same_source_pdf_xlsx_rows_align_one_to_one_with_time_precision_difference(self):
        rows = []
        for idx in range(20):
            account = f"620000000000{idx:04d}"
            pdf = transaction(
                "同源流水.pdf", "622908 **** 2028", f"2026-04-01 10:{idx:02d}:00",
                "", f"对手{idx}", account,
            )
            xlsx = transaction(
                "同源流水.xlsx", "622908 **** 2028", f"2026-04-01 10:{idx:02d}:30",
                "", f"对手{idx}", account,
            )
            pdf["收入金额"] = xlsx["收入金额"] = "100.00"
            pdf["支出金额"] = xlsx["支出金额"] = ""
            pdf["账户余额"] = xlsx["账户余额"] = ""
            pdf["来源行号"] = xlsx["来源行号"] = str(idx + 1)
            rows.extend([pdf, xlsx])
        unique_pdf = transaction(
            "同源流水.pdf", "622908 **** 2028", "2026-04-02 10:00:00", "",
            "PDF独有", "6299999999999999",
        )
        unique_pdf["收入金额"] = "88.00"
        unique_pdf["支出金额"] = ""
        unique_pdf["账户余额"] = ""
        rows.append(unique_pdf)

        aligned, report = integrate.align_same_source_cross_format(pd.DataFrame(rows))

        self.assertEqual(len(aligned), 21)
        self.assertEqual(report["移除笔数"], 20)
        self.assertEqual(report["来源组"][0]["较小来源覆盖率"], 1.0)
        self.assertTrue(report["来源组"][0]["自动折叠"])
        self.assertEqual((aligned["来源文件名"] == "同源流水.pdf").sum(), 1)

    def test_same_source_cross_format_does_not_fold_when_coverage_is_below_gate(self):
        rows = []
        for idx in range(21):
            opponent_account = f"620000000000{idx:04d}" if idx < 20 else ""
            for source, second in (("同源流水.pdf", 0), ("同源流水.xlsx", 30)):
                row = transaction(
                    source, "622908 **** 2028", f"2026-04-01 10:{idx:02d}:{second:02d}",
                    "", f"对手{idx}", opponent_account,
                )
                row["收入金额"] = "100.00"
                row["支出金额"] = ""
                row["账户余额"] = ""
                rows.append(row)

        aligned, report = integrate.align_same_source_cross_format(pd.DataFrame(rows))

        self.assertEqual(len(aligned), 42)
        self.assertEqual(report["移除笔数"], 0)
        self.assertFalse(report["来源组"][0]["自动折叠"])
        self.assertIn("覆盖率低于", report["来源组"][0]["未启用原因"])
        self.assertEqual(report["来源组"][0]["待核查候选数"], 1)
        self.assertEqual(report["待核查候选"][0]["未自动折叠原因"], "缺少完整对手账户")

    def test_same_account_exact_overlap_keeps_more_complete_cross_file_row(self):
        sparse = transaction(
            "分段简版.pdf", "6222081505000091789", "2025-12-23 15:24:18", "72734.52",
            "", "",
        )
        sparse["支出金额"] = "50.00"
        sparse["收入金额"] = ""
        complete = transaction(
            "分段详版.pdf", "6222081505000091789", "2025-12-23 15:24:18", "72734.52",
            "分宜海螺建筑材料有限责任公司", "6222000000000000000",
        )
        complete["支出金额"] = "50.00"
        complete["收入金额"] = ""

        deduped, report = integrate.dedup_same_account_exact_overlap(pd.DataFrame([sparse, complete]))

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped.iloc[0]["来源文件名"], "分段详版.pdf")
        self.assertEqual(report["移除笔数"], 1)

    def test_same_account_exact_overlap_preserves_ambiguous_same_source_rows(self):
        rows = []
        for source in ("分段1.pdf", "分段1.pdf", "分段2.pdf"):
            row = transaction(source, "6222081505000091789", "2025-12-23 15:24:18", "72734.52")
            row["支出金额"] = "50.00"
            row["收入金额"] = ""
            rows.append(row)

        deduped, report = integrate.dedup_same_account_exact_overlap(pd.DataFrame(rows))

        self.assertEqual(len(deduped), 3)
        self.assertEqual(report["移除笔数"], 0)

    def test_reciprocal_transfer_restores_unknown_account_and_monthly_suffix_group(self):
        known = transaction(
            "北京银行.xlsx", "20000080375000116971117", "2026-03-30 08:08:07", "1000.00",
            "江西昌浩实业有限公司", "149799090000014882",
        )
        known["收入金额"] = ""
        known["支出金额"] = "80000.00"
        known["__对手开户行"] = "宜春农村商业银行股份有限公司"

        direct = transaction(
            "2026年3月农商行（4882）.xls", "未识别账户#三月", "2026-03-30 08:08:07", "81000.00",
            NAME, "2000008037500011697111",
        )
        direct["本方名称"] = ""
        direct["开户行"] = ""
        direct["收入金额"] = "80000.00"
        direct["支出金额"] = ""

        monthly = transaction(
            "2026年2月农商行（4882）.xls", "未识别账户#二月", "2026-02-01 10:00:00", "100.00",
        )
        monthly["本方名称"] = ""
        monthly["开户行"] = ""

        resolved, report = integrate.infer_identity_from_reciprocal_transfers(
            pd.DataFrame([known, direct, monthly])
        )

        rural = resolved[resolved["来源文件名"].str.contains("4882")]
        self.assertEqual(set(rural["本方账户"]), {"149799090000014882"})
        self.assertEqual(set(rural["本方名称"]), {"江西昌浩实业有限公司"})
        self.assertEqual(set(rural["开户行"]), {"宜春农村商业银行"})
        self.assertEqual(report["补全文件数"], 2)
        self.assertEqual(report["末四位归并文件数"], 1)

    def test_reciprocal_transfer_requires_counterparty_corroboration(self):
        known = transaction(
            "known.xlsx", "6222000000000001", "2026-01-01 10:00:00", "1000.00",
            "目标公司", "149799090000014882",
        )
        known["收入金额"] = ""
        known["支出金额"] = "100.00"
        unknown = transaction(
            "unknown.xls", "未识别账户#unknown", "2026-01-01 10:00:00", "100.00",
            "无关对手", "9999999999999999",
        )
        unknown["本方名称"] = ""
        unknown["开户行"] = ""
        unknown["收入金额"] = "100.00"
        unknown["支出金额"] = ""

        resolved, report = integrate.infer_identity_from_reciprocal_transfers(
            pd.DataFrame([known, unknown])
        )

        self.assertIn("未识别账户#unknown", set(resolved["本方账户"]))
        self.assertEqual(report["补全文件数"], 0)

    def test_reciprocal_transfer_rejects_prefix_only_account_match(self):
        known = transaction(
            "known.xlsx", "20000080375000116971117", "2026-01-01 10:00:00", "1000.00",
            "目标公司", "149799090000014882",
        )
        known["收入金额"] = ""
        known["支出金额"] = "100.00"
        unknown = transaction(
            "unknown.xls", "未识别账户#unknown", "2026-01-01 10:00:00", "100.00",
            "名称不一致", "2000008037500011697111",
        )
        unknown["本方名称"] = ""
        unknown["开户行"] = ""
        unknown["收入金额"] = "100.00"
        unknown["支出金额"] = ""

        resolved, report = integrate.infer_identity_from_reciprocal_transfers(
            pd.DataFrame([known, unknown])
        )

        self.assertIn("未识别账户#unknown", set(resolved["本方账户"]))
        self.assertEqual(report["补全文件数"], 0)

    def test_reciprocal_transfer_accepts_exact_account_when_name_is_missing(self):
        known = transaction(
            "known.xlsx", "6222000000000001", "2026-01-01 10:00:00", "1000.00",
            "目标公司", "149799090000014882",
        )
        known["收入金额"] = ""
        known["支出金额"] = "100.00"
        unknown = transaction(
            "unknown.xls", "未识别账户#unknown", "2026-01-01 10:00:00", "100.00",
            "", "6222000000000001",
        )
        unknown["本方名称"] = ""
        unknown["开户行"] = ""
        unknown["收入金额"] = "100.00"
        unknown["支出金额"] = ""

        resolved, report = integrate.infer_identity_from_reciprocal_transfers(
            pd.DataFrame([known, unknown])
        )

        self.assertEqual(set(resolved[resolved["来源文件名"] == "unknown.xls"]["本方账户"]),
                         {"149799090000014882"})
        self.assertEqual(report["补全文件数"], 1)

    def test_reciprocal_transfer_accepts_multi_date_time_tolerance_consensus(self):
        rows = []
        target_account = "1714022019004509141"
        for day, second in (("2026-01-01", 1), ("2026-01-02", 2), ("2026-01-03", 3)):
            known = transaction(
                "三湘流水.xls", "0070010101000003132", f"{day} 10:00:00", "1000.00",
                NAME, target_account,
            )
            known["收入金额"] = ""
            known["支出金额"] = "100.00"
            known["__对手开户行"] = "中国工商银行总行清算中心"
            unknown = transaction(
                "工行分卷.xlsx", "未识别账户#工行分卷", f"{day} 10:00:0{second}", "1100.00",
                NAME, "",
            )
            unknown["本方名称"] = ""
            unknown["开户行"] = ""
            unknown["收入金额"] = "100.00"
            unknown["支出金额"] = ""
            rows.extend([known, unknown])

        resolved, report = integrate.infer_identity_from_reciprocal_transfers(pd.DataFrame(rows))

        unknown_rows = resolved[resolved["来源文件名"] == "工行分卷.xlsx"]
        self.assertEqual(set(unknown_rows["本方账户"]), {target_account})
        self.assertEqual(set(unknown_rows["本方名称"]), {NAME})
        self.assertEqual(set(unknown_rows["开户行"]), {"中国工商银行"})
        detail = next(item for item in report["补全明细"] if item["来源文件"] == ["工行分卷.xlsx"])
        self.assertEqual(detail["归并方式"], "跨账户反向互转记录（时间容差共识）")
        self.assertEqual(detail["时间容差秒数"], 5)
        self.assertEqual(detail["容差证据数"], 3)
        self.assertEqual(detail["容差证据日期数"], 3)

    def test_reciprocal_transfer_rejects_single_near_time_match(self):
        known = transaction(
            "known.xlsx", "0070010101000003132", "2026-01-01 10:00:00", "1000.00",
            NAME, "1714022019004509141",
        )
        known["收入金额"] = ""
        known["支出金额"] = "100.00"
        unknown = transaction(
            "unknown.xlsx", "未识别账户#unknown", "2026-01-01 10:00:02", "1100.00",
            NAME, "",
        )
        unknown["本方名称"] = ""
        unknown["开户行"] = ""
        unknown["收入金额"] = "100.00"
        unknown["支出金额"] = ""

        resolved, report = integrate.infer_identity_from_reciprocal_transfers(
            pd.DataFrame([known, unknown])
        )

        self.assertIn("未识别账户#unknown", set(resolved["本方账户"]))
        self.assertEqual(report["补全文件数"], 0)

    def test_reciprocal_transfer_rejects_date_precision_even_when_times_match(self):
        known = transaction(
            "known.xlsx", "6222000000000001", "2026-01-01 00:00:00", "1000.00",
            "目标公司", "149799090000014882",
        )
        known["收入金额"] = ""
        known["支出金额"] = "100.00"
        known["__time_precision"] = "date"
        unknown = transaction(
            "unknown.xls", "未识别账户#unknown", "2026-01-01 00:00:00", "1100.00",
            "目标公司", "6222000000000001",
        )
        unknown["本方名称"] = ""
        unknown["开户行"] = ""
        unknown["收入金额"] = "100.00"
        unknown["支出金额"] = ""
        unknown["__time_precision"] = "date"

        resolved, report = integrate.infer_identity_from_reciprocal_transfers(
            pd.DataFrame([known, unknown])
        )

        self.assertIn("未识别账户#unknown", set(resolved["本方账户"]))
        self.assertEqual(report["补全文件数"], 0)

    def test_verified_legal_entity_identity_promotes_account_type_to_corporate(self):
        rows = [
            transaction("unknown.xls", "149719090000097738", "2026-01-01 10:00:00", "100.00"),
            transaction("inferred.xls", "149719090000097738", "2026-01-02 10:00:00", "150.00"),
        ]
        for row, account_type in zip(rows, ("未知", "拟对公")):
            row["本方名称"] = "江西昌浩实业有限公司"
            row["账户类型"] = account_type

        resolved, report = integrate.complete_account_type_by_verified_identity(pd.DataFrame(rows))

        self.assertEqual(set(resolved["账户类型"]), {"对公"})
        self.assertEqual(report["补全账户数"], 1)
        self.assertEqual(report["补全交易数"], 2)

    def test_natural_person_identity_does_not_promote_account_type(self):
        row = transaction("personal.xls", "6217994240007322914", "2026-01-01 10:00:00", "100.00")
        row["本方名称"] = "陈桂森"
        row["账户类型"] = "未知"

        resolved, report = integrate.complete_account_type_by_verified_identity(pd.DataFrame([row]))

        self.assertEqual(resolved.iloc[0]["账户类型"], "未知")
        self.assertEqual(report["补全账户数"], 0)

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
        self.assertEqual(report["归并明细"][0]["归并方式"], "同户名同银行唯一明确账号")
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
        self.assertTrue({"router_bank", "inferred_bank", "batch_pair", "bank_source"}.isdisjoint(output.columns))

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
            )
            package_deliverable.run(args.client, args)
            report_path = Path(tmp) / "_工作区" / args.client / f"{args.client}__整合报告.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))

        overview = report["客户整合概览"]
        self.assertEqual(overview["原始交易数"], 11107)
        self.assertEqual(overview["跨文件去重笔数"], 5548)
        self.assertEqual(overview["整合交易数"], 5559)
        self.assertEqual(overview["整合账户数"], 6)
        self.assertEqual(report["同账号元数据补全"]["补全交易数"], 203)
        self.assertEqual(report["批次未知账户配对"]["已配对组数"], 0)


if __name__ == "__main__":
    unittest.main()
