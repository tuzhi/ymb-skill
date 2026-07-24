import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE_PACKAGE = ROOT.parent / "ymb-standardization-core" / "src"
if str(CORE_PACKAGE) not in sys.path:
    sys.path.insert(0, str(CORE_PACKAGE))

from ymb_standardization_core.transforms.registry import RowTransformRegistry  # noqa: E402
from ymb_standardization_core.transforms.row_options import (  # noqa: E402
    apply_reader_options,
    reader_row_transform_registry,
)


class RowTransformRegistryTests(unittest.TestCase):
    def test_reader_transform_ids_have_stable_execution_order(self):
        self.assertEqual(
            reader_row_transform_registry().ids(),
            (
                "drop_rows",
                "split_amount_balance",
                "amount_columns",
                "extract_patterns",
                "direction_from_column",
            ),
        )

    def test_registry_rejects_duplicate_transform_id(self):
        registry = RowTransformRegistry()
        transform = lambda rows, _config: rows
        registry.register("demo", transform)
        with self.assertRaisesRegex(ValueError, "duplicate transform_id"):
            registry.register("demo", transform)

    def test_only_configured_transforms_are_applied(self):
        rows = [
            ["状态", "金额"],
            ["删除", "CNY 12.34"],
            ["保留", "CNY 56.78"],
        ]

        transformed = apply_reader_options(rows, {
            "drop_rows": [{"column": "状态", "values": ["删除"]}],
            "amount_columns": ["金额"],
        })

        self.assertEqual(transformed, [
            ["状态", "金额"],
            ["保留", "56.78"],
        ])


if __name__ == "__main__":
    unittest.main()
