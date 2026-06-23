from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict


VaultMapping = Dict[str, Dict[str, str]]


@dataclass(frozen=True)
class TokenVaultCacheEntry:
    """本机 SQLite 持久化缓存条目。"""

    file_sha256: str
    file_size: int
    strategy_version: str
    enabled_labels: tuple[str, ...]
    token_vault: VaultMapping


class TokenVaultCache:
    """最近 Token Vault 的本机 SQLite LRU 缓存。"""

    def __init__(
        self,
        *,
        db_path: Path | str | None = None,
        max_size: int = 200,
    ) -> None:
        self._db_path = Path(db_path or "data/token-vault-cache.sqlite3")
        self._max_size = max_size
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def put(
        self,
        *,
        file_sha256: str,
        file_size: int,
        strategy_version: str,
        enabled_labels: list[str] | None,
        token_vault: VaultMapping,
    ) -> None:
        now = _now_ns()
        labels = tuple(enabled_labels or ())
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM token_vault_cache WHERE file_sha256 = ?",
                (file_sha256,),
            ).fetchone()
            if existing:
                connection.execute(
                    """
                    UPDATE token_vault_cache
                    SET last_used_at = ?
                    WHERE file_sha256 = ?
                    """,
                    (now, file_sha256),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO token_vault_cache (
                        file_sha256,
                        file_size,
                        strategy_version,
                        enabled_labels_json,
                        token_vault_json,
                        created_at,
                        last_used_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        file_sha256,
                        file_size,
                        strategy_version,
                        json.dumps(labels, ensure_ascii=False),
                        json.dumps(_copy_vault(token_vault), ensure_ascii=False),
                        now,
                        now,
                    ),
                )
            self._trim(connection)

    def get(self, file_sha256: str) -> VaultMapping | None:
        now = _now_ns()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT token_vault_json
                FROM token_vault_cache
                WHERE file_sha256 = ?
                """,
                (file_sha256,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE token_vault_cache
                SET last_used_at = ?
                WHERE file_sha256 = ?
                """,
                (now, file_sha256),
            )
        return _copy_vault(json.loads(row["token_vault_json"]))

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS token_vault_cache (
                    file_sha256 TEXT PRIMARY KEY,
                    file_size INTEGER NOT NULL,
                    strategy_version TEXT NOT NULL,
                    enabled_labels_json TEXT NOT NULL,
                    token_vault_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    last_used_at INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_token_vault_cache_last_used
                ON token_vault_cache(last_used_at, created_at)
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _trim(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            DELETE FROM token_vault_cache
            WHERE file_sha256 IN (
                SELECT file_sha256
                FROM token_vault_cache
                ORDER BY last_used_at ASC, created_at ASC
                LIMIT MAX(
                    (SELECT COUNT(*) FROM token_vault_cache) - ?,
                    0
                )
            )
            """,
            (self._max_size,),
        )


def _copy_vault(token_vault: VaultMapping) -> VaultMapping:
    return {
        token: {key: str(value) for key, value in mapping.items()}
        for token, mapping in token_vault.items()
    }


def _now_ns() -> int:
    return time.time_ns()
