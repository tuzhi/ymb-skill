from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ymb_standardization_core import (
    NotABankStatement,
    standardize as standardize_bank_statement,
)


@dataclass(frozen=True)
class StandardizationResult:
    """标准化阶段的统一返回值。

    该对象只描述标准化是否成功、产物在哪里，以及失败摘要。
    它不承载原始文件内容，也不承载 Token Vault。
    """

    ok: bool
    standardized_path: Path | None = None
    error: str | None = None
    summary: dict[str, object] = field(default_factory=dict)


class BundledStandardizationAdapter:
    """共享标准化核心适配器。

    标准化代码由 `ymb-standardization-core` 提供。
    本适配器直接 import 并调用 `standardize(...)` 函数，不动态加载脚本文件，
    不走命令行，也不启动子进程。

    职责边界：
    - 不改写标准化算法；
    - 只管理本次请求的输出目录；
    - 将标准化函数返回值转换成服务内部统一结构；
    - 标准化失败时直接退出，不进入 Token Vault 和 WorkBuddy / AI 链路。
    """

    def __init__(
        self,
        standardize_func: Callable[..., tuple[str, str, dict[str, Any]]] | None = None,
    ) -> None:
        self._standardize = standardize_func or standardize_bank_statement

    def standardize(self, input_path: Path, work_dir: Path) -> StandardizationResult:
        output_dir = work_dir / "standardized"
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            csv_path, _mapping_path, report = self._standardize(
                str(input_path),
                out_dir=str(output_dir),
            )
        except NotABankStatement as exc:
            return StandardizationResult(
                ok=False,
                error="not_bank_statement",
                summary={"reason": exc.reason},
            )
        except Exception as exc:
            return StandardizationResult(
                ok=False,
                error="standardizer_failed",
                summary={"exception": type(exc).__name__},
            )

        standardized_path = Path(csv_path)
        if not standardized_path.exists():
            return StandardizationResult(ok=False, error="standardized_output_missing")

        return StandardizationResult(
            ok=True,
            standardized_path=standardized_path,
            summary={
                "module": "ymb_standardization_core",
                "output": str(standardized_path),
                "rows": report.get("标准化统计", {}).get("交易笔数")
                if isinstance(report, dict)
                else None,
            },
        )
