"""输入文件路由。

本模块只负责按文件类型选择 reader，并返回统一 ReadResult；
字段映射、金额方向、账户识别仍由 core.standardize 处理。
"""

from dataclasses import dataclass
import os

from ymb_standardization_core.parsers.router import read_pdf_rows


@dataclass
class ReadResult:
    kind: str
    preamble: str
    rows: list
    route_info: dict


_excel_reader = None
_csv_reader = None
_unsupported_error = RuntimeError


def configure_readers(excel_reader, csv_reader, unsupported_error=RuntimeError):
    """注册 core 中已有的 Excel/CSV reader，避免 router 反向依赖标准化主流程。"""
    global _excel_reader, _csv_reader, _unsupported_error
    _excel_reader = excel_reader
    _csv_reader = csv_reader
    _unsupported_error = unsupported_error


def _require_reader(reader, name):
    if reader is None:
        raise RuntimeError(f"{name} reader is not configured")
    return reader


def read_rows(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm", ".xls"):
        sheet, rows = _require_reader(_excel_reader, "excel")(path)
        return ReadResult(
            kind="excel",
            preamble="",
            rows=rows,
            route_info={
                "parser": "generic_excel",
                "route_confidence": 0.7,
                "route_evidence": {"ext": ext, "sheet": sheet},
                "ocr_used": False,
            },
        )
    if ext in (".csv", ".txt", ".tsv"):
        preamble, rows = _require_reader(_csv_reader, "csv")(path)
        return ReadResult(
            kind="csv",
            preamble=preamble,
            rows=rows,
            route_info={
                "parser": "generic_csv",
                "route_confidence": 0.7,
                "route_evidence": {"ext": ext},
                "ocr_used": False,
            },
        )
    if ext == ".pdf":
        preamble, rows, route_info = read_pdf_rows(path)
        return ReadResult(kind="pdf", preamble=preamble, rows=rows, route_info=route_info)
    raise _unsupported_error(f"不支持的文件类型：{ext}")
