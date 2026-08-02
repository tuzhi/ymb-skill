import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime import tag as tag_module
from runtime.tag import (
    _apply_alipay_order_reversals,
    _apply_transaction_relations,
    direction_series,
    load_rules,
    match,
)

class TagOptimizedTest(unittest.TestCase):
    def test_direction_series_matches_row_direction_logic(self):
        df = pd.DataFrame(
            [
                {"收入金额": "100", "支出金额": ""},
                {"收入金额": "", "支出金额": "20"},
                {"收入金额": "50", "支出金额": "10"},
                {"收入金额": "", "支出金额": ""},
            ]
        )

        expected = ["收入", "支出", "收入", "未知"]

        self.assertEqual(direction_series(df).tolist(), expected)

    def test_grouped_rule_index_preserves_rule_order_for_same_condition(self):
        rules = pd.DataFrame(
            [
                {
                    "规则编号": "R001",
                    "适用方向": "收入",
                    "依据字段": "银行备注",
                    "匹配方式": "包含",
                    "关键词": "工资",
                    "排除关键词": "",
                    "对手名称含": "",
                    "一级标签": "经营类",
                    "二级标签": "人力",
                    "三级标签": "工资",
                    "优先级": "900",
                    "备注": "",
                },
                {
                    "规则编号": "R002",
                    "适用方向": "收入",
                    "依据字段": "银行备注",
                    "匹配方式": "包含",
                    "关键词": "奖金",
                    "排除关键词": "",
                    "对手名称含": "",
                    "一级标签": "经营类",
                    "二级标签": "人力",
                    "三级标签": "工资",
                    "优先级": "900",
                    "备注": "",
                },
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rules.csv"
            rules.to_csv(path, index=False, encoding="utf-8-sig")

            buckets = load_rules(path)

        self.assertIn("__groups__", buckets)
        self.assertEqual(len(buckets["__groups__"]["收入"]), 1)

        row = pd.Series({"对手名称": "", "银行备注": "奖金和工资一起发放", "账户方附言": ""})
        rule, hit_field = match(row, "收入", buckets)

        self.assertEqual(rule["编号"], "R001")
        self.assertEqual(rule["关键词"], "工资")
        self.assertEqual(hit_field, "银行备注")

    def test_grouped_rule_index_does_not_reorder_interleaved_groups(self):
        rules = pd.DataFrame(
            [
                {
                    "规则编号": "R001",
                    "适用方向": "收入",
                    "依据字段": "账户方附言",
                    "匹配方式": "包含",
                    "关键词": "不会命中",
                    "排除关键词": "",
                    "对手名称含": "",
                    "一级标签": "经营类",
                    "二级标签": "主营业务",
                    "三级标签": "销售收入",
                    "优先级": "900",
                    "备注": "",
                },
                {
                    "规则编号": "R002",
                    "适用方向": "收入",
                    "依据字段": "银行备注",
                    "匹配方式": "包含",
                    "关键词": "款",
                    "排除关键词": "",
                    "对手名称含": "",
                    "一级标签": "经营类",
                    "二级标签": "主营业务",
                    "三级标签": "销售收入",
                    "优先级": "900",
                    "备注": "",
                },
                {
                    "规则编号": "R003",
                    "适用方向": "收入",
                    "依据字段": "账户方附言",
                    "匹配方式": "包含",
                    "关键词": "收单清算",
                    "排除关键词": "",
                    "对手名称含": "",
                    "一级标签": "经营类",
                    "二级标签": "主营业务",
                    "三级标签": "销售收入",
                    "优先级": "900",
                    "备注": "",
                },
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rules.csv"
            rules.to_csv(path, index=False, encoding="utf-8-sig")

            buckets = load_rules(path)

        row = pd.Series({"对手名称": "", "银行备注": "客户入款", "账户方附言": "收单清算"})
        rule, hit_field = match(row, "收入", buckets)

        self.assertEqual(rule["编号"], "R002")
        self.assertEqual(rule["关键词"], "款")
        self.assertEqual(hit_field, "银行备注")

    def test_baby_balance_product_rules_tag_as_internal_transfer(self):
        buckets = load_rules(ROOT / "assets" / "tag_rules.csv")

        cases = [
            ("收入", "收入金额", "零钱通转出-到零钱"),
            ("支出", "支出金额", "转入零钱通-来自零钱"),
            ("收入", "收入金额", "零钱充值"),
            ("支出", "支出金额", "零钱提现"),
            ("支出", "支出金额", "提现-实时提现"),
            ("收入", "收入金额", "余额宝-2026.04.29-收益"),
            ("支出", "支出金额", "余额宝-自动转入"),
            ("收入", "收入金额", "余额升级服务收益发放"),
        ]
        for direction, amount_col, memo in cases:
            row = pd.Series({
                "对手名称": "",
                "银行备注": memo,
                "账户方附言": "",
                "收入金额": "100" if amount_col == "收入金额" else "",
                "支出金额": "100" if amount_col == "支出金额" else "",
            })
            rule, hit_field = match(row, direction, buckets)

            self.assertIsNotNone(rule)
            self.assertEqual(rule["L1"], "内部调拨类")
            self.assertEqual(rule["L2"], "自有资金调拨")
            self.assertEqual(rule["L3"], "类现金余额产品调拨")
            self.assertEqual(hit_field, "银行备注")

    def test_alipay_cancel_pair_zeroes_analysis_amounts_and_tags_refund(self):
        df = pd.DataFrame([
            {
                "交易唯一编号": "TX-original",
                "本方账户": "alipay-account",
                "来源文件名": "支付宝交易明细.pdf",
                "银行备注": "95新联想拯救者",
                "账户方附言": "支付宝商家订单号=T200P1；支付宝交易订单号=ORDER1",
                "收入金额": "",
                "支出金额": "8200.00",
                "收支方向": "支出",
                "一级标签": "经营类",
                "二级标签": "主营业务",
                "三级标签": "采购支出",
                "标签来源": "规则库",
                "标签置信度": "0.72",
                "命中规则编号": "R",
                "命中关键词": "订单",
                "命中字段": "账户方附言",
            },
            {
                "交易唯一编号": "TX-cancel",
                "本方账户": "alipay-account",
                "来源文件名": "支付宝交易明细.pdf",
                "银行备注": "退款-95新联想拯救者",
                "账户方附言": "支付宝订单状态=取消/退款关联；支付宝商家订单号=T200P1；支付宝交易订单号=ORDER1_T200P1",
                "收入金额": "",
                "支出金额": "",
                "收支方向": "未知",
                "一级标签": "其他类",
                "二级标签": "其他",
                "三级标签": "其他",
                "标签来源": "兜底",
                "标签置信度": "0.3",
                "命中规则编号": "",
                "命中关键词": "",
                "命中字段": "",
            },
        ])

        summary = _apply_alipay_order_reversals(df)

        self.assertEqual(summary, {"配对组数": 1, "冲正原始交易数": 1, "冲正记录数": 1})
        self.assertEqual(df.loc[0, "支出金额"], "8200.00")
        self.assertEqual(df.loc[0, "分析支出金额"], 0)
        self.assertEqual(df.loc[1, "分析支出金额"], 0)
        self.assertEqual(df.loc[0, "交易状态"], "被取消")
        self.assertEqual(df.loc[1, "交易状态"], "取消")
        self.assertEqual(df.loc[0, "关联冲正交易编号"], "TX-cancel")
        self.assertEqual(df.loc[1, "关联冲正交易编号"], "TX-original")
        self.assertEqual(df.loc[1, "二级标签"], "退款交易")
        self.assertEqual(df.loc[1, "三级标签"], "退款支出")
        self.assertEqual(df.loc[1, "标签来源"], "支付宝订单配对")

    def test_alipay_cancel_pair_allows_multiple_cancel_rows_for_one_order(self):
        df = pd.DataFrame([
            {
                "交易唯一编号": "TX-original",
                "本方账户": "alipay-account",
                "来源文件名": "支付宝交易明细.pdf",
                "银行备注": "代付",
                "账户方附言": "支付宝商家订单号=M1；支付宝交易订单号=M1",
                "收入金额": "",
                "支出金额": "6054.90",
                "收支方向": "支出",
                "一级标签": "其他类",
                "二级标签": "其他",
                "三级标签": "其他支出",
                "标签来源": "兜底",
                "标签置信度": "0.3",
                "命中规则编号": "",
                "命中关键词": "",
                "命中字段": "",
            },
            {
                "交易唯一编号": "TX-cancel-1",
                "本方账户": "alipay-account",
                "来源文件名": "支付宝交易明细.pdf",
                "银行备注": "退款-代付",
                "账户方附言": "支付宝订单状态=取消/退款关联；支付宝商家订单号=M1；支付宝交易订单号=M1_R1",
                "收入金额": "",
                "支出金额": "",
                "收支方向": "未知",
                "一级标签": "其他类",
                "二级标签": "其他",
                "三级标签": "其他",
                "标签来源": "兜底",
                "标签置信度": "0.3",
                "命中规则编号": "",
                "命中关键词": "",
                "命中字段": "",
            },
            {
                "交易唯一编号": "TX-cancel-2",
                "本方账户": "alipay-account",
                "来源文件名": "支付宝交易明细.pdf",
                "银行备注": "退款-代付",
                "账户方附言": "支付宝订单状态=取消/退款关联；支付宝商家订单号=M1；支付宝交易订单号=M1_R2",
                "收入金额": "",
                "支出金额": "",
                "收支方向": "未知",
                "一级标签": "其他类",
                "二级标签": "其他",
                "三级标签": "其他",
                "标签来源": "兜底",
                "标签置信度": "0.3",
                "命中规则编号": "",
                "命中关键词": "",
                "命中字段": "",
            },
        ])

        summary = _apply_alipay_order_reversals(df)

        self.assertEqual(summary, {"配对组数": 1, "冲正原始交易数": 1, "冲正记录数": 2})
        self.assertEqual(df.loc[0, "交易状态"], "被取消")
        self.assertEqual(df.loc[0, "关联冲正交易编号"], "TX-cancel-1；TX-cancel-2")
        self.assertEqual(df.loc[1, "关联冲正交易编号"], "TX-original")
        self.assertEqual(df.loc[2, "关联冲正交易编号"], "TX-original")
        self.assertEqual(df.loc[0, "分析支出金额"], 0)
        self.assertEqual(df.loc[1, "三级标签"], "退款支出")
        self.assertEqual(df.loc[2, "三级标签"], "退款支出")

    def test_alipay_cancel_pair_only_cancels_transaction_order_related_rows(self):
        df = pd.DataFrame([
            {
                "交易唯一编号": "TX-original-1",
                "本方账户": "alipay-account",
                "来源文件名": "支付宝交易明细.pdf",
                "银行备注": "订单支付",
                "账户方附言": "支付宝商家订单号=M2；支付宝交易订单号=O1",
                "收入金额": "",
                "支出金额": "100.00",
                "收支方向": "支出",
                "一级标签": "其他类",
                "二级标签": "其他",
                "三级标签": "其他支出",
                "标签来源": "兜底",
                "标签置信度": "0.3",
                "命中规则编号": "",
                "命中关键词": "",
                "命中字段": "",
            },
            {
                "交易唯一编号": "TX-original-2",
                "本方账户": "alipay-account",
                "来源文件名": "支付宝交易明细.pdf",
                "银行备注": "订单支付",
                "账户方附言": "支付宝商家订单号=M2；支付宝交易订单号=O2",
                "收入金额": "",
                "支出金额": "200.00",
                "收支方向": "支出",
                "一级标签": "其他类",
                "二级标签": "其他",
                "三级标签": "其他支出",
                "标签来源": "兜底",
                "标签置信度": "0.3",
                "命中规则编号": "",
                "命中关键词": "",
                "命中字段": "",
            },
            {
                "交易唯一编号": "TX-cancel",
                "本方账户": "alipay-account",
                "来源文件名": "支付宝交易明细.pdf",
                "银行备注": "退款-订单支付",
                "账户方附言": "支付宝订单状态=取消/退款关联；支付宝商家订单号=M2；支付宝交易订单号=O1_REFUND",
                "收入金额": "",
                "支出金额": "",
                "收支方向": "未知",
                "一级标签": "其他类",
                "二级标签": "其他",
                "三级标签": "其他",
                "标签来源": "兜底",
                "标签置信度": "0.3",
                "命中规则编号": "",
                "命中关键词": "",
                "命中字段": "",
            },
        ])

        summary = _apply_alipay_order_reversals(df)

        self.assertEqual(summary, {"配对组数": 1, "冲正原始交易数": 1, "冲正记录数": 1})
        self.assertEqual(df.loc[0, "交易状态"], "被取消")
        self.assertEqual(df.loc[1, "交易状态"], "正常")
        self.assertEqual(df.loc[2, "交易状态"], "取消")
        self.assertEqual(df.loc[2, "关联冲正交易编号"], "TX-original-1")
        self.assertEqual(df.loc[0, "分析支出金额"], 0)
        self.assertEqual(df.loc[1, "分析支出金额"], 200)
        self.assertEqual(df.loc[2, "三级标签"], "退款支出")

    def test_alipay_cancel_pair_rejects_unrelated_interest_pseudo_order(self):
        df = pd.DataFrame([
            {
                "交易唯一编号": "TX-material", "本方账户": "alipay-account",
                "来源文件名": "支付宝交易明细.pdf", "银行备注": "材料付2万",
                "账户方附言": "支付宝商家订单号=502054834515101INTEREST；支付宝交易订单号=PAYMENT1",
                "收入金额": "", "支出金额": "20000", "收支方向": "支出",
                "一级标签": "经营类", "二级标签": "主营业务", "三级标签": "采购支出",
                "标签来源": "规则库", "标签置信度": "0.9", "命中规则编号": "R",
                "命中关键词": "材料", "命中字段": "银行备注",
            },
            {
                "交易唯一编号": "TX-interest", "本方账户": "alipay-account",
                "来源文件名": "支付宝交易明细.pdf", "银行备注": "余额宝收益",
                "账户方附言": "支付宝订单状态=取消/退款关联；支付宝商家订单号=502054834515101INTEREST；支付宝交易订单号=INTEREST1",
                "收入金额": "", "支出金额": "", "收支方向": "未知",
                "一级标签": "其他类", "二级标签": "其他", "三级标签": "其他",
                "标签来源": "兜底", "标签置信度": "0.3", "命中规则编号": "",
                "命中关键词": "", "命中字段": "",
            },
        ])

        summary = _apply_alipay_order_reversals(df)

        self.assertEqual(summary, {"配对组数": 0, "冲正原始交易数": 0, "冲正记录数": 0})
        self.assertEqual(df["交易状态"].tolist(), ["正常", "正常"])
        self.assertEqual(df.loc[0, "分析支出金额"], 20000)

    def test_bank_reversal_pairs_only_adjacent_rows_with_balance_closure(self):
        common = {
            "本方账户": "6227002061070284107",
            "来源文件名": "建设银行流水.pdf",
            "交易时间": "2025-07-09 00:00:00",
            "对手名称": "刘文平",
            "对手账户": "6216617003004764283",
            "收支方向": "支出",
            "一级标签": "其他类",
            "二级标签": "其他",
            "三级标签": "其他支出",
            "标签来源": "兜底",
            "标签置信度": "0.3",
            "命中规则编号": "",
            "命中关键词": "",
            "命中字段": "",
        }
        df = pd.DataFrame([
            {**common, "交易唯一编号": "TX-out-1", "来源行号": "90", "银行备注": "跨行转出",
             "账户方附言": "跨行转出", "收入金额": "", "支出金额": "20000", "账户余额": "647932.08"},
            {**common, "交易唯一编号": "TX-reversal-1", "来源行号": "91", "银行备注": "冲正",
             "账户方附言": "跨行转出", "收入金额": "20000", "支出金额": "", "账户余额": "667932.08"},
            {**common, "交易唯一编号": "TX-out-2", "来源行号": "92", "银行备注": "跨行转出",
             "账户方附言": "跨行转出", "收入金额": "", "支出金额": "20000", "账户余额": "647932.08"},
            {**common, "交易唯一编号": "TX-reversal-2", "来源行号": "93", "银行备注": "冲正",
             "账户方附言": "跨行转出", "收入金额": "20000", "支出金额": "", "账户余额": "667932.08"},
        ])

        summary = _apply_transaction_relations(df)

        self.assertEqual(summary["银行冲正"]["配对组数"], 2)
        self.assertEqual(summary["银行冲正"]["待复核冲正数"], 0)
        self.assertEqual(df["交易状态"].tolist(), ["被冲正", "冲正", "被冲正", "冲正"])
        self.assertEqual(df.loc[0, "关联冲正交易编号"], "TX-reversal-1")
        self.assertEqual(df.loc[1, "关联冲正交易编号"], "TX-out-1")
        self.assertEqual(df["分析交易金额"].tolist(), [0, 0, 0, 0])
        self.assertEqual(df.loc[1, "标签来源"], "银行冲正配对")

    def test_bank_reversal_does_not_search_past_another_source_row(self):
        df = pd.DataFrame([
            {
                "交易唯一编号": "TX-out", "本方账户": "A", "来源文件名": "银行流水.pdf",
                "来源行号": "10", "交易时间": "2025-01-01", "对手名称": "张三", "对手账户": "12345678",
                "银行备注": "跨行转出", "账户方附言": "跨行转出", "收入金额": "", "支出金额": "100",
                "账户余额": "900", "一级标签": "其他类", "二级标签": "其他", "三级标签": "其他支出",
            },
            {
                "交易唯一编号": "TX-reversal", "本方账户": "A", "来源文件名": "银行流水.pdf",
                "来源行号": "12", "交易时间": "2025-01-01", "对手名称": "张三", "对手账户": "",
                "银行备注": "冲正", "账户方附言": "跨行转出", "收入金额": "100", "支出金额": "",
                "账户余额": "1000", "一级标签": "其他类", "二级标签": "其他", "三级标签": "其他收入",
            },
        ])

        summary = _apply_transaction_relations(df)

        self.assertEqual(summary["银行冲正"]["配对组数"], 0)
        self.assertEqual(summary["银行冲正"]["待复核冲正数"], 1)
        self.assertEqual(df.loc[1, "交易状态"], "正常")
        self.assertEqual(df.loc[1, "分析收入金额"], 100)

    def test_bank_reversal_accepts_full_and_masked_counterparty_accounts(self):
        common = {
            "本方账户": "1502069301003807332", "来源文件名": "工商银行历史明细.pdf",
            "对手名称": "冯俊昌", "交易时间": "2026-01-22 13:32:17",
            "一级标签": "其他类", "二级标签": "其他", "三级标签": "其他支出",
            "账户方附言": "",
        }
        df = pd.DataFrame([
            {**common, "交易唯一编号": "TX-out", "来源行号": "39",
             "对手账户": "6251939201318881", "银行备注": "跨行汇款",
             "收入金额": "", "支出金额": "3961", "账户余额": "6682.62"},
            {**common, "交易唯一编号": "TX-reversal", "来源行号": "40",
             "交易时间": "2026-01-22 13:32:19", "对手账户": "6251****8881",
             "银行备注": "汇款冲正", "收入金额": "3961", "支出金额": "",
             "账户余额": "10643.62"},
        ])

        summary = _apply_transaction_relations(df)

        self.assertEqual(summary["银行冲正"]["配对组数"], 1)
        self.assertEqual(df["交易状态"].tolist(), ["被冲正", "冲正"])

    def test_bank_reversal_accepts_explicit_original_transaction_reference(self):
        common = {
            "本方账户": "6228480678975281778", "来源文件名": "农业银行流水.pdf",
            "交易时间": "2025-10-11 17:42:11", "对手名称": "张朋贤",
            "对手账户": "", "一级标签": "其他类", "二级标签": "其他",
            "三级标签": "其他支出",
        }
        df = pd.DataFrame([
            {**common, "交易唯一编号": "TX-out", "来源行号": "550",
             "银行备注": "转支", "账户方附言": "", "收入金额": "",
             "支出金额": "20000", "账户余额": "898.30"},
            {**common, "交易唯一编号": "TX-reversal", "来源行号": "551",
             "银行备注": "冲正", "账户方附言": "冲正原交易，原流水号为1031000000262025101273846687",
             "收入金额": "20000", "支出金额": "", "账户余额": "20898.30"},
        ])

        summary = _apply_transaction_relations(df)

        self.assertEqual(summary["银行冲正"]["配对组数"], 1)
        self.assertEqual(df["交易状态"].tolist(), ["被冲正", "冲正"])

    def test_bank_write_off_uses_same_strict_pairing_as_reversal(self):
        common = {
            "本方账户": "6227002061070284107",
            "来源文件名": "银行流水.pdf",
            "交易时间": "2025-07-09 00:00:00",
            "对手名称": "刘文平",
            "对手账户": "6216617003004764283",
            "一级标签": "其他类",
            "二级标签": "其他",
            "三级标签": "其他支出",
        }
        df = pd.DataFrame([
            {**common, "交易唯一编号": "TX-out", "来源行号": "90", "银行备注": "跨行转出",
             "账户方附言": "跨行转出", "收入金额": "", "支出金额": "20000", "账户余额": "647932.08"},
            {**common, "交易唯一编号": "TX-write-off", "来源行号": "91", "银行备注": "抹账",
             "账户方附言": "跨行转出", "收入金额": "", "支出金额": "20000", "账户余额": "667932.08"},
        ])

        summary = _apply_transaction_relations(df)

        self.assertEqual(summary["银行冲正"]["配对组数"], 1)
        self.assertEqual(summary["银行冲正"]["待复核冲正数"], 0)
        self.assertEqual(df["交易状态"].tolist(), ["被抹账", "抹账"])
        self.assertEqual(df["分析交易金额"].tolist(), [0, 0])
        self.assertEqual(df.loc[1, "二级标签"], "冲正交易")
        self.assertEqual(df.loc[1, "三级标签"], "抹账")
        self.assertEqual(df.loc[1, "命中关键词"], "抹账+相邻交易+余额闭环")

    def test_bank_write_off_pairs_adjacent_rows_in_descending_export(self):
        common = {
            "本方账户": "A", "来源文件名": "农商流水.xls",
            "交易时间": "2025-06-09 18:47:35", "对手名称": "南昌飞硕贸易有限公司",
            "对手账户": "791917857900055", "一级标签": "其他类",
            "二级标签": "其他", "三级标签": "其他支出",
        }
        df = pd.DataFrame([
            {**common, "交易唯一编号": "TX-write-off", "来源行号": "33",
             "银行备注": "超级网银往贷抹账", "账户方附言": "采购款",
             "收入金额": "18720", "支出金额": "", "账户余额": "128000"},
            {**common, "交易唯一编号": "TX-original", "来源行号": "34",
             "交易时间": "2025-06-09 18:47:34", "银行备注": "采购款",
             "账户方附言": "采购款", "收入金额": "", "支出金额": "18720",
             "账户余额": "109280"},
        ])

        summary = _apply_transaction_relations(df)

        self.assertEqual(summary["银行冲正"]["配对组数"], 1)
        self.assertEqual(summary["银行冲正"]["待复核冲正数"], 0)
        self.assertEqual(df["交易状态"].tolist(), ["抹账", "被抹账"])
        self.assertEqual(df["分析交易金额"].tolist(), [0, 0])

    def test_bank_explicit_write_back_pairs_placeholder_counterparty(self):
        common = {
            "本方账户": "6216286618800011264", "来源文件名": "顺银流水.pdf",
            "交易时间": "2025-06-04 09:50:43", "一级标签": "其他类",
            "二级标签": "其他", "三级标签": "其他支出", "账户方附言": "",
        }
        df = pd.DataFrame([
            {**common, "交易唯一编号": "TX-original", "来源行号": "10",
             "对手名称": "熊益文", "对手账户": "6228481568727174579", "银行备注": "转帐",
             "收入金额": "", "支出金额": "10710", "账户余额": "1283528"},
            {**common, "交易唯一编号": "TX-write-back", "来源行号": "11",
             "交易时间": "2025-06-04 09:50:44", "对手名称": "/",
             "对手账户": "3118083303100200700151", "银行备注": "冲销",
             "收入金额": "10710", "支出金额": "", "账户余额": "1294238"},
        ])

        summary = _apply_transaction_relations(df)

        self.assertEqual(summary["银行冲正"]["配对组数"], 1)
        self.assertEqual(df["交易状态"].tolist(), ["被冲销", "冲销"])
        self.assertEqual(df.loc[1, "三级标签"], "冲销")

    def test_bank_implicit_reversal_requires_same_precise_time_and_balance_closure(self):
        common = {
            "本方账户": "A100", "来源文件名": "工商银行流水.xls",
            "交易时间": "2025-01-01 10:20:30", "对手名称": "张三", "对手账户": "B200",
            "一级标签": "其他类", "二级标签": "其他", "三级标签": "其他",
        }
        df = pd.DataFrame([
            {**common, "交易唯一编号": "TX-out", "来源行号": "10", "银行备注": "转账",
             "账户方附言": "", "收入金额": "", "支出金额": "2000", "账户余额": "178404.59"},
            {**common, "交易唯一编号": "TX-back", "来源行号": "11", "银行备注": "转账",
             "账户方附言": "", "收入金额": "2000", "支出金额": "", "账户余额": "180404.59"},
        ])

        summary = _apply_transaction_relations(df)

        self.assertEqual(summary["银行冲正"]["隐式冲正数"], 1)
        self.assertEqual(df["交易状态"].tolist(), ["被隐式冲正", "隐式冲正"])
        self.assertEqual(df["分析交易金额"].tolist(), [0, 0])

    def test_transaction_relations_compute_alipay_mask_once_per_strategy(self):
        common = {
            "本方账户": "A100", "来源文件名": "银行流水.xls",
            "交易时间": "2025-01-01 10:20:30", "对手名称": "张三", "对手账户": "B200",
            "银行备注": "转账", "账户方附言": "", "一级标签": "其他类",
            "二级标签": "其他", "三级标签": "其他",
        }
        df = pd.DataFrame([
            {**common, "交易唯一编号": "TX-out", "来源行号": "10", "收入金额": "",
             "支出金额": "2000", "账户余额": "178404.59"},
            {**common, "交易唯一编号": "TX-back", "来源行号": "11", "收入金额": "2000",
             "支出金额": "", "账户余额": "180404.59"},
        ])

        with patch.object(
            tag_module,
            "_is_alipay_rows",
            wraps=tag_module._is_alipay_rows,
        ) as is_alipay_rows:
            _apply_transaction_relations(df)

        # 支付宝订单策略一次、银行冲正策略一次；不再按相邻交易重复构造 DataFrame。
        self.assertEqual(is_alipay_rows.call_count, 2)

    def test_bank_implicit_reversal_rejects_date_only_time(self):
        common = {
            "本方账户": "A100", "来源文件名": "银行流水.xls", "交易时间": "2025-01-01",
            "对手名称": "张三", "对手账户": "B200", "银行备注": "转账", "账户方附言": "",
            "一级标签": "其他类", "二级标签": "其他", "三级标签": "其他",
        }
        df = pd.DataFrame([
            {**common, "交易唯一编号": "TX-out", "来源行号": "10", "收入金额": "",
             "支出金额": "1000", "账户余额": "2488.09"},
            {**common, "交易唯一编号": "TX-back", "来源行号": "11", "收入金额": "1000",
             "支出金额": "", "账户余额": "3488.09"},
        ])

        summary = _apply_transaction_relations(df)

        self.assertEqual(summary["银行冲正"]["隐式冲正数"], 0)
        self.assertEqual(df["交易状态"].tolist(), ["正常", "正常"])

    def test_technical_alipay_order_ids_do_not_match_order_keyword_rules(self):
        rules = pd.DataFrame([
            {
                "规则编号": "R001",
                "适用方向": "支出",
                "依据字段": "账户方附言",
                "匹配方式": "包含",
                "关键词": "订单",
                "排除关键词": "",
                "对手名称含": "",
                "一级标签": "经营类",
                "二级标签": "主营业务",
                "三级标签": "采购支出",
                "优先级": "900",
                "备注": "",
            },
        ])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rules.csv"
            rules.to_csv(path, index=False, encoding="utf-8-sig")

            buckets = load_rules(path)

        row = pd.Series({
            "对手名称": "",
            "银行备注": "",
            "账户方附言": "支付宝商家订单号=T200P1；支付宝交易订单号=ORDER1",
            "收入金额": "",
            "支出金额": "100",
        })
        rule, hit_field = match(row, "支出", buckets)

        self.assertIsNone(rule)
        self.assertIsNone(hit_field)


if __name__ == "__main__":
    unittest.main()
