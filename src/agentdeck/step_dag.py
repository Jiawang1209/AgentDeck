"""Deterministic step-dependency derivation for the run-loop wave guard.

The single-wave guard used to be "dispatch only the earliest incomplete step".
The property it actually protects has always been:

> **never dispatch work whose inputs are not ready.**

"Earliest number" is merely a *sufficient but too strong* implementation of that
property on purely linear plans. This module states the property directly, as a
dependency relation, so a review group — whose members review the **same
already-finished** implementation step and depend on nothing else — can fan out
in one wave instead of queueing.

**Dependencies are derived by the program, never authored by the Leader.**
Letting a provider emit a dependency graph would hand away half of the
"the program owns the loop" north-star principle and would change the plan
schema shared by four live providers. So the relation is a pure function of the
step markers that already exist:

| case | direct dependencies |
| --- | --- |
| ordinary step N | the step before it (`[]` for N=1) |
| review-group member | the step before the group's **first** member |
| ...and if that "step before" is itself a group member | **every** member of that group |

The last row matters in both directions: a step that follows a review group must
wait for the *whole* group, not just the numerically adjacent member.

Two derivations are exposed. `derive_step_dependencies` is the direct edge set —
readable provenance for `agentdeck plan status`. `derive_step_ancestors` is its
transitive closure, and **that** is what the guard consumes: requiring the whole
ancestor set to be complete is what makes the linear case *provably* identical
to the old `step == earliest_incomplete` test (a linear step N is ready exactly
when steps 1..N-1 are all complete, i.e. exactly when N is the earliest
incomplete one), while still letting group siblings — who are not each other's
ancestors — run together.

Pure module: zero IO, zero LLM, no tmux, no state writes; it does not import
`cli`, `state` or `config`. Nothing here is persisted and nothing here is an
authorization: the approval gate, the allowlist, the budgets and the
never-force-spawn rule are all unchanged and still upstream of every dispatch.

See docs/superpowers/specs/2026-08-03-dag-step-dependencies-design.md.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

STEP_DAG_RULE = "linear_plus_review_group_fanout"


def _step_number(step: Any) -> int | None:
    if not isinstance(step, Mapping):
        return None
    number = step.get("step")
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        return None
    return number


def _group_of(step: Mapping[str, Any]) -> int | None:
    group = step.get("review_group")
    if isinstance(group, bool) or not isinstance(group, int):
        return None
    return group


def _group_members(steps: Sequence[Any]) -> tuple[dict[int, int], dict[int, list[int]]]:
    """({step number: group}, {group: sorted member numbers})."""
    group_by_step: dict[int, int] = {}
    members: dict[int, list[int]] = {}
    for step in steps:
        number = _step_number(step)
        if number is None:
            continue
        group = _group_of(step)
        if group is None:
            continue
        group_by_step[number] = group
        members.setdefault(group, []).append(number)
    for group in members:
        members[group] = sorted(members[group])
    return group_by_step, members


def derive_step_dependencies(steps: Sequence[Any]) -> dict[int, list[int]]:
    """{step number: direct dependency step numbers}, ascending.

    Steps without a usable positive integer `step` are ignored rather than
    guessed at.
    """
    group_by_step, members = _group_members(steps)
    numbers = sorted(
        number for number in (_step_number(step) for step in steps) if number is not None
    )

    def closing_set(before: int) -> list[int]:
        """What must be finished for the step numbered `before + 1` to start."""
        if before < 1:
            return []
        group = group_by_step.get(before)
        if group is not None:
            return list(members[group])
        return [before]

    dependencies: dict[int, list[int]] = {}
    for number in numbers:
        group = group_by_step.get(number)
        anchor = min(members[group]) if group is not None else number
        dependencies[number] = closing_set(anchor - 1)
    return dependencies


def derive_step_ancestors(steps: Sequence[Any]) -> dict[int, list[int]]:
    """Transitive closure of `derive_step_dependencies`, ascending per step."""
    dependencies = derive_step_dependencies(steps)
    resolved: dict[int, set[int]] = {}

    def walk(number: int, seen: frozenset[int]) -> set[int]:
        cached = resolved.get(number)
        if cached is not None:
            return cached
        collected: set[int] = set()
        for dependency in dependencies.get(number, ()):
            if dependency in seen:
                continue  # defensive: the derivation cannot cycle, never loop anyway
            collected.add(dependency)
            collected |= walk(dependency, seen | {number})
        resolved[number] = collected
        return collected

    return {number: sorted(walk(number, frozenset())) for number in dependencies}


def dependencies_for(dependencies: Mapping[int, Sequence[int]], step: int) -> list[int]:
    """Direct dependencies of `step`, falling back to the linear chain.

    A step number the plan body does not describe falls back to the old
    sequential shape — the fallback never *widens* what may run.
    """
    known = dependencies.get(step)
    if known is not None:
        return list(known)
    return [step - 1] if step > 1 else []


def ancestors_for(ancestors: Mapping[int, Sequence[int]], step: int) -> list[int]:
    """Ancestors of `step`, falling back to "every earlier step"."""
    known = ancestors.get(step)
    if known is not None:
        return list(known)
    return list(range(1, max(step, 1)))
