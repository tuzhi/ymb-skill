import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from runtime.input_hints import consume_file_password_hints  # noqa: E402


class InputHintsTest(unittest.TestCase):
    def test_historical_yaml_becomes_memory_password_and_is_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "nested"
            nested.mkdir()
            (nested / "流水.pdf").write_bytes(b"%PDF")
            hints = root / "_file_hints.yaml"
            hints.write_text(
                'file_info:\n  "nested/流水.pdf":\n    open_password: "123456"\n',
                encoding="utf-8",
            )

            result = consume_file_password_hints(root)

            self.assertEqual(result, {"nested/流水.pdf": "123456"})
            self.assertFalse(hints.exists())

    def test_invalid_hint_is_removed_and_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hints = root / "_file_hints.yaml"
            hints.write_text(
                'file_info:\n  "../流水.pdf":\n    open_password: "secret"\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "相对文件路径"):
                consume_file_password_hints(root)

            self.assertFalse(hints.exists())


if __name__ == "__main__":
    unittest.main()
