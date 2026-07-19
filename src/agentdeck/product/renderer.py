"""Explicit plain-text rendering for Product presentations."""

from __future__ import annotations

from typing import Final

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


class RenderError(ValueError):
    """Raised when input is not an exact supported presentation."""


_PRESENTATION_FIELDS: Final = {
    "setup": (SetupPresentation, ("project", "leaders", "permission")),
    "status": (StatusPresentation, ("state", "agents")),
    "mission_preview": (
        MissionPreviewPresentation,
        (
            "objective", "scope", "leader_backend", "leader_model", "workers",
            "tasks", "task_dependencies", "acp_routes", "permission", "project_boundary",
            "acceptance_criteria", "retry_budget", "revision_budget", "non_goals",
            "risks", "preview_id", "version", "content_hash",
            "leader_adapter", "leader_version", "additional_budgets",
        ),
    ),
    "running": (
        RunningPresentation,
        ("mission_id", "stage", "agents", "completed_tasks", "total_tasks"),
    ),
    "approval": (
        ApprovalPresentation,
        ("actor", "effect", "risk", "reason"),
    ),
    "diagnosis": (DiagnosisPresentation, ("diagnostic",)),
    "exit": (
        ExitPresentation,
        ("summary", "active_attempts", "requires_confirmation"),
    ),
    "final": (
        FinalPresentation,
        ("state", "summary", "completed", "incomplete", "evidence"),
    ),
}
_SEQUENCE_FIELDS: Final = frozenset(
    {
        "leaders", "agents", "workers", "tasks", "task_dependencies", "acp_routes",
        "acceptance_criteria", "non_goals", "risks", "active_attempts",
        "completed", "incomplete", "evidence",
        "additional_budgets",
    }
)
_OPTIONAL_FIELDS: Final = {
    "mission_preview": {
        "leader_adapter": "acp",
        "leader_version": "unreported",
        "additional_budgets": (),
    }
}


def render(value: object) -> str:
    """Render one known presentation without repr or JSON fallback."""

    presentation = _coerce_presentation(value)
    if type(presentation) is SetupPresentation:
        return _render_setup(presentation)
    if type(presentation) is StatusPresentation:
        return _render_status(presentation)
    if type(presentation) is MissionPreviewPresentation:
        return _render_mission_preview(presentation)
    if type(presentation) is RunningPresentation:
        return _render_running(presentation)
    if type(presentation) is ApprovalPresentation:
        return _render_approval(presentation)
    if type(presentation) is DiagnosisPresentation:
        return _render_diagnosis(presentation)
    if type(presentation) is ExitPresentation:
        return _render_exit(presentation)
    if type(presentation) is FinalPresentation:
        return _render_final(presentation)
    raise RenderError("unsupported presentation")


def _coerce_presentation(value: object) -> object:
    supported = tuple(spec[0] for spec in _PRESENTATION_FIELDS.values())
    if type(value) in supported:
        return value
    snapshot = _safe_mapping_snapshot(value)
    if snapshot is None:
        raise RenderError("unsupported presentation")
    mode = snapshot.get("mode")
    if type(mode) is not str or mode not in _PRESENTATION_FIELDS:
        raise RenderError("unsupported presentation")
    constructor, fields = _PRESENTATION_FIELDS[mode]
    defaults = _OPTIONAL_FIELDS.get(mode, {})
    required = {"mode", *fields} - set(defaults)
    if not required <= set(snapshot) or not set(snapshot) <= {"mode", *fields}:
        raise RenderError("unsupported presentation")
    facts: dict[str, object] = {}
    for field in fields:
        fact = snapshot[field] if field in snapshot else defaults[field]
        if field in _SEQUENCE_FIELDS and type(fact) in {list, tuple}:
            fact = tuple(fact)
        facts[field] = fact
    presentation = _safe_construct(constructor, facts)
    if presentation is None:
        raise RenderError("unsupported presentation")
    return presentation


def _safe_mapping_snapshot(value: object) -> dict[str, object] | None:
    try:
        if type(value) is not dict:
            return None
        items = tuple(value.items())
        if any(type(key) is not str for key, _ in items):
            return None
        return {key: fact for key, fact in items}
    except BaseException:
        return None


