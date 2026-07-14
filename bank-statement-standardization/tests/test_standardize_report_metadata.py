import csv
import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook


REPO_ROOT = Path(__file__).resolve().parents[2]
STANDARDIZE_PATH = REPO_ROOT / "bank-statement-standardization" / "scripts" / "standardize.py"
spec = importlib.util.spec_from_file_location("standardize", STANDARDIZE_PATH)
standardize = importlib.util.module_from_spec(spec)
spec.loader.exec_module(standardize)


class StandardizeReportMetadataTest(unittest.TestCase):
    def test_boc_payer_payee_columns_resolve_against_inquirer_account(self):
        excel = (
            REPO_ROOT
            / "bank-statement-standardization"
            / "testdata"
            / "斑马商业对公流水"
            / "商业中国银行7920(青山路支行)流水.xls"
        )
        if not excel.exists():
            self.skipTest("本地未提供中国银行查询账号流水样本")

        with tempfile.TemporaryDirectory() as tmp:
            csv_path, _json_path, report = standardize.standardize(str(excel), out_dir=tmp)
            with open(csv_path, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))

        self.assertEqual(len(report["文件画像"]["conditional_mapping"]), 2)
        self.assertEqual({row["本方账户"] for row in rows}, {"202264057920"})
        normalize_name = lambda value: str(value or "").replace(" ", "").replace("（", "(").replace("）", ")")
        self.assertEqual({normalize_name(row["本方名称"]) for row in rows}, {"斑马(南昌)商业有限公司"})

        self.assertEqual({row["对手账户"] for row in rows}, {""})
        self.assertEqual({row["对手名称"] for row in rows}, {""})

    def test_bank_aliases_are_loaded_from_yaml_without_python_patterns(self):
        self.assertFalse(hasattr(standardize, "BANK_PATTERNS"))
        self.assertEqual(standardize.infer_bank("开户行：中国工商银行"), "中国工商银行")
        self.assertEqual(standardize.infer_bank("开户行：工行南昌支行"), "中国工商银行")

    def test_internal_transaction_profile_infers_bank_without_changing_router_bank(self):
        records = []
        for idx, memo in enumerate(("非分期贷款放款", "非分期贷款扣款", "非分期贷款扣款")):
            records.append({
                "交易唯一编号": f"TX-{idx}",
                "对手账户": f"10010{idx} 上饶银行",
                "银行备注": memo,
                "账户方附言": "",
            })

        bank, profile = standardize.infer_bank_from_internal_transactions(records)

        self.assertEqual(bank, "上饶银行")
        self.assertEqual(profile["candidate_count"], 3)
        self.assertEqual(profile["candidate_ratio"], 1.0)
        self.assertEqual(profile["evidence_transaction_ids"], ["TX-0", "TX-1", "TX-2"])

    def test_regular_counterparty_bank_does_not_infer_self_bank(self):
        records = [
            {
                "交易唯一编号": "TX-1",
                "对手账户": "6217000000000000 中国建设银行",
                "银行备注": "采购货款",
                "账户方附言": "",
            }
            for _ in range(5)
        ]

        bank, profile = standardize.infer_bank_from_internal_transactions(records)

        self.assertEqual(bank, "")
        self.assertEqual(profile["candidate_count"], 0)

    def test_bank_fees_do_not_infer_self_bank(self):
        records = [
            {
                "交易唯一编号": f"TX-{idx}",
                "对手账户": "100101 上饶银行",
                "银行备注": "银行手续费",
                "账户方附言": "",
            }
            for idx in range(3)
        ]

        bank, profile = standardize.infer_bank_from_internal_transactions(records)

        self.assertEqual(bank, "")
        self.assertEqual(profile["candidate_count"], 0)

    def test_srbank_corporate_pdf_separates_router_and_inferred_bank(self):
        pdf = (
            REPO_ROOT
            / "bank-statement-standardization"
            / "testdata"
            / "斑马商业对公流水"
            / "斑马商业上饶一般户（南昌县支行）-8259流水........pdf"
        )
        if not pdf.exists():
            self.skipTest("本地未提供斑马商业上饶 PDF 样本")

        with tempfile.TemporaryDirectory() as tmp:
            _csv_path, _json_path, report = standardize.standardize(str(pdf), out_dir=tmp)

        image = report["文件画像"]
        self.assertEqual(image["router_bank"], "未识别")
        self.assertEqual(image["inferred_bank"], "上饶银行")
        self.assertEqual(image["bank_status"], "inferred")
        self.assertEqual(image["bank_source"], "internal_transaction_profile")
        self.assertEqual(image["确认银行"], "上饶银行")
        self.assertEqual(image["internal_transaction_profile"]["candidate_count"], 11)
    def test_account_metadata_is_not_guessed_without_route_configuration(self):
        info = standardize.sniff_account_info(
            [["交易时间", "交易金额", "余额"]],
            0,
            "企业名称：张三 账号：6222000000000001 参考号：9988776655443322",
        )

        self.assertEqual(info["本方名称"], "")
        self.assertEqual(info["本方账户"], "")
        self.assertEqual(info["账户类型线索"], "")

    def test_account_metadata_uses_route_configured_extractors(self):
        info = standardize.sniff_account_info(
            [["交易时间", "交易金额", "余额"]],
            0,
            "微信昵称：[刘伟兰]",
            preamble_extractors=[
                {
                    "field": "本方名称",
                    "pattern": r"微信昵称[:：]?\s*[［\[]\s*([^］\]\s]+)\s*[］\]]",
                },
                {
                    "field": "本方账户",
                    "pattern": r"微信昵称[:：]?\s*[［\[]\s*([^］\]\s]+)\s*[］\]]",
                    "template": "微信支付#{value}",
                },
            ],
        )

        self.assertEqual(info["本方名称"], "刘伟兰")
        self.assertEqual(info["本方账户"], "微信支付#刘伟兰")

    def test_extract_mapping_extracts_one_field(self):
        rules = [{
            "source": "对方账号与户名",
            "field": "对手账户",
            "pattern": r"^(\d{8,})/.*$",
        }, {
            "source": "对方账号与户名",
            "field": "对手名称",
            "pattern": r"^\d{8,}/",
            "replacement": "",
        }]

        split = standardize.apply_extract_mapping(
            ["6217002020026242362/邓俊英/附加信息"],
            ["对方账号与户名"],
            rules,
        )
        fallback = standardize.apply_extract_mapping(
            ["浙江民禾南昌律师事务所"],
            ["对方账号与户名"],
            rules,
        )

        self.assertEqual(split, {
            "对手账户": "6217002020026242362",
            "对手名称": "邓俊英/附加信息",
        })
        self.assertEqual(fallback, {})

    def test_extract_mapping_maps_account_and_name_separately(self):
        rules = [{
            "source": "对手信息",
            "field": "对手账户",
            "pattern": r"^.*?(?<!\d)(\d{8,})(?!\d).*$",
        }, {
            "source": "对手信息",
            "field": "对手名称",
            "pattern": r"(?<!\d)\d{8,}(?!\d)",
            "replacement": "",
        }]

        combined = standardize.apply_extract_mapping(
            ["曾小园 6217002020025481698 招商银行第三方平台交易资金"],
            ["对手信息"],
            rules,
        )
        account_only = standardize.apply_extract_mapping(
            ["12591713522210004"],
            ["对手信息"],
            rules,
        )

        self.assertEqual(combined, {
            "对手账户": "6217002020025481698",
            "对手名称": "曾小园 招商银行第三方平台交易资金",
        })
        self.assertEqual(account_only, {
            "对手账户": "12591713522210004",
            "对手名称": "",
        })

    def test_zeng_xiaoyuan_cmb_pdf_extracts_owner_account_and_counterparty_account(self):
        pdf = (
            REPO_ROOT / "bank-statement-standardization" / "testdata" / "曾小园"
            / "招商银行交易流水(申请时间2026年06月05日13时47分40秒).pdf"
        )
        if not pdf.exists():
            self.skipTest("本地未提供曾小园招商银行 PDF 样本")

        with tempfile.TemporaryDirectory() as tmp:
            csv_path, _json_path, report = standardize.standardize(str(pdf), out_dir=tmp)
            with open(csv_path, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))

        self.assertEqual(len(rows), 337)
        self.assertEqual({row["本方名称"] for row in rows}, {"宋志鹏"})
        self.assertEqual({row["本方账户"] for row in rows}, {"6214853380229907"})
        self.assertEqual(sum(not row["对手账户"] for row in rows), 29)
        self.assertFalse(any(re.search(r"\d{8,}", row["对手名称"]) for row in rows))
        self.assertEqual(rows[0]["对手账户"], "6227002022070397612")
        self.assertEqual(rows[0]["对手名称"], "宋志鹏")
        multi_number = rows[2]
        self.assertEqual(multi_number["对手账户"], "12591713522210004")
        self.assertEqual(multi_number["对手名称"], "招商银行第三方平台交易资金")
        self.assertEqual(report["字段映射"]["对手账户"]["原始字段"], "对手信息")
        self.assertEqual(report["文件画像"]["extract_mapping"][0]["field"], "对手账户")

    def test_zeng_xiaoyuan_ccb_excels_split_counterparty_account_and_name(self):
        folder = REPO_ROOT / "bank-statement-standardization" / "testdata" / "曾小园"
        samples = [
            (folder / "hqmx_20260605134404.xls", 3315, "曾小园", "6217002020025481698"),
            (folder / "hqmx_20260605135123.xls", 1036, "宋志鹏", "6227002022070397612"),
        ]
        if not all(path.exists() for path, *_ in samples):
            self.skipTest("本地未提供曾小园建设银行 XLS 样本")

        with tempfile.TemporaryDirectory() as tmp:
            for path, expected_rows, owner, account in samples:
                csv_path, _json_path, report = standardize.standardize(str(path), out_dir=tmp)
                with open(csv_path, encoding="utf-8-sig", newline="") as f:
                    rows = list(csv.DictReader(f))

                self.assertEqual(len(rows), expected_rows)
                self.assertEqual({row["本方名称"] for row in rows}, {owner})
                self.assertEqual({row["本方账户"] for row in rows}, {account})
                self.assertFalse(any("/" in row["对手账户"] for row in rows))
                self.assertEqual(report["字段映射"]["对手账户"]["原始字段"], "对方账号与户名")
                self.assertEqual(report["字段映射"]["对手名称"]["原始字段"], "对方账号与户名")

    def test_zeng_yao_xia_weipeng_owner_and_counterparty_fields(self):
        folder = (
            REPO_ROOT / "bank-statement-standardization" / "testdata"
            / "曾耀夏伟鹏个人流水"
        )
        samples = [
            ("夏伟鹏的交易明细20260422120619.pdf", 1325, "夏伟鹏", "622908 **** 2028"),
            ("夏伟鹏的交易明细20260422120619.xlsx", 559, "夏伟鹏", "622908 **** 2028"),
            ("曾耀招商密码350142.pdf", 1731, "曾耀", "6215581502003868833"),
            ("曾耀建行.xls", 2984, "曾耀", "6217002020031660616"),
            ("曾耀建行-0616.pdf", 3000, "曾耀", "6217002020031660616"),
            ("曾耀建行0616.pdf", 474, "曾耀", "6217002020031660616"),
        ]
        if not all((folder / name).exists() for name, *_ in samples):
            self.skipTest("本地未提供曾耀、夏伟鹏个人流水样本")

        ccb_accounts = set()
        with tempfile.TemporaryDirectory() as tmp:
            for index, (name, expected_rows, owner, account) in enumerate(samples):
                out_dir = Path(tmp) / str(index)
                csv_path, _json_path, _report = standardize.standardize(
                    str(folder / name),
                    out_dir=str(out_dir),
                )
                with open(csv_path, encoding="utf-8-sig", newline="") as f:
                    rows = list(csv.DictReader(f))

                self.assertEqual(len(rows), expected_rows, name)
                self.assertEqual({row["本方名称"] for row in rows}, {owner}, name)
                self.assertEqual({row["本方账户"] for row in rows}, {account}, name)
                self.assertFalse(
                    any("/" in row["对手账户"] for row in rows),
                    name,
                )
                self.assertFalse(
                    any("\n" in row["对手名称"] for row in rows),
                    name,
                )
                self.assertFalse(
                    any("户          名" in row["交易时间"] for row in rows),
                    name,
                )
                if name == "曾耀建行.xls":
                    ccb_accounts = {row["对手账户"] for row in rows}

        self.assertIn("4******9202", ccb_accounts)
        self.assertIn("Z******0010", ccb_accounts)

    def test_changhao_bank_owner_account_and_counterparty_fields(self):
        folder = REPO_ROOT / "bank-statement-standardization" / "testdata" / "昌浩公司流水"
        samples = [
            (
                "北京银行2025.4.1-2026.3.31号流水.xlsx", 490,
                "江西昌浩实业有限公司", "20000080375000116971117", "北京银行",
            ),
            (
                "北京银行流水明细2025.5-2026.4.xlsx", 270,
                "上饶昌浩玻璃有限公司", "20000089851200169748190", "北京银行",
            ),
            (
                "九江银行流水明细2025.5-2026.4.xlsx", 792,
                "", "337059300000010618", "九江银行",
            ),
            (
                "农行2025.4.1-10.31号流水.xlsx", 231,
                "江西昌浩实业有限公司", "14-382401040007179", "中国农业银行",
            ),
        ]
        if not all((folder / name).exists() for name, *_ in samples):
            self.skipTest("本地未提供昌浩公司流水样本")

        with tempfile.TemporaryDirectory() as tmp:
            for index, (name, expected_rows, owner, account, bank) in enumerate(samples):
                csv_path, _json_path, _report = standardize.standardize(
                    str(folder / name), out_dir=str(Path(tmp) / str(index)),
                )
                with open(csv_path, encoding="utf-8-sig", newline="") as f:
                    rows = list(csv.DictReader(f))

                self.assertEqual(len(rows), expected_rows, name)
                self.assertEqual({row["本方账户"] for row in rows}, {account}, name)
                self.assertEqual({row["开户行"] for row in rows}, {bank}, name)
                if owner:
                    self.assertEqual({row["本方名称"] for row in rows}, {owner}, name)
                self.assertTrue(all(row["交易时间"] for row in rows), name)
                if name.startswith("北京银行"):
                    self.assertTrue(all(row["对手名称"] for row in rows), name)
                    self.assertTrue(all(row["对手账户"] for row in rows), name)

    def test_cao_jian_pdfs_extract_owner_and_split_ccb_counterparty(self):
        folder = REPO_ROOT / "bank-statement-standardization" / "testdata" / "曹吉安"
        abc = folder / "26060309491107220299.pdf"
        ccb = folder / "hqmx_20260603095339(9).pdf"
        if not abc.exists() or not ccb.exists():
            self.skipTest("本地未提供曹吉安 PDF 样本")

        with tempfile.TemporaryDirectory() as tmp:
            abc_csv, _abc_json, _abc_report = standardize.standardize(str(abc), out_dir=tmp)
            with open(abc_csv, encoding="utf-8-sig", newline="") as f:
                abc_rows = list(csv.DictReader(f))

            ccb_csv, _ccb_json, ccb_report = standardize.standardize(str(ccb), out_dir=tmp)
            with open(ccb_csv, encoding="utf-8-sig", newline="") as f:
                ccb_rows = list(csv.DictReader(f))

        self.assertEqual({row["本方名称"] for row in abc_rows}, {"吴春梅"})
        self.assertEqual({row["本方账户"] for row in abc_rows}, {"6230520920052630479"})
        self.assertEqual({row["本方名称"] for row in ccb_rows}, {"吴春梅"})
        self.assertEqual({row["本方账户"] for row in ccb_rows}, {"6217002020093758837"})
        self.assertEqual(ccb_rows[0]["对手账户"], "6217002020026242362")
        self.assertEqual(ccb_rows[0]["对手名称"], "邓俊英")
        fallback_rows = [row for row in ccb_rows if row["对手名称"] == "浙江民禾南昌律师事务所"]
        self.assertTrue(fallback_rows)
        self.assertEqual({row["对手账户"] for row in fallback_rows}, {""})
        self.assertEqual(ccb_report["字段映射"]["对手账户"]["原始字段"], "对方账号与户名")
        self.assertEqual(ccb_report["字段映射"]["对手名称"]["原始字段"], "对方账号与户名")
        self.assertFalse(any(item["字段"] == "对手名称" for item in ccb_report["人工复核事项"]))

    def test_report_account_metadata_uses_data_columns_when_header_sniff_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "statement.xlsx"
            out_dir = tmp_path / "out"
            rows = [
                {
                    "客户账号": "36050183115000001593",
                    "账户名称": "江西省鹏达石业有限公司",
                    "交易时间": "2026-01-02 09:00:00",
                    "借方发生额(支取)": "",
                    "贷方发生额(收入)": "100.00",
                    "余额": "1100.00",
                    "对方户名": "付款方",
                    "对方账号": "10001",
                    "摘要": "收款",
                },
                {
                    "客户账号": "36050183115000001593",
                    "账户名称": "江西省鹏达石业有限公司",
                    "交易时间": "2026-01-03 10:00:00",
                    "借方发生额(支取)": "30.00",
                    "贷方发生额(收入)": "",
                    "余额": "1070.00",
                    "对方户名": "收款方",
                    "对方账号": "10002",
                    "摘要": "付款",
                },
            ]
            wb = Workbook()
            ws = wb.active
            ws.append(list(rows[0].keys()))
            for row in rows:
                ws.append([row[key] for key in rows[0].keys()])
            wb.save(src)

            csv_path, json_path, report = standardize.standardize(str(src), out_dir=str(out_dir))

            with open(csv_path, encoding="utf-8-sig", newline="") as f:
                out_rows = list(csv.DictReader(f))
            self.assertEqual(out_rows[0]["本方名称"], "江西省鹏达石业有限公司")
            self.assertEqual(out_rows[0]["本方账户"], "36050183115000001593")

            self.assertEqual(report["文件画像"]["本方名称"], "江西省鹏达石业有限公司")
            self.assertEqual(report["文件画像"]["本方账户"], "36050183115000001593")
            self.assertNotIn("_账号未识别", json.dumps(report, ensure_ascii=False))

            with open(json_path, encoding="utf-8") as f:
                saved_report = json.load(f)
            self.assertEqual(saved_report["文件画像"]["本方名称"], "江西省鹏达石业有限公司")
            self.assertEqual(saved_report["文件画像"]["本方账户"], "36050183115000001593")

    def test_card_bin_overrides_corporate_template_default_to_personal(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "statement.xlsx"
            out_dir = tmp_path / "out"
            headers = [
                "客户账号", "账户名称", "交易时间", "借方发生额（支取）", "贷方发生额（收入）",
                "余额", "币种", "对方户名", "对方账号", "对方开户机构", "记账日期", "摘要",
            ]
            row = [
                "6212263602003903457", "张三", "2026-01-02 09:00:00", "", "100.00",
                "1100.00", "人民币", "付款方", "10001", "开户行", "2026-01-02", "收款",
            ]
            wb = Workbook()
            ws = wb.active
            ws.append(headers)
            ws.append(row)
            wb.save(src)

            csv_path, _json_path, report = standardize.standardize(str(src), out_dir=str(out_dir))

            with open(csv_path, encoding="utf-8-sig", newline="") as f:
                out_rows = list(csv.DictReader(f))
            self.assertEqual(out_rows[0]["账户类型"], "个人")
            self.assertEqual(report["文件画像"]["账户类型"], "个人")
            self.assertEqual(report["文件画像"]["account_type_source"], "card_bin")
            self.assertEqual(report["文件画像"]["开户行"], "中国工商银行")
            self.assertEqual(report["文件画像"]["开户行识别来源"], "card_bin")
            self.assertEqual(out_rows[0]["开户行"], "中国工商银行")

    def test_card_bin_bank_name_uses_external_mapping_config(self):
        self.assertEqual(
            standardize.bank_name_from_card_bin({"bank": "CEB"}),
            "中国光大银行",
        )

    def test_mybank_pdf_uses_enterprise_name_and_router_bank(self):
        pdf = (
            REPO_ROOT
            / "bank-statement-standardization"
            / "testdata"
            / "程旭"
            / "江西嘟咔熊网商银行对账单2025.1.1-2025.12.31.pdf"
        )
        if not pdf.exists():
            self.skipTest("本地未提供网商银行 PDF 样本")

        with tempfile.TemporaryDirectory() as tmp:
            csv_path, _json_path, report = standardize.standardize(str(pdf), out_dir=tmp)
            with open(csv_path, encoding="utf-8-sig", newline="") as f:
                out_rows = list(csv.DictReader(f))

        self.assertEqual(report["文件画像"]["本方名称"], "江西嘟咔熊电子商务有限公司")
        self.assertEqual(report["文件画像"]["本方账户"], "8888888826100206")
        self.assertEqual(report["文件画像"]["开户行"], "浙江网商银行")
        self.assertEqual(report["文件画像"]["开户行识别来源"], "router")
        self.assertEqual(out_rows[0]["本方名称"], "江西嘟咔熊电子商务有限公司")
        self.assertEqual(out_rows[0]["本方账户"], "8888888826100206")
        self.assertEqual(out_rows[0]["开户行"], "浙江网商银行")

    def test_wechat_bill_excel_uses_nickname_as_payment_account(self):
        excel = (
            REPO_ROOT
            / "bank-statement-standardization"
            / "testdata"
            / "万建平"
            / "微信支付账单流水文件(20250301-20260301)——【.xlsx"
        )
        if not excel.exists():
            self.skipTest("本地未提供微信支付账单 Excel 样本")

        with tempfile.TemporaryDirectory() as tmp:
            csv_path, _json_path, report = standardize.standardize(str(excel), out_dir=tmp)
            with open(csv_path, encoding="utf-8-sig", newline="") as f:
                out_rows = list(csv.DictReader(f))

        self.assertEqual(report["文件画像"]["本方名称"], "刘伟兰")
        self.assertEqual(report["文件画像"]["本方账户"], "微信支付#刘伟兰")
        self.assertEqual(out_rows[0]["本方名称"], "刘伟兰")
        self.assertEqual(out_rows[0]["本方账户"], "微信支付#刘伟兰")
        self.assertFalse(out_rows[0]["本方账户"].startswith("未识别账户#"))

    def test_cmb_corporate_pdf_treats_payee_account_as_counterparty_account(self):
        pdf = (
            REPO_ROOT
            / "bank-statement-standardization"
            / "testdata"
            / "斑马商业对公流水"
            / "斑马商业招行一般户（青山湖支行）-1221流水.1.pdf"
        )
        if not pdf.exists():
            self.skipTest("本地未提供斑马商业招行 PDF 样本")

        with tempfile.TemporaryDirectory() as tmp:
            csv_path, _json_path, report = standardize.standardize(str(pdf), out_dir=tmp)
            with open(csv_path, encoding="utf-8-sig", newline="") as f:
                out_rows = list(csv.DictReader(f))

        self.assertEqual(len(out_rows), 1377)
        self.assertEqual({row["本方账户"] for row in out_rows}, {
            "未识别账户#斑马商业招行一般户（青山湖支行）-1221流水.1"
        })
        self.assertEqual({row["本方名称"] for row in out_rows}, {"斑马（南昌）商业有限公司"})
        self.assertEqual(out_rows[1]["对手账户"], "979154850070019810")
        self.assertEqual(report["文件画像"]["本方名称"], "斑马（南昌）商业有限公司")
        self.assertEqual(report["文件画像"]["账户类型"], "对公")
        self.assertEqual(report["文件画像"]["开户行"], "")
        self.assertEqual(report["文件画像"]["router_bank"], "未识别")
        self.assertEqual(report["文件画像"]["bank_status"], "unknown")

    def test_cmb_corporate_excel_uses_statement_account_and_owner(self):
        excel = (
            REPO_ROOT
            / "bank-statement-standardization"
            / "testdata"
            / "斑马商业对公流水"
            / "斑马商业招行一般户（青山湖支行）-1221流水.xlsx"
        )
        if not excel.exists():
            self.skipTest("本地未提供斑马商业招行 Excel 样本")

        with tempfile.TemporaryDirectory() as tmp:
            csv_path, _json_path, report = standardize.standardize(str(excel), out_dir=tmp)
            with open(csv_path, encoding="utf-8-sig", newline="") as f:
                out_rows = list(csv.DictReader(f))

        self.assertEqual(len(out_rows), 1377)
        self.assertEqual({row["本方账户"] for row in out_rows}, {"791912215110008"})
        self.assertEqual({row["本方名称"] for row in out_rows}, {"斑马（南昌）商业有限公司"})
        self.assertEqual(out_rows[1]["对手账户"], "979154850070019810")
        self.assertEqual(report["文件画像"]["本方账户"], "791912215110008")
        self.assertEqual(report["文件画像"]["本方名称"], "斑马（南昌）商业有限公司")

    def test_enterprise_counterparty_ratio_marks_probable_corporate(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "statement.xlsx"
            out_dir = tmp_path / "out"
            headers = ["交易时间", "收入金额", "支出金额", "账户余额", "对方户名", "对方账号"]
            enterprise_names = [
                "江西新怡光学仪器有限公司",
                "上饶伟力达塑胶制品有限公司",
                "上海付费通信息服务有限公司",
                "连连银通电子支付有限公司",
                "南京汇合堂文化传播中心",
                "中国民生银行股份有限公司上饶分行营业部",
            ]
            personal_names = [f"张三{i}" for i in range(14)]

            wb = Workbook()
            ws = wb.active
            ws.append(headers)
            for idx, name in enumerate(enterprise_names + personal_names, start=1):
                ws.append([
                    f"2026-01-{idx:02d} 09:00:00",
                    "100.00" if idx % 2 else "",
                    "" if idx % 2 else "50.00",
                    str(1000 + idx),
                    name,
                    f"10{idx:04d}",
                ])
            wb.save(src)

            csv_path, _json_path, report = standardize.standardize(str(src), out_dir=str(out_dir))

            with open(csv_path, encoding="utf-8-sig", newline="") as f:
                out_rows = list(csv.DictReader(f))
            self.assertEqual(len(out_rows), 20)
            self.assertEqual(out_rows[0]["账户类型"], "拟对公")
            self.assertEqual(report["文件画像"]["账户类型"], "拟对公")
            self.assertEqual(report["文件画像"]["account_type_source"], "counterparty_profile")
            self.assertEqual(report["文件画像"]["counterparty_profile"]["enterprise_counterparty_count"], 6)
            self.assertEqual(report["文件画像"]["counterparty_profile"]["valid_counterparty_count"], 20)


if __name__ == "__main__":
    unittest.main()
