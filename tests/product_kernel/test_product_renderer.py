from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from agentdeck.kernel.diagnostics import Diagnostic, Severity
from agentdeck.product.presenter import (
    ApprovalPresentation,
    DiagnosisPresentation,
    ExitPresentation,
    FinalPresentation,
    MissionPreviewPresentation,
    RunningPresentation,
    SetupPresentation,
    StatusPresentation,
)
from agentdeck.product.renderer import RenderError, render


def diagnostic() -> Diagnostic:
    return Diagnostic.create(
        code="leader_unavailable",
        stage="discovery",
        severity=Severity.ERROR,
        actor="codex",
        summary="Codex is not ready.",
        cause="The configured executable was not found.",
        impact="The Mission did not start.",
        protection="No fallback transport was selected.",
        recovery_actions=("Run /setup.", "Select another discovered Leader."),
        retryable=True,
        outcome_known=True,
        occurred_at="2026-07-19T00:00:00+00:00",
        mission_id="msn_1",
        trace_id="trc_1",
    )


def mission_preview() -> MissionPreviewPresentation:
    return MissionPreviewPresentation(
        objective="Build an accessible page",
        scope="project",
        leader_backend="codex-cli",
        leader_model="native-default",
        workers=("Codex implementer", "Claude reviewer"),
        tasks=("implementation", "review", "revision", "acceptance"),
        task_dependencies=(
            "implementation: none",
            "review: implementation",
            "revision: review",
            "acceptance: revision",
        ),
        acp_routes=("acp://codex/implementation", "acp://claude/review"),
        permission="approve-for-me",
        project_boundary="/tmp/project",
        acceptance_criteria=("All checks pass",),
        retry_budget=2,
        revision_budget=1,
        non_goals=("No publication",),
        risks=("Unknown outcomes stop work",),
        preview_id="prv_" + "a" * 24,
        version=1,
        content_hash="a" * 64,
    )


def test_status_mapping_renders_the_exact_plan_contract_without_json() -> None:
    text = render({"mode": "status", "state": "ready", "agents": []})

    assert text == "AgentDeck is ready.\nAgents: none"
    assert '{"mode":' not in text


def test_presentations_are_immutable_bounded_human_facts() -> None:
    presentation = StatusPresentation(state="ready", agents=("planner",))

    with pytest.raises(FrozenInstanceError):
        presentation.state = "running"  # type: ignore[misc]
    with pytest.raises(ValueError, match="bounded human-readable text") as error:
        StatusPresentation(state="sensitive-marker-" + "x" * 4_096, agents=())
    assert "sensitive-marker" not in str(error.value)
    with pytest.raises(TypeError, match="tuple"):
        StatusPresentation(state="ready", agents=["planner"])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unsupported"):
        StatusPresentation(state="invented", agents=())
    with pytest.raises(ValueError, match="unsupported"):
        SetupPresentation(project="/tmp/project", leaders=(), permission="root")


def test_diagnosis_presentation_retains_one_stable_diagnostic_fact() -> None:
    fact = diagnostic()
    presentation = DiagnosisPresentation(diagnostic=fact)

    assert presentation.diagnostic is fact
    with pytest.raises(TypeError, match="Diagnostic"):
        DiagnosisPresentation(diagnostic={})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="bounded tuple"):
        DiagnosisPresentation(diagnostic=replace(fact, recovery_actions=()))


def test_mission_preview_renders_every_confirmable_human_fact() -> None:
    text = render(mission_preview())

    for expected in (
        "Mission Preview prv_aaaaaaaaaaaaaaaaaaaaaaaa v1",
        "Objective: Build an accessible page",
        "Scope: project",
        "Leader: codex-cli / native-default",
        "Workers:\n- Codex implementer\n- Claude reviewer",
        "Tasks:\n- implementation\n- review\n- revision\n- acceptance",
        "Task dependencies:\n- implementation: none\n- review: implementation",
        "ACP routes:\n- acp://codex/implementation",
        "Permission: approve-for-me",
        "Project boundary: /tmp/project",
        "Acceptance criteria:\n- All checks pass",
        "Budgets: 2 retries; 1 revisions",
        "Non-goals:\n- No publication",
        "Risks:\n- Unknown outcomes stop work",
        "Content hash: " + "a" * 64,
        "Confirm only this exact preview.",
    ):
        assert expected in text
    assert "{" not in text


