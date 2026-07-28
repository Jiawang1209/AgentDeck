"""G2 planner-stage brief schema, snapshot, and prompt template.

The planner stage produces a macro brief (goal restatement, acceptance
criteria, risks, coarse macro steps) that the orchestrator stage later
expands into the existing leader-plan step schema. Macro steps must not
assign agents; worker context enters the prompt only at the orchestrator
stage. See docs/superpowers/specs/2026-07-28-g2-planner-orchestrator-split-design.md.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .base import leader_skill_context_prompt_lines

PLANNER_BRIEF_SCHEMA_VERSION = "planner-brief/v1"
PLANNER_BRIEF_REQUIRED_FIELDS = ("goal", "acceptance_criteria", "risks", "macro_steps")
_LIST_FIELDS = ("acceptance_criteria", "risks", "macro_steps")
_NON_EMPTY_LIST_FIELDS = ("acceptance_criteria", "macro_steps")


def _invalid() -> ValueError:
    return ValueError("planner brief schema is invalid")


def validate_planner_brief(payload: object) -> dict[str, Any]:
    if type(payload) is not dict:
        raise _invalid()
    if set(payload) != set(PLANNER_BRIEF_REQUIRED_FIELDS):
        raise _invalid()
    goal = payload["goal"]
    if type(goal) is not str or not goal.strip():
        raise _invalid()
    for field in _LIST_FIELDS:
        values = payload[field]
        if type(values) is not list:
            raise _invalid()
        if field in _NON_EMPTY_LIST_FIELDS and not values:
            raise _invalid()
        for item in values:
            if type(item) is not str or not item.strip():
                raise _invalid()
    return {field: payload[field] for field in PLANNER_BRIEF_REQUIRED_FIELDS}


def planner_brief_content_hash(brief: dict[str, Any]) -> str:
    canonical = json.dumps(
        {field: brief[field] for field in PLANNER_BRIEF_REQUIRED_FIELDS},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def planner_brief_snapshot(brief: dict[str, Any]) -> dict[str, Any]:
    validated = validate_planner_brief(brief)
    snapshot: dict[str, Any] = {"schema_version": PLANNER_BRIEF_SCHEMA_VERSION}
    snapshot.update(validated)
    snapshot["content_hash"] = planner_brief_content_hash(validated)
    return snapshot


def build_planner_prompt(
    goal: str, skill_context: dict[str, Any] | None = None
) -> str:
    lines = [
        "You are the planner sub-role of the AgentDeck logical Leader.",
        "Produce a macro planning brief for the goal below. Do not assign",
        "agents, do not name workers, and do not produce executable steps;",
        "a separate orchestrator stage expands this brief later.",
        "",
        f"Goal: {goal}",
        "",
        "Respond with exactly one JSON object and nothing else, containing:",
        '- "goal": one-sentence restatement of the goal (non-empty string)',
        '- "acceptance_criteria": non-empty list of concrete, checkable',
        "  completion criteria (strings)",
        '- "risks": list of foreseeable risks (strings, may be empty)',
        '- "macro_steps": non-empty list of coarse-grained intents in order',
        "  (strings, no agent assignment)",
        "",
    ]
    lines.extend(leader_skill_context_prompt_lines(skill_context))
    return "\n".join(lines)
