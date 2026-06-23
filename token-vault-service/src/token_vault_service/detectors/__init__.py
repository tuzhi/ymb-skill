from __future__ import annotations

# detector 包只负责“识别原码片段”，不生成 token、不持有 Token Vault。
# Token 生成和 1:1 映射复用仍由 tokenization.py 的编排层统一处理。
from .base import DetectionContext, EntityDetector, Span
from .constants import ConstantDetector
from .id_number import IdNumberDetector, valid_id_number
from .person_name import HanlpPersonNameReviewer, PersonNameDetector, PersonNameReviewer
from .phone import PhoneDetector

__all__ = [
    "ConstantDetector",
    "DetectionContext",
    "EntityDetector",
    "HanlpPersonNameReviewer",
    "IdNumberDetector",
    "PersonNameDetector",
    "PersonNameReviewer",
    "PhoneDetector",
    "Span",
    "valid_id_number",
]
