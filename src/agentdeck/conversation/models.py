from __future__ import annotations

from typing import Literal


MAX_CONTEXT_TURNS = 24
MAX_CONTEXT_BYTES = 128 * 1024

EntityType = Literal["conversation", "turn", "preview", "ownership"]

CONVERSATION_EDGES = {
    "created": frozenset({"ready"}),
    "ready": frozenset({"busy", "waiting_confirmation", "closing"}),
    "busy": frozenset({"ready", "waiting_confirmation", "closing"}),
    "waiting_confirmation": frozenset({"ready", "busy", "closing"}),
    "closing": frozenset({"closed"}),
    "closed": frozenset(),
}

TURN_EDGES = {
    "created": frozenset({"routing"}),
    "routing": frozenset(
        {
            "waiting_leader",
            "presenting_preview",
            "executing",
            "completed",
            "blocked",
            "failed",
            "cancelled",
        }
    ),
    "waiting_leader": frozenset(
        {
            "presenting_preview",
            "completed",
            "blocked",
            "failed",
            "cancelled",
            "ambiguous",
        }
    ),
    "presenting_preview": frozenset({"completed", "failed", "cancelled"}),
    "executing": frozenset(
        {"completed", "blocked", "failed", "cancelled", "ambiguous"}
    ),
    "completed": frozenset(),
    "blocked": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
    "ambiguous": frozenset(),
}

PREVIEW_EDGES = {
    "pending": frozenset({"consumed", "expired", "invalidated"}),
    "consumed": frozenset(),
    "expired": frozenset(),
    "invalidated": frozenset(),
}

OWNERSHIP_EDGES = {
    "agentdeck_owned": frozenset({"takeover_pending"}),
    "takeover_pending": frozenset({"human_owned", "agentdeck_owned"}),
    "human_owned": frozenset({"return_pending"}),
    "return_pending": frozenset({"agentdeck_owned", "human_owned"}),
}

ENTITY_EDGES = {
    "conversation": CONVERSATION_EDGES,
    "turn": TURN_EDGES,
    "preview": PREVIEW_EDGES,
    "ownership": OWNERSHIP_EDGES,
}

INITIAL_STATES = {
    "conversation": "created",
    "turn": "created",
    "preview": "pending",
    "ownership": "agentdeck_owned",
}

TERMINAL_STATES = {
    "conversation": frozenset({"closed"}),
    "turn": frozenset({"completed", "blocked", "failed", "cancelled", "ambiguous"}),
    "preview": frozenset({"consumed", "expired", "invalidated"}),
    "ownership": frozenset(),
}


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def build_conversation_record(
    conversation_id: str,
    *,
    created_at: str,
) -> dict[str, object]:
    return {
        "conversation_id": _required_text(conversation_id, "conversation_id"),
        "created_at": _required_text(created_at, "created_at"),
    }


def build_turn_record(
    turn_id: str,
    *,
    conversation_id: str,
    created_at: str,
) -> dict[str, object]:
    return {
        "turn_id": _required_text(turn_id, "turn_id"),
        "conversation_id": _required_text(conversation_id, "conversation_id"),
        "created_at": _required_text(created_at, "created_at"),
    }


def build_preview_binding_record(
    preview_id: str,
    *,
    conversation_id: str,
    turn_id: str | None,
    preview_kind: str,
    execution_digest: str,
    expires_at: str,
    created_at: str,
) -> dict[str, object]:
    if turn_id is not None:
        _required_text(turn_id, "turn_id")
    digest = _required_text(execution_digest, "execution_digest")
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError("execution_digest must be a lowercase sha256 digest")
    return {
        "preview_id": _required_text(preview_id, "preview_id"),
        "conversation_id": _required_text(conversation_id, "conversation_id"),
        "turn_id": turn_id,
        "preview_kind": _required_text(preview_kind, "preview_kind"),
        "execution_digest": digest,
        "expires_at": _required_text(expires_at, "expires_at"),
        "created_at": _required_text(created_at, "created_at"),
    }


def build_conversation_transition(
    *,
    transition_id: str,
    conversation_id: str,
    entity_type: EntityType,
    entity_id: str,
    from_state: str | None,
    to_state: str,
    reason: str,
    created_at: str,
) -> dict[str, object]:
    if entity_type not in ENTITY_EDGES:
        raise ValueError("invalid conversation transition entity_type")
    if from_state is not None:
        _required_text(from_state, "from_state")
    return {
        "transition_id": _required_text(transition_id, "transition_id"),
        "conversation_id": _required_text(conversation_id, "conversation_id"),
        "entity_type": entity_type,
        "entity_id": _required_text(entity_id, "entity_id"),
        "from_state": from_state,
        "to_state": _required_text(to_state, "to_state"),
        "reason": _required_text(reason, "reason"),
        "created_at": _required_text(created_at, "created_at"),
    }
