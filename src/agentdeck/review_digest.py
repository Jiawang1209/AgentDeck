"""Classify a bound review commit against what that branch points at today.

A verdict is a judgement about a specific tree. The merge gate has always asked
what the verdict *said* -- pass, group complete, any verdict at all -- and never
what it was *about*, so code that changed after it was reviewed merged on the
strength of a stale pass.

Three states, deliberately not two:

| state | meaning |
| --- | --- |
| `match` | the reviewed commit is still where that branch points |
| `drift` | the branch moved -- reviewed, then changed |
| `unverifiable` | we could not check: nothing recorded, branch gone, no repo |

`unverifiable` must never render as "verified". `drift: false` and
`verified: false` both read like "fine", which is exactly the class of untrue
statement this repository keeps having to fix.

Pure module: zero IO, zero LLM, no git, no tmux; it does not import `cli`,
`state` or `config`. Git resolution happens in the caller and arrives here as
already-resolved values. Nothing here is an authorization -- it only ever
withholds the *automatic* merge, never the human's explicit one.

See docs/superpowers/specs/2026-08-03-review-digest-binding-design.md.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

REVIEW_DIGEST_STATES = ("match", "drift", "unverifiable")
UNVERIFIABLE_REASONS = ("not_recorded", "branch_missing", "no_git_repo")


def _short(commit: object) -> str:
    text = str(commit or "")
    return text[:7] if text else "?"


def classify_review_binding(
    base_commit: str | None,
    resolved_commit: str | None,
    *,
    git_available: bool = True,
) -> dict[str, Any]:
    """One binding's state. `resolved_commit` is what the branch points at now."""
    if not git_available:
        return {"state": "unverifiable", "reason": "no_git_repo"}
    if not base_commit:
        return {"state": "unverifiable", "reason": "not_recorded"}
    if not resolved_commit:
        return {"state": "unverifiable", "reason": "branch_missing"}
    if base_commit == resolved_commit:
        return {"state": "match", "reason": None}
    return {"state": "drift", "reason": None}


def summarize_review_bindings(
    items: Iterable[Mapping[str, Any]], *, git_available: bool = True
) -> dict[str, Any]:
    """Classify every binding and derive the auto-merge blocker, if any.

    Each item carries `message_id`, `agent_id`, `step`, `base_branch`,
    `base_commit` and `resolved_commit`.

    Drift outranks an unverifiable sibling in the blocker sentence: one proves
    the code moved, the other only says we could not read it, and the human
    reading a single line deserves the stronger fact.

    A blocker is deliberately NOT raised for `not_recorded`: plans created
    before this feature carry no commit, and blocking them would withhold every
    in-flight plan. That single fail-open is documented in the spec and in the
    contract, and it still reports as `not_recorded` -- never as a match.
    """
    bindings: list[dict[str, Any]] = []
    counts = {state: 0 for state in REVIEW_DIGEST_STATES}
    drifted: list[dict[str, Any]] = []
    unverified: list[dict[str, Any]] = []
    for item in items:
        verdict = classify_review_binding(
            item.get("base_commit"),
            item.get("resolved_commit"),
            git_available=git_available,
        )
        binding = {
            "message_id": item.get("message_id"),
            "agent_id": item.get("agent_id"),
            "step": item.get("step"),
            "base_branch": item.get("base_branch"),
            "base_commit": item.get("base_commit"),
            "current_commit": item.get("resolved_commit"),
            "state": verdict["state"],
            "reason": verdict["reason"],
        }
        counts[verdict["state"]] += 1
        if verdict["state"] == "drift":
            drifted.append(binding)
        elif verdict["state"] == "unverifiable" and verdict["reason"] != "not_recorded":
            unverified.append(binding)
        bindings.append(binding)

    blocker: str | None = None
    if drifted:
        first = drifted[0]
        blocker = (
            f"review verdict is bound to {first['base_branch']}@{_short(first['base_commit'])} "
            f"but that branch is now at {_short(first['current_commit'])}; auto-merge withheld"
        )
    elif unverified:
        first = unverified[0]
        blocker = (
            f"cannot verify the reviewed state of {first['base_branch']} "
            f"({first['reason']}); auto-merge withheld"
        )

    return {
        "count": len(bindings),
        "match": counts["match"],
        "drift": counts["drift"],
        "unverifiable": counts["unverifiable"],
        "bindings": bindings,
        "blocker": blocker,
    }
