"""Pure governance gates and exact-bound confirmation previews.

The helpers in this module grant no authority by themselves.  They turn
authoritative, already-persisted facts into a deterministic decision that the
project daemon can revalidate immediately before its queued mutation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import re
from typing import Any

from ..conversation.bindings import (
    PreviewBindingError,
    consume_preview_binding,
    execution_digest,
)


class GovernanceError(ValueError):
    """Governance facts are malformed or cannot authorize the action."""


_ACTIONS = frozenset(
    {
        "takeover",
        "return_control",
        "reroute",
        "permission_decision",
        "mission_pause",
        "mission_resume",
        "mission_cancel",
        "force_daemon_stop",
    }
)
_EFFECT_FIELDS = frozenset(
    {
        "mission_id",
        "step_id",
        "agent_id",
        "action_class",
        "transport",
        "target",
    }
)
_PREVIEW_FIELDS = frozenset(
    {"action", "generation", "execution_digest", "previewed_at", "expires_at", "state"}
)
_REROUTE_RECORD_FIELDS = frozenset(
    {
        "mission_id",
        "step_id",
        "agent_id",
        "snapshot_hash",
        "mission_status",
        "current_step",
        "ownership",
        "attempt_state",
        "from_transport",
        "to_transport",
        "preview_id",
        "generation",
        "created_at",
    }
)


@dataclass(frozen=True)
class GateResult:
    allowed: bool
    gate: str | None
    blocker: str | None

    @classmethod
    def allow(cls) -> "GateResult":
        return cls(True, None, None)

    @classmethod
    def block(cls, gate: str, blocker: str) -> "GateResult":
        return cls(False, gate, blocker)


def _canonical(value: object, *, label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise GovernanceError(f"{label} are invalid")
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(encoded.encode("utf-8")) > 65536:
            raise GovernanceError(f"{label} are invalid")
        decoded = json.loads(encoded)
    except GovernanceError:
        raise
    except (TypeError, ValueError):
        raise GovernanceError(f"{label} are invalid") from None
    if type(decoded) is not dict:
        raise GovernanceError(f"{label} are invalid")
    return decoded


def _string_list(value: object) -> list[str] | None:
    if (
        type(value) is not list
        or any(type(item) is not str or not item for item in value)
        or len(value) != len(set(value))
    ):
        return None
    return value


def frozen_scope_gate(effect: object, snapshot: object) -> GateResult:
    try:
        current = _canonical(effect, label="effect")
        frozen = _canonical(snapshot, label="frozen scope")
    except GovernanceError as exc:
        return GateResult.block("frozen_scope", str(exc))
    if set(current) != _EFFECT_FIELDS:
        return GateResult.block("frozen_scope", "effect fields are invalid")
    steps = frozen.get("steps")
    actions = _string_list(frozen.get("allowed_action_classes"))
    targets = _string_list(frozen.get("allowed_targets"))
    if (
        frozen.get("mission_id") != current.get("mission_id")
        or type(steps) is not list
        or actions is None
        or targets is None
    ):
        return GateResult.block("frozen_scope", "frozen Mission scope is invalid")
    matches = [
        item
        for item in steps
        if type(item) is dict
        and item.get("step_id") == current.get("step_id")
        and item.get("agent_id") == current.get("agent_id")
        and item.get("transport") == current.get("transport")
    ]
    if len(matches) != 1:
        return GateResult.block("frozen_scope", "effect is outside frozen Worker scope")
    if current.get("action_class") not in actions:
        return GateResult.block("frozen_scope", "effect action class is outside frozen scope")
    if current.get("target") not in targets:
        return GateResult.block("frozen_scope", "effect target is outside frozen scope")
    return GateResult.allow()


def permission_policy_gate(effect: object, policy: object) -> GateResult:
    try:
        current = _canonical(effect, label="effect")
        current_policy = _canonical(policy, label="permission policy")
    except GovernanceError as exc:
        return GateResult.block("permission_policy", str(exc))
    actions = _string_list(current_policy.get("allowed_action_classes"))
    if actions is None:
        return GateResult.block("permission_policy", "permission policy is invalid")
    if current.get("action_class") not in actions:
        return GateResult.block("permission_policy", "action class is not permitted")
    if current_policy.get("permission_state") != "approved":
        return GateResult.block("permission_policy", "permission is not approved")
    # Additional fields are deliberately ignored: controller possession, ACP
    # recommendations, Worker prose, and role context cannot grant permission.
    return GateResult.allow()


def runtime_ownership_gate(effect: object, runtime: object) -> GateResult:
    try:
        current = _canonical(effect, label="effect")
        current_runtime = _canonical(runtime, label="runtime ownership")
    except GovernanceError as exc:
        return GateResult.block("runtime_ownership", str(exc))
    if current_runtime.get("owner") != "agentdeck_owned":
        return GateResult.block("runtime_ownership", "Worker is not AgentDeck-owned")
    if current_runtime.get("ready") is not True:
        return GateResult.block("runtime_ownership", "Worker runtime is not ready")
    if current_runtime.get("effective_transport") != current.get("transport"):
        return GateResult.block("runtime_ownership", "Worker transport ownership drift")
    return GateResult.allow()


def authorize_effect(
    effect: object, *, snapshot: object, policy: object, runtime: object
) -> GateResult:
    """Require scope, policy, and runtime ownership as independent gates."""
    for decision in (
        frozen_scope_gate(effect, snapshot),
        permission_policy_gate(effect, policy),
        runtime_ownership_gate(effect, runtime),
    ):
        if not decision.allowed:
            return decision
    return GateResult.allow()


def build_governance_preview(
    action: str,
    *,
    facts: Mapping[str, object],
    generation: int,
    now: datetime,
    ttl_seconds: float = 300,
) -> dict[str, object]:
    if action not in _ACTIONS:
        raise GovernanceError("governance action is invalid")
    if type(generation) is not int or generation < 1:
        raise GovernanceError("governance generation is invalid")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise GovernanceError("governance time is invalid")
    if (
        type(ttl_seconds) not in {int, float}
        or not math.isfinite(float(ttl_seconds))
        or ttl_seconds <= 0
    ):
        raise GovernanceError("governance preview TTL is invalid")
    current = _canonical(dict(facts), label="governance facts")
    previewed_at = now.astimezone(timezone.utc)
    return {
        "action": action,
        "generation": generation,
        "execution_digest": execution_digest(current),
        "previewed_at": previewed_at.isoformat(),
        "expires_at": (
            previewed_at + timedelta(seconds=float(ttl_seconds))
        ).isoformat(),
        "state": "pending",
    }


def consume_governance_preview(
    preview: Mapping[str, object],
    *,
    action: str,
    facts: Mapping[str, object],
    generation: int,
    now: datetime,
) -> dict[str, object]:
    expected_fields = set(_PREVIEW_FIELDS)
    if "preview_id" in preview:
        expected_fields.add("preview_id")
    if preview.get("state") == "consumed":
        expected_fields.add("consumed_at")
    if "controller_lineage" in preview:
        expected_fields.add("controller_lineage")
    if set(preview) != expected_fields:
        raise PreviewBindingError("preview fields are invalid")
    digest = preview.get("execution_digest")
    if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise PreviewBindingError("preview execution_digest invalid")
    if preview.get("action") != action:
        raise PreviewBindingError("preview action drift")
    if preview.get("generation") != generation:
        raise PreviewBindingError("preview generation drift")
    binding = {
        key: preview.get(key)
        for key in ("execution_digest", "expires_at", "state")
    }
    if "consumed_at" in preview:
        binding["consumed_at"] = preview["consumed_at"]
    consumed = consume_preview_binding(binding, facts, now=now)
    return {
        **dict(preview),
        "state": consumed["state"],
        "consumed_at": consumed["consumed_at"],
    }


def governance_transition_gate(action: str, facts: object) -> GateResult:
    try:
        current = _canonical(facts, label="governance transition facts")
    except GovernanceError as exc:
        return GateResult.block("runtime_ownership", str(exc))
    ownership = current.get("ownership")
    if action == "prompt":
        if ownership != "agentdeck_owned":
            return GateResult.block("runtime_ownership", "Worker is human-owned")
        return GateResult.allow()
    if action == "takeover":
        if ownership != "agentdeck_owned":
            return GateResult.block("runtime_ownership", "Worker is not AgentDeck-owned")
        if current.get("safe_boundary") is not True:
            return GateResult.block("runtime_ownership", "takeover requires a safe boundary")
        return GateResult.allow()
    if action == "return_control":
        if ownership != "human_owned":
            return GateResult.block("runtime_ownership", "Worker is not human-owned")
        if current.get("reconciled") is not True:
            return GateResult.block("runtime_ownership", "return requires reconciliation")
        return GateResult.allow()
    if action == "reroute":
        if ownership != "agentdeck_owned":
            return GateResult.block("runtime_ownership", "Worker is not AgentDeck-owned")
        if current.get("attempt_state") != "none":
            return GateResult.block("frozen_scope", "reroute cannot modify a frozen attempt")
        if (
            current.get("from_transport") not in {"acp", "tmux"}
            or current.get("to_transport") not in {"acp", "tmux"}
            or current.get("from_transport") == current.get("to_transport")
        ):
            return GateResult.block("frozen_scope", "reroute transport is invalid")
        return GateResult.allow()
    return GateResult.block("frozen_scope", "governance transition is unsupported")


def classify_force_stop_attempts(
    attempts: Sequence[Mapping[str, object]],
) -> dict[str, str]:
    """Classify only incomplete attempts without erasing unknown effects."""
    result: dict[str, str] = {}
    seen: set[str] = set()
    for item in attempts:
        if not isinstance(item, Mapping):
            raise GovernanceError("force-stop attempt facts are invalid")
        attempt_id = item.get("attempt_id")
        state = item.get("state")
        receipt = item.get("receipt_summary")
        if type(attempt_id) is not str or not attempt_id or attempt_id in seen:
            raise GovernanceError("force-stop attempt facts are invalid")
        seen.add(attempt_id)
        if state in {"succeeded", "completed", "failed", "cancelled", "interrupted", "ambiguous"}:
            continue
        if state == "prepared" and receipt is None:
            result[attempt_id] = "interrupted"
        elif state in {"admitting", "submitted", "running"}:
            result[attempt_id] = "ambiguous"
        else:
            raise GovernanceError("force-stop attempt state is invalid")
    return result


def effective_transport_for_step(
    state: Mapping[str, object],
    *,
    mission_id: str,
    step_id: str,
    agent_id: str,
    frozen_transport: str,
) -> str:
    """Resolve only one exact confirmed future-attempt reroute."""
    if frozen_transport not in {"acp", "tmux"}:
        raise GovernanceError("frozen transport is invalid")
    records = state.get("mission_transport_reroutes", [])
    if type(records) is not list or any(type(item) is not dict for item in records):
        raise GovernanceError("transport reroute state is invalid")
    matches = [
        item for item in records
        if item.get("mission_id") == mission_id and item.get("step_id") == step_id
    ]
    if not matches:
        return frozen_transport
    if len(matches) != 1:
        raise GovernanceError("transport reroute lineage is ambiguous")
    item = matches[0]
    if (
        set(item) != _REROUTE_RECORD_FIELDS
        or item.get("agent_id") != agent_id
        or item.get("from_transport") != frozen_transport
        or item.get("to_transport") not in {"acp", "tmux"}
        or item.get("to_transport") == frozen_transport
        or type(item.get("snapshot_hash")) is not str
        or re.fullmatch(r"sha256:[0-9a-f]{64}", str(item.get("snapshot_hash"))) is None
        or item.get("mission_status") not in {
            "pending_confirmation", "preparing", "running", "stopped"
        }
        or type(item.get("current_step")) is not int
        or int(item["current_step"]) < 0
        or item.get("ownership") != "agentdeck_owned"
        or item.get("attempt_state") != "none"
        or type(item.get("preview_id")) is not str
        or not item["preview_id"]
        or type(item.get("generation")) is not int
        or int(item["generation"]) < 1
        or type(item.get("created_at")) is not str
    ):
        raise GovernanceError("transport reroute lineage is invalid")
    try:
        created_at = datetime.fromisoformat(str(item["created_at"]))
    except ValueError:
        raise GovernanceError("transport reroute lineage is invalid") from None
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise GovernanceError("transport reroute lineage is invalid")
    return str(item["to_transport"])
