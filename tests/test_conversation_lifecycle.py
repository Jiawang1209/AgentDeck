from __future__ import annotations

from copy import deepcopy

import pytest

from agentdeck.conversation.lifecycle import (
    append_validated_transition,
    project_conversation_history,
    validate_conversation_history,
)
from agentdeck.conversation.models import (
    build_conversation_record,
    build_conversation_transition,
    build_preview_binding_record,
    build_turn_record,
)


def _base_records() -> dict[str, list[dict[str, object]]]:
    return {
        "conversation_sessions": [
            build_conversation_record("c1", created_at="2026-07-13T00:00:00+00:00")
        ],
        "conversation_turns": [],
        "conversation_preview_bindings": [],
    }


def _transition(
    entity_type: str,
    entity_id: str,
    from_state: str | None,
    to_state: str,
    *,
    transition_id: str,
    conversation_id: str = "c1",
) -> dict[str, object]:
    return build_conversation_transition(
        transition_id=transition_id,
        conversation_id=conversation_id,
        entity_type=entity_type,
        entity_id=entity_id,
        from_state=from_state,
        to_state=to_state,
        reason="test",
        created_at="2026-07-13T00:00:00+00:00",
    )


def test_valid_history_projects_current_states() -> None:
    records = _base_records()
    records["conversation_turns"].append(
        build_turn_record(
            "t1", conversation_id="c1", created_at="2026-07-13T00:00:00+00:00"
        )
    )
    transitions = [
        _transition("conversation", "c1", None, "created", transition_id="x1"),
        _transition("conversation", "c1", "created", "ready", transition_id="x2"),
        _transition("turn", "t1", None, "created", transition_id="x3"),
        _transition("turn", "t1", "created", "routing", transition_id="x4"),
        _transition("turn", "t1", "routing", "completed", transition_id="x5"),
    ]

    projection = validate_conversation_history(records, transitions)

    assert projection == project_conversation_history(records, transitions)
    assert projection["conversation_states"] == {"c1": "ready"}
    assert projection["turn_states"] == {"t1": "completed"}
    assert projection["active_turn_by_conversation"] == {}


def test_history_rejects_two_active_turns_for_one_conversation() -> None:
    records = _base_records()
    records["conversation_turns"].extend(
        [
            build_turn_record(
                "t1", conversation_id="c1", created_at="2026-07-13T00:00:00+00:00"
            ),
            build_turn_record(
                "t2", conversation_id="c1", created_at="2026-07-13T00:00:01+00:00"
            ),
        ]
    )
    transitions = [
        _transition("conversation", "c1", None, "created", transition_id="x1"),
        _transition("turn", "t1", None, "created", transition_id="x2"),
        _transition("turn", "t1", "created", "routing", transition_id="x3"),
        _transition("turn", "t2", None, "created", transition_id="x4"),
    ]

    with pytest.raises(ValueError, match="one active turn"):
        validate_conversation_history(records, transitions)


def test_history_rejects_two_pending_previews_for_one_conversation() -> None:
    records = _base_records()
    records["conversation_preview_bindings"].extend(
        [
            build_preview_binding_record(
                "p1",
                conversation_id="c1",
                turn_id=None,
                preview_kind="project_init",
                execution_digest="a" * 64,
                expires_at="2026-07-13T01:00:00+00:00",
                created_at="2026-07-13T00:00:00+00:00",
            ),
            build_preview_binding_record(
                "p2",
                conversation_id="c1",
                turn_id=None,
                preview_kind="project_init",
                execution_digest="b" * 64,
                expires_at="2026-07-13T01:00:00+00:00",
                created_at="2026-07-13T00:00:01+00:00",
            ),
        ]
    )
    transitions = [
        _transition("conversation", "c1", None, "created", transition_id="x1"),
        _transition("preview", "p1", None, "pending", transition_id="x2"),
        _transition("preview", "p2", None, "pending", transition_id="x3"),
    ]

    with pytest.raises(ValueError, match="one pending preview"):
        validate_conversation_history(records, transitions)


@pytest.mark.parametrize(
    ("transitions", "message"),
    [
        (
            [
                _transition("conversation", "c1", None, "created", transition_id="x1"),
                _transition("conversation", "c1", "ready", "busy", transition_id="x2"),
            ],
            "current state",
        ),
        (
            [
                _transition("conversation", "c1", None, "created", transition_id="x1"),
                _transition("conversation", "c1", "created", "closed", transition_id="x2"),
            ],
            "illegal conversation transition",
        ),
        (
            [
                _transition("conversation", "missing", None, "created", transition_id="x1")
            ],
            "unknown conversation",
        ),
    ],
)
def test_history_rejects_stale_illegal_and_unknown_transitions(
    transitions: list[dict[str, object]], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_conversation_history(_base_records(), transitions)


def test_terminal_entity_cannot_transition_again() -> None:
    records = _base_records()
    records["conversation_turns"].append(
        build_turn_record(
            "t1", conversation_id="c1", created_at="2026-07-13T00:00:00+00:00"
        )
    )
    transitions = [
        _transition("turn", "t1", None, "created", transition_id="x1"),
        _transition("turn", "t1", "created", "routing", transition_id="x2"),
        _transition("turn", "t1", "routing", "completed", transition_id="x3"),
        _transition("turn", "t1", "completed", "failed", transition_id="x4"),
    ]

    with pytest.raises(ValueError, match="terminal turn"):
        validate_conversation_history(records, transitions)


def test_transition_cannot_move_turn_to_another_conversation() -> None:
    records = _base_records()
    records["conversation_sessions"].append(
        build_conversation_record("c2", created_at="2026-07-13T00:00:01+00:00")
    )
    records["conversation_turns"].append(
        build_turn_record(
            "t1", conversation_id="c1", created_at="2026-07-13T00:00:00+00:00"
        )
    )

    with pytest.raises(ValueError, match="turn belongs to another conversation"):
        validate_conversation_history(
            records,
            [
                _transition(
                    "turn",
                    "t1",
                    None,
                    "created",
                    transition_id="x1",
                    conversation_id="c2",
                )
            ],
        )


def test_duplicate_record_and_transition_ids_are_rejected() -> None:
    records = _base_records()
    records["conversation_sessions"].append(deepcopy(records["conversation_sessions"][0]))
    with pytest.raises(ValueError, match="duplicate conversation_id"):
        validate_conversation_history(records, [])

    records = _base_records()
    transitions = [
        _transition("conversation", "c1", None, "created", transition_id="x1"),
        _transition("conversation", "c1", "created", "ready", transition_id="x1"),
    ]
    with pytest.raises(ValueError, match="duplicate transition_id"):
        validate_conversation_history(records, transitions)


def test_append_validated_transition_returns_new_list_without_mutating_input() -> None:
    records = _base_records()
    transitions = [
        _transition("conversation", "c1", None, "created", transition_id="x1")
    ]
    before = deepcopy(transitions)

    proposed = append_validated_transition(
        records,
        transitions,
        _transition("conversation", "c1", "created", "ready", transition_id="x2"),
    )

    assert transitions == before
    assert len(proposed) == 2
    assert project_conversation_history(records, proposed)["conversation_states"] == {
        "c1": "ready"
    }
