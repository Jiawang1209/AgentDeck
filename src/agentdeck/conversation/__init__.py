"""Foreground conversation domain primitives."""

from .lifecycle import (
    append_validated_transition,
    project_conversation_history,
    validate_conversation_history,
)

__all__ = [
    "append_validated_transition",
    "project_conversation_history",
    "validate_conversation_history",
]
