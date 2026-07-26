from __future__ import annotations

import pytest

from agentdeck.storage.secrets import SecretFactError, validate_fact_key_value, validate_fact_tree


@pytest.mark.parametrize("key", [
    "OpenAIAPIKey",
    "OPENAIAPIKEY",
    "openaiapikey",
    "credentials",
    "api_keys",
    "access_tokens",
    "rawprotocolframe",
    "terminal_outputs",
    "OPENAI_API_KEY_VALUE",
    "providerCredentials",
    "accessTokens",
    "terminalOutputChunk",
    "token",
    "password",
    "clientSecret",
    "sessionCookie",
    "authorization_header",
    "hidden_reasoning",
])
def test_credential_and_raw_keys_are_rejected(key: str) -> None:
    with pytest.raises(SecretFactError, match="secret|credential|prohibited"):
        validate_fact_key_value(key, "raw")


@pytest.mark.parametrize("key", ["monkey", "keyboard", "workspace_mode", "message_id", "role_prompt"])
def test_nonsecret_words_remain_safe(key: str) -> None:
    validate_fact_key_value(key, "value")


def test_exact_safe_usage_metadata() -> None:
    validate_fact_key_value("token_count", 3)
    with pytest.raises(SecretFactError):
        validate_fact_key_value("token_count", -1)
    with pytest.raises(SecretFactError):
        validate_fact_key_value("token_count", "3")


def test_provenance_digest_must_be_lower_hex_64() -> None:
    validate_fact_key_value("content_hash", "a" * 64)
    with pytest.raises(SecretFactError):
        validate_fact_key_value("content_hash", "A" * 64)
    with pytest.raises(SecretFactError):
        validate_fact_key_value("content_hash", "a" * 63)


def test_credential_source_name_labels() -> None:
    validate_fact_key_value("credential_source_name", "keychain")
    validate_fact_key_value("credential_source_name", "DEEPSEEK_API_KEY")
    with pytest.raises(SecretFactError):
        validate_fact_key_value("credential_source_name", "sk-raw-value")


def test_non_string_key_is_rejected() -> None:
    with pytest.raises(SecretFactError):
        validate_fact_key_value(42, "x")  # type: ignore[arg-type]


def test_tree_walk_rejects_nested_secret_keys() -> None:
    with pytest.raises(SecretFactError):
        validate_fact_tree({"nested": {"terminal_outputs": ["raw"]}})
    with pytest.raises(SecretFactError):
        validate_fact_tree([{"deep": [{"apiKey": "raw"}]}])
    validate_fact_tree({"messages": [{"message_id": "msg_1", "task": "do"}], "count": 1})
