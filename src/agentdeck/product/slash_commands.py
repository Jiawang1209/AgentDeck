"""Deterministic, LLM-free parsing for ProductSession slash controls."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Final


_MAX_COMMAND_BYTES: Final = 4_096
_MAX_ARGUMENT_BYTES: Final = 256
_COMMAND_LINE: Final = re.compile(r"/([a-z]+)(?: ([^\s]+))?")


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
    EXIT = "exit"


@dataclass(frozen=True)
class SlashCommand:
    kind: CommandKind
    argument: str | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not CommandKind:
            raise TypeError("kind must be a CommandKind")
        if self.argument is not None and type(self.argument) is not str:
            raise TypeError("argument must be a string or None")


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
    match = _COMMAND_LINE.fullmatch(text)
    if match is None:
        return None
    try:
        kind = CommandKind(match.group(1))
    except ValueError:
        return None
    argument = match.group(2)
    if argument is not None and not _valid_argument(argument):
        return None
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
