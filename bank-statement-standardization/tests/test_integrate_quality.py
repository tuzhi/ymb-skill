import sys
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime import integrate


def transfer(tx_id, own_name, own_account, opponent_name, opponent_account,
             income=0, expense=0, precision="second"):
    return {
        "交易唯一编号": tx_id,
        "本方名称": own_name,
        "本方账户": own_account,
        "对手名称": opponent_name,
        "对手账户": opponent_account,
        "收入金额_num": float(income),
        "支出金额_num": float(expense),
        "__t": pd.Timestamp("2025-08-11 10:00:00"),
        "__time_precision": precision,
    }


class IntegrateQualityTests(unittest.TestCase):
    def test_alipay_distinct_trade_orders_are_not_auto_deduplicated(self):
        rows = []
        for source, order_id in [("支付宝-a.pdf", "ORDER-1"), ("支付宝-b.pdf", "ORDER-2")]:
            rows.append({
                "交易唯一编号": "TX-same-content", "本方账户": "alipay", "交易时间": "2025-01-01 10:00:00",
                "收入金额": "", "支出金额": "", "账户余额": "", "对手名称": "退款-代付",
                "开户行": "支付宝", "来源文件名": source, "来源行号": "1", "来源行号_num": 1,
                "账户方附言": f"支付宝商家订单号=M1；支付宝交易订单号={order_id}",
            })

        result, info = integrate.dedup_cross_file(pd.DataFrame(rows))

        self.assertEqual(len(result), 2)
        self.assertEqual(info["移除笔数"], 0)

    def test_alipay_different_trade_order_ids_are_not_duplicates(self):
        rows = []
        for tx_id, order_id in [("tx-1", "ORDER-1"), ("tx-2", "ORDER-2")]:
            rows.append({
                "交易唯一编号": tx_id, "本方账户": "alipay", "交易时间": "2025-01-01 10:00:00",
                "收入金额": "", "支出金额": "", "账户余额": "", "对手名称": "退款-代付",
                "开户行": "支付宝", "来源文件名": "支付宝.pdf",
                "账户方附言": f"支付宝商家订单号=M1；支付宝交易订单号={order_id}",
            })

        self.assertEqual(integrate.detect_duplicates(pd.DataFrame(rows)), [])

    def test_alipay_same_trade_order_id_remains_duplicate_candidate(self):
        rows = []
        for tx_id, source in [("tx-1", "支付宝.pdf"), ("tx-2", "支付宝.xlsx")]:
            rows.append({
                "交易唯一编号": tx_id, "本方账户": "alipay", "交易时间": "2025-01-01 10:00:00",
                "收入金额": "", "支出金额": "", "账户余额": "", "对手名称": "退款-代付",
                "开户行": "支付宝", "来源文件名": source,
                "账户方附言": "支付宝商家订单号=M1；支付宝交易订单号=ORDER-1",
            })

        groups = integrate.detect_duplicates(pd.DataFrame(rows))

        self.assertEqual(len(groups), 1)
        self.assertIn("支付宝交易订单号相同", groups[0]["判断原因"])

    def test_self_transfer_prefers_two_way_reciprocal_evidence(self):
        rows = [
            transfer("out", "刘若豪", "62160001", "刘若豪", "62170002", expense=30000),
            transfer("wrong", "刘若豪", "62170002", "罗庆", "99990003", income=30000),
            transfer("right", "刘若豪", "62170002", "刘若豪", "62160001", income=30000),
        ]

        pairs = integrate.detect_self_transfers(pd.DataFrame(rows), ["62160001", "62170002"])

        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["转入交易唯一编号"], "right")
        self.assertEqual(pairs[0]["置信度"], 0.9)
        self.assertIn("双向", pairs[0]["判断原因"])

    def test_date_only_reciprocal_transfer_is_not_high_confidence(self):
        rows = [
            transfer("out", "刘若豪", "62160001", "刘若豪", "62170002", expense=100, precision="date"),
            transfer("in", "刘若豪", "62170002", "刘若豪", "62160001", income=100, precision="date"),
        ]

        pairs = integrate.detect_self_transfers(pd.DataFrame(rows), ["62160001", "62170002"])

        self.assertEqual(pairs[0]["置信度"], 0.75)
        self.assertIn("日期精度", pairs[0]["判断原因"])

    def test_one_balance_warning_prevents_perfect_score(self):
        balance = [{"校验状态": "预警", "异常数量": 1}]

        self.assertEqual(integrate.calculate_quality_score(balance, [], []), 95)


if __name__ == "__main__":
    unittest.main()