@pytest.mark.parametrize(
    ("presentation", "expected"),
    (
        (
            SetupPresentation(
                project="/tmp/project",
                leaders=("codex-cli", "claude-cli"),
                permission="approve-for-me",
            ),
            "AgentDeck setup\nProject: /tmp/project\nLeaders: codex-cli, claude-cli",
        ),
        (
            RunningPresentation(
                mission_id="msn_1", stage="review", agents=("reviewer",),
                completed_tasks=1, total_tasks=4,
            ),
            "Mission msn_1 is running.\nStage: review\nProgress: 1/4 tasks",
        ),
        (
            ApprovalPresentation(
                actor="reviewer", effect="network", risk="external access",
                reason="Dependency metadata is required.",
            ),
            "Approval required.\nActor: reviewer\nEffect: network",
        ),
        (
            ExitPresentation(
                summary="The active Attempt will be interrupted.",
                active_attempts=("att_1",), requires_confirmation=True,
            ),
            "Exit needs confirmation.\nActive attempts: att_1",
        ),
        (
            FinalPresentation(
                state="completed", summary="Mission accepted.",
                completed=("implementation", "review"), incomplete=(),
                evidence=("tests passed",),
            ),
            "Mission completed.\nSummary: Mission accepted.",
        ),
    ),
)
def test_renderer_has_explicit_human_cases(
    presentation: object, expected: str
) -> None:
    text = render(presentation)

    assert expected in text
    assert "{" not in text


@pytest.mark.parametrize(
    ("active_attempts", "requires_confirmation"),
    (
        (("sensitive-marker",), False),
        ((), True),
    ),
)
def test_exit_presentation_rejects_inconsistent_attempt_and_confirmation_state(
    active_attempts: tuple[str, ...], requires_confirmation: bool
) -> None:
    with pytest.raises(ValueError, match="active attempts and confirmation") as error:
        ExitPresentation(
            summary="Persist the current ProductSession.",
            active_attempts=active_attempts,
            requires_confirmation=requires_confirmation,
        )

    assert "sensitive-marker" not in str(error.value)


def test_exit_mapping_never_renders_safe_to_exit_with_active_attempts() -> None:
    with pytest.raises(RenderError, match="unsupported presentation"):
        render(
            {
                "mode": "exit",
                "summary": "The active Attempt will be interrupted.",
                "active_attempts": ["att_1"],
                "requires_confirmation": False,
            }
        )


def test_exit_without_active_attempts_is_safe_without_confirmation() -> None:
    text = render(
        ExitPresentation(
            summary="The ProductSession is persisted.",
            active_attempts=(),
            requires_confirmation=False,
        )
    )

    assert text.startswith("Session is safe to exit.")


def test_diagnosis_answers_what_happened_and_what_the_user_can_do() -> None:
    text = render(DiagnosisPresentation(diagnostic()))

    for expected in (
        "Diagnosis leader_unavailable [error]",
        "Stage: discovery",
        "Actor: codex",
        "What happened: Codex is not ready.",
        "Why: The configured executable was not found.",
        "Impact: The Mission did not start.",
        "Protected: No fallback transport was selected.",
        "Next actions:\n- Run /setup.\n- Select another discovered Leader.",
        "Retryable: yes",
        "Outcome known: yes",
        "Mission: msn_1",
        "Trace: trc_1",
        "Occurred at: 2026-07-19T00:00:00+00:00",
    ):
        assert expected in text


