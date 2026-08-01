"""North-star role topology: pure derivation of the six layered roles.

This module is **pure**: zero IO, zero LLM, no tmux, no state writes. It does
not import `cli`, `state` or `config` — every input is plain data so the whole
binding matrix can be tested without a project on disk.

The pivot of the design (see
`docs/superpowers/specs/2026-08-01-g6-role-topology-design.md`): the six
north-star roles are **not the same kind of thing**. `frontdesk` is a command
with no agent, `planner` / `orchestrator` are logical Leader sub-roles with no
tmux pane, and `coder` / `code_reviewer` / `round_reviewer` are worker agents
that do have panes. Flattening them into one agent table would lie, so every
role carries a closed `binding_kind` which also explains why some fields are
*necessarily* null.

Derivation is fail-closed: when several agents could fill the same worker role
the answer is `ambiguous` with every candidate listed — never a silent pick.
The topology is an **observation surface, not an authorization**: it changes no
gate, authorizes no dispatch.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

ROLE_TOPOLOGY_LAYERS = ("intake", "orchestration", "work", "acceptance")
ROLE_BINDING_KINDS = ("command", "logical_leader", "worker_agent")
ROLE_BINDING_STATUSES = ("bound", "unbound", "ambiguous")
ROLE_LIFECYCLES = ("persistent", "task_scoped", "on_demand")

# The static skeleton of the six north-star layers; declaration order is the
# display order.
ROLE_SPECS: tuple[dict[str, str], ...] = (
    {"role": "frontdesk", "layer": "intake", "binding_kind": "command", "lifecycle": "persistent"},
    {"role": "planner", "layer": "orchestration", "binding_kind": "logical_leader", "lifecycle": "persistent"},
    {"role": "orchestrator", "layer": "orchestration", "binding_kind": "logical_leader", "lifecycle": "persistent"},
    {"role": "coder", "layer": "work", "binding_kind": "worker_agent", "lifecycle": "task_scoped"},
    {"role": "code_reviewer", "layer": "work", "binding_kind": "worker_agent", "lifecycle": "task_scoped"},
    {"role": "round_reviewer", "layer": "acceptance", "binding_kind": "worker_agent", "lifecycle": "on_demand"},
)

# Semantic matching over the free-text `role` of `[[agents]]` (lowercase
# substring). These are hints, not a schema: a project is free to phrase its
# roles however it likes, and anything unmatched is reported as `unbound`
# rather than guessed at.
IMPLEMENTATION_ROLE_HINTS = ("implement", "coder", "coding", "实现")
REVIEW_ROLE_HINTS = ("review", "审查", "复审")


def resolve_worker_role(
    agents: Sequence[Any] | None, hints: Iterable[str]
) -> tuple[str | None, str, list[str]]:
    """Resolve one worker layer from plain agent rows.

    Returns `(agent_id, binding_status, candidates)`:

    - exactly one match  -> `(agent_id, "bound", [])`
    - no match           -> `(None, "unbound", [])`
    - several matches    -> `(None, "ambiguous", [every candidate])`

    `agents` is plain data: `[{"agent_id": str, "role": str}, ...]`. Rows that
    are not objects, or that carry no `agent_id`, are skipped. `candidates`
    keeps input order. This never picks the first match — ambiguity is reported
    as such so a human resolves it.
    """
    hint_values = [str(hint).lower() for hint in hints]
    matches: list[str] = []
    for item in agents or []:
        if not isinstance(item, dict):
            continue
        agent_id = item.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id:
            continue
        role = str(item.get("role", "")).lower()
        if not role:
            continue
        if any(hint in role for hint in hint_values):
            matches.append(agent_id)
    if not matches:
        return None, "unbound", []
    if len(matches) == 1:
        return matches[0], "bound", []
    return None, "ambiguous", matches
