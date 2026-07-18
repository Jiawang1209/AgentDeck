from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from agentdeck.product.slash_commands import CommandKind, SlashCommand, parse_command


@pytest.mark.parametrize(
    ("text", "kind", "argument"),
    (
        ("/help", CommandKind.HELP, None),
        (" /status ", CommandKind.STATUS, None),
        ("/setup", CommandKind.SETUP, None),
        ("/setup confirm", CommandKind.SETUP, "confirm"),
        ("/leader codex-cli", CommandKind.LEADER, "codex-cli"),
        ("/model native-default", CommandKind.MODEL, "native-default"),
        ("/agents", CommandKind.AGENTS, None),
        ("/permissions full-access", CommandKind.PERMISSIONS, "full-access"),
        ("/mission", CommandKind.MISSION, None),
        ("/pause", CommandKind.PAUSE, None),
        ("/resume", CommandKind.RESUME, None),
        ("/takeover att_1", CommandKind.TAKEOVER, "att_1"),
        ("/diagnose --json", CommandKind.DIAGNOSE, "--json"),
        ("/exit", CommandKind.EXIT, None),
    ),
)
def test_parser_recognizes_the_exact_declared_command_grammar(
    text: str, kind: CommandKind, argument: str | None
) -> None:
    command = parse_command(text)

    assert command == SlashCommand(kind=kind, argument=argument)


def test_parsed_commands_are_immutable_values() -> None:
    command = parse_command("/status")

    assert command is not None
    with pytest.raises(FrozenInstanceError):
        command.argument = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "text",
    (
        "please /exit later",
        "/unknown",
        "/help now",
        "/status extra",
        "/setup apply",
        "/leader",
        "/leader codex cli",
        "/model",
        "/permissions root",
        "/permissions full_access",
        "/pause now",
        "/takeover",
        "/takeover att_1 extra",
        "/diagnose --raw",
        "/exit\n/status",
        "/status\0",
    ),
)
def test_unknown_embedded_or_malformed_commands_fail_closed(text: str) -> None:
    assert parse_command(text) is None


@pytest.mark.parametrize("value", (None, 1, object(), "\ud800", "/leader " + "x" * 257))
def test_parser_rejects_unbounded_or_non_text_input_without_conversion(
    value: object,
) -> None:
    assert parse_command(value) is None
