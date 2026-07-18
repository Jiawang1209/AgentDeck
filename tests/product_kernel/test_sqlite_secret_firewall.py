from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentdeck.adapters.sqlite import SQLiteStore, StoreSerializationError

from .fakes import FrozenClock


NOW = datetime(2026, 7, 19, 8, 9, 10, tzinfo=timezone.utc)


@pytest.mark.parametrize("payload", [
    {"OpenAIAPIKey": "raw"},
    {"OPENAIAPIKEY": "raw"},
    {"openaiapikey": "raw"},
    {"credentials": "raw"},
    {"api_keys": "raw"},
    {"access_tokens": "raw"},
    {"rawprotocolframe": "raw"},
    {"nested": {"terminal_outputs": ["raw"]}},
])
def test_fused_acronym_plural_and_nested_secret_keys_are_zero_write(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        with pytest.raises(StoreSerializationError, match="secret|credential|prohibited"):
            store.execute_once("cmd_firewall", "persist", lambda transaction: payload)
        assert store.count("commands") == 0
    finally:
        store.close()


@pytest.mark.parametrize("key", [
    "OPENAI_API_KEY_VALUE", "providerCredentials", "accessTokens",
    "terminalOutputChunk",
])
def test_semantic_family_suffixes_are_rejected(tmp_path: Path, key: str) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        with pytest.raises(StoreSerializationError):
            store.execute_once("cmd_family", "persist", lambda transaction: {key: "raw"})
        assert store.count("commands") == 0
    finally:
        store.close()


def test_nonsecret_words_and_exact_usage_metadata_remain_safe(tmp_path: Path) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        assert store.execute_once("cmd_safe", "persist", lambda transaction: {
            "monkey": 1, "keyboard": 2, "token_count": 3,
        }) == {"keyboard": 2, "monkey": 1, "token_count": 3}
    finally:
        store.close()


@pytest.mark.parametrize("key", ["auth_header", "authHeader", "AUTH_HEADER"])
def test_auth_header_alias_is_zero_write(tmp_path: Path, key: str) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        with pytest.raises(StoreSerializationError):
            store.execute_once("cmd_auth_header", "persist", lambda transaction: {
                key: "Bearer raw-secret",
            })
        assert store.count("commands") == 0
    finally:
        store.close()
