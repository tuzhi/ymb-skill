from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RedactSummary(BaseModel):
    """脱敏或逆脱敏的非敏感摘要。

    只包含命中数量和类别计数，不包含原文片段、span 位置或 Token Vault 内容。
    """

    span_count: int
    by_label: dict[str, int]


class TextPage(BaseModel):
    """文本级调试接口的分页输入/输出。

    文件级主流程不依赖该结构；它主要用于验证规则和逆脱敏能力。
    """

    page_no: int
    text: str


class TokenMappingValue(BaseModel):
    """Token Vault 中单个 token 的映射值。

    `original` 只返回给调用方自持，不写入日志，也不能提交给 WorkBuddy / AI。
    `source_column` 用于说明该映射来自哪个标准化字段，便于本地复核。
    """

    label: str
    original: str
    source_column: str | None = None


class TokenizeTextRequest(BaseModel):
    """文本 Token 化请求。

    这是底层调试能力，不作为标准化失败后的全文兜底路径。
    """

    pages: list[TextPage]
    enabled_labels: list[str] | None = None
    token_vault: dict[str, TokenMappingValue] | None = None


class TokenizeTextResponse(BaseModel):
    """文本 Token 化响应。

    响应会返回 Token 化文本和调用方自持的 Token Vault。
    """

    ok: Literal[True] = True
    request_id: str
    latency_ms: int
    pages: list[TextPage]
    token_vault: dict[str, TokenMappingValue]
    summary: RedactSummary


class TokenizeStandardizedRequest(BaseModel):
    """标准化行列数据 Token 化请求。

    输入应当已经是 ymb-skill 标准字段口径，不负责读取原始 PDF/Excel。
    """

    columns: list[str]
    rows: list[list[str]]
    enabled_labels: list[str] | None = None
    token_vault: dict[str, TokenMappingValue] | None = None


class TokenizeStandardizedResponse(BaseModel):
    """标准化行列数据 Token 化响应。

    输出保持原行列结构，只替换敏感单元格内容。
    """

    ok: Literal[True] = True
    request_id: str
    latency_ms: int
    columns: list[str]
    rows: list[list[str]]
    token_vault: dict[str, TokenMappingValue]
    summary: RedactSummary


class DetokenizeRequest(BaseModel):
    """逆脱敏请求。

    逆脱敏必须在本地服务执行。调用方可以直接提供 Token Vault，
    也可以提供原始文件 sha256，由服务从最近 10 个内存缓存中查找 Token Vault。
    """

    text: str
    token_vault: dict[str, TokenMappingValue] | None = Field(
        default=None, description="Caller-held reversible token vault."
    )
    file_sha256: str | None = None


class DetokenizeResponse(BaseModel):
    """逆脱敏响应。

    返回本地还原后的文本和替换数量摘要。
    """

    ok: Literal[True] = True
    request_id: str
    latency_ms: int
    text: str
    summary: dict[str, int]


class ErrorResponse(BaseModel):
    """统一错误响应。

    错误响应不得包含原始报文、Token 化正文或 Token Vault。
    """

    ok: Literal[False] = False
    request_id: str
    error: str


class RuntimeResponse(BaseModel):
    """运行时信息响应。

    用于前端确认服务配置，尤其是当前使用的标准化 core 模块。
    """

    ok: bool
    max_chars: int
    standardization_module: str


