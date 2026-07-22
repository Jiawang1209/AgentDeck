"""Deterministic, LLM-free parsing for ProductSession slash controls."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


_MAX_COMMAND_BYTES: Final = 4_096
_MAX_ARGUMENT_BYTES: Final = 256
_LOWER_HEX: Final = frozenset("0123456789abcdef")


class CommandKind(StrEnum):
    HELP = "help"
    STATUS = "status"
    SETUP = "setup"
    LEADER = "leader"
    MODEL = "model"
    AGENTS = "agents"
    PERMISSIONS = "permissions"
    MISSION = "mission"
    PAUSE = "pause"
    RESUME = "resume"
    TAKEOVER = "takeover"
    DIAGNOSE = "diagnose"
    SUPPORT = "support"
    TRACE = "trace"
    EXIT = "exit"


@dataclass(frozen=True)
class SlashCommand:
    kind: CommandKind
    argument: str | None = None
    request_id: str | None = None
    content_hash: str | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not CommandKind:
            raise TypeError("kind must be a CommandKind")
        if self.argument is not None and type(self.argument) is not str:
            raise TypeError("argument must be a string or None")
        if (self.request_id is None) is not (self.content_hash is None):
            raise ValueError("exit request identity and hash must be a closed pair")
        if self.kind is CommandKind.EXIT and self.argument not in {
            None, "confirm", "decline",
        }:
            raise ValueError("exit decision is unsupported")
        if self.request_id is None:
            if self.kind is CommandKind.EXIT and self.argument in {"confirm", "decline"}:
                raise ValueError("exit decision requires exact authority")
            return
        if self.kind is not CommandKind.EXIT or self.argument not in {
            "confirm", "decline",
        }:
            raise ValueError("only an exit decision may carry exit authority")
        if not _valid_request_id(self.request_id):
            raise ValueError("exit request identity is malformed")
        if not _valid_lower_hex(self.content_hash, 64):
            raise ValueError("exit request hash is malformed")


_NO_ARGUMENT: Final = frozenset(
    {
        CommandKind.HELP,
        CommandKind.STATUS,
        CommandKind.AGENTS,
        CommandKind.MISSION,
        CommandKind.PAUSE,
        CommandKind.RESUME,
        CommandKind.EXIT,
    }
)
_REQUIRED_ARGUMENT: Final = frozenset(
    {
        CommandKind.LEADER,
        CommandKind.MODEL,
        CommandKind.PERMISSIONS,
        CommandKind.TAKEOVER,
        CommandKind.TRACE,
    }
)
_PERMISSIONS: Final = frozenset(
    {"ask-for-approval", "approve-for-me", "full-access"}
)


def parse_command(value: object) -> SlashCommand | None:
    """Return one exact slash command, or ``None`` for non-command input."""

    if type(value) is not str:
        return None
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        return None
    if len(encoded) > _MAX_COMMAND_BYTES:
        return None
    text = value.strip()
    tokens = text.split()
    if not tokens or any(not _valid_argument(token) for token in tokens):
        return None
    command = tokens[0]
    if len(command) < 2 or command[0] != "/" or not command[1:].isalpha():
        return None
    try:
        kind = CommandKind(command[1:])
    except ValueError:
        return None
    if kind is CommandKind.EXIT:
        if len(tokens) == 1:
            return SlashCommand(kind)
        if len(tokens) != 4 or tokens[1] not in {"confirm", "decline"}:
            return None
        try:
            return SlashCommand(kind, tokens[1], tokens[2], tokens[3])
        except (TypeError, ValueError):
            return None
    if len(tokens) not in {1, 2}:
        return None
    argument = tokens[1] if len(tokens) == 2 else None
    if kind in _NO_ARGUMENT:
        return SlashCommand(kind) if argument is None else None
    if kind in _REQUIRED_ARGUMENT and argument is None:
        return None
    if kind is CommandKind.SETUP and argument not in {None, "confirm"}:
        return None
    if kind is CommandKind.DIAGNOSE and argument not in {None, "--json"}:
        return None
    if kind is CommandKind.PERMISSIONS and argument not in _PERMISSIONS:
        return None
    return SlashCommand(kind, argument)


def _valid_argument(value: str) -> bool:
    if not value.isprintable():
        return False
    try:
        return len(value.encode("utf-8", "strict")) <= _MAX_ARGUMENT_BYTES
    except UnicodeEncodeError:
        return False


def _valid_lower_hex(value: object, length: int) -> bool:
    return (
        type(value) is str
        and len(value) == length
        and all(character in _LOWER_HEX for character in value)
    )


def _valid_request_id(value: object) -> bool:
    return (
        type(value) is str
        and value.startswith("xrt_")
        and _valid_lower_hex(value[4:], 32)
    )
