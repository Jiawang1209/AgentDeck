"""Deterministic canonical-fact key firewall for SQLite persistence.

Ported from the rewrite donor (`adapters/sqlite_secrets.py`, commit
6790e29f) with the exception type decoupled from the donor's schema module.
Pure lexical policy: keys that look like credentials or raw protocol dumps
are refused before anything can be serialized into the database.
"""

from __future__ import annotations

import re
from typing import Final


class SecretFactError(ValueError):
    """A fact key/value pair is prohibited from persistent storage."""


_WORDS: Final = re.compile(
    r"[A-Z]+(?=[A-Z][a-z]|[0-9]|$)|[A-Z]?[a-z]+|[A-Z]+|[0-9]+"
)
_LOWER_HEX: Final = frozenset("0123456789abcdef")
_DIGEST_METADATA: Final = frozenset({"authorizationdigest", "contenthash"})
_SOURCE_METADATA: Final = "credentialsourcename"
_SAFE_USAGE_METADATA: Final = frozenset({"tokencount"})
_COMPACT_CREDENTIALS: Final = frozenset({
    "token", "apikey", "accesstoken", "refreshtoken", "sessiontoken",
    "authtoken", "authheader", "authorization", "authorizationheader", "bearer", "password",
    "userpassword", "secret", "clientsecret", "cookie", "sessioncookie",
    "credential", "credentialvalue",
})
_DIRECT_CREDENTIAL_WORDS: Final = frozenset({
    "token", "password", "secret", "bearer", "cookie", "credential",
    "authorization",
})
_RAW_PAIRS: Final = frozenset({
    ("raw", "protocol"), ("protocol", "frame"), ("raw", "terminal"),
    ("terminal", "output"), ("full", "environment"), ("hidden", "reasoning"),
})
_PLURAL_WORDS: Final = {
    "keys": "key", "tokens": "token", "credentials": "credential",
    "passwords": "password", "secrets": "secret", "cookies": "cookie",
    "outputs": "output", "frames": "frame", "headers": "header",
}
_KEY_FAMILIES: Final = frozenset({
    "api", "access", "private", "signing", "encryption", "ssh", "client",
})
_FUSED_TOKEN_MARKERS: Final = (
    "accesstoken", "authtoken", "refreshtoken", "sessiontoken",
)
_FUSED_CREDENTIAL_MARKERS: Final = (
    "password", "secret", "credential", "cookie", "bearer", "authorization",
)
_FUSED_RAW_MARKERS: Final = (
    "rawprotocol", "protocolframe", "rawterminal", "terminaloutput",
    "fullenvironment", "hiddenreasoning",
)


def _key_words(key: str) -> tuple[str, ...]:
    return tuple(match.group(0).lower() for match in _WORDS.finditer(key))


def _collapsed_key(key: str) -> str:
    return "".join(character.lower() for character in key if character.isalnum())


def _valid_source_label(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        return False
    if not encoded or len(encoded) > 128:
        return False
    if value in {"keychain", "cli-login", "env"}:
        return True
    return (
        value.endswith("_API_KEY")
        and value[0].isalpha()
        and all(character.isupper() or character.isdigit() or character == "_"
                for character in value)
    )


def validate_fact_key_value(key: str, value: object) -> None:
    """Reject raw credential/protocol keys; validate exact safe metadata."""
    if type(key) is not str:
        raise SecretFactError("canonical object keys must be strings")
    try:
        encoded = key.encode("utf-8", "strict")
    except UnicodeEncodeError as error:
        raise SecretFactError("canonical object key must be strict UTF-8") from error
    if len(encoded) > 65_536:
        raise SecretFactError("canonical object key is too large")
    collapsed = _collapsed_key(key)
    if collapsed in _DIGEST_METADATA:
        if (
            type(value) is not str or len(value) != 64
            or any(character not in _LOWER_HEX for character in value)
        ):
            raise SecretFactError("provenance digest must be 64 lowercase hex")
        return
    if collapsed == _SOURCE_METADATA:
        if not _valid_source_label(value):
            raise SecretFactError("credential source name is invalid")
        return
    if collapsed in _SAFE_USAGE_METADATA:
        if type(value) is not int or value < 0:
            raise SecretFactError("token count must be a nonnegative integer")
        return
    words = tuple(_PLURAL_WORDS.get(word, word) for word in _key_words(key))
    word_set = frozenset(words)
    pairs = frozenset(zip(words, words[1:]))
    token_family = (
        "token" in word_set
        or "token" in collapsed
        or any(marker in collapsed for marker in _FUSED_TOKEN_MARKERS)
        or collapsed.endswith(("token", "tokens"))
        or collapsed.startswith(("tokenvalue", "tokenid", "tokensecret", "tokenhash"))
    )
    key_family = (
        "apikey" in collapsed
        or any(f"{family}key" in collapsed for family in _KEY_FAMILIES)
        or ("key" in word_set and bool(word_set & _KEY_FAMILIES))
    )
    credential = (
        collapsed in _COMPACT_CREDENTIALS
        or bool(word_set & _DIRECT_CREDENTIAL_WORDS)
        or key_family
        or token_family
        or any(marker in collapsed for marker in _FUSED_CREDENTIAL_MARKERS)
        or ("authorization", "header") in pairs
    )
    raw_fact = (
        bool(pairs & _RAW_PAIRS)
        or any(marker in collapsed for marker in _FUSED_RAW_MARKERS)
    )
    if credential or raw_fact:
        raise SecretFactError("raw secret or credential facts are prohibited")


def validate_fact_tree(payload: object) -> None:
    """Walk nested dict/list payloads and validate every object key."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            validate_fact_key_value(key, value)
            validate_fact_tree(value)
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            validate_fact_tree(item)
