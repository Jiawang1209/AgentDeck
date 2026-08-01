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

Derivation is fail-closed in **both directions**: when several agents could fill
the same worker role the answer is `ambiguous` with every candidate listed, and
when one agent's role reads as *several layers at once* that agent is ambiguous
evidence for each of them and binds none. Never a silent pick.
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

# The worker layers that are resolved *from role text* rather than from explicit
# configuration, in display order. Resolving them together is what makes
# cross-layer collisions visible: a role matching two of these sets is ambiguous
# evidence for both, never a clean binding for either.
WORKER_ROLE_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("coder", IMPLEMENTATION_ROLE_HINTS),
    ("code_reviewer", REVIEW_ROLE_HINTS),
)


def _agent_rows(agents: Sequence[Any] | None) -> list[tuple[str, str]]:
    """Normalize plain agent rows to `(agent_id, lowercase role)` pairs.

    Rows that are not objects, carry no `agent_id`, or have an empty `role` are
    skipped. Input order is preserved.
    """
    rows: list[tuple[str, str]] = []
    for item in agents or []:
        if not isinstance(item, dict):
            continue
        agent_id = item.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id:
            continue
        role = str(item.get("role", "")).lower()
        if not role:
            continue
        rows.append((agent_id, role))
    return rows


def resolve_worker_roles(
    agents: Sequence[Any] | None,
    hint_sets: Sequence[tuple[str, Iterable[str]]] = WORKER_ROLE_HINTS,
) -> dict[str, dict[str, Any]]:
    """Resolve every hint-matched worker layer *together*, fail-closed.

    Resolving the layers in one pass is the point: it is the only way to see an
    agent whose free-text role reads as more than one layer. Such an agent is
    ambiguous evidence — binding it to either layer would be a silent pick, and
    binding it to *both* would make one worker review its own work, which the
    review-iteration design explicitly bars.

    Returns `{layer: {"agent_id", "binding_status", "candidates", "conflicts"}}`:

    - exactly one match, matching only this layer -> `bound`
    - no match                                    -> `unbound`
    - several matches, or any match that also reads as another layer
      -> `ambiguous`, `agent_id = None`, every match listed in `candidates`

    `conflicts` lists the cross-layer offenders as
    `[{"agent_id": str, "layers": [layer, ...]}]` (layers in declaration order)
    so the caller can name the collision in its blocker. It is empty for
    plain within-layer ambiguity. Candidate and conflict order follows input
    order; the derivation is pure and does no IO.
    """
    rows = _agent_rows(agents)
    layers = [(str(layer), [str(hint).lower() for hint in hints]) for layer, hints in hint_sets]

    matches_by_layer: dict[str, list[str]] = {}
    layers_by_agent: dict[str, list[str]] = {}
    for layer, hint_values in layers:
        matched = [agent_id for agent_id, role in rows if any(hint in role for hint in hint_values)]
        matches_by_layer[layer] = matched
        for agent_id in matched:
            layers_by_agent.setdefault(agent_id, []).append(layer)

    resolved: dict[str, dict[str, Any]] = {}
    for layer, _hints in layers:
        matched = matches_by_layer[layer]
        conflicts = [
            {"agent_id": agent_id, "layers": list(layers_by_agent[agent_id])}
            for agent_id in matched
            if len(layers_by_agent[agent_id]) > 1
        ]
        if not matched:
            status, agent_id, candidates = "unbound", None, []
        elif conflicts or len(matched) > 1:
            status, agent_id, candidates = "ambiguous", None, list(matched)
        else:
            status, agent_id, candidates = "bound", matched[0], []
        resolved[layer] = {
            "agent_id": agent_id,
            "binding_status": status,
            "candidates": candidates,
            "conflicts": conflicts,
        }
    return resolved


def resolve_worker_role(
    agents: Sequence[Any] | None, hints: Iterable[str]
) -> tuple[str | None, str, list[str]]:
    """Resolve one worker layer from plain agent rows.

    Returns `(agent_id, binding_status, candidates)`. This is a thin projection
    of :func:`resolve_worker_roles`, so it is fail-closed against cross-layer
    collisions too: the requested hint set is resolved *alongside* every other
    known worker hint set, and an agent that reads as more than one of them is
    reported `ambiguous` rather than bound to whichever layer asked first.
    """
    requested = tuple(hints)
    hint_sets: list[tuple[str, Iterable[str]]] = [("requested", requested)]
    for layer, layer_hints in WORKER_ROLE_HINTS:
        if tuple(layer_hints) != requested:
            hint_sets.append((layer, layer_hints))
    resolved = resolve_worker_roles(agents, tuple(hint_sets))["requested"]
    return resolved["agent_id"], str(resolved["binding_status"]), list(resolved["candidates"])
