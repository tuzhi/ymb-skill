import importlib.util
from types import SimpleNamespace
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
    def test_duplicate_stem_standardized_artifacts_keep_source_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            duplicate_stems = package_deliverable.S.duplicate_source_stems([
                work / "subject-a" / "同名流水.pdf",
                work / "subject-b" / "同名流水.xlsx",
            ])
            self.assertEqual(duplicate_stems, {"同名流水"})

            csv_path = work / "同名流水__standardized.csv"
            json_path = work / "同名流水__mapping.json"
            csv_path.write_text("来源文件名\n同名流水.pdf\n", encoding="utf-8-sig")
            json_path.write_text("{}", encoding="utf-8")

            renamed_csv, renamed_json = package_deliverable.S.rename_duplicate_artifacts(
                Path(tmp) / "同名流水.pdf",
                csv_path,
                json_path,
                duplicate_stems,
            )

            self.assertEqual(Path(renamed_csv).name, "同名流水__pdf__standardized.csv")
            self.assertEqual(Path(renamed_json).name, "同名流水__pdf__mapping.json")
            self.assertFalse(csv_path.exists())
            self.assertFalse(json_path.exists())
            self.assertTrue(Path(renamed_csv).exists())
            self.assertTrue(Path(renamed_json).exists())

    def test_folder_mode_recursively_collects_candidate_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            branch = root / "新余分公司"
            branch.mkdir()
            pdf = branch / "2025年10-12月-2.pdf"
            pdf.write_bytes(b"%PDF-1.4\n")
            ignored = branch / "说明.docx"
            ignored.write_bytes(b"not a statement")

            args = SimpleNamespace(
                subject=None,
                folder=str(root),
                client="潘荣平消防设备",
                account_type=None,
            )

            subjects, skipped = package_deliverable.gather_subjects(args)

            self.assertEqual(subjects[0][0], "潘荣平消防设备")
            self.assertEqual(subjects[0][1], [(str(pdf), None)])
            self.assertEqual(len(skipped), 1)
            self.assertEqual(skipped[0][0], "说明.docx")

    def test_build_workbook_styles_without_reloading_saved_xlsx(self):
        tagged = pd.DataFrame([{
            "交易唯一编号": "tx-1",
            "客户名称": "测试客户",
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
                    out_path,
                    [],
                )

            self.assertTrue(out_path.exists())


if __name__ == "__main__":
    unittest.main()
