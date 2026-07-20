from dataclasses import FrozenInstanceError

import pytest

import agentdeck.kernel.session as session_module
from agentdeck.kernel.session import ProductSession, SessionState, TransitionError


ALLOWED_TRANSITIONS = frozenset(
    {
        (SessionState.SETUP, SessionState.READY),
        (SessionState.SETUP, SessionState.CANCELLED),
        (SessionState.READY, SessionState.DRAFTING),
        (SessionState.READY, SessionState.CANCELLED),
        (SessionState.DRAFTING, SessionState.AWAITING_CONFIRMATION),
        (SessionState.DRAFTING, SessionState.FAILED),
        (SessionState.AWAITING_CONFIRMATION, SessionState.DRAFTING),
        (SessionState.AWAITING_CONFIRMATION, SessionState.RUNNING),
        (SessionState.RUNNING, SessionState.AWAITING_APPROVAL),
        (SessionState.RUNNING, SessionState.PAUSED),
        (SessionState.RUNNING, SessionState.NEEDS_ATTENTION),
        (SessionState.RUNNING, SessionState.COMPLETED),
        (SessionState.RUNNING, SessionState.FAILED),
        (SessionState.RUNNING, SessionState.CANCELLED),
        (SessionState.AWAITING_APPROVAL, SessionState.RUNNING),
        (SessionState.AWAITING_APPROVAL, SessionState.PAUSED),
        (SessionState.AWAITING_APPROVAL, SessionState.FAILED),
        (SessionState.AWAITING_APPROVAL, SessionState.CANCELLED),
        (SessionState.PAUSED, SessionState.RUNNING),
        (SessionState.PAUSED, SessionState.CANCELLED),
        (SessionState.NEEDS_ATTENTION, SessionState.RUNNING),
        (SessionState.NEEDS_ATTENTION, SessionState.PAUSED),
        (SessionState.NEEDS_ATTENTION, SessionState.FAILED),
        (SessionState.NEEDS_ATTENTION, SessionState.CANCELLED),
    }
)


def test_new_session_starts_in_setup_with_declared_states() -> None:
    session = ProductSession.new("ses_1", "/tmp/project")

    assert session.state is SessionState.SETUP
    assert {state.value for state in SessionState} == {
        "setup",
        "ready",
        "drafting",
        "awaiting_confirmation",
        "running",
        "awaiting_approval",
        "paused",
        "needs_attention",
        "completed",
        "failed",
        "cancelled",
    }


def test_session_transition_matrix_matches_the_declared_edges_exactly() -> None:
    assert len(ALLOWED_TRANSITIONS) == 24

    for source in SessionState:
        for target in SessionState:
            session = ProductSession("ses_1", "/tmp/project", source)
            if (source, target) in ALLOWED_TRANSITIONS:
                updated = session.transition(target)
                assert updated.state is target
                assert updated is not session
            else:
                with pytest.raises(TransitionError, match="illegal session transition"):
                    session.transition(target)
            assert session.state is source


@pytest.mark.parametrize(
    "source",
    [
        SessionState.RUNNING,
        SessionState.AWAITING_APPROVAL,
        SessionState.NEEDS_ATTENTION,
    ],
)
def test_executing_project_can_pause_and_only_paused_project_can_resume(
    source: SessionState,
) -> None:
    paused = ProductSession("ses_1", "/project", source).transition(
        SessionState.PAUSED
    )

    assert paused.state is SessionState.PAUSED
    assert paused.transition(SessionState.RUNNING).state is SessionState.RUNNING


def test_transition_authority_cannot_be_changed_to_restart_terminal_session() -> None:
    authority = session_module._TRANSITIONS
    original_terminal_edges = (
        authority[SessionState.COMPLETED] if isinstance(authority, dict) else None
    )
    try:
        with pytest.raises(TypeError):
            authority[SessionState.COMPLETED] = frozenset(  # type: ignore[index]
                {SessionState.READY}
            )
    finally:
        if isinstance(authority, dict):
            assert original_terminal_edges is not None
            authority[SessionState.COMPLETED] = original_terminal_edges

    completed = ProductSession("ses_1", "/tmp/project", SessionState.COMPLETED)
    with pytest.raises(TransitionError, match="illegal session transition"):
        completed.transition(SessionState.READY)


def test_transition_authority_rebinding_cannot_restart_terminal_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        session_module,
        "_TRANSITIONS",
        frozenset({(SessionState.COMPLETED, SessionState.READY)}),
    )

    completed = ProductSession("ses_1", "/tmp/project", SessionState.COMPLETED)
    with pytest.raises(TransitionError, match="illegal session transition"):
        completed.transition(SessionState.READY)


def test_session_rejects_illegal_transition_without_changing_original() -> None:
    session = ProductSession.new("ses_1", "/tmp/project")

    with pytest.raises(TransitionError, match="illegal session transition"):
        session.transition(SessionState.COMPLETED)

    assert session.state is SessionState.SETUP


def test_open_goal_is_retained_by_a_copy_during_setup() -> None:
    session = ProductSession.new("ses_1", "/tmp/project")

    updated = session.retain_goal("build the page")

    assert updated.pending_goal == "build the page"
    assert session.pending_goal is None
    assert updated is not session


@pytest.mark.parametrize(
    "state", tuple(state for state in SessionState if state is not SessionState.SETUP)
)
def test_goal_retention_is_rejected_outside_setup(state: SessionState) -> None:
    session = ProductSession("ses_1", "/tmp/project", state)

    with pytest.raises(TransitionError, match="goal retention requires setup"):
        session.retain_goal("build the page")

    assert session.pending_goal is None


def test_retained_goal_survives_setup_to_ready_transition() -> None:
    original = ProductSession.new("ses_1", "/tmp/project")
    retained = original.retain_goal("build the page")

    ready = retained.transition(SessionState.READY)

    assert ready.pending_goal == "build the page"
    assert ready.state is SessionState.READY
    assert retained.state is SessionState.SETUP
    assert original.pending_goal is None


def test_session_facts_are_immutable() -> None:
    session = ProductSession.new("ses_1", "/tmp/project")

    with pytest.raises(FrozenInstanceError):
        session.state = SessionState.READY  # type: ignore[misc]


@pytest.mark.parametrize(
    ("session_id", "project_root"),
    (("", "/tmp/project"), ("   ", "/tmp/project"), ("ses_1", ""), ("ses_1", "\t")),
)
def test_new_session_rejects_empty_identity_values(
    session_id: str, project_root: str
) -> None:
    with pytest.raises(ValueError):
        ProductSession.new(session_id, project_root)


@pytest.mark.parametrize(
    ("session_id", "project_root"),
    ((1, "/tmp/project"), ("ses_1", 1)),
)
def test_new_session_rejects_non_string_identity_values(
    session_id: object, project_root: object
) -> None:
    with pytest.raises(TypeError):
        ProductSession.new(session_id, project_root)  # type: ignore[arg-type]


@pytest.mark.parametrize("goal", ("", "  ", 1, None))
def test_retain_goal_rejects_empty_or_non_string_goal(goal: object) -> None:
    session = ProductSession.new("ses_1", "/tmp/project")

    with pytest.raises((TypeError, ValueError)):
        session.retain_goal(goal)  # type: ignore[arg-type]


def test_session_rejects_non_enum_state_and_transition_target() -> None:
    with pytest.raises(TypeError):
        ProductSession("ses_1", "/tmp/project", "setup")  # type: ignore[arg-type]

    session = ProductSession.new("ses_1", "/tmp/project")
    with pytest.raises(TypeError):
        session.transition("ready")  # type: ignore[arg-type]
