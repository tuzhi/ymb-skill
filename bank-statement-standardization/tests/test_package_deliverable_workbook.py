import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd


SKILL_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DELIVERABLE = SKILL_ROOT / "scripts" / "package_deliverable.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


package_deliverable = load_module("package_deliverable", PACKAGE_DELIVERABLE)


class PackageDeliverableWorkbookTest(unittest.TestCase):
    def test_build_workbook_styles_without_reloading_saved_xlsx(self):
        tagged = pd.DataFrame([{
            "交易唯一编号": "tx-1",
            "客户名称": "测试客户",
            "主体名称": "测试主体",
            "账户类型": "对公",
            "本方名称": "测试主体",
            "本方账户": "10001",
            "开户行": "测试银行",
            "交易时间": "2026-01-01 10:00:00",
            "对手名称": "对手",
            "对手账户": "20001",
            "收入金额": "100.00",
            "支出金额": "0.00",
            "交易金额": "100.00",
            "账户余额": "100.00",
            "虚拟账户余额": "100.00",
            "银行备注": "备注",
            "账户方附言": "",
            "收支方向": "收入",
            "一级标签": "经营",
            "二级标签": "销售",
            "三级标签": "销售回款",
            "标签来源": "规则库",
            "标签置信度": "1.0",
            "命中关键词": "销售",
            "交易渠道": "网银",
            "来源文件名": "sample.csv",
            "来源行号": "2",
        }])
        daily = pd.DataFrame([{"日期": "2026-01-01", "10001": 100.0, "合计余额": 100.0}])
        irep = {
            "客户整合概览": {
                "交易期间": {"开始日期": "2026-01-01", "结束日期": "2026-01-01"},
                "整合账户数": 1,
                "整合文件数": 1,
                "原始交易数": 1,
                "跨文件去重笔数": 0,
                "整合交易数": 1,
            },
            "疑似重复交易组": [],
            "自有账户互转组": [],
            "人工复核事项": [],
        }
        srep = {
            "标签梳理概览": {"规则命中率": 1.0},
            "人工复核事项": [],
        }
        pbrep = {
            "组合虚拟账户": {
                "期末合计余额": 100.0,
                "峰值合计余额": 100.0,
                "谷值合计余额": 100.0,
            },
            "账户余额校验": {
                "通过账户数": 1,
                "预警账户数": 0,
                "余额断点合计": 0,
                "账户明细": [{"账户": "10001", "交易数": 1, "校验状态": "未校验"}],
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "deliverable.xlsx"
            # 性能约束：写出后不允许再 load_workbook 读回整份 xlsx 做样式处理。
            with patch("openpyxl.load_workbook", side_effect=AssertionError("不应二次加载已写出的 xlsx")):
                package_deliverable.build_workbook(
                    "测试客户",
                    tagged,
                    daily,
                    irep,
                    srep,
                    pbrep,
                    [("测试主体", [])],
                    out_path,
                    [],
                )

            self.assertTrue(out_path.exists())


if __name__ == "__main__":
    unittest.main()
