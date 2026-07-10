import csv
import importlib.util
import json
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
        self.assertEqual(report["文件画像"]["开户行"], "招商银行")

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
