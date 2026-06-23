from __future__ import annotations

from .detectors import PersonNameDetector
from .tokenization import (
    RuleDetector,
    TokenizationResult,
    detokenize_text,
    tokenize_pages,
    tokenize_standardized_rows,
)


class TokenVaultService:
    """Token Vault 业务服务外观。

    对 API 层隐藏底层规则检测和行列处理细节。
    该服务不持有跨请求状态；每次请求的映射由入参 Token Vault 和本次处理共同决定。
    """

    loaded = True

    def __init__(self, person_name_detector: PersonNameDetector | None = None) -> None:
        # 人名识别可能接入已加载的 HanLP 模型，不能在每次请求里重复初始化。
        # 手机号、身份证号、本方常量目前都是无状态规则，由编排层按需创建。
        self._person_name_detector = person_name_detector

    def tokenize_text_pages(
        self,
        pages: list[dict[str, object]],
        enabled_labels: list[str] | None = None,
        token_vault: dict[str, dict[str, str]] | None = None,
    ) -> TokenizationResult:
        return tokenize_pages(
            pages,
            enabled_labels=enabled_labels,
            detector=RuleDetector(),
            token_vault=token_vault,
        )

    def tokenize_standardized(
        self,
        columns: list[str],
        rows: list[list[object]],
        enabled_labels: list[str] | None = None,
        token_vault: dict[str, dict[str, str]] | None = None,
    ) -> TokenizationResult:
        return tokenize_standardized_rows(
            columns=columns,
            rows=rows,
            enabled_labels=enabled_labels,
            detector=RuleDetector(),
            person_name_detector=self._person_name_detector,
            token_vault=token_vault,
        )

    def detokenize(
        self,
        text: str,
        token_vault: dict[str, dict[str, str]],
    ) -> str:
        return detokenize_text(text, token_vault)


