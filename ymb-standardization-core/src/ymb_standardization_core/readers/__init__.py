"""银行流水输入 reader 包。

reader 只负责把原始文件结构还原为二维 rows 和路由审计信息；
标准字段归一、金额方向、交易唯一编号、mapping 落盘仍由 ymb_standardization_core.core 统一处理。
"""

from .base import PdfReader
from .registry import FunctionPdfReader, PdfReaderRegistry

__all__ = ["FunctionPdfReader", "PdfReader", "PdfReaderRegistry"]
