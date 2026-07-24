"""Reader 注册表。"""

from dataclasses import dataclass
from typing import Any, Callable

from .base import PdfReader, RawRows, ReaderOptions


PdfExtractor = Callable[[Any, ReaderOptions], RawRows]


@dataclass(frozen=True)
class FunctionPdfReader:
    """把已有确定性读取函数适配为 ``PdfReader``。"""

    reader_id: str
    extractor: PdfExtractor

    def read(self, pdf: Any, options: ReaderOptions) -> RawRows:
        return self.extractor(pdf, options)


class PdfReaderRegistry:
    """以稳定 ``reader_id`` 管理 PDF Reader 实现。"""

    def __init__(self) -> None:
        self._readers: dict[str, PdfReader] = {}

    def register(self, reader: PdfReader) -> None:
        reader_id = str(reader.reader_id or "").strip()
        if not reader_id:
            raise ValueError("reader_id must be non-empty")
        if reader_id in self._readers:
            raise ValueError(f"duplicate PDF reader_id: {reader_id}")
        self._readers[reader_id] = reader

    def get(self, reader_id: str) -> PdfReader | None:
        return self._readers.get(str(reader_id or "").strip())

    def ids(self) -> tuple[str, ...]:
        return tuple(self._readers)
