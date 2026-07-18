from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from agentdeck.adapters.config import ConfigResolver


def test_config_precedence_is_session_project_global_discovery() -> None:
    resolver = ConfigResolver(
        discovered={"leader": "codex-cli", "model": "native-default"},
        global_values={"leader": "claude-cli"},
        project_values={"leader": "api:deepseek", "model": "deepseek-chat"},
        session_values={"leader": "codex-cli"},
    )

    leader = resolver.resolve("leader")
    model = resolver.resolve("model")

    assert (leader.value, leader.source) == ("codex-cli", "session")
    assert (model.value, model.source) == ("deepseek-chat", "project")


def test_config_falls_back_through_global_and_discovery() -> None:
    resolver = ConfigResolver(
        discovered={"model": "native-default", "language": "en"},
        global_values={"language": "zh-CN"},
        project_values={},
        session_values={},
    )

    assert (resolver.resolve("language").value, resolver.resolve("language").source) == (
        "zh-CN",
        "global",
    )
    assert (resolver.resolve("model").value, resolver.resolve("model").source) == (
        "native-default",
        "discovery",
    )


def test_config_copies_input_mappings_and_returns_frozen_results() -> None:
    session_values = {"leader": "codex-cli"}
    resolver = ConfigResolver(
        discovered={},
        global_values={},
        project_values={},
        session_values=session_values,
    )
    session_values["leader"] = "claude-cli"

    result = resolver.resolve("leader")

    assert result.value == "codex-cli"
    with pytest.raises(FrozenInstanceError):
        result.value = "claude-cli"  # type: ignore[misc]


def test_missing_config_key_has_stable_key_error() -> None:
    resolver = ConfigResolver(
        discovered={},
        global_values={},
        project_values={},
        session_values={},
    )

    with pytest.raises(KeyError, match="configuration key is not configured"):
        resolver.resolve("leader")


@pytest.mark.parametrize("key", [None, 1, b"leader"])
def test_resolve_rejects_non_string_keys_without_echoing_them(key: object) -> None:
    resolver = ConfigResolver(
        discovered={},
        global_values={},
        project_values={},
        session_values={},
    )

    with pytest.raises(TypeError, match="configuration key must be a string") as error:
        resolver.resolve(key)  # type: ignore[arg-type]

    assert repr(key) not in str(error.value)


@pytest.mark.parametrize("key", ["", " ", "\t"])
def test_resolve_rejects_empty_keys(key: str) -> None:
    resolver = ConfigResolver(
        discovered={},
        global_values={},
        project_values={},
        session_values={},
    )

    with pytest.raises(ValueError, match="configuration key must not be empty"):
        resolver.resolve(key)


@pytest.mark.parametrize(
    ("mapping", "error_type", "message"),
    [
        ({1: "secret-value"}, TypeError, "configuration keys must be strings"),
        ({"": "secret-value"}, ValueError, "configuration keys must not be empty"),
        ({"leader": 7}, TypeError, "configuration values must be strings"),
        ({"leader": "  "}, ValueError, "configuration values must not be empty"),
    ],
)
def test_config_rejects_invalid_entries_without_leaking_values(
    mapping: dict[object, object], error_type: type[Exception], message: str
) -> None:
    with pytest.raises(error_type, match=message) as error:
        ConfigResolver(
            discovered=mapping,  # type: ignore[arg-type]
            global_values={},
            project_values={},
            session_values={},
        )

    assert "secret-value" not in str(error.value)
