"""银行流水单文件标准化内核。

该包只负责把原始文件解析并映射为统一标准字段；状态机、整合、打标和交付物组装仍留在外层脚本。
"""

from .core import NotABankStatement, standardize

__all__ = ["NotABankStatement", "standardize"]
