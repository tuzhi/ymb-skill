import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
CORE_PACKAGE = REPO_ROOT / "ymb-standardization-core"
if str(CORE_PACKAGE) not in sys.path:
    sys.path.insert(0, str(CORE_PACKAGE))

from ymb_standardization_core import core  # noqa: E402
from ymb_standardization_core.readers.routing.rule_loader import ExcelRouteRule  # noqa: E402
from ymb_standardization_core.readers.routing.rule_loader import fingerprint_md5  # noqa: E402

def load_input_router():
    spec = importlib.util.spec_from_file_location(
        "input_router",
        CORE_PACKAGE / "ymb_standardization_core" / "readers" / "input_router.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.configure_readers(core.read_rows_excel, core.read_rows_csv, core.NotABankStatement)
    return module

class InputRouterTests(unittest.TestCase):
    def test_csv_input_is_not_supported_as_raw_statement(self):
        module = load_input_router()
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "sample.csv"
            csv_path.write_text("交易日期,收入金额,账户余额\n2026-01-01,1.00,2.00\n", encoding="utf-8")

            with self.assertRaises(core.NotABankStatement) as cm:
                module.read_rows(str(csv_path))

        self.assertIn("CSV/TXT/TSV 当前不作为原始流水支持格式", str(cm.exception))

    def test_excel_input_uses_specialized_excel_route(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "丰城市利华金属制品有限公司" / "2025.5.1-2025.5.31农行.xlsx"
        if not excel.exists():
            self.skipTest("本地未提供 Excel 样本")

        result = module.read_rows(str(excel))

        self.assertEqual(result.kind, "excel")
        self.assertNotIn("parser", result.route_info)

        self.assertEqual(result.route_info["reader_id"], "openpyxl_grid")
        self.assertEqual(result.route_info["decision"], "matched")
        self.assertEqual(result.route_info["file_type"], "excel")
        self.assertEqual(result.route_info["bank"], "中国农业银行")
        self.assertEqual(result.route_info["account_type"], "对公")
        self.assertEqual(result.route_info["fingerprint_id"], "md5:67782b663739efc4e1fe6abab80507cf")
        self.assertEqual(
            result.route_info["preamble_extractors"],
            [
                {"field": "本方账户", "pattern": r"账号[:：]\s*([0-9-]+)"},
                {"field": "本方名称", "pattern": r"户名[:：]\s*(.+?)\s+币种[:：]"},
            ],
        )

    def test_legacy_abc_xls_uses_specialized_excel_route(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "丰城市利华金属制品有限公司" / "2025.1.1-2025.1.31农行.xls"
        if not excel.exists():
            self.skipTest("本地未提供农行旧版 XLS 样本")

        result = module.read_rows(str(excel))

        self.assertNotIn("parser", result.route_info)

        self.assertEqual(result.route_info["reader_id"], "openpyxl_grid")
        self.assertEqual(result.route_info["decision"], "matched")
        self.assertEqual(result.route_info["bank"], "中国农业银行")
        self.assertTrue(result.route_info["style_evidence"])

    def test_account_detail_income_expense_usage_excel_does_not_infer_bank(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "江西轩宇塑业有限公司" / "农行01-07.xls"
        if not excel.exists():
            self.skipTest("本地未提供账户明细收入支出用途 Excel 样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "未识别")
        self.assertEqual(route["account_type"], "对公")

    def test_hunan_sanxiang_bank_account_detail_excel_route(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "河南路诚机电制造有限公司" / "三湘2022-01-01-2022-12-31流水.xls"
        if not excel.exists():
            self.skipTest("本地未提供湖南三湘银行账户明细样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "湖南三湘银行")
        self.assertEqual(route["account_type"], "对公")

    def test_icbc_debit_history_electronic_excel_route(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "徐赵亮" / "徐工行-流水.xlsx"
        if not excel.exists():
            self.skipTest("本地未提供工行借记账户历史明细电子版 Excel 样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "中国工商银行")
        self.assertEqual(route["account_type"], "个人")

    def test_detail_download_debit_credit_counterparty_excel_does_not_infer_bank(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "昌浩公司流水" / "北京银行2025.4.1-2026.3.31号流水.xlsx"
        if not excel.exists():
            self.skipTest("本地未提供明细下载借贷发生额 Excel 样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "未识别")
        self.assertEqual(route["account_type"], "对公")

    def test_current_account_detail_query_excel_does_not_infer_bank(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "湖南新普翔供应链-银行流水" / "普翔长沙银行对公.xlsx"
        if not excel.exists():
            self.skipTest("本地未提供活期账户明细查询 Excel 样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "未识别")
        self.assertEqual(route["account_type"], "对公")

    def test_debit_card_date_range_detail_excel_does_not_infer_bank(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "湖南新普翔供应链-银行流水" / "浦发银行对私.xls"
        if not excel.exists():
            self.skipTest("本地未提供借记卡日期范围明细 Excel 样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "未识别")
        self.assertEqual(route["account_type"], "个人")

    def test_mybank_corporate_transaction_detail_excel_route(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "程旭" / "江西嘟咔熊网商银行对账单2025.1.1-2025.12.31.xlsx"
        if not excel.exists():
            self.skipTest("本地未提供浙江网商银行企业账户交易明细样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "浙江网商银行")
        self.assertEqual(route["account_type"], "对公")

    def test_historydetail_debit_credit_excel_route_does_not_infer_bank(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "万建平" / "historydetail375.xlsx"

        result = module.read_rows(str(excel))

        self.assertNotIn("parser", result.route_info)

        self.assertEqual(result.route_info["reader_id"], "openpyxl_grid")
        self.assertEqual(result.route_info["decision"], "matched")
        self.assertEqual(result.route_info["bank"], "未识别")
        self.assertEqual(result.route_info["account_type"], "对公")

    def test_historydetail_transfer_amount_excel_route_does_not_infer_bank(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "共青极兔" / "2025年10月.xlsx"

        result = module.read_rows(str(excel))

        self.assertNotIn("parser", result.route_info)

        self.assertEqual(result.route_info["reader_id"], "openpyxl_grid")
        self.assertEqual(result.route_info["decision"], "matched")
        self.assertEqual(result.route_info["bank"], "未识别")
        self.assertEqual(result.route_info["account_type"], "对公")

    def test_historydetail_transfer_in_out_excel_route_does_not_infer_bank(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "河南路诚机电制造有限公司" / "工行2022年1月-6月流水.xlsx"
        if not excel.exists():
            self.skipTest("本地未提供 HISTORYDETAIL 转入转出 Excel 样本")

        result = module.read_rows(str(excel))

        self.assertNotIn("parser", result.route_info)

        self.assertEqual(result.route_info["reader_id"], "openpyxl_grid")
        self.assertEqual(result.route_info["decision"], "matched")
        self.assertEqual(result.route_info["bank"], "未识别")
        self.assertEqual(
            result.route_info["series_family"],
            "historydetail_transfer_in_out_v1",
        )

    def test_boc_hisxls_bilingual_corporate_excel_route(self):
        module = load_input_router()
        excel = (
            ROOT
            / "testdata"
            / "吉安超创电子PCB"
            / "HISXLS-20250101-20250630-0842744197688651565.xls"
        )

        result = module.read_rows(str(excel))

        self.assertNotIn("parser", result.route_info)

        self.assertEqual(result.route_info["reader_id"], "openpyxl_grid")
        self.assertEqual(result.route_info["decision"], "matched")
        self.assertEqual(result.route_info["bank"], "中国银行")
        self.assertEqual(result.route_info["account_type"], "对公")

    def test_account_transaction_detail_export_excel_does_not_infer_bank(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "奥特联盈" / "2022.08.01-2023.08.01.xls"

        result = module.read_rows(str(excel))

        self.assertNotIn("parser", result.route_info)

        self.assertEqual(result.route_info["reader_id"], "openpyxl_grid")
        self.assertEqual(result.route_info["decision"], "matched")
        self.assertEqual(result.route_info["bank"], "未识别")
        self.assertEqual(result.route_info["account_type"], "对公")

    def test_account_header_debit_credit_excel_does_not_infer_bank_from_filename(self):
        module = load_input_router()
        excel = (
            ROOT
            / "testdata"
            / "广州沛瑾家具"
            / "广州沛瑾家具有限公司_中国工商银行_TF_1.xlsx"
        )

        result = module.read_rows(str(excel))

        self.assertNotIn("parser", result.route_info)

        self.assertEqual(result.route_info["reader_id"], "openpyxl_grid")
        self.assertEqual(result.route_info["decision"], "matched")
        self.assertEqual(result.route_info["bank"], "未识别")
        self.assertEqual(result.route_info["account_type"], "对公")

    def test_ccb_flat_xls_has_single_specialized_route(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "张运贞" / "25年1-5月.xls"
        if not excel.exists():
            self.skipTest("本地未提供建行扁平对公 XLS 样本")

        result = module.read_rows(str(excel))

        self.assertNotIn("parser", result.route_info)

        self.assertEqual(result.route_info["reader_id"], "openpyxl_grid")
        self.assertEqual(result.route_info["decision"], "matched")
        self.assertEqual(result.route_info["bank"], "中国建设银行")

    def test_ccb_flat_xls_keeps_counterparty_bank_as_internal_evidence(self):
        module = load_input_router()
        excel = (
            ROOT
            / "testdata"
            / "河南路诚机电制造有限公司"
            / "建行2022年10-12月份流水.xls"
        )
        if not excel.exists():
            self.skipTest("本地未提供河南路诚建行 XLS 样本")

        result = module.read_rows(str(excel))

        self.assertEqual(result.route_info["bank"], "中国建设银行")
        self.assertEqual(
            result.route_info["column_mapping"]["对方开户机构"],
            "__对手开户行",
        )

    def test_ccb_account_detail_info_excel_route(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "广源流水" / "建行流水1.xlsx"

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "中国建设银行")
        self.assertEqual(route["account_type"], "对公")
        self.assertEqual(route["metadata_evidence"]["creator"], "中国建设银行")
        self.assertTrue(route["style_evidence"])
        self.assertEqual(route["date_format_evidence"], ["yyyymmdd"])

    def test_ccb_detail_query_download_xls_route(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "潘荣平消防设备" / "温州总公司" / "2025年1-3月.xls"
        if not excel.exists():
            self.skipTest("本地未提供建行明细查询结果下载 XLS 样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "中国建设银行")
        self.assertEqual(route["account_type"], "对公")

    def test_bank_of_nanjing_transaction_detail_xls_route(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "金鼎" / "金鼎南京2022年10月对账明细.xls"
        if not excel.exists():
            self.skipTest("本地未提供南京银行交易明细 XLS 样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "南京银行")

    def test_bank_of_jiangsu_corporate_statement_excel_route(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "金鼎" / "金鼎江苏2022年10月份对账明细.xls"
        if not excel.exists():
            self.skipTest("本地未提供江苏银行对公帐户对帐单样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "江苏银行")
        self.assertEqual(route["account_type"], "对公")

    def test_account_serial_income_expense_excel_does_not_infer_bank(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "顺民制衣" / "江西银行(1).xls"
        if not excel.exists():
            self.skipTest("本地未提供账户号交易流水号收支 Excel 样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "未识别")
        self.assertEqual(route["account_type"], "对公")

    def test_jiujiang_bank_corporate_detail_excel_route(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "宁聚&付亮亮&徐美琴" / "宁聚九江银行一般户4212.xlsx"
        if not excel.exists():
            self.skipTest("本地未提供九江银行对公交易明细 Excel 样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "九江银行")
        self.assertEqual(route["account_type"], "对公")

    def test_srbank_personal_history_excel_route(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "宁聚&付亮亮&徐美琴" / "付亮亮上饶银行5813.xlsx"
        if not excel.exists():
            self.skipTest("本地未提供上饶银行个人历史流水 Excel 样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "上饶银行")
        self.assertEqual(route["account_type"], "个人")

    def test_srbank_corporate_online_detail_excel_route(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "斑马商业对公流水" / "斑马商业上饶一般户（南昌县支行）-8259流水............xls"
        if not excel.exists():
            self.skipTest("本地未提供上饶银行企业网银交易明细 Excel 样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "上饶银行")
        self.assertEqual(route["account_type"], "对公")

    def test_corporate_query_account_summary_excel_does_not_infer_bank(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "斑马商业对公流水" / "10.17-1.15斑马商业九江银行7553（赣江新区分行营业部)流水.xlsx"
        if not excel.exists():
            self.skipTest("本地未提供查询账号汇总交易明细 Excel 样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "未识别")
        self.assertEqual(route["account_type"], "对公")

    def test_account_history_detail_excel_does_not_infer_bank(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "昌浩公司流水" / "农行2025.4.1-10.31号流水.xlsx"
        if not excel.exists():
            self.skipTest("本地未提供账户历史明细 Excel 样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "未识别")
        self.assertEqual(route["account_type"], "对公")

    def test_counterparty_wide_debit_credit_excel_does_not_infer_bank(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "昌浩公司流水" / "北京银行流水明细2025.5-2026.4.xlsx"
        if not excel.exists():
            self.skipTest("本地未提供对手方宽表借贷明细 Excel 样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "未识别")
        self.assertEqual(route["account_type"], "对公")

    def test_industrial_bank_transaction_detail_excel_route(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "曾耀夏伟鹏个人流水" / "夏伟鹏的交易明细20260422120619.xlsx"
        if not excel.exists():
            self.skipTest("本地未提供兴业银行交易流水 Excel 样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "兴业银行")
        self.assertEqual(route["account_type"], "个人")

    def test_rural_commercial_laptop_account_query_excel_does_not_infer_bank(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "江西赣驰" / "江西赣驰2025年流水(1).xls"
        if not excel.exists():
            self.skipTest("本地未提供 Laptop 农商账户明细查询 Excel 样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "未识别")
        self.assertEqual(route["account_type"], "未知")

    def test_rural_commercial_biff_super_online_debit_excel_does_not_infer_bank(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "江西轩宇塑业有限公司" / "农商2601-03.xls"
        if not excel.exists():
            self.skipTest("本地未提供 BIFF 农商超级网银往贷 Excel 样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "未识别")
        self.assertEqual(route["account_type"], "未知")

    def test_rural_account_query_header_variants_share_series_family(self):
        module = load_input_router()
        samples = (
            (ROOT / "testdata" / "广源流水" / "农商流水1.xls", "md5:5ee6d3955fa1cca84bd4bed11ae23c6f"),
            (ROOT / "testdata" / "广源流水" / "农商流水4.xls", "md5:94ff523d47e25a05d64e18944ecb9bf4"),
            (ROOT / "testdata" / "秦国有" / "20260604.xls", "md5:5ee6d3955fa1cca84bd4bed11ae23c6f"),
            (ROOT / "testdata" / "袁军" / "1-3.xls", "md5:5ee6d3955fa1cca84bd4bed11ae23c6f"),
            (ROOT / "testdata" / "江西轩宇塑业有限公司" / "农商2601-03.xls", "md5:94ff523d47e25a05d64e18944ecb9bf4"),
        )
        for excel, fingerprint_id in samples:
            with self.subTest(filename=str(excel.relative_to(ROOT / "testdata"))):
                result = module.read_rows(str(excel))
                route = result.route_info

                self.assertEqual(route["decision"], "matched")
                self.assertEqual(route["fingerprint_id"], fingerprint_id)
                self.assertEqual(route["bank"], "未识别")
                self.assertEqual(route["account_type"], "未知")
                self.assertEqual(
                    route["series_family"],
                    "rural_account_detail_query_biff_v1",
                )
                self.assertEqual(
                    {item["text"] for item in route["style_evidence"]},
                    {"账户明细查询结果", "开始日期：", "截止日期：", "交易日期"},
                )

    def test_srbank_personal_history_excel_route_allows_summary_header(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "熊岱" / "上饶银行-5086.xlsx"
        if not excel.exists():
            self.skipTest("本地未提供上饶银行个人摘要列 Excel 样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "上饶银行")
        self.assertEqual(route["account_type"], "个人")

    def test_unknown_excel_structure_uses_generic_fallback_route(self):
        module = load_input_router()

        def fake_excel_reader(_path):
            return "Sheet1", [["not", "a", "known", "statement"], ["1", "2", "3", "4"]]

        module.configure_readers(fake_excel_reader, core.read_rows_csv, core.NotABankStatement)

        result = module.read_rows("unknown.xlsx")

        self.assertEqual(result.kind, "excel")
        self.assertNotIn("parser", result.route_info)

        self.assertEqual(result.route_info["reader_id"], "openpyxl_grid")
        self.assertEqual(result.route_info["reader_id"], "openpyxl_grid")
        self.assertEqual(result.route_info["decision"], "unmatched")
        self.assertEqual(result.route_info["file_type"], "excel")

    def test_excel_route_config_uses_fingerprint_columns_for_layout_and_mapping(self):
        rules_path = CORE_PACKAGE / "ymb_standardization_core" / "readers" / "routing" / "excel_rules.yaml"
        items = yaml.safe_load(rules_path.read_text(encoding="utf-8"))

        for item in items:
            self.assertNotIn("parser", item)
            self.assertIn("reader_id", item)
            self.assertNotIn("column_mapping", item)
            self.assertNotIn("identity", item)
            self.assertNotIn("layout", item)
            fingerprint = item.get("fingerprint") or {}
            self.assertIn("id", item)
            self.assertNotIn("version", item)
            self.assertEqual(item["id"], fingerprint_md5(fingerprint))
            self.assertIn("identity", fingerprint)
            self.assertNotIn("layout", fingerprint)
            self.assertNotIn("data", fingerprint)
            columns = fingerprint.get("columns") or {}
            self.assertIsInstance(columns.get("all"), dict)
            self.assertTrue(columns.get("all"))

    def test_excel_route_without_yaml_fingerprint_falls_back_to_generic(self):
        module = load_input_router()
        original = module.load_excel_route_rules
        try:
            module.load_excel_route_rules = lambda: [
                ExcelRouteRule(
                    id="md5:test",
                                        reader_id="openpyxl_grid",
                    file_type="excel",
                    bank="测试银行",
                    account_type="未知",
                    column_mapping={},
                    identity_any=["测试银行"],
                    column_markers=["交易时间", "账户余额"],
                    metadata_all={},
                    style_all=[],
                    date_format_any=[],
                )
            ]

            route = module.route_excel([["测试银行", "交易时间", "账户余额"]], "Sheet1", context={})

            self.assertNotIn("parser", route)

            self.assertEqual(route["reader_id"], "openpyxl_grid")
            self.assertEqual(route["decision"], "unmatched")
            self.assertIn("candidate_fingerprints", route)
            self.assertNotIn("parser", route["candidate_fingerprints"][0])

            self.assertEqual(route["candidate_fingerprints"][0]["reader_id"], "openpyxl_grid")
            self.assertEqual(route["candidate_fingerprints"][0]["reader_id"], "openpyxl_grid")
            self.assertEqual(route["candidate_fingerprints"][0]["reason"], "missing_yaml_fingerprint")
        finally:
            module.load_excel_route_rules = original

    def test_headerless_excel_transfer_detail_route(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "杨德嘎" / "20260611105021.xlsx"
        if not excel.exists():
            self.skipTest("本地未提供无抬头 Excel 样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "未识别")
        self.assertTrue(route["metadata_evidence"])
        self.assertTrue(route["style_evidence"])

    def test_account_query_result_does_not_infer_nanchang_rural_commercial_bank_from_transaction_rows(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "秦国有" / "20260604 (2).xls"
        if not excel.exists():
            self.skipTest("本地未提供南昌农商账户明细查询样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "未识别")
        self.assertTrue(route["style_evidence"])
        self.assertIn("交易日期", route["columns_evidence"])

    def test_account_query_result_does_not_infer_jiangxi_lushan_rural_commercial_bank_from_transaction_rows(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "袁军" / "1-3.xls"
        if not excel.exists():
            self.skipTest("本地未提供江西庐山农商账户明细查询样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "未识别")
        self.assertTrue(route["style_evidence"])
        self.assertIn("交易日期", route["columns_evidence"])

    def test_generic_rural_commercial_account_query_does_not_infer_bank(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "广源流水" / "农商流水1.xls"

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "未识别")
        self.assertEqual(route["account_type"], "未知")
        self.assertTrue(route["style_evidence"])
        self.assertEqual(route["metadata_evidence"]["application"], "BIFF/XLS")
        self.assertEqual(route["date_format_evidence"], ["yyyy-mm-dd hh:mm:ss"])
        self.assertIn("交易日期", route["columns_evidence"])

    def test_rural_commercial_super_online_debit_excel_does_not_infer_bank(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "江西轩宇塑业有限公司" / "农商202501-03.xls"
        if not excel.exists():
            self.skipTest("本地未提供农商超级网银往贷账户明细样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "未识别")
        self.assertEqual(route["account_type"], "未知")

    def test_rural_commercial_administrator_account_query_excel_does_not_infer_bank(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "昌浩公司流水" / "2025年4月份农商行（7738）网银流水 - 副本.xls"
        if not excel.exists():
            self.skipTest("本地未提供 Administrator 农商账户明细样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "未识别")
        self.assertEqual(route["account_type"], "未知")

    def test_rural_commercial_administrator_no_range_excel_does_not_infer_bank(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "昌浩公司流水" / "农商银行流水明细2025.5-2026.4.xls"
        if not excel.exists():
            self.skipTest("本地未提供无起止日期 Administrator 农商账户明细样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "未识别")
        self.assertEqual(route["account_type"], "未知")

    def test_rural_commercial_expedited_transfer_excel_does_not_infer_bank(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "永瑞制衣-周康" / "20260413.xls"
        if not excel.exists():
            self.skipTest("本地未提供农商加急汇账户明细样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "未识别")
        self.assertEqual(route["account_type"], "未知")

    def test_pdf_input_keeps_existing_pdf_router(self):
        module = load_input_router()
        pdf = ROOT / "testdata" / "李先根" / "GRZD-9A202606081958362818-20250608-20260607-X_unsign_sign_18831.pdf"
        if not pdf.exists():
            self.skipTest("本地未提供李先根 GRZD 浙江庆元农商 PDF 样本")

        result = module.read_rows(str(pdf))

        self.assertEqual(result.kind, "pdf")
        self.assertNotIn("parser", result.route_info)

        self.assertEqual(result.route_info["reader_id"], "pdfplumber_coordinate_table")
        self.assertGreater(len(result.rows), 200)

    def test_cmb_transaction_pdf_route_matches_local_sample(self):
        module = load_input_router()
        pdf = (
            ROOT
            / "testdata"
            / "宁聚&付亮亮&徐美琴"
            / "付亮亮招商银行交易流水(申请时间2026年03月10日17时56分58秒).pdf"
        )

        result = module.read_rows(str(pdf))

        self.assertEqual(result.kind, "pdf")
        self.assertNotIn("parser", result.route_info)

        self.assertEqual(result.route_info["reader_id"], "pdfplumber_text_lines")
        self.assertEqual(result.route_info["decision"], "matched")
        self.assertEqual(result.route_info["bank"], "招商银行")
        self.assertEqual(result.route_info["account_type"], "个人")
        self.assertEqual(result.route_info["text_table_layout"], "currency")
        self.assertEqual(result.route_info["metadata_evidence"]["Producer"], "openhtmltopdf.com")
        self.assertEqual(result.route_info["date_format_evidence"], ["yyyy-mm-dd hh:mm:ss"])
        self.assertIn("记账日期", result.route_info["columns_evidence"])
        self.assertGreater(len(result.rows), 10)
        self.assertEqual(result.rows[0], ["记账日期", "货币", "交易金额", "联机余额", "交易摘要", "对手信息"])

    def test_srbank_corporate_statement_merges_cross_page_transaction(self):
        module = load_input_router()
        pdf = ROOT / "testdata" / "皓景-顾利斌" / "皓景近1年交易流水-上饶银行.pdf"
        if not pdf.exists():
            self.skipTest("本地未提供皓景上饶银行对公 PDF 样本")

        result = module.read_rows(str(pdf))
        route = result.route_info
        headers = result.rows[0]
        sequence_index = headers.index("序号")
        row_553 = next(row for row in result.rows[1:] if row[sequence_index] == "553")
        values = dict(zip(headers, row_553))

        self.assertEqual(route["reader_id"], "pdfplumber_table")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["fingerprint_id"], "md5:f2ac81b34fea59be828e6e5dbd017b63")
        self.assertEqual(route["bank"], "上饶银行")
        self.assertEqual(route["account_type"], "对公")
        self.assertEqual(
            route["row_anchor"],
            {
                "column": "序号",
                "pattern": r"^\d+$",
                "continuation": "until_next_anchor_across_pages",
            },
        )
        self.assertEqual(len(result.rows) - 1, 560)
        self.assertEqual(values["交易时间"], "2025-03-1414:52:19")
        self.assertEqual(values["对方账号"], "100101223011001005")
        self.assertEqual(values["支出"], "340")
        self.assertEqual(values["账户余额"], "59267.77")

    def test_boc_personal_statement_extracts_customer_name_from_preamble(self):
        module = load_input_router()
        pdf = ROOT / "testdata" / "郭金伟" / "1.pdf"
        if not pdf.exists():
            self.skipTest("本地未提供郭金伟中国银行个人流水 PDF 样本")

        result = module.read_rows(str(pdf))
        route = result.route_info

        self.assertEqual(route["reader_id"], "pdfplumber_table")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "中国银行")
        self.assertEqual(route["account_type"], "个人")
        self.assertEqual(route["fingerprint_id"], "md5:46cb259aaeb59d1ed620281dcd3f1714")
        self.assertEqual(
            route["preamble_extractors"],
            [{"field": "本方名称", "pattern": r"客户姓名[:：]\s*([^\s]+)"}],
        )
        self.assertIn("客户姓名", result.preamble)
        self.assertIn("郭金伟", result.preamble)
        self.assertGreater(len(result.rows), 10)

    def test_corporate_account_statement_pdf_route_does_not_infer_bank(self):
        module = load_input_router()
        pdf = ROOT / "testdata" / "宁聚&付亮亮&徐美琴" / "宁聚招商银行基本户1245.pdf"

        result = module.read_rows(str(pdf))

        self.assertEqual(result.kind, "pdf")
        self.assertNotIn("parser", result.route_info)

        self.assertEqual(result.route_info["reader_id"], "pdfplumber_line_table")
        self.assertEqual(result.route_info["decision"], "matched")
        self.assertEqual(result.route_info["bank"], "未识别")
        self.assertEqual(result.route_info["account_type"], "对公")
        self.assertEqual(result.route_info["metadata_evidence"]["Producer"], "OpenPDF 1.3.30")
        self.assertEqual(result.route_info["date_format_evidence"], ["yyyy-mm-dd"])
        self.assertIn("交易日期", result.route_info["columns_evidence"])
        self.assertEqual(result.rows[0], [
            "交易日期",
            "借方(出账)",
            "贷方(入账)",
            "余额",
            "摘要",
            "收(付)方名称",
            "收(付)方账号",
            "交易类型",
        ])
        self.assertEqual(len(result.rows) - 1, 162)

    def test_corporate_account_statement_excel_route_matches_openpyxl_sample(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "宁聚&付亮亮&徐美琴" / "宁聚招商银行基本户1245.xlsx"

        result = module.read_rows(str(excel))

        self.assertEqual(result.kind, "excel")
        self.assertNotIn("parser", result.route_info)

        self.assertEqual(result.route_info["reader_id"], "openpyxl_grid")
        self.assertEqual(result.route_info["decision"], "matched")
        self.assertEqual(result.route_info["bank"], "招商银行")
        self.assertEqual(result.route_info["account_type"], "对公")

    def test_card_detail_download_excel_route_does_not_infer_bank(self):
        module = load_input_router()
        excel = (
            ROOT
            / "testdata"
            / "广州沛瑾家具"
            / "广州沛瑾家具有限公司@李果红_中国工商银行_TF_1.xlsx"
        )

        result = module.read_rows(str(excel))

        self.assertEqual(result.kind, "excel")
        self.assertNotIn("parser", result.route_info)

        self.assertEqual(result.route_info["reader_id"], "openpyxl_grid")
        self.assertEqual(result.route_info["decision"], "matched")
        self.assertEqual(result.route_info["bank"], "未识别")
        self.assertEqual(result.route_info["account_type"], "个人")
        self.assertEqual(result.route_info["fingerprint_id"], "md5:5b60788dfc16d41a099768cd619f2641")
        self.assertEqual(result.route_info["source_order"], "descending")
        self.assertIsNone(result.route_info["column_mapping"]["交易金额(收入)"])
        self.assertIsNone(result.route_info["column_mapping"]["交易金额(支出)"])
        self.assertEqual(result.route_info["column_mapping"]["记账金额(收入)"], "收入金额")
        self.assertEqual(result.route_info["column_mapping"]["记账金额(支出)"], "支出金额")

    def test_jiujiang_bank_transaction_detail_excel_route(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "广源流水" / "九江银行交易明细1.xlsx"

        result = module.read_rows(str(excel))

        self.assertEqual(result.kind, "excel")
        self.assertNotIn("parser", result.route_info)

        self.assertEqual(result.route_info["reader_id"], "openpyxl_grid")
        self.assertEqual(result.route_info["decision"], "matched")
        self.assertEqual(result.route_info["bank"], "九江银行")
        self.assertEqual(result.route_info["account_type"], "对公")
        self.assertEqual(
            result.route_info["series_family"],
            "jiujiang_corporate_detail_grid_v1",
        )

    def test_jiujiang_bank_simple_transaction_export_does_not_infer_bank(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "广源流水" / "九江银行交易明细2.xlsx"

        result = module.read_rows(str(excel))

        self.assertEqual(result.kind, "excel")
        self.assertNotIn("parser", result.route_info)

        self.assertEqual(result.route_info["reader_id"], "openpyxl_grid")
        self.assertEqual(result.route_info["decision"], "matched")
        self.assertEqual(result.route_info["bank"], "未识别")
        self.assertEqual(result.route_info["account_type"], "对公")
        self.assertEqual(
            result.route_info["series_family"],
            "jiujiang_corporate_detail_grid_v1",
        )
        self.assertEqual(result.route_info["metadata_evidence"]["creator"], "openpyxl")
        self.assertEqual(result.route_info["metadata_evidence"]["last_modified_by"], "陈会开")
        self.assertEqual(result.route_info["date_format_evidence"], ["yyyy-mm-dd hh:mm:ss"])
        self.assertIn("交易时间", result.route_info["columns_evidence"])

    def test_jiujiang_bank_wide_transaction_export_does_not_infer_bank(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "昌浩公司流水" / "九江银行流水明细2025.5-2026.4.xlsx"
        if not excel.exists():
            self.skipTest("本地未提供九江银行宽表交易明细样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "未识别")
        self.assertEqual(route["account_type"], "未知")

    def test_jiujiang_bank_transaction_statement_pdf_route(self):
        module = load_input_router()
        pdf = ROOT / "testdata" / "广源流水" / "熊亮流水.pdf"

        result = module.read_rows(str(pdf))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "pdfplumber_text_lines")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "九江银行")
        self.assertEqual(route["account_type"], "个人")
        self.assertEqual(route["fingerprint_id"], "md5:99ce159ec31cbf24d7dc7279c6844048")
        self.assertEqual(route["text_table_layout"], "currency")
        self.assertEqual(
            route["preamble_extractors"],
            [
                {"field": "本方名称", "pattern": r"姓名[:：]\s*([^\s]+)"},
                {"field": "本方账户", "pattern": r"账号[:：]\s*(\d[\d*\s]{5,}\d)"},
            ],
        )
        self.assertEqual(route["date_format_evidence"], ["yyyy-mm-dd hh:mm:ss"])
        self.assertIn("记账日期", route["columns_evidence"])
        self.assertGreater(len(result.rows), 10)
        self.assertEqual(result.rows[0], ["记账日期", "货币", "交易金额", "联机余额", "交易摘要", "对手信息"])

    def test_cmbc_personal_statement_pdf_text_rows(self):
        module = load_input_router()
        pdf = ROOT / "testdata" / "范新春" / "20260527134259699999991324503110064813999998417140.pdf"
        if not pdf.exists():
            self.skipTest("本地未提供民生银行个人账户对账单 PDF 样本")

        result = module.read_rows(str(pdf))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "pdfplumber_text_lines")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "中国民生银行")
        self.assertEqual(route["account_type"], "个人")
        self.assertEqual(route["fingerprint_id"], "md5:907beda6d95ea54b2f6e380193726787")
        self.assertEqual(route["text_table_layout"], "cmbc_personal")
        self.assertEqual(
            route["preamble_extractors"],
            [
                {"field": "本方名称", "pattern": r"客户姓名[:：]\s*([^\s]+)"},
                {"field": "本方账户", "pattern": r"客户账号[:：]\s*(\d{8,})"},
            ],
        )
        self.assertEqual(len(route["extract_mapping"]), 2)
        self.assertEqual(route["extract_mapping"][0]["field"], "对手账户")
        self.assertEqual(route["extract_mapping"][1]["field"], "对手名称")
        self.assertGreater(len(result.rows), 10)
        self.assertEqual(
            result.rows[0],
            ["凭证类型", "凭证号码", "交易时间", "摘要", "交易金额", "账户余额",
             "现转标志", "交易渠道", "交易机构", "对方户名/账号", "对方行名"],
        )

    def test_industrial_bank_transaction_detail_pdf_route_matches_local_sample(self):
        module = load_input_router()
        pdf = ROOT / "testdata" / "曾耀夏伟鹏个人流水" / "夏伟鹏的交易明细20260422120619.pdf"
        if not pdf.exists():
            self.skipTest("本地未提供兴业银行交易流水 PDF 样本")

        result = module.read_rows(str(pdf))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "pdfplumber_table")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "兴业银行")
        self.assertEqual(route["account_type"], "个人")

    def test_wechat_pay_proof_pdf_route_matches_real_statement(self):
        module = load_input_router()
        pdf = ROOT / "testdata" / "张运贞" / "微信支付交易明细证明(20250601-20260601)_20260603100250.pdf"

        result = module.read_rows(str(pdf))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "pdfplumber_table")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "微信支付")
        self.assertEqual(route["account_type"], "个人")
        self.assertIn("交易单号", route["columns_evidence"])
        self.assertGreater(len(result.rows), 1)

    def test_alipay_proof_pdf_route_matches_real_statement(self):
        module = load_input_router()
        pdf = ROOT / "testdata" / "徐育发" / "支付宝交易明细(20250501-20260430).pdf"

        result = module.read_rows(str(pdf))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "pdfplumber_coordinate_table")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "支付宝")
        self.assertEqual(route["account_type"], "个人")
        self.assertIn("收/支", route["columns_evidence"])
        self.assertGreater(len(result.rows), 1)
        self.assertEqual(result.rows[0], ["收/支", "交易对方", "商品说明", "收/付款方式", "金额", "交易订单号", "商家订单号", "交易时间"])
        self.assertRegex(result.rows[1][7], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertIn("支付宝商家订单号=", result.rows[1][6])
        self.assertIn("支付宝交易订单号=", result.rows[1][6])

    def test_alipay_word_column_reader_continues_pages_without_repeated_header(self):
        module = load_input_router()
        pdf = ROOT / "testdata" / "吕建光" / "支付宝交易明细(20250701-20260630).pdf"

        result = module.read_rows(str(pdf))

        self.assertEqual(result.route_info["reader_id"], "pdfplumber_coordinate_table")
        self.assertGreater(len(result.rows), 1000)

    def test_jiangxi_rural_commercial_pdf_route_matches_watermarked_export(self):
        module = load_input_router()
        pdf = ROOT / "testdata" / "张华峰" / "江西·农商银行(2026年03月04日09时54分56秒).pdf"

        result = module.read_rows(str(pdf))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "pdfplumber_coordinate_table")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "江西农商银行")
        self.assertEqual(route["account_type"], "个人")
        self.assertEqual(route["date_format_evidence"], ["yyyy-mm-dd"])
        self.assertIn("记账日期", route["columns_evidence"])

    def test_jiangxi_rural_commercial_watermarked_excel_does_not_match_from_transaction_values(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "张华峰" / "江西·农商银行(2026年03月04日09时54分56秒).xlsx"
        if not excel.exists():
            self.skipTest("本地未提供张华峰江西农商 xlsx 样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "unmatched")
        self.assertEqual(route["bank"], "")
        self.assertEqual(route["account_type"], "")
        self.assertEqual(route["fingerprint_id"], "")

    def test_wechat_pay_proof_excel_with_dot_numeric_cells_route(self):
        module = load_input_router()
        excel = ROOT / "testdata" / "赵景楚" / "微信支付交易明细证明(20250521-20260521)——【解压密码可在微信支付公众号查看】(1).xlsx"
        if not excel.exists():
            self.skipTest("本地未提供带异常数字单元格的微信支付交易明细证明 Excel 样本")

        result = module.read_rows(str(excel))
        route = result.route_info

        self.assertNotIn("parser", route)

        self.assertEqual(route["reader_id"], "openpyxl_grid")
        self.assertEqual(route["decision"], "matched")
        self.assertEqual(route["bank"], "微信支付")
        self.assertEqual(route["account_type"], "个人")
        self.assertGreater(len(result.rows), 100)

if __name__ == "__main__":
    unittest.main()
