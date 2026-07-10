import importlib.util
import sys
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
CORE_PACKAGE = REPO_ROOT / "ymb-standardization-core"
if str(CORE_PACKAGE) not in sys.path:
    sys.path.insert(0, str(CORE_PACKAGE))

ROUTER_SPEC = importlib.util.spec_from_file_location(
    "router",
    CORE_PACKAGE / "ymb_standardization_core" / "readers" / "router.py")
router = importlib.util.module_from_spec(ROUTER_SPEC)
ROUTER_SPEC.loader.exec_module(router)

from ymb_standardization_core.readers.routing.rule_loader import fingerprint_md5  # noqa: E402
from ymb_standardization_core.readers.routing.rule_loader import load_pdf_route_rules  # noqa: E402
from ymb_standardization_core.readers.routing.rule_loader import PdfRouteRule  # noqa: E402


class PdfRouterDecisionTests(unittest.TestCase):
    def test_pdfplumber_table_rows_can_use_pdfplumber_line_table_reader(self):
        path = (
            ROOT / "testdata" / "斑马商业对公流水"
            / "斑马商业招行一般户（青山湖支行）-1221流水.1.pdf"
        )

        _preamble, rows, route_info = router.read_pdf_rows(str(path))

        self.assertEqual(route_info["reader_id"], "pdfplumber_line_table")
        self.assertEqual(route_info["fingerprint_id"], "md5:cb2278ac12a08930475e412c101c28f6")
        self.assertEqual(route_info["preamble_mapping"], {"用户所属公司": "本方名称"})
        self.assertEqual(rows[0], [
            "交易日期",
            "借方(出账)",
            "贷方(入账)",
            "余额",
            "摘要",
            "收(付)方名称",
            "收(付)方账号",
            "交易类型",
        ])
        self.assertEqual(len(rows) - 1, 1377)
        self.assertEqual(rows[1][0], "2025-01-15 17:42:45")
        self.assertEqual(rows[1][1], "1,564.14")
        self.assertEqual(rows[1][3], "411.64")
        self.assertEqual(rows[2][0], "2025-02-03 07:55:20")
        self.assertEqual(rows[2][5], "对公中间业务收入-网上其他收入")
        self.assertEqual(rows[2][6], "979154850070019810")
        self.assertEqual(rows[11][5], "上海寻梦信息技术有限公司")

    def test_pdfplumber_line_table_reader_does_not_call_default_extract_tables(self):
        path = (
            ROOT / "testdata" / "斑马商业对公流水"
            / "斑马商业招行一般户（青山湖支行）-1221流水.1.pdf"
        )
        original = router._extract_pdf_tables_default
        try:
            router._extract_pdf_tables_default = lambda _pdf: (_ for _ in ()).throw(
                AssertionError("pdfplumber_line_table must not call extract_tables()")
            )

            _preamble, rows, route_info = router.read_pdf_rows(str(path))

            self.assertEqual(route_info["reader_id"], "pdfplumber_line_table")
            self.assertEqual(len(rows) - 1, 1377)
        finally:
            router._extract_pdf_tables_default = original

    def test_pdfplumber_text_separator_table_reader_groups_multiline_rows(self):
        path = (
            ROOT / "testdata" / "罗美英"
            / "交易明细记录SHLSMX20260602415882_1.pdf"
        )

        _preamble, rows, route_info = router.read_pdf_rows(str(path))

        self.assertEqual(route_info["reader_id"], "pdfplumber_coordinate_table")
        self.assertEqual(route_info["fingerprint_id"], "md5:099d6dd5362e8052b2b079fac6ebf6e0")
        self.assertEqual(rows[0], [
            "交易时间",
            "存入/支取",
            "对方账号",
            "对方户名",
            "对方行",
            "交易后余额",
            "交易渠道",
            "摘要",
            "备注",
        ])
        self.assertGreater(len(rows), 100)
        self.assertEqual(rows[1], [
            "2025-06-04 09:29:38",
            "-30870.00",
            "62284823286 64065375",
            "朱小平",
            "中国农业银行股份 有限公司",
            "1525858.00",
            "核心渠道",
            "转帐",
            "２０２５０６０２货款",
        ])

    def test_pdfplumber_word_column_table_reader_groups_words_by_serial_number(self):
        cases = [
            (
                "程旭/江西嘟咔熊网商银行对账单2025.1.1-2025.12.31.pdf",
                1088,
                ["1", "202501011112 052015470049 0932671", "2025-01-01 00:01:52"],
            ),
            (
                "程旭/鼎信网商银行2025.1.1-2025.7.31交易明细.pdf",
                1402,
                ["1", "202501011112 052015690008 7157781", "2025-01-01 00:01:52"],
            ),
            (
                "程旭/鼎信网商银行2025.8.1-2025.12.31交易明细.pdf",
                2371,
                ["1", "202508011112 052015690042 6641531", "2025-08-01 00:01:52"],
            ),
        ]

        for relative_path, expected_rows, expected_prefix in cases:
            with self.subTest(relative_path=relative_path):
                path = ROOT / "testdata" / relative_path

                _preamble, rows, route_info = router.read_pdf_rows(str(path))

                self.assertEqual(route_info["reader_id"], "pdfplumber_coordinate_table")
                self.assertEqual(route_info["fingerprint_id"], "md5:6cadae92bf0342082ec8ce1556cf1ac0")
                self.assertEqual(rows[0], route_info["reader_headers"])
                self.assertEqual(rows[0], [
                    "序号",
                    "账务流水号",
                    "提交时间",
                    "交易时间",
                    "交易名称",
                    "借方金额（收）",
                    "贷方金额（支）",
                    "余额",
                    "对方户名",
                    "对方账号",
                    "对方机构",
                    "备注",
                ])
                self.assertEqual(len(rows) - 1, expected_rows)
                self.assertEqual(rows[1][:3], expected_prefix)

    def test_pdfplumber_word_column_table_reader_uses_composite_header_from_columns(self):
        path = ROOT / "testdata" / "陈国付103135" / "26060214275857136186.pdf"

        _preamble, rows, route_info = router.read_pdf_rows(str(path))

        self.assertEqual(route_info["reader_id"], "pdfplumber_coordinate_table")
        self.assertEqual(rows[0], [
            "交易日期",
            "交易时间",
            "交易摘要",
            "交易金额",
            "本次余额",
            "对手信息",
            "日 志 号",
            "交易渠道",
            "交易附言",
        ])
        self.assertEqual(len(rows) - 1, 395)
        self.assertEqual(rows[6], [
            "20250608",
            "",
            "短信费",
            "-2.50",
            "670.14",
            "081701940050307",
            "R016358142",
            "",
            "短信费",
        ])
        self.assertEqual(rows[23][5], "国网江西省电力有限公司")
        self.assertEqual(rows[23][8], "")
        self.assertEqual(rows[51][7], "大额支付")
        self.assertEqual(rows[51][8], "网商银行转账")

    def test_jiangxi_yumin_pdf_uses_word_column_table_reader(self):
        path = ROOT / "testdata" / "陈国付103135" / "APPLY2026060214573700135618149968_trade_history_sign.pdf"

        _preamble, rows, route_info = router.read_pdf_rows(str(path))

        self.assertEqual(route_info["reader_id"], "pdfplumber_coordinate_table")
        self.assertEqual(rows[0], [
            "交易日期",
            "业务摘要",
            "交易金额(元)",
            "账户余额(元)",
            "交易币种",
            "交易类别",
            "对方户名",
            "对方账号",
        ])
        self.assertEqual(len(rows) - 1, 289)
        self.assertEqual(rows[7], [
            "2025-06-30",
            "转出",
            "50.00",
            "1194.45",
            "人民币",
            "行内转账支取（非支票）",
            "陈国泉",
            "6236433910000395765",
        ])
        self.assertEqual(rows[22][6], "丰城华英种鸭有限公司")
        self.assertEqual(rows[22][7], "14081901040002355")
        self.assertEqual(rows[26][5], "银联全渠道贷记发卡侧入账")
        self.assertEqual(rows[26][6], "银联待清算往来")

    def test_jiangxi_rural_commercial_pdf_uses_word_column_table_reader(self):
        path = ROOT / "testdata" / "艾晓林" / "江西·农商银行(2026年05月20日11时29分50秒)-2.pdf"

        _preamble, rows, route_info = router.read_pdf_rows(str(path))

        self.assertEqual(route_info["reader_id"], "pdfplumber_coordinate_table")
        self.assertEqual(rows[0], [
            "记账日期",
            "交易金额(元)",
            "交易后余额(元)",
            "交易摘要",
            "对方户名",
            "对方账号",
        ])
        self.assertEqual(len(rows) - 1, 1328)
        target = next(row for row in rows[1:] if row[0] == "2025-06-12" and row[1] == "-41,100.00")
        self.assertEqual(target, [
            "2025-06-12",
            "-41,100.00",
            "832,420.54",
            "跨行转出-南昌巨鲸农牧发展有限公司",
            "南昌巨鲸农牧发展有限公司",
            "36050182035200000593",
        ])

    def test_zhejiang_qyrcb_pdf_uses_coordinate_table_reader_with_anchor_blocks(self):
        path = ROOT / "testdata" / "李先根" / "GRZD-9A202606081958362818-20250608-20260607-X_unsign_sign_18831.pdf"
        if not path.exists():
            self.skipTest("本地未提供李先根 GRZD 浙江庆元农商 PDF 样本")

        _preamble, rows, route_info = router.read_pdf_rows(str(path))

        self.assertEqual(route_info["reader_id"], "pdfplumber_coordinate_table")
        self.assertEqual(route_info["fingerprint_id"], "md5:eb90af33b5f89117b801f28b10fdc111")
        self.assertEqual(rows[0], [
            "交易日期",
            "币种",
            "交易摘要",
            "交易金额",
            "账户余额",
            "对方账号",
            "对方户名",
            "对方行",
            "交易渠道",
            "备注",
        ])
        self.assertEqual(len(rows) - 1, 240)
        target = next(
            row for row in rows[1:]
            if row[0] == "2025-06-16" and row[2] == "汇出" and row[3] == "-168.65"
        )
        self.assertEqual(target[5], "201000022997361")
        self.assertEqual(target[6], "庆元县供排水有限公司水费专户")
        self.assertEqual(target[8], "人行接口（大小额）")

    def test_pdf_specialized_routes_are_loaded_from_config(self):
        rules = load_pdf_route_rules()

        by_id = {rule.id: rule for rule in rules}
        for fingerprint_id in [
            "md5:cd253a8df83a6adee5ab5e047e54bc4e",
            "md5:0bdf0854f29ad6928e2fdd0da1d52dc5",
            "md5:b75cf43e9a35b4ca0c082906f3aa2c7b",
            "md5:eb90af33b5f89117b801f28b10fdc111",
            "md5:0488448d0f1d96413a25254a500aab29",
            "md5:a18a99243ad411aa342820d09cbcdaf1",
        ]:
            self.assertIn(fingerprint_id, by_id)
        self.assertEqual(rules[0].bank, "中国农业银行")
        self.assertEqual(rules[0].file_type, "pdf")
        self.assertTrue(rules[0].id.startswith("md5:"))

        self.assertEqual(by_id["md5:cd253a8df83a6adee5ab5e047e54bc4e"].account_type, "个人")
        for marker in ["交易日期", "交易时间", "交易摘要", "交易金额", "本次余额", "对手信息", "日 志 号", "交易渠道", "交易附言"]:
            self.assertIn(marker, by_id["md5:cd253a8df83a6adee5ab5e047e54bc4e"].column_markers)
        self.assertEqual(by_id["md5:0488448d0f1d96413a25254a500aab29"].account_type, "对公")
        self.assertEqual(by_id["md5:aecf32d3b7fafab4b468106cd8a06d3a"].account_type, "对公")
        for marker in ["收/支/其他", "金额(元)", "交易对方", "商户单号"]:
            self.assertIn(marker, by_id["md5:a18a99243ad411aa342820d09cbcdaf1"].column_markers)

    def test_pdf_route_config_uses_fingerprint_columns_for_layout_and_mapping(self):
        rules_path = CORE_PACKAGE / "ymb_standardization_core" / "readers" / "routing" / "pdf_rules.yaml"
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

    def test_wechat_pay_proof_pdf_route_requires_full_statement_header(self):
        text = (
            "微信支付交易明细证明 兹证明 交易明细对应时间段 具体交易明细 "
            "交易单号 交易时间 交易类型 收/支/其他 交易方式 金额(元) 交易对方 商户单号"
        )

        result = router.route_pdf(text, 1, 1)

        self.assertNotIn("parser", result)
        self.assertEqual(result["reader_id"], "pdfplumber_table")
        self.assertEqual(result["column_mapping"]["交易时间"], "交易时间")
        self.assertEqual(result["column_mapping"]["金额(元)"], "交易金额")
        self.assertTrue(result["fingerprint_id"].startswith("md5:"))
        self.assertEqual(result["decision"], "matched")

    def test_wechat_pay_proof_wps_pdf_route_allows_truncated_table_extract(self):
        text = (
            "微信支付交易明细证明 兹证明 交易明细对应时间段 具体交易明细 "
            "交易单号 交易时间 交易类型 收/支/其他 交易方式 金额(元) 交易对方 商户单号"
        )
        context = {
            "metadata": {"Creator": "WPS 表格"},
            "date_patterns": ["yyyy-mm-dd hh:mm:ss"],
            "lines": ["交易单号 交易时间 交易类型 收/支/其他 交易方式 金额(元)"],
        }

        result = router.route_pdf(text, 1, 1, context=context)

        self.assertNotIn("parser", result)
        self.assertEqual(result["fingerprint_id"], "md5:a18a99243ad411aa342820d09cbcdaf1")
        self.assertEqual(result["reader_id"], "pdfplumber_table")
        self.assertEqual(result["decision"], "matched")

    def test_identity_only_kasikorn_markers_do_not_create_ambiguous_route(self):
        text = (
            "中国农业银行账户活期交易明细清单 "
            "交易日期 交易时间 交易摘要 交易金额 本次余额 对手信息 日 志 号 交易渠道 交易附言 "
            "K PLUS K BIZ AccountMR. Account Number "
            "01-01-26 10:00 Transfer 100.00 "
            "02-01-26 10:00 Transfer 100.00 "
            "03-01-26 10:00 Transfer 100.00 "
            "04-01-26 10:00 Transfer 100.00 "
            "05-01-26 10:00 Transfer 100.00"
        )

        result = router.route_pdf(text, 0, 1)

        self.assertNotIn("parser", result)
        self.assertIn(result["fingerprint_id"], {'md5:cd253a8df83a6adee5ab5e047e54bc4e'})
        self.assertEqual(result["decision"], "matched")

    def test_abc_pdf_route_requires_transaction_header_columns(self):
        result = router.route_pdf("中国农业银行账户活期交易明细清单", 0, 1)

        self.assertNotIn("parser", result)
        self.assertEqual(result["decision"], "unmatched")
        self.assertEqual(result["reader_id"], "none")

    def test_specialized_route_without_yaml_fingerprint_falls_back_to_generic(self):
        original = router.load_pdf_route_rules
        try:
            router.load_pdf_route_rules = lambda: [
                PdfRouteRule(
                    id="md5:test",
                                        reader_id="pdfplumber_table",
                    file_type="pdf",
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

            result = router.route_pdf("测试银行 交易时间 账户余额", 0, 1)

            self.assertNotIn("parser", result)
            self.assertEqual(result["decision"], "unmatched")
            self.assertEqual(result["reader_id"], "none")
            self.assertIn("candidate_fingerprints", result)
            self.assertEqual(result["candidate_fingerprints"][0]["reader_id"], "pdfplumber_table")
            self.assertEqual(result["candidate_fingerprints"][0]["reason"], "missing_yaml_fingerprint")
        finally:
            router.load_pdf_route_rules = original

    def test_multiple_strict_fingerprint_matches_are_ambiguous(self):
        original = router.load_pdf_route_rules
        rules = []
        for parser_name in ("first_pdf", "second_pdf"):
            rules.append(PdfRouteRule(
                id=f"md5:{parser_name}",
                                reader_id="pdfplumber_table",
                file_type="pdf",
                bank="测试银行",
                account_type="未知",
                column_mapping={},
                identity_any=["测试银行"],
                column_markers=["交易时间", "账户余额"],
                metadata_all={"Producer": "UnitTest"},
                style_all=[],
                date_format_any=[],
                has_fingerprint=True,
            ))
        try:
            router.load_pdf_route_rules = lambda: rules
            context = {"metadata": {"Producer": "UnitTest"}, "styles": [], "lines": [], "date_patterns": []}

            result = router.route_pdf("测试银行 交易时间 账户余额", 0, 1, context=context)

            self.assertNotIn("parser", result)
            self.assertEqual(result["reader_id"], "none")
            self.assertEqual(result["decision"], "ambiguous")
            self.assertEqual([c["fingerprint_id"] for c in result["candidates"]], ["md5:first_pdf", "md5:second_pdf"])
        finally:
            router.load_pdf_route_rules = original

    def test_specialized_pdf_route_exposes_identity_and_columns_evidence(self):
        text = (
            "江西·农商银行交易流水 江西·农商银行 户 名 张华峰 账 号 6226822011500474554 起止日期 "
            "记账日期 交易金额(元) 交易后余额(元) 交易摘要 对方户名 对方账号 "
            "2025-01-01 1.00 2.00 2025-01-02 1.00 3.00 2025-01-03 1.00 4.00 "
            "2025-01-04 1.00 5.00 2025-01-05 1.00 6.00"
        )
        context = {"lines": text.splitlines(), "date_patterns": ["yyyy-mm-dd"]}

        result = router.route_pdf(text, 0, 1, context=context)

        self.assertNotIn("parser", result)
        self.assertIn(result["fingerprint_id"], {'md5:0bdf0854f29ad6928e2fdd0da1d52dc5'})
        self.assertEqual(result["decision"], "matched")
        self.assertEqual(result["bank"], "江西农商银行")
        self.assertEqual(result["file_type"], "pdf")
        self.assertTrue(result["id"].startswith("md5:"))
        self.assertIn("江西·农商银行", result["identity_evidence"])
        self.assertIn("户 名", result["columns_evidence"])
        self.assertEqual(result["date_format_evidence"], ["yyyy-mm-dd"])

    def test_identity_only_jiangxi_rural_commercial_pdf_does_not_match(self):
        text = (
            "江西·农商银行 户 名 张华峰 账 号 6226822011500474554 起止日期"
        )

        result = router.route_pdf(text, 0, 1)

        self.assertNotIn("parser", result)
        self.assertEqual(result["decision"], "unmatched")
        self.assertEqual(result["reader_id"], "none")
        self.assertEqual(result["decision"], "unmatched")

    def test_table_pdf_routes_to_specialized_icbc_parser(self):
        text = (
            "中国工商银行账户明细清单 账号：1502000209100022223 币种：人民币 "
            "交易时间 本方账号 对方户名 对方账号 对方账户开户行 凭证号 "
            "借/贷 借方发生额 贷方发生额 摘要 用途 余额"
        )

        result = router.route_pdf(text, 1, 1)

        self.assertNotIn("parser", result)
        self.assertIn(result["fingerprint_id"], {'md5:0488448d0f1d96413a25254a500aab29'})
        self.assertEqual(result["decision"], "matched")
        self.assertEqual(result["bank"], "中国工商银行")
        self.assertEqual(result["file_type"], "pdf")
        self.assertEqual(result["account_type"], "对公")

    def test_corporate_online_detail_table_pdf_route(self):
        text = (
            "企业网上银行--账户管理/明细查询 单位名称 账号 开户行 户名 "
            "交易时间 转出金额 转入金额 币种 余额 对方单位 对方账号 摘要 操作 详细信息"
        )

        result = router.route_pdf(text, 1, 1)

        self.assertNotIn("parser", result)
        self.assertIn(result["fingerprint_id"], {'md5:fcbe39d694aa34cf564500f187aa8137'})
        self.assertEqual(result["decision"], "matched")
        self.assertEqual(result["bank"], "未识别")
        self.assertEqual(result["account_type"], "对公")

    def test_jiujiang_bank_corporate_detail_pdf_route(self):
        text = (
            "交易明细清单 查询账号:787079100000024212 开户银行:九江银行股份有限公司南昌分行营业部 "
            "账户名称:南昌宁聚商贸有限公司 交易时间范围:2026-01-01至2026-03-10 "
            "交易时间 收入(元) 支出(元) 余额(元) 对方账号 对方户名 摘要 交易用途"
        )

        result = router.route_pdf(text, 1, 1)

        self.assertNotIn("parser", result)
        self.assertIn(result["fingerprint_id"], {'md5:cdccd6123047eb2b26165e7e19d4e205'})
        self.assertEqual(result["decision"], "matched")
        self.assertEqual(result["bank"], "九江银行")
        self.assertEqual(result["account_type"], "对公")

    def test_electronic_transaction_proof_pdf_route(self):
        text = (
            "中国光大银行账户明细查询清单 Transaction Statement of China Everbright Bank "
            "交易日期 支出金额 存入金额 账户余额 对手信息 摘要 "
            "Trans Date Trans Amt Dr Trans Amt Cr Account Balance Payment Receipt Account Information Abstract "
            "2025-08-30 89.00 660.55 支付宝 网上支付"
        )

        result = router.route_pdf(text, 1, 1, context={"date_patterns": ["yyyy-mm-dd"]})

        self.assertNotIn("parser", result)
        self.assertIn(result["fingerprint_id"], {'md5:ae05becb79352db902ea07365adcc6fa'})
        self.assertEqual(result["decision"], "matched")
        self.assertEqual(result["bank"], "中国光大银行")

    def test_corporate_debit_credit_timestamp_pdf_route(self):
        text = (
            "交易时间 借贷标志 对方单位 摘要 转出金额 转入金额 余额 时间戳 "
            "2025-07-31 17:57:53 借 赣州市百诚工程咨询有限公司 保证金 10000.00 214746.53"
        )

        result = router.route_pdf(text, 1, 1, context={"date_patterns": ["yyyy-mm-dd hh:mm:ss"]})

        self.assertNotIn("parser", result)
        self.assertIn(result["fingerprint_id"], {'md5:faa7f06d9f76df95dee8e82cb190016f'})
        self.assertEqual(result["decision"], "matched")
        self.assertEqual(result["bank"], "未识别")

    def test_corporate_online_transaction_detail_pdf_does_not_infer_bank(self):
        text = (
            "交易明细 序号 交易流水号 交易时间 对方户名 对方账号 对方账户开户网点 "
            "支出 收入 账户余额 批次号 总笔数 附言 摘要 "
            "2025-12-27 19:45:07 江西万宏纺织有限公司 中国建设银行 13284 2980.7 纱 超网-贷记转出"
        )

        context = {
            "metadata": {"Creator": "JasperReports (testReport)", "Producer": "iText 2.1.7 by 1T3XT"},
            "styles": [{"text": "交易明细", "font": "STSong-Light", "size": 24, "top": 21, "x0": 260, "x1": 335, "page_width": 595}],
            "date_patterns": ["yyyy-mm-dd hh:mm:ss"],
        }

        result = router.route_pdf(text, 1, 1, context=context)

        self.assertNotIn("parser", result)
        self.assertIn(result["fingerprint_id"], {'md5:b175ebd2e387d181fe3c4a5fbdfa998d'})
        self.assertEqual(result["decision"], "matched")
        self.assertEqual(result["bank"], "未识别")

    def test_icbc_account_detail_pdf_allows_title_and_header_on_separate_lines(self):
        lines = [
            "中国工商银行账户明细清单",
            "账号： 1502000209100022223 币种： 人民币 单位： 元",
            "本方账号户名： 南昌玺诚房地产营销策划有限公司 本方账号开户行： 工行南昌市南昌银湖 时间范围： 20250601 - 20251130",
            "交易时间 本方账号 对方户名 对方账号 对方账户开户行 凭证号 借/贷 借方发生额 贷方发生额 摘要 用途 余额",
        ]

        result = router.route_pdf("\n".join(lines), 1, 1, context={"lines": lines})

        self.assertNotIn("parser", result)
        self.assertIn(result["fingerprint_id"], {'md5:0488448d0f1d96413a25254a500aab29'})
        self.assertEqual(result["decision"], "matched")
        self.assertEqual(result["bank"], "中国工商银行")
        self.assertEqual(result["account_type"], "对公")

    def test_srbank_personal_history_pdf_route_is_distinct_from_corporate_detail(self):
        lines = [
            "上饶银行历史交易流水",
            "户名：付亮亮 账号：6214169112455813",
            "流水日期：2026/03/01 - 2026/03/10 开户行：南昌县支行",
            "申请时间：2026-03-10 17:45:22",
            "序号 交易日期 交易时间 交易金额 余额 对方银行 对方户名 摘要",
            "卡号",
        ]
        context = {
            "metadata": {},
            "styles": [
                {
                    "text": "上饶银行历史交易流水",
                    "font": "STSongStd-Light",
                    "size": 20,
                    "top": 62.4,
                    "x0": 197.5,
                    "x1": 397.5,
                    "page_width": 595,
                }
            ],
            "lines": lines,
            "date_patterns": ["yyyy-mm-dd hh:mm:ss"],
        }

        result = router.route_pdf("\n".join(lines), 1, 1, context=context)

        self.assertNotIn("parser", result)
        self.assertEqual(result["fingerprint_id"], "md5:7f811c14e0a4fdfc0d0efeaf64be0210")
        self.assertEqual(result["decision"], "matched")
        self.assertEqual(result["bank"], "上饶银行")
        self.assertEqual(result["account_type"], "个人")

        corporate_lines = [
            "上饶银行账户交易明细",
            "序号 交易时间 流水号 对方账号 对方户名 支出 收入 账户余额 摘要 附言",
        ]
        corporate_result = router.route_pdf(
            "\n".join(corporate_lines),
            1,
            1,
            context={"lines": corporate_lines, "date_patterns": ["yyyy-mm-dd hh:mm:ss"]},
        )
        self.assertNotIn("parser", corporate_result)
        self.assertEqual(corporate_result["decision"], "unmatched")
        self.assertEqual(corporate_result["reader_id"], "pdfplumber_table")

    def test_icbc_debit_history_electronic_pdf_route(self):
        text = (
            "中国工商银行借记账户历史明细（电子版） 卡号 6215581502001090422 户名：秦国有 "
            "交易日期 账号 储种 序号 币种 钞汇 地区 收入/支出金额 余额 渠道"
        )
        context = {
            "metadata": {"Creator": "PDFium", "Producer": "PDFium"},
            "date_patterns": ["yyyy-mm-dd"],
            "styles": [],
            "lines": text.splitlines(),
        }

        result = router.route_pdf(text, 1, 1, context=context)

        self.assertNotIn("parser", result)
        self.assertIn(result["fingerprint_id"], {'md5:f6e2839cbb6173153037ad740c0be800', 'md5:c32bf342dabb921d88641d06db8b4b54', 'md5:90b7f0bdd1ba30c6488303e4c29eeb14'})
        self.assertEqual(result["decision"], "matched")
        self.assertEqual(result["account_type"], "个人")

    def test_abc_corporate_account_detail_pdf_route(self):
        text = (
            "账户明细 账号：14-016301040004172 户名：新建区西山罗广兴旺家电店 币种：人民币 起止日期 "
            "交易时间 收入金额 支出金额 账户余额 交易行名 对方城市 对方账号 对方户名 交易用途 会计日期"
        )
        context = {
            "metadata": {"Producer": "OpenPDF 1.3.32"},
            "date_patterns": [],
            "styles": [],
            "lines": text.splitlines(),
        }

        result = router.route_pdf(text, 1, 1, context=context)

        self.assertNotIn("parser", result)
        self.assertIn(result["fingerprint_id"], {'md5:6c0358919ac011286e44822dbcd66c8b'})
        self.assertEqual(result["decision"], "matched")
        self.assertEqual(result["bank"], "中国农业银行")

    def test_icbc_corporate_account_statement_pdf_route(self):
        text = (
            "中国工商银行账户明细清单 账号：1512201409000016548 本方账号户名 本方账号开户行 时间范围 "
            "对方账号 交易时间 借贷标志 对方单位 用途 摘要 附言 回单个性化信息 转出金额 转入金额 余额"
        )
        context = {
            "metadata": {"Producer": "iText 2.1.7 by 1T3XT"},
            "date_patterns": ["yyyy-mm-dd hh:mm:ss"],
            "styles": [],
            "lines": text.splitlines(),
        }

        result = router.route_pdf(text, 1, 1, context=context)

        self.assertNotIn("parser", result)
        self.assertIn(result["fingerprint_id"], {'md5:84a33d2b19cac75ce0e72118080eb538'})
        self.assertEqual(result["decision"], "matched")
        self.assertEqual(result["account_type"], "对公")

    def test_icbc_debit_history_wps_pdf_route(self):
        text = (
            "中国工商银行借记账户历史明细（电子版） 卡号 户名：徐长河 起止日期 "
            "交易日期 账号 储种 序号 币种 钞汇 摘要 地区 收入/支出金额"
        )
        context = {
            "metadata": {"Creator": "WPS 表格", "Title": "借记卡账户明细清单"},
            "date_patterns": ["yyyy-mm-dd hh:mm:ss"],
            "styles": [],
            "lines": text.splitlines(),
        }

        result = router.route_pdf(text, 1, 1, context=context)

        self.assertNotIn("parser", result)
        self.assertIn(result["fingerprint_id"], {'md5:f6e2839cbb6173153037ad740c0be800', 'md5:c32bf342dabb921d88641d06db8b4b54', 'md5:90b7f0bdd1ba30c6488303e4c29eeb14'})
        self.assertEqual(result["decision"], "matched")
        self.assertEqual(result["account_type"], "个人")

    def test_icbc_debit_history_openpdf_pdf_route(self):
        text = (
            "中国工商银行借记账户历史明细（电子版） 卡号 户名：夏侯军刚 起止日期 "
            "交易日期 账号 储种 序号 币种 钞汇 摘要 地区 收入/支出金额 余额 对方户名 对方账号 渠道"
        )
        context = {
            "metadata": {"Producer": "OpenPDF 1.3.27", "Title": "借记卡账户明细清单"},
            "date_patterns": ["yyyy-mm-dd hh:mm:ss"],
            "styles": [],
            "lines": text.splitlines(),
        }

        result = router.route_pdf(text, 1, 1, context=context)

        self.assertNotIn("parser", result)
        self.assertIn(result["fingerprint_id"], {'md5:f6e2839cbb6173153037ad740c0be800', 'md5:c32bf342dabb921d88641d06db8b4b54', 'md5:90b7f0bdd1ba30c6488303e4c29eeb14'})
        self.assertEqual(result["decision"], "matched")
        self.assertEqual(result["account_type"], "个人")

    def test_industrial_bank_transaction_detail_pdf_route(self):
        text = (
            "兴业银⾏交易流⽔ Bank Transaction Details Transaction Time Accounting Date "
            "Transaction Type Transaction Amount Account Balance Counterparty’s "
            "Counterparty’s Account No."
        )
        context = {
            "date_patterns": ["yyyy-mm-dd hh:mm:ss"],
            "styles": [],
            "lines": text.splitlines(),
        }

        result = router.route_pdf(text, 1, 1, context=context)

        self.assertNotIn("parser", result)
        self.assertEqual(result["fingerprint_id"], "md5:a70735a0cf8fd249d144458896f7346c")
        self.assertEqual(result["decision"], "matched")
        self.assertEqual(result["bank"], "兴业银行")
        self.assertEqual(result["account_type"], "个人")

    def test_srbank_corporate_transaction_detail_pdf_route(self):
        text = (
            "上饶银行账户交易明细 账号：209103090000064662 户名：上饶市皓景光电科技有限公司 "
            "银行盖章： 打印时间：2026-03-11 操作员号：200158192402 "
            "序号 交易时间 流水号 对方账号 对方户名 支出 收入 账户余额 摘要 附言"
        )

        result = router.route_pdf(text, 1, 1)

        self.assertNotIn("parser", result)
        self.assertIn(result["fingerprint_id"], {'md5:c17d8fd6703ef2b8dab16bb0d02d16ef'})
        self.assertEqual(result["decision"], "matched")
        self.assertEqual(result["bank"], "上饶银行")
        self.assertEqual(result["account_type"], "对公")

    def test_mybank_corporate_statement_pdf_route(self):
        text = (
            "企业名称 江西嘟咔熊电子商务有限公司 企业账号 8888888826100206(人民币) "
            "借方交易笔数 935笔 借方交易金额 11562010.64元 "
            "贷方交易笔数 153笔 贷方交易金额 11565004.40元 "
            "序号 账务流水号 提交时间 交易时间 交易名称 借方金额（收） "
            "贷方金额（支） 余额 对方户名 对方账号 对方机构 备注"
        )

        result = router.route_pdf(text, 1, 1)

        self.assertNotIn("parser", result)
        self.assertIn(result["fingerprint_id"], {'md5:6cadae92bf0342082ec8ce1556cf1ac0'})
        self.assertEqual(result["decision"], "matched")
        self.assertEqual(result["bank"], "浙江网商银行")
        self.assertEqual(result["account_type"], "对公")


if __name__ == "__main__":
    unittest.main()