@pytest.mark.parametrize(
    "value",
    (
        {"mode": "unknown", "secret": "sensitive-marker"},
        {"mode": "status", "state": "ready", "agents": [], "extra": "raw"},
        {"mode": "status", "state": "ready", "agents": [object()]},
        object(),
        ["status"],
    ),
)
def test_unknown_types_modes_and_shapes_fail_closed_without_repr(value: object) -> None:
    with pytest.raises(RenderError, match="unsupported presentation") as error:
        render(value)

    assert "sensitive-marker" not in str(error.value)
    assert repr(value) not in str(error.value)


@pytest.mark.parametrize(
    "secret",
    (
        "sk-" + "A" * 48,
        "Authorization: Bearer sensitive-marker",
        '{"password":"sensitive-marker"}',
        'Details: {"password":"sensitive-marker"}',
        'Details: {"OPENAI_API_KEY":"sensitive-marker"}',
        "Cookie: sid=sensitive-marker",
    ),
)
def test_secret_like_or_raw_json_human_fields_never_render(secret: str) -> None:
    with pytest.raises(ValueError, match="prohibited presentation text") as error:
        FinalPresentation(
            state="failed", summary=secret, completed=(),
            incomplete=("implementation",), evidence=(),
        )

    assert "sensitive-marker" not in str(error.value)
    assert secret not in str(error.value)


@pytest.mark.parametrize(
    "safe_text",
    (
        'Discuss the JSON key "password" without assigning a value.',
        'Schema details: {"password": null}',
        'Details: {"password policy":"minimum length"}',
        'Details: {"monkey":"banana"}',
    ),
)
def test_safe_json_credential_discussion_is_not_overclassified(
    safe_text: str,
) -> None:
    presentation = FinalPresentation(
        state="completed", summary=safe_text, completed=("review",),
        incomplete=(), evidence=("tests passed",),
    )

    assert safe_text in render(presentation)


def test_hostile_mapping_is_rejected_without_inspection() -> None:
    class HostileDict(dict[str, object]):
        def get(self, key: str, default: object = None) -> object:
            raise RuntimeError("sensitive-marker")

    with pytest.raises(RenderError, match="unsupported presentation") as error:
        render(HostileDict(mode="status"))

    assert "sensitive-marker" not in str(error.value)


def _assert_content_free_render_error(value: object) -> None:
    with pytest.raises(RenderError, match="unsupported presentation") as error:
        render(value)

    assert "sensitive-marker" not in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


def test_exact_dict_rejects_hostile_equality_key_without_raw_exception() -> None:
    class HostileEqualityKey:
        def __hash__(self) -> int:
            return hash("mode")

        def __eq__(self, other: object) -> bool:
            raise RuntimeError("sensitive-marker")

        def __repr__(self) -> str:
            raise RuntimeError("sensitive-marker")

    _assert_content_free_render_error({HostileEqualityKey(): "status"})


def test_exact_dict_rejects_hostile_hash_key_without_raw_exception() -> None:
    class HostileHashKey:
        armed = False

        def __hash__(self) -> int:
            if self.armed:
                raise RuntimeError("sensitive-marker")
            return 1

        def __repr__(self) -> str:
            raise RuntimeError("sensitive-marker")

    hostile_key = HostileHashKey()
    value: dict[object, object] = {
        "mode": "status", "state": "ready", "agents": [], hostile_key: "extra",
    }
    hostile_key.armed = True

    _assert_content_free_render_error(value)


def test_exact_dict_rejects_hostile_value_without_raw_exception() -> None:
    class HostileValue:
        def __hash__(self) -> int:
            raise RuntimeError("sensitive-marker")

        def __str__(self) -> str:
            raise RuntimeError("sensitive-marker")

        def __repr__(self) -> str:
            raise RuntimeError("sensitive-marker")

    _assert_content_free_render_error(
        {
            "mode": "final",
            "state": HostileValue(),
            "summary": "Mission stopped.",
            "completed": [],
            "incomplete": ["implementation"],
            "evidence": [],
        }
    )
