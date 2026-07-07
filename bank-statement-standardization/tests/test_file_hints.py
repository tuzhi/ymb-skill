import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import pdfplumber


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
CORE_PACKAGE = REPO_ROOT / "ymb-standardization-core"
if str(CORE_PACKAGE) not in sys.path:
    sys.path.insert(0, str(CORE_PACKAGE))


class FileHintsTests(unittest.TestCase):
    def test_file_info_matches_exact_relative_path_only(self):
        from ymb_standardization_core.file_hints import load_file_hints

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "_file_hints.yaml").write_text(
                'file_info:\n'
                '  "KA0200035ec56a887520001.pdf":\n'
                '    open_password: "secret-pdf"\n'
                '  "nested/book.xlsx":\n'
                '    open_password: "secret-xlsx"\n',
                encoding="utf-8",
            )

            hints = load_file_hints(root)

        self.assertEqual(
            hints.for_file("KA0200035ec56a887520001.pdf").get("open_password"),
            "secret-pdf",
        )
        self.assertEqual(
            hints.for_file(Path("nested") / "book.xlsx").get("open_password"),
            "secret-xlsx",
        )
        self.assertEqual(hints.for_file("KA999.pdf"), {})

    def test_file_patterns_are_rejected(self):
        from ymb_standardization_core.file_hints import load_file_hints

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "_file_hints.yaml").write_text(
                'file_patterns:\n'
                '  - pattern: "KA*.pdf"\n'
                '    open_password: "secret"\n',
                encoding="utf-8",
            )

            with self.assertRaises(ValueError) as cm:
                load_file_hints(root)

        self.assertIn("不支持 file_patterns", str(cm.exception))

    def test_audit_metadata_checks_passes_open_password_to_pdfplumber(self):
        module = self._load_audit_module()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf_path = root / "locked.pdf"
            pdf_path.write_bytes(b"%PDF")
            (root / "_file_hints.yaml").write_text(
                'file_info:\n'
                '  "locked.pdf":\n'
                '    open_password: "pdf-secret"\n',
                encoding="utf-8",
            )
            calls = []

            class FakePdf:
                metadata = {}

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

            def fake_open(path, password=None):
                calls.append((Path(path).name, password))
                return FakePdf()

            original_open = pdfplumber.open
            pdfplumber.open = fake_open
            try:
                result = module.metadata_checks(pdf_path, hints_root=root)
            finally:
                pdfplumber.open = original_open

        self.assertEqual(calls, [("locked.pdf", "pdf-secret")])
        self.assertIn("创建时间≈修改时间", result)

    def test_core_read_rows_loads_open_password_from_nearest_file_hints(self):
        from ymb_standardization_core import core
        from ymb_standardization_core.readers import input_router

        captured = []
        original_read_rows = input_router.read_rows

        def fake_read_rows(path, hints=None):
            captured.append((Path(path).name, hints))
            return input_router.ReadResult(
                kind="pdf",
                preamble="",
                rows=[["交易日期", "收入金额", "账户余额"], ["2026-01-01", "1.00", "2.00"]],
                route_info={"reader_id": "pdfplumber_table", "fingerprint_id": "", "column_mapping": {}},
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf_path = root / "locked.pdf"
            pdf_path.write_bytes(b"%PDF")
            (root / "_file_hints.yaml").write_text(
                'file_info:\n'
                '  "locked.pdf":\n'
                '    open_password: "pdf-secret"\n',
                encoding="utf-8",
            )
            input_router.read_rows = fake_read_rows
            try:
                kind, _preamble, _rows, route_info = core.read_rows(str(pdf_path))
            finally:
                input_router.read_rows = original_read_rows

        self.assertEqual(kind, "pdf")
        self.assertEqual(captured, [("locked.pdf", {"open_password": "pdf-secret"})])
        self.assertEqual(route_info["file_hints"]["hints_applied"], ["open_password"])
        self.assertTrue(route_info["file_hints"]["sensitive_values_redacted"])

    @staticmethod
    def _load_audit_module():
        spec = importlib.util.spec_from_file_location(
            "audit_testdata_support",
            ROOT / "scripts" / "audit_testdata_support.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


if __name__ == "__main__":
    unittest.main()
