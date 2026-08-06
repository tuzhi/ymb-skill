from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


STANDARDIZATION_MODULE = "ymb_standardization_core"


@dataclass(frozen=True)
class Settings:
    """服务运行配置。

    当前项目通过 pyproject.toml 依赖 ymb-standardization-core，不复制标准化源码。
    """

    host: str = "127.0.0.1"
    port: int = 8010
    max_chars: int = 20000
    log_path: Path = Path("token-vault-service/logs/token-vault-service.jsonl")
    vault_cache_path: Path = Path("token-vault-service/data/token-vault-cache.sqlite3")
    vault_cache_size: int = 200
    standardization_module: str = STANDARDIZATION_MODULE


def get_settings() -> Settings:
    return Settings(
        host=os.getenv("TOKEN_VAULT_SERVICE_HOST", "127.0.0.1"),
        port=int(os.getenv("TOKEN_VAULT_SERVICE_PORT", "8010")),
        max_chars=int(os.getenv("TOKEN_VAULT_SERVICE_MAX_CHARS", "20000")),
        log_path=Path(
            os.getenv("TOKEN_VAULT_SERVICE_LOG_PATH", "token-vault-service/logs/token-vault-service.jsonl")
        ),
        vault_cache_path=Path(
            os.getenv(
                "TOKEN_VAULT_SERVICE_VAULT_CACHE_PATH",
                "token-vault-service/data/token-vault-cache.sqlite3",
            )
        ),
        vault_cache_size=int(os.getenv("TOKEN_VAULT_SERVICE_VAULT_CACHE_SIZE", "200")),
        standardization_module=STANDARDIZATION_MODULE,
    )


