from __future__ import annotations

from pathlib import Path

from token_vault_service.vault_cache import TokenVaultCache


def test_token_vault_cache_persists_to_sqlite_across_instances(tmp_path: Path) -> None:
    db_path = tmp_path / "vault-cache.sqlite3"
    cache = TokenVaultCache(db_path=db_path, max_size=200)
    cache.put(
        file_sha256="a" * 64,
        file_size=10,
        strategy_version="v1",
        enabled_labels=["person"],
        token_vault={"张某001": {"label": "person", "original": "张三"}},
    )

    reopened = TokenVaultCache(db_path=db_path, max_size=200)

    assert reopened.get("a" * 64) == {
        "张某001": {"label": "person", "original": "张三"}
    }


def test_token_vault_cache_keeps_only_recent_entries(tmp_path: Path) -> None:
    db_path = tmp_path / "vault-cache.sqlite3"
    cache = TokenVaultCache(db_path=db_path, max_size=2)

    for index in range(3):
        cache.put(
            file_sha256=str(index) * 64,
            file_size=index,
            strategy_version="v1",
            enabled_labels=None,
            token_vault={f"令牌{index}": {"label": "person", "original": f"姓名{index}"}},
        )

    reopened = TokenVaultCache(db_path=db_path, max_size=2)

    assert reopened.get("0" * 64) is None
    assert reopened.get("1" * 64) == {"令牌1": {"label": "person", "original": "姓名1"}}
    assert reopened.get("2" * 64) == {"令牌2": {"label": "person", "original": "姓名2"}}
