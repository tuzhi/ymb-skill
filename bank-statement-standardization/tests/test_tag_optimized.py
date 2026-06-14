import tempfile
import unittest
from pathlib import Path

import pandas as pd

from tag import direction_of, direction_series, load_rules, match


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

        expected = [direction_of(row) for _, row in df.iterrows()]

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


if __name__ == "__main__":
    unittest.main()
