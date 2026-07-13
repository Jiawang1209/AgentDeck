from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .models import ENTITY_EDGES, INITIAL_STATES, TERMINAL_STATES


_RECORD_SPECS = {
    "conversation_sessions": ("conversation_id", "conversation"),
    "conversation_turns": ("turn_id", "turn"),
    "conversation_preview_bindings": ("preview_id", "preview"),
}


def _records(
    base_records: Mapping[str, Sequence[Mapping[str, object]]], key: str
) -> Sequence[Mapping[str, object]]:
    value = base_records.get(key, ())
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{key} must be a list")
    if any(not isinstance(item, Mapping) for item in value):
        raise ValueError(f"{key} items must be records")
    return value


def _identity_sets(
    base_records: Mapping[str, Sequence[Mapping[str, object]]],
) -> dict[str, set[str]]:
    identities: dict[str, set[str]] = {
        "conversation": set(),
        "turn": set(),
        "preview": set(),
        "ownership": set(),
    }
    conversation_ids: set[str] = set()
    for collection, (id_field, entity_type) in _RECORD_SPECS.items():
        for record in _records(base_records, collection):
            identity = record.get(id_field)
            if not isinstance(identity, str) or not identity:
                raise ValueError(f"invalid {id_field}")
            if identity in identities[entity_type]:
                raise ValueError(f"duplicate {id_field}")
            identities[entity_type].add(identity)
            if entity_type == "conversation":
                conversation_ids.add(identity)

    for collection in ("conversation_turns", "conversation_preview_bindings"):
        for record in _records(base_records, collection):
            conversation_id = record.get("conversation_id")
            if conversation_id not in conversation_ids:
                raise ValueError(f"unknown conversation: {conversation_id}")
            turn_id = record.get("turn_id")
            if collection == "conversation_preview_bindings" and turn_id is not None:
                if turn_id not in identities["turn"]:
                    raise ValueError(f"unknown turn: {turn_id}")
    return identities


def _transition_records(
    transitions: Sequence[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    if not isinstance(transitions, Sequence) or isinstance(transitions, (str, bytes)):
        raise ValueError("conversation transitions must be a list")
    records = list(transitions)
    if any(not isinstance(item, Mapping) for item in records):
        raise ValueError("conversation transition items must be records")
    transition_ids = [item.get("transition_id") for item in records]
    if any(not isinstance(item, str) or not item for item in transition_ids):
        raise ValueError("invalid transition_id")
    if len(transition_ids) != len(set(transition_ids)):
        raise ValueError("duplicate transition_id")
    return records


def project_conversation_history(
    base_records: Mapping[str, Sequence[Mapping[str, object]]],
    transitions: Sequence[Mapping[str, object]],
) -> dict[str, Any]:
    identities = _identity_sets(base_records)
    records = _transition_records(transitions)
    record_conversations = {
        (entity_type, str(record[id_field])): str(record["conversation_id"])
        for collection, (id_field, entity_type) in _RECORD_SPECS.items()
        if entity_type != "conversation"
        for record in _records(base_records, collection)
    }
    states: dict[str, dict[str, str]] = {
        "conversation": {},
        "turn": {},
        "preview": {},
        "ownership": {},
    }
    entity_conversations: dict[tuple[str, str], str] = {}

    for transition in records:
        entity_type = transition.get("entity_type")
        if entity_type not in ENTITY_EDGES:
            raise ValueError("invalid conversation transition entity_type")
        entity_id = transition.get("entity_id")
        conversation_id = transition.get("conversation_id")
        if not isinstance(entity_id, str) or not entity_id:
            raise ValueError("invalid conversation transition entity_id")
        if conversation_id not in identities["conversation"]:
            raise ValueError(f"unknown conversation: {conversation_id}")
        if entity_type != "ownership" and entity_id not in identities[entity_type]:
            raise ValueError(f"unknown {entity_type}: {entity_id}")
        if entity_type == "ownership":
            identities["ownership"].add(entity_id)
        elif entity_type != "conversation" and record_conversations[
            (entity_type, entity_id)
        ] != conversation_id:
            raise ValueError(f"{entity_type} belongs to another conversation")

        reference = (entity_type, entity_id)
        prior_conversation = entity_conversations.setdefault(reference, str(conversation_id))
        if prior_conversation != conversation_id:
            raise ValueError("conversation transition identity changed conversation")

        current = states[entity_type].get(entity_id)
        from_state = transition.get("from_state")
        to_state = transition.get("to_state")
        if current is None:
            if from_state is not None:
                raise ValueError(f"{entity_type} transition from_state does not match current state")
            if to_state != INITIAL_STATES[entity_type]:
                raise ValueError(f"illegal initial {entity_type} transition")
        else:
            if current in TERMINAL_STATES[entity_type]:
                raise ValueError(f"terminal {entity_type} cannot transition")
            if from_state != current:
                raise ValueError(f"{entity_type} transition from_state does not match current state")
            if to_state not in ENTITY_EDGES[entity_type][current]:
                raise ValueError(f"illegal {entity_type} transition")
        states[entity_type][entity_id] = str(to_state)

    active_turns: dict[str, str] = {}
    for record in _records(base_records, "conversation_turns"):
        turn_id = str(record["turn_id"])
        state = states["turn"].get(turn_id)
        if state is not None and state not in TERMINAL_STATES["turn"]:
            conversation_id = str(record["conversation_id"])
            if conversation_id in active_turns:
                raise ValueError("conversation may have only one active turn")
            active_turns[conversation_id] = turn_id

    pending_previews: dict[str, str] = {}
    for record in _records(base_records, "conversation_preview_bindings"):
        preview_id = str(record["preview_id"])
        if states["preview"].get(preview_id) == "pending":
            conversation_id = str(record["conversation_id"])
            if conversation_id in pending_previews:
                raise ValueError("conversation may have only one pending preview")
            pending_previews[conversation_id] = preview_id

    return {
        "conversation_states": states["conversation"],
        "turn_states": states["turn"],
        "preview_states": states["preview"],
        "ownership_states": states["ownership"],
        "active_turn_by_conversation": active_turns,
        "pending_preview_by_conversation": pending_previews,
    }


def validate_conversation_history(
    base_records: Mapping[str, Sequence[Mapping[str, object]]],
    transitions: Sequence[Mapping[str, object]],
) -> dict[str, Any]:
    return project_conversation_history(base_records, transitions)


def append_validated_transition(
    base_records: Mapping[str, Sequence[Mapping[str, object]]],
    transitions: Sequence[Mapping[str, object]],
    candidate: Mapping[str, object],
) -> list[dict[str, object]]:
    proposed = [dict(item) for item in transitions]
    proposed.append(dict(candidate))
    validate_conversation_history(base_records, proposed)
    return proposed