def _safe_construct(
    constructor: type[object], facts: dict[str, object]
) -> object | None:
    try:
        return constructor(**facts)
    except BaseException:
        return None


def _render_setup(value: SetupPresentation) -> str:
    leaders = ", ".join(value.leaders) if value.leaders else "none"
    return (
        "AgentDeck setup\n"
        f"Project: {value.project}\n"
        f"Leaders: {leaders}\n"
        f"Permission: {value.permission}\n"
        "Choose a Leader and model, then confirm setup."
    )


def _render_status(value: StatusPresentation) -> str:
    agents = ", ".join(value.agents) if value.agents else "none"
    return f"AgentDeck is {value.state}.\nAgents: {agents}"


def _render_mission_preview(value: MissionPreviewPresentation) -> str:
    return "\n".join(
        (
            f"Mission Preview {value.preview_id} v{value.version}",
            f"Objective: {value.objective}",
            f"Scope: {value.scope}",
            f"Leader: {value.leader_backend} / {value.leader_model}",
            f"Leader transport: {value.leader_adapter} ({value.leader_version})",
            _section("Workers", value.workers),
            _section("Tasks", value.tasks),
            _section("Task dependencies", value.task_dependencies),
            _section("ACP routes", value.acp_routes),
            f"Permission: {value.permission}",
            f"Project boundary: {value.project_boundary}",
            _section("Acceptance criteria", value.acceptance_criteria),
            f"Budgets: {value.retry_budget} retries; {value.revision_budget} revisions",
            _section("Additional budgets", value.additional_budgets),
            _section("Non-goals", value.non_goals),
            _section("Risks", value.risks),
            f"Content hash: {value.content_hash}",
            "Confirm only this exact preview.",
        )
    )


def _render_running(value: RunningPresentation) -> str:
    agents = ", ".join(value.agents) if value.agents else "none"
    return (
        f"Mission {value.mission_id} is running.\n"
        f"Stage: {value.stage}\n"
        f"Progress: {value.completed_tasks}/{value.total_tasks} tasks\n"
        f"Agents: {agents}"
    )


def _render_approval(value: ApprovalPresentation) -> str:
    return (
        "Approval required.\n"
        f"Actor: {value.actor}\n"
        f"Effect: {value.effect}\n"
        f"Risk: {value.risk}\n"
        f"Why: {value.reason}"
    )


def _render_diagnosis(value: DiagnosisPresentation) -> str:
    fact = value.diagnostic
    lines = [
        f"Diagnosis {fact.code} [{fact.severity.value}]",
        f"Stage: {fact.stage}",
        f"Actor: {fact.actor}",
        f"What happened: {fact.summary}",
        f"Why: {fact.cause}",
        f"Impact: {fact.impact}",
        f"Protected: {fact.protection}",
        _section("Next actions", fact.recovery_actions),
        f"Retryable: {_yes_no(fact.retryable)}",
        f"Outcome known: {_yes_no(fact.outcome_known)}",
    ]
    for label, identity in (
        ("Mission", fact.mission_id),
        ("Task", fact.task_id),
        ("Attempt", fact.attempt_id),
        ("Trace", fact.trace_id),
    ):
        if identity is not None:
            lines.append(f"{label}: {identity}")
    lines.append(f"Occurred at: {fact.occurred_at}")
    return "\n".join(lines)


def _render_exit(value: ExitPresentation) -> str:
    if value.requires_confirmation:
        attempts = ", ".join(value.active_attempts) if value.active_attempts else "unknown"
        return (
            "Exit needs confirmation.\n"
            f"Active attempts: {attempts}\n"
            f"Impact: {value.summary}"
        )
    return f"Session is safe to exit.\n{value.summary}"


def _render_final(value: FinalPresentation) -> str:
    return "\n".join(
        (
            f"Mission {value.state}.",
            f"Summary: {value.summary}",
            _section("Completed", value.completed),
            _section("Not completed", value.incomplete),
            _section("Evidence", value.evidence),
        )
    )


def _section(label: str, items: tuple[str, ...]) -> str:
    if not items:
        return f"{label}: none"
    return f"{label}:\n" + "\n".join(f"- {item}" for item in items)


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"
