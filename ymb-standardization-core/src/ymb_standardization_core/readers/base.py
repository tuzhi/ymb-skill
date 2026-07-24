"""Reader 接口定义。"""

from typing import Any, Mapping, Protocol, TypeAlias


RawRows: TypeAlias = list[list[Any]]
ReaderOptions: TypeAlias = Mapping[str, Any]


class PdfReader(Protocol):
    """PDF Reader 结构化接口。

    实现只负责把已经打开的 PDF 载体转换成原始二维记录；模板识别、
    标准字段归一、账户推断和余额校验不属于该接口。
    """

    reader_id: str

    def read(self, pdf: Any, options: ReaderOptions) -> RawRows:
        ...
