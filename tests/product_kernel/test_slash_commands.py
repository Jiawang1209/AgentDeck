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


@pytest.mark.parametrize("decision", ("confirm", "decline"))
def test_exit_confirmation_grammar_carries_exact_identity_and_hash(
    decision: str,
) -> None:
    request_id = "xrt_" + "1" * 32
    content_hash = "a" * 64

    command = parse_command(
        f"/exit {decision} {request_id} {content_hash}"
    )

    assert command == SlashCommand(
        kind=CommandKind.EXIT,
        argument=decision,
        request_id=request_id,
        content_hash=content_hash,
    )


@pytest.mark.parametrize(
    "text",
    (
        "/exit yes",
        "/exit confirm",
        "/exit decline xrt_bad " + "a" * 64,
        "/exit confirm xrt_" + "1" * 32 + " bad",
        "/exit confirm xrt_" + "1" * 32 + " " + "a" * 64 + " extra",
    ),
)
def test_inexact_exit_confirmation_grammar_is_rejected(text: str) -> None:
    assert parse_command(text) is None


@pytest.mark.parametrize(
    "values",
    (
        {"request_id": "xrt_" + "1" * 32},
        {"content_hash": "a" * 64},
        {
            "request_id": "xrt_" + "1" * 32,
            "content_hash": "a" * 64,
        },
    ),
)
def test_non_confirmation_commands_cannot_carry_exit_authority(
    values: dict[str, str],
) -> None:
    with pytest.raises(ValueError):
        SlashCommand(kind=CommandKind.STATUS, **values)


@pytest.mark.parametrize("argument", ("yes", "confirm", "decline"))
def test_exit_command_value_rejects_incomplete_or_unknown_decisions(
    argument: str,
) -> None:
    with pytest.raises(ValueError):
        SlashCommand(kind=CommandKind.EXIT, argument=argument)


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
