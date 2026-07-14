from __future__ import annotations

from datetime import datetime, timedelta, timezone
import asyncio
import subprocess

import pytest

from agentdeck.config import load_config, write_default_config
from agentdeck.conversation.bindings import PreviewBindingError
from agentdeck.daemon.governance import (
    GovernanceError,
    authorize_effect,
    build_governance_preview,
    classify_force_stop_attempts,
    consume_governance_preview,
    governance_transition_gate,
    effective_transport_for_step,
)
from agentdeck.daemon.lease import expire_controller, grant_controller, release_controller
from agentdeck.daemon.scheduler import SchedulerFacts
from agentdeck.daemon.service import (
    apply_force_stop_request,
    apply_permission_decision_request,
    apply_mission_pause_request,
    apply_mission_state_request,
    apply_worker_ownership_request,
    apply_transport_reroute_request,
    ProjectDaemonService,
    ServiceError,
    permission_state_for_attempt,
)
from agentdeck.models import AgentRuntimeBinding
from agentdeck.state import StateStore, canonical_snapshot_hash
from agentdeck.runtime.protocol import TransportCapabilities


NOW = datetime(2026, 7, 14, 2, 0, tzinfo=timezone.utc)


def _admitted_mission(root, *, status: str) -> dict[str, object]:
    digest = "sha256:" + "a" * 64
    mission_scope = {
        "mission_id": "mis_0123456789ab", "schema_version": "mission/v1",
        "plan_id": "pln_111111111111", "plan_hash": digest,
        "goal_hash": digest, "summary_hash": digest,
        "steps": [
            {"step_id": "step_1", "position": 1, "agent_id": "worker-a", "role": "implementation", "task_hash": digest},
            {"step_id": "step_2", "position": 2, "agent_id": "worker-b", "role": "review", "task_hash": digest},
        ],
        "project_scope_hash": canonical_snapshot_hash({"project_root": str(root.resolve())}),
        "action_classes": ["worker_task", "declared_local_verification"],
        "skill_provenance": [], "memory_provenance": [],
        "declared_tests_hash": None, "acceptance_criteria_hash": None,
    }
    workers = [
        {
            "agent_id": agent_id, "role": role, "provider": provider,
            "workspace_mode": "shared", "configured_transport": transport,
            "runtime_identity_hash": digest,
            "capability_provenance": {
                "source": "project_config", "transport": transport,
                "adapter_configuration": "present" if transport == "acp" else "not_applicable",
            },
        }
        for agent_id, role, provider, transport in (
            ("worker-a", "implementation", "codex", "acp"),
            ("worker-b", "review", "claude", "tmux"),
        )
    ]
    policy = {
        "approval_mode": "confirm", "autonomous_allowed_agents": [],
        "autonomous_max_approvals": 0, "policy_source": "project_config",
    }
    snapshot = {
        "mission": mission_scope, "workers": workers, "policy": policy,
        "limits": {"step_count": 2, "timeout_seconds": 60, "retry_limit": 0, "worker_budget": 2},
        "mission_hash": canonical_snapshot_hash(mission_scope),
        "policy_hash": canonical_snapshot_hash(policy),
    }
    snapshot["execution_hash"] = canonical_snapshot_hash(snapshot)
    return {
        "mission_id": "mis_0123456789ab", "status": status,
        "stop_reason": "human_pause" if status == "stopped" else None,
        "current_step": 1, "snapshot_hash": snapshot["execution_hash"],
        "execution_snapshot": snapshot, "updated_at": NOW.isoformat(),
        "daemon_admission": {
            "state": "admitted", "snapshot_hash": snapshot["execution_hash"],
            "blocker": None, "recovery_command": None, "updated_at": NOW.isoformat(),
        },
    }


def _seed_acp_worker_runtime(
    store: StateStore, root, *, agent_id: str = "worker-a"
) -> None:
    (root / ".agentdeck" / "config.toml").write_text(
        f'''[project]\nname = "test"\n\n[leader]\nagent_id = "leader"\nprovider = "fake"\nmodel = "fake"\napproval_mode = "confirm"\n\n[[agents]]\nagent_id = "{agent_id}"\nrole = "implementation"\nprovider = "codex"\ncommand = "codex"\nworkspace_mode = "shared"\nrole_prompt = "implement"\ntransport = "acp"\ntransport_command = ["fake-agent-acp"]\n\n[runtime]\nbackend = "tmux"\nsession_name = "agentdeck"\nsocket_name = "agentdeck-test"\n''',
        encoding="utf-8",
    )
    session = store.record_agent_session(
        agent_id,
        "codex",
        "acp-adapter",
        "native-worker",
        str(root),
        TransportCapabilities(True, True, True, True, True, False),
    )
    store.record_protocol_transition(
        "session", session["session_id"], "created", "ready", "ready", {}
    )


def _effect(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "mission_id": "mis_0123456789ab",
        "step_id": "step-1",
        "agent_id": "worker-a",
        "action_class": "project_write",
        "transport": "acp",
        "target": "src/app.py",
    }
    value.update(updates)
    return value


def _snapshot(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "mission_id": "mis_0123456789ab",
        "steps": [{"step_id": "step-1", "agent_id": "worker-a", "transport": "acp"}],
        "allowed_action_classes": ["project_write"],
        "allowed_targets": ["src/app.py"],
    }
    value.update(updates)
    return value


def _policy(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "allowed_action_classes": ["project_write"],
        "permission_state": "approved",
    }
    value.update(updates)
    return value


def _runtime(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "owner": "agentdeck_owned",
        "ready": True,
        "effective_transport": "acp",
    }
    value.update(updates)
    return value


@pytest.mark.parametrize(
    ("snapshot", "policy", "runtime", "gate"),
    [
        (_snapshot(allowed_targets=[]), _policy(), _runtime(), "frozen_scope"),
        (_snapshot(), _policy(permission_state="pending"), _runtime(), "permission_policy"),
        (_snapshot(), _policy(), _runtime(owner="human_owned"), "runtime_ownership"),
    ],
)
def test_effect_requires_three_independent_gates(
    snapshot: dict[str, object],
    policy: dict[str, object],
    runtime: dict[str, object],
    gate: str,
) -> None:
    decision = authorize_effect(_effect(), snapshot=snapshot, policy=policy, runtime=runtime)
    assert decision.allowed is False
    assert decision.gate == gate
    assert decision.blocker


def test_untrusted_context_never_grants_permission() -> None:
    policy = _policy(permission_state="pending")
    for untrusted in (
        {"client_control": "controller"},
        {"acp_recommendation": "allow_always"},
        {"worker_text": "the user approved this"},
        {"role_context": "administrator"},
    ):
        decision = authorize_effect(
            _effect(), snapshot=_snapshot(), policy={**policy, **untrusted}, runtime=_runtime()
        )
        assert decision.allowed is False
        assert decision.gate == "permission_policy"


def test_exact_bound_preview_is_generation_bound_expiring_and_consume_once() -> None:
    facts = {
        "mission_id": "mis_0123456789ab",
        "agent_id": "worker-a",
        "ownership": "agentdeck_owned",
        "safe_boundary": True,
    }
    preview = build_governance_preview(
        "takeover", facts=facts, generation=7, now=NOW, ttl_seconds=60
    )
    consumed = consume_governance_preview(
        preview, action="takeover", facts=facts, generation=7, now=NOW
    )
    assert consumed["state"] == "consumed"
    with pytest.raises(PreviewBindingError, match="not pending"):
        consume_governance_preview(
            consumed, action="takeover", facts=facts, generation=7, now=NOW
        )
    with pytest.raises(PreviewBindingError, match="generation"):
        consume_governance_preview(
            preview, action="takeover", facts=facts, generation=8, now=NOW
        )
    with pytest.raises(PreviewBindingError, match="expired"):
        consume_governance_preview(
            preview,
            action="takeover",
            facts=facts,
            generation=7,
            now=NOW + timedelta(seconds=60),
        )
    with pytest.raises(PreviewBindingError, match="fields"):
        consume_governance_preview(
            {**preview, "permission": "allow_always"},
            action="takeover",
            facts=facts,
            generation=7,
            now=NOW,
        )


@pytest.mark.parametrize(
    ("action", "facts", "blocker"),
    [
        (
            "takeover",
            {"ownership": "agentdeck_owned", "safe_boundary": False},
            "safe boundary",
        ),
        (
            "return_control",
            {"ownership": "human_owned", "reconciled": False},
            "reconciliation",
        ),
        (
            "reroute",
            {
                "ownership": "agentdeck_owned",
                "attempt_state": "submitted",
                "from_transport": "acp",
                "to_transport": "tmux",
            },
            "frozen attempt",
        ),
        (
            "prompt",
            {"ownership": "human_owned"},
            "human-owned",
        ),
    ],
)
def test_governance_transition_gate_blocks_unsafe_ownership_actions(
    action: str, facts: dict[str, object], blocker: str
) -> None:
    decision = governance_transition_gate(action, facts)
    assert decision.allowed is False
    assert blocker in (decision.blocker or "")


def test_force_stop_preserves_unknown_external_effect_as_ambiguous() -> None:
    attempts = [
        {"attempt_id": "mat_000000000001", "state": "prepared", "receipt_summary": None},
        {"attempt_id": "mat_000000000002", "state": "admitting", "receipt_summary": None},
        {"attempt_id": "mat_000000000003", "state": "submitted", "receipt_summary": "accepted"},
        {"attempt_id": "mat_000000000004", "state": "succeeded", "receipt_summary": "done"},
    ]
    assert classify_force_stop_attempts(attempts) == {
        "mat_000000000001": "interrupted",
        "mat_000000000002": "ambiguous",
        "mat_000000000003": "ambiguous",
    }


def test_governance_preview_rejects_unknown_action_and_noncanonical_facts() -> None:
    with pytest.raises(GovernanceError, match="action"):
        build_governance_preview("publish", facts={}, generation=1, now=NOW)
    with pytest.raises(GovernanceError, match="facts"):
        build_governance_preview(
            "takeover", facts={"bad": object()}, generation=1, now=NOW
        )


class _Server:
    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None


def _idle_facts() -> SchedulerFacts:
    return SchedulerFacts(
        mission_id="mis_0123456789ab",
        mission_state="idle",
        step_id=None,
        step_state="none",
        attempt_id=None,
        attempt_state="none",
        reply_state="none",
        handoff_state="none",
        permission_state="none",
        worker_ready=False,
        next_step_eligible=False,
        all_steps_completed=False,
        snapshot_state="valid",
        lineage_state="valid",
        ownership_state="owned",
        active_attempt_count=0,
        blocker=None,
    )


def test_governed_mutation_revalidates_authority_at_queue_execution() -> None:
    async def case() -> None:
        authority = {"valid": True}
        writes: list[str] = []
        service = ProjectDaemonService(
            server=_Server(),
            reconcile_all=lambda: None,
            flush_safe_outboxes=lambda: None,
            load_scheduler_facts=_idle_facts,
            apply_transition=lambda _decision: None,
        )
        await service.start()
        pending = service.submit_governed_mutation(
            revalidate=lambda: authority["valid"],
            mutate=lambda: writes.append("written"),
        )
        authority["valid"] = False
        await service.tick()
        with pytest.raises(ServiceError, match="authority"):
            await pending
        assert writes == []
        await service.close()

    asyncio.run(case())


def test_permission_waiter_requires_exact_authority_and_resolves_once() -> None:
    async def case() -> None:
        service = ProjectDaemonService(
            server=_Server(),
            reconcile_all=lambda: None,
            flush_safe_outboxes=lambda: None,
            load_scheduler_facts=_idle_facts,
            apply_transition=lambda _decision: None,
        )
        await service.start()
        authority = {
            "permission_id": "prm_000000000001",
            "attempt_id": "mat_000000000001",
            "session_id": "ags_session1",
            "generation": 5,
        }
        pending = asyncio.create_task(
            service.wait_for_permission(authority, read_decision=lambda: "pending")
        )
        await asyncio.sleep(0)
        with pytest.raises(ServiceError, match="authority"):
            service.resolve_permission_waiter(
                {**authority, "generation": 6}, "approved"
            )
        service.resolve_permission_waiter(authority, "approved")
        assert await pending == "approved"
        with pytest.raises(ServiceError, match="pending"):
            service.resolve_permission_waiter(authority, "approved")
        await service.close()

    asyncio.run(case())


def test_permission_waiter_is_failed_when_daemon_closes() -> None:
    async def case() -> None:
        service = ProjectDaemonService(
            server=_Server(),
            reconcile_all=lambda: None,
            flush_safe_outboxes=lambda: None,
            load_scheduler_facts=_idle_facts,
            apply_transition=lambda _decision: None,
        )
        await service.start()
        authority = {
            "permission_id": "prm_000000000001",
            "attempt_id": "mat_000000000001",
            "session_id": "ags_session1",
            "generation": 5,
        }
        pending = asyncio.create_task(
            service.wait_for_permission(authority, read_decision=lambda: "pending")
        )
        await asyncio.sleep(0)
        await service.close()
        with pytest.raises(ServiceError, match="closed"):
            await pending

    asyncio.run(case())


def test_pending_permission_is_derived_from_bound_authoritative_transition() -> None:
    attempt = {
        "attempt_id": "mat_000000000001",
        "mission_id": "mis_0123456789ab",
        "agent_id": "worker-a",
    }
    state = {
        "mission_permission_bindings": [
            {
                "mission_id": "mis_0123456789ab",
                "attempt_id": "mat_000000000001",
                "permission_id": "prm_000000000001",
            }
        ],
        "permission_requests": [
            {
                "permission_id": "prm_000000000001",
                "session_id": "ags_session1",
                "status": "pending",
            }
        ],
        "agent_sessions": [
            {"session_id": "ags_session1", "agent_id": "worker-a"}
        ],
        "protocol_state_transitions": [],
    }
    assert permission_state_for_attempt(state, attempt) == "pending"
    state["protocol_state_transitions"] = [
        {
            "entity_type": "permission",
            "entity_id": "prm_000000000001",
            "from_state": "pending",
            "to_state": "approved",
        }
    ]
    assert permission_state_for_attempt(state, attempt) == "approved"


def test_latest_of_multiple_attempt_permissions_is_authoritative() -> None:
    attempt = {
        "attempt_id": "mat_000000000001",
        "mission_id": "mis_0123456789ab",
        "agent_id": "worker-a",
    }
    state = {
        "mission_permission_bindings": [
            {**attempt, "permission_id": "prm_000000000002"},
            {**attempt, "permission_id": "prm_000000000001"},
        ],
        "permission_requests": [
            {
                "permission_id": "prm_000000000002",
                "session_id": "ags_session1",
                "status": "pending",
                "created_at": NOW.isoformat(),
            },
            {
                "permission_id": "prm_000000000001",
                "session_id": "ags_session1",
                "status": "pending",
                "created_at": NOW.isoformat(),
            },
        ],
        "agent_sessions": [
            {"session_id": "ags_session1", "agent_id": "worker-a"}
        ],
        "protocol_state_transitions": [
            {
                "entity_type": "permission",
                "entity_id": "prm_000000000002",
                "from_state": "pending",
                "to_state": "approved",
            }
        ],
        "transport_updates": [
            {
                "kind": "permission_request", "sequence": 0,
                "payload": {"permission_id": "prm_000000000002"},
            },
            {
                "kind": "permission_request", "sequence": 1,
                "payload": {"permission_id": "prm_000000000001"},
            },
        ],
    }
    assert permission_state_for_attempt(state, attempt) == "pending"


def test_permission_binding_lineage_drift_fails_closed() -> None:
    attempt = {
        "attempt_id": "mat_000000000001",
        "mission_id": "mis_0123456789ab",
        "agent_id": "worker-a",
    }
    state = {
        "mission_permission_bindings": [
            {
                "mission_id": "mis_0123456789ab",
                "attempt_id": "mat_000000000001",
                "permission_id": "prm_000000000001",
            }
        ],
        "permission_requests": [
            {
                "permission_id": "prm_000000000001",
                "session_id": "ags_session1",
                "status": "pending",
            }
        ],
        "agent_sessions": [
            {"session_id": "ags_session1", "agent_id": "other-worker"}
        ],
        "protocol_state_transitions": [],
    }
    with pytest.raises(ServiceError, match="permission"):
        permission_state_for_attempt(state, attempt)


def test_permission_decision_history_cannot_transition_after_terminal() -> None:
    attempt = {
        "attempt_id": "mat_000000000001",
        "mission_id": "mis_0123456789ab",
        "agent_id": "worker-a",
    }
    state = {
        "mission_permission_bindings": [{
            "mission_id": attempt["mission_id"],
            "attempt_id": attempt["attempt_id"],
            "permission_id": "prm_000000000001",
        }],
        "permission_requests": [{
            "permission_id": "prm_000000000001",
            "session_id": "ags_session1",
            "status": "pending",
        }],
        "agent_sessions": [{"session_id": "ags_session1", "agent_id": "worker-a"}],
        "protocol_state_transitions": [
            {"entity_type": "permission", "entity_id": "prm_000000000001", "from_state": "pending", "to_state": "approved"},
            {"entity_type": "permission", "entity_id": "prm_000000000001", "from_state": "approved", "to_state": "denied"},
        ],
    }
    with pytest.raises(ServiceError, match="permission history"):
        permission_state_for_attempt(state, attempt)


def test_durable_governance_preview_consumes_once_and_rejects_drift(tmp_path) -> None:
    store = StateStore(tmp_path)
    facts = {
        "mission_id": "mis_0123456789ab",
        "status": "running",
        "current_step": 1,
    }
    preview = store.record_governance_preview(
        "mission_pause", facts=facts, generation=3, now=NOW, ttl_seconds=60
    )
    consumed = store.consume_governance_preview(
        preview_id=preview["preview_id"],
        action="mission_pause",
        facts=facts,
        generation=3,
        now=NOW,
    )
    assert consumed["state"] == "consumed"
    before = store.load()
    with pytest.raises(PreviewBindingError, match="not pending"):
        store.consume_governance_preview(
            preview_id=preview["preview_id"],
            action="mission_pause",
            facts=facts,
            generation=3,
            now=NOW,
        )
    assert store.load() == before

    second = store.record_governance_preview(
        "mission_pause", facts=facts, generation=3, now=NOW, ttl_seconds=60
    )
    before = store.load()
    with pytest.raises(PreviewBindingError, match="state drift"):
        store.consume_governance_preview(
            preview_id=second["preview_id"],
            action="mission_pause",
            facts={**facts, "current_step": 2},
            generation=3,
            now=NOW,
        )
    assert store.load() == before


def test_production_mission_pause_rpc_requires_exact_preview(tmp_path) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["missions"] = [_admitted_mission(tmp_path, status="running")]
    state["mission_attempts"] = []
    store.save(state)

    preview = apply_mission_pause_request(
        store,
        {"mission_id": "mis_0123456789ab"},
        generation=4,
        now=NOW,
    )
    assert preview["state"] == "pending"
    assert store.mission_by_id("mis_0123456789ab")["status"] == "running"

    apply_mission_pause_request(
        store,
        {
            "mission_id": "mis_0123456789ab",
            "preview_id": preview["preview_id"],
        },
        generation=4,
        now=NOW,
    )
    paused = store.mission_by_id("mis_0123456789ab")
    assert paused["status"] == "stopped"
    assert paused["stop_reason"] == "human_pause"


def test_production_mission_pause_rejects_active_attempt_without_writes(tmp_path) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["missions"] = [_admitted_mission(tmp_path, status="running")]
    state["mission_attempts"] = [
        _attempt("mat_000000000001", "submitted", claim="adm_1", receipt="accepted")
    ]
    store.save(state)

    before = store.load()
    with pytest.raises(ServiceError, match="active attempt"):
        apply_mission_pause_request(
            store,
            {"mission_id": "mis_0123456789ab"},
            generation=4,
            now=NOW,
        )
    assert store.load() == before

    facts = {
        "mission_id": "mis_0123456789ab",
        "status": "running",
        "current_step": 1,
        "snapshot_hash": "sha256:" + "a" * 64,
        "active_attempts": [{
            "attempt_id": "mat_000000000001",
            "state": "submitted",
            "dispatch_key": before["mission_attempts"][0]["dispatch_key"],
        }],
    }
    preview = store.record_governance_preview(
        "mission_pause", facts=facts, generation=4, now=NOW
    )
    before_confirm = store.load()
    with pytest.raises(ValueError, match="idle boundary"):
        store.pause_mission_with_governance_preview(
            mission_id="mis_0123456789ab",
            preview_id=preview["preview_id"],
            facts=facts,
            generation=4,
            now=NOW,
        )
    assert store.load() == before_confirm


def test_production_permission_decision_rpc_is_preview_bound(tmp_path) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["permission_requests"] = [{
        "permission_id": "prm_000000000001",
        "session_id": "ags_session1",
        "turn_id": "trn_turn1",
        "tool_name": "write_file",
        "target": "src/app.py",
        "risk": "project_write",
        "status": "pending",
        "decision": None,
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
    }]
    state["agent_sessions"] = [{
        "session_id": "ags_session1",
        "agent_id": "worker-a",
        "provider": "claude",
        "transport": "acp",
        "native_session_id": "native-1",
        "workspace": str(tmp_path),
        "capabilities": {
            "structured_sessions": True,
            "streaming_updates": True,
            "structured_tools": True,
            "permission_requests": True,
            "resume_session": True,
            "observable_terminal": False,
        },
        "state": "ready",
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        "observation_bindings": [],
    }]
    state["protocol_turns"] = [{
        "turn_id": "trn_turn1",
        "session_id": "ags_session1",
        "message_id": "msg_1",
        "kind": "prompt",
        "state": "waiting_permission",
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
    }]
    store.save(state)
    params = {"permission_id": "prm_000000000001", "decision": "approved"}
    preview = apply_permission_decision_request(
        store, params, generation=5, now=NOW
    )
    assert preview["state"] == "pending"
    assert store._derived_protocol_state(
        store.load(), "permission", "prm_000000000001",
        store.load()["permission_requests"][0],
    ) == "pending"

    result = apply_permission_decision_request(
        store,
        {**params, "preview_id": preview["preview_id"]},
        generation=5,
        now=NOW,
    )
    assert result["state"] == "approved"
    persisted = store.load()
    assert store._derived_protocol_state(
        persisted, "permission", "prm_000000000001",
        persisted["permission_requests"][0],
    ) == "approved"
    before = store.load()
    with pytest.raises(ServiceError, match="terminal|confirmation"):
        apply_permission_decision_request(
            store,
            {**params, "preview_id": preview["preview_id"]},
            generation=5,
            now=NOW,
        )
    assert store.load() == before


def _attempt(
    attempt_id: str,
    state: str,
    *,
    claim: str | None,
    receipt: str | None,
    mission_id: str = "mis_0123456789ab",
) -> dict[str, object]:
    return {
        "attempt_id": attempt_id,
        "mission_id": mission_id,
        "step_id": "step_1",
        "agent_id": "worker-a",
        "configured_transport": "acp",
        "dispatch_key": "dsp_" + attempt_id[-12:] * 2 + "0" * 8,
        "admission_claim_id": claim,
        "snapshot_hash": "sha256:" + "a" * 64,
        "state": state,
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        "receipt_summary": receipt,
        "blocker": None,
        "terminal_reason": None,
    }


def test_production_force_stop_preserves_unknown_effects(tmp_path) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["missions"] = [
        {
            "mission_id": mission_id,
            "status": "running",
            "stop_reason": None,
            "current_step": 1,
            "snapshot_hash": "sha256:" + "a" * 64,
            "updated_at": NOW.isoformat(),
        }
        for mission_id in ("mis_0123456789ab", "mis_0123456789ac")
    ]
    state["mission_attempts"] = [
        _attempt("mat_000000000001", "prepared", claim=None, receipt=None),
        _attempt(
            "mat_000000000002",
            "submitted",
            claim="adm_000000000002",
            receipt="ACP accepted",
            mission_id="mis_0123456789ac",
        ),
    ]
    store.save(state)
    preview = apply_force_stop_request(store, {}, generation=6, now=NOW)
    assert preview["state"] == "pending"
    result = apply_force_stop_request(
        store,
        {"preview_id": preview["preview_id"]},
        generation=6,
        now=NOW,
    )
    assert result["state"] == "stopping"
    persisted = store.load()
    assert [item["status"] for item in persisted["missions"]] == [
        "interrupted",
        "interrupted",
    ]
    assert [item["state"] for item in persisted["mission_attempts"]] == [
        "interrupted",
        "ambiguous",
    ]


@pytest.mark.parametrize(
    ("action", "initial", "final"),
    [
        ("mission_resume", "stopped", "running"),
        ("mission_cancel", "running", "interrupted"),
    ],
)
def test_production_mission_control_actions_are_exact_bound(
    tmp_path, action: str, initial: str, final: str
) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["missions"] = [_admitted_mission(tmp_path, status=initial)]
    store.save(state)
    preview = apply_mission_state_request(
        store,
        action=action,
        params={"mission_id": "mis_0123456789ab"},
        generation=7,
        now=NOW,
    )
    result = apply_mission_state_request(
        store,
        action=action,
        params={
            "mission_id": "mis_0123456789ab",
            "preview_id": preview["preview_id"],
        },
        generation=7,
        now=NOW,
    )
    assert result["state"] == final
    assert store.mission_by_id("mis_0123456789ab")["status"] == final


def test_mission_resume_confirm_requires_released_predecessor_and_next_lease(
    tmp_path,
) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["missions"] = [_admitted_mission(tmp_path, status="stopped")]
    state["daemon_runtime"] = {"instance_id": "dmn_instance_a"}
    store.save(state)
    first = grant_controller(
        client_id="mission-controller", now=NOW, ttl_seconds=60
    )
    first_lease = first.current
    assert first_lease is not None
    store.commit_controller_lease(first)
    authority_one = {
        **first_lease.summary(), "daemon_instance_id": "dmn_instance_a",
    }
    preview = apply_mission_state_request(
        store, action="mission_resume",
        params={"mission_id": "mis_0123456789ab"}, generation=1, now=NOW,
        current_authority=authority_one,
    )
    released = release_controller(
        first_lease, lease_id=first_lease.lease_id, generation=1,
        now=NOW + timedelta(seconds=1),
    )
    store.commit_controller_lease(released)
    second = grant_controller(
        client_id="mission-controller", now=NOW + timedelta(seconds=2),
        ttl_seconds=60, previous=released.current,
    )
    second_lease = second.current
    assert second_lease is not None
    store.commit_controller_lease(second)

    result = apply_mission_state_request(
        store, action="mission_resume",
        params={
            "mission_id": "mis_0123456789ab",
            "preview_id": preview["preview_id"],
        },
        generation=2, now=NOW + timedelta(seconds=3),
        current_authority={
            **second_lease.summary(), "daemon_instance_id": "dmn_instance_a",
        },
    )

    assert result["state"] == "running"
    persisted = store.load()
    assert next(
        item for item in persisted["governance_previews"]
        if item["preview_id"] == preview["preview_id"]
    )["state"] == "consumed"


def test_mission_resume_rejects_conflicting_predecessor_terminal_evidence(
    tmp_path,
) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["missions"] = [_admitted_mission(tmp_path, status="stopped")]
    state["daemon_runtime"] = {"instance_id": "dmn_instance_a"}
    store.save(state)
    first = grant_controller(
        client_id="mission-controller", now=NOW, ttl_seconds=60
    )
    first_lease = first.current
    assert first_lease is not None
    store.commit_controller_lease(first)
    preview = apply_mission_state_request(
        store, action="mission_resume",
        params={"mission_id": "mis_0123456789ab"}, generation=1, now=NOW,
        current_authority={
            **first_lease.summary(), "daemon_instance_id": "dmn_instance_a",
        },
    )
    released = release_controller(
        first_lease, lease_id=first_lease.lease_id, generation=1,
        now=NOW + timedelta(seconds=1),
    )
    store.commit_controller_lease(released)
    store.flush_daemon_event_outbox()
    second = grant_controller(
        client_id="mission-controller", now=NOW + timedelta(seconds=2),
        ttl_seconds=60, previous=released.current,
    )
    second_lease = second.current
    assert second_lease is not None
    store.commit_controller_lease(second)
    conflicting = expire_controller(
        first_lease, now=NOW + timedelta(seconds=61)
    )
    state = store.load()
    state["daemon_event_outbox"].append(conflicting.audit_event.summary())
    store.save(state)
    before = store.load()

    with pytest.raises(ServiceError, match="confirmation failed"):
        apply_mission_state_request(
            store, action="mission_resume",
            params={
                "mission_id": "mis_0123456789ab",
                "preview_id": preview["preview_id"],
            },
            generation=2, now=NOW + timedelta(seconds=3),
            current_authority={
                **second_lease.summary(), "daemon_instance_id": "dmn_instance_a",
            },
        )
    assert store.load() == before


@pytest.mark.parametrize(
    ("next_client", "next_instance"),
    [("intervening-controller", "dmn_instance_a"),
     ("mission-controller", "dmn_instance_b")],
)
def test_mission_resume_confirm_rejects_intervening_controller_or_restart(
    tmp_path, next_client: str, next_instance: str,
) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["missions"] = [_admitted_mission(tmp_path, status="stopped")]
    state["daemon_runtime"] = {"instance_id": "dmn_instance_a"}
    store.save(state)
    first = grant_controller(
        client_id="mission-controller", now=NOW, ttl_seconds=60
    )
    first_lease = first.current
    assert first_lease is not None
    store.commit_controller_lease(first)
    preview = apply_mission_state_request(
        store, action="mission_resume",
        params={"mission_id": "mis_0123456789ab"}, generation=1, now=NOW,
        current_authority={
            **first_lease.summary(), "daemon_instance_id": "dmn_instance_a",
        },
    )
    released = release_controller(
        first_lease, lease_id=first_lease.lease_id, generation=1,
        now=NOW + timedelta(seconds=1),
    )
    store.commit_controller_lease(released)
    second = grant_controller(
        client_id=next_client, now=NOW + timedelta(seconds=2), ttl_seconds=60,
        previous=released.current,
    )
    second_lease = second.current
    assert second_lease is not None
    store.commit_controller_lease(second)
    if next_instance != "dmn_instance_a":
        restarted = store.load()
        restarted["daemon_runtime"] = {"instance_id": next_instance}
        store.save(restarted)
    before = store.load()

    with pytest.raises(ServiceError, match="confirmation failed"):
        apply_mission_state_request(
            store, action="mission_resume",
            params={
                "mission_id": "mis_0123456789ab",
                "preview_id": preview["preview_id"],
            },
            generation=2, now=NOW + timedelta(seconds=3),
            current_authority={
                **second_lease.summary(), "daemon_instance_id": next_instance,
            },
        )
    assert store.load() == before


def test_production_takeover_and_return_control_are_preview_bound(tmp_path) -> None:
    store = StateStore(tmp_path)
    _seed_acp_worker_runtime(store, tmp_path)
    state = store.load()
    state["conversation_sessions"] = [{
        "conversation_id": "cvs_session1",
        "created_at": NOW.isoformat(),
    }]
    state["conversation_state_transitions"] = [
        {
            "transition_id": "cst_created",
            "conversation_id": "cvs_session1",
            "entity_type": "conversation",
            "entity_id": "cvs_session1",
            "from_state": None,
            "to_state": "created",
            "reason": "session_started",
            "created_at": NOW.isoformat(),
        },
        {
            "transition_id": "cst_ready",
            "conversation_id": "cvs_session1",
            "entity_type": "conversation",
            "entity_id": "cvs_session1",
            "from_state": "created",
            "to_state": "ready",
            "reason": "session_ready",
            "created_at": NOW.isoformat(),
        },
    ]
    store.save(state)
    target = {"agent_id": "worker-a"}
    preview = apply_worker_ownership_request(
        store, action="takeover", params=target, generation=8, now=NOW
    )
    apply_worker_ownership_request(
        store,
        action="takeover",
        params={**target, "preview_id": preview["preview_id"]},
        generation=8,
        now=NOW,
    )
    from agentdeck.conversation.lifecycle import validate_conversation_history

    persisted = store.load()
    projection = validate_conversation_history(
        {
            key: persisted[key]
            for key in (
                "conversation_sessions",
                "conversation_turns",
                "conversation_preview_bindings",
            )
        },
        persisted["conversation_state_transitions"],
    )
    assert projection["ownership_states"]["worker-a"] == "human_owned"

    return_target = {
        **target,
        "reported_changes": {"summary": "no human changes", "paths": []},
    }
    preview = apply_worker_ownership_request(
        store, action="return_control", params=return_target, generation=8, now=NOW
    )
    apply_worker_ownership_request(
        store,
        action="return_control",
        params={**return_target, "preview_id": preview["preview_id"]},
        generation=8,
        now=NOW,
    )
    persisted = store.load()
    projection = validate_conversation_history(
        {
            key: persisted[key]
            for key in (
                "conversation_sessions",
                "conversation_turns",
                "conversation_preview_bindings",
            )
        },
        persisted["conversation_state_transitions"],
    )
    assert projection["ownership_states"]["worker-a"] == "agentdeck_owned"


def test_return_control_requires_exact_human_change_report_and_execution_rescan(
    tmp_path,
) -> None:
    store = StateStore(tmp_path)
    _seed_acp_worker_runtime(store, tmp_path)
    state = store.load()
    state["conversation_sessions"] = [{
        "conversation_id": "cvs_session1", "created_at": NOW.isoformat()
    }]
    state["conversation_state_transitions"] = [
        {
            "transition_id": "cst_created", "conversation_id": "cvs_session1",
            "entity_type": "conversation", "entity_id": "cvs_session1",
            "from_state": None, "to_state": "created", "reason": "started",
            "created_at": NOW.isoformat(),
        },
        {
            "transition_id": "cst_ready", "conversation_id": "cvs_session1",
            "entity_type": "conversation", "entity_id": "cvs_session1",
            "from_state": "created", "to_state": "ready", "reason": "ready",
            "created_at": NOW.isoformat(),
        },
    ]
    store.save(state)
    target = {"agent_id": "worker-a"}
    takeover = apply_worker_ownership_request(
        store, action="takeover", params=target, generation=8, now=NOW
    )
    accepted = apply_worker_ownership_request(
        store,
        action="takeover",
        params={**target, "preview_id": takeover["preview_id"]},
        generation=8,
        now=NOW,
    )
    assert accepted["baseline_id"].startswith("wob_")
    assert store.load()["worker_takeover_baselines"][0]["state"] == "active"

    (tmp_path / "human.txt").write_text("first\n", encoding="utf-8")
    with pytest.raises(ServiceError, match="return requires reconciliation"):
        apply_worker_ownership_request(
            store,
            action="return_control",
            params={
                **target,
                "reported_changes": {"summary": "wrong", "paths": []},
            },
            generation=8,
            now=NOW,
        )
    report = {"summary": "created human.txt", "paths": ["human.txt"]}
    preview = apply_worker_ownership_request(
        store,
        action="return_control",
        params={**target, "reported_changes": report},
        generation=8,
        now=NOW,
    )
    (tmp_path / "human.txt").write_text("changed again\n", encoding="utf-8")
    with pytest.raises(ServiceError, match="confirmation failed"):
        apply_worker_ownership_request(
            store,
            action="return_control",
            params={
                **target,
                "reported_changes": report,
                "preview_id": preview["preview_id"],
            },
            generation=8,
            now=NOW,
        )
    assert store.load()["worker_takeover_baselines"][0]["state"] == "active"

    preview = apply_worker_ownership_request(
        store,
        action="return_control",
        params={**target, "reported_changes": report},
        generation=8,
        now=NOW,
    )
    result = apply_worker_ownership_request(
        store,
        action="return_control",
        params={
            **target,
            "reported_changes": report,
            "preview_id": preview["preview_id"],
        },
        generation=8,
        now=NOW,
    )
    assert result["ownership"] == "agentdeck_owned"
    assert store.load()["worker_takeover_baselines"][0]["state"] == "reconciled"


def test_return_control_runtime_drift_persists_ambiguous_blocker(tmp_path) -> None:
    store = StateStore(tmp_path)
    _seed_acp_worker_runtime(store, tmp_path)
    state = store.load()
    state["conversation_sessions"] = [{
        "conversation_id": "cvs_session1", "created_at": NOW.isoformat()
    }]
    state["conversation_state_transitions"] = [
        {
            "transition_id": "cst_created", "conversation_id": "cvs_session1",
            "entity_type": "conversation", "entity_id": "cvs_session1",
            "from_state": None, "to_state": "created", "reason": "started",
            "created_at": NOW.isoformat(),
        },
        {
            "transition_id": "cst_ready", "conversation_id": "cvs_session1",
            "entity_type": "conversation", "entity_id": "cvs_session1",
            "from_state": "created", "to_state": "ready", "reason": "ready",
            "created_at": NOW.isoformat(),
        },
    ]
    store.save(state)
    target = {"agent_id": "worker-a"}
    preview = apply_worker_ownership_request(
        store, action="takeover", params=target, generation=8, now=NOW
    )
    apply_worker_ownership_request(
        store,
        action="takeover",
        params={**target, "preview_id": preview["preview_id"]},
        generation=8,
        now=NOW,
    )
    state = store.load()
    state["agent_sessions"][0]["workspace"] = str(tmp_path.parent)
    store.save(state)

    with pytest.raises(ServiceError, match="reconciliation"):
        apply_worker_ownership_request(
            store,
            action="return_control",
            params={
                **target,
                "reported_changes": {"summary": "no changes", "paths": []},
            },
            generation=8,
            now=NOW,
        )
    persisted = store.load()
    assert persisted["worker_takeover_baselines"][0]["state"] == "active"
    decision = persisted["worker_reconciliation_decisions"][-1]
    assert decision["status"] == "ambiguous"
    assert decision["agent_id"] == "worker-a"
    assert decision["baseline_id"] == persisted["worker_takeover_baselines"][0]["baseline_id"]
    assert "runtime" in decision["blocker"]
    view = store.project_view(load_config(tmp_path))
    assert any("worker reconciliation ambiguous" in item for item in view.conversation["blockers"])


def test_return_control_rejects_closed_acp_session_evidence(tmp_path) -> None:
    store = StateStore(tmp_path)
    _seed_acp_worker_runtime(store, tmp_path)
    state = store.load()
    state["conversation_sessions"] = [{
        "conversation_id": "cvs_session1", "created_at": NOW.isoformat()
    }]
    state["conversation_state_transitions"] = [
        {
            "transition_id": "cst_created", "conversation_id": "cvs_session1",
            "entity_type": "conversation", "entity_id": "cvs_session1",
            "from_state": None, "to_state": "created", "reason": "started",
            "created_at": NOW.isoformat(),
        },
        {
            "transition_id": "cst_ready", "conversation_id": "cvs_session1",
            "entity_type": "conversation", "entity_id": "cvs_session1",
            "from_state": "created", "to_state": "ready", "reason": "ready",
            "created_at": NOW.isoformat(),
        },
    ]
    store.save(state)
    target = {"agent_id": "worker-a"}
    preview = apply_worker_ownership_request(
        store, action="takeover", params=target, generation=8, now=NOW
    )
    apply_worker_ownership_request(
        store,
        action="takeover",
        params={**target, "preview_id": preview["preview_id"]},
        generation=8,
        now=NOW,
    )
    protocol = store.validated_protocol_state()
    session = protocol["agent_sessions"][0]
    store.record_protocol_transition(
        "session",
        session["session_id"],
        "ready",
        "disconnected",
        "transport_closed",
        {},
    )

    with pytest.raises(ServiceError, match="reconciliation"):
        apply_worker_ownership_request(
            store,
            action="return_control",
            params={
                **target,
                "reported_changes": {"summary": "no changes", "paths": []},
            },
            generation=8,
            now=NOW,
        )
    assert store.load()["worker_reconciliation_decisions"][-1]["status"] == "ambiguous"


def test_tmux_return_control_revalidates_project_pane_runtime(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = StateStore(tmp_path)
    write_default_config(tmp_path)
    store.bind_agent(
        AgentRuntimeBinding(
            agent_id="planner",
            pane_id="%7",
            session_name="agentdeck",
            cwd=str(tmp_path),
            status="running",
        )
    )
    probes: list[tuple[str, str]] = []

    def pane_exists(_self, config, pane_id: str) -> bool:
        probes.append((config.socket_name, pane_id))
        return pane_id == "%7" and config.session_name == "agentdeck"

    monkeypatch.setattr(
        "agentdeck.runtime.tmux.TmuxBackend.pane_exists", pane_exists
    )
    state = store.load()
    state["conversation_sessions"] = [{
        "conversation_id": "cvs_session1", "created_at": NOW.isoformat()
    }]
    state["conversation_state_transitions"] = [
        {
            "transition_id": "cst_created", "conversation_id": "cvs_session1",
            "entity_type": "conversation", "entity_id": "cvs_session1",
            "from_state": None, "to_state": "created", "reason": "started",
            "created_at": NOW.isoformat(),
        },
        {
            "transition_id": "cst_ready", "conversation_id": "cvs_session1",
            "entity_type": "conversation", "entity_id": "cvs_session1",
            "from_state": "created", "to_state": "ready", "reason": "ready",
            "created_at": NOW.isoformat(),
        },
    ]
    store.save(state)
    target = {"agent_id": "planner"}
    preview = apply_worker_ownership_request(
        store, action="takeover", params=target, generation=9, now=NOW
    )
    apply_worker_ownership_request(
        store,
        action="takeover",
        params={**target, "preview_id": preview["preview_id"]},
        generation=9,
        now=NOW,
    )
    report = {"summary": "no changes", "paths": []}
    preview = apply_worker_ownership_request(
        store,
        action="return_control",
        params={**target, "reported_changes": report},
        generation=9,
        now=NOW,
    )
    result = apply_worker_ownership_request(
        store,
        action="return_control",
        params={
            **target,
            "reported_changes": report,
            "preview_id": preview["preview_id"],
        },
        generation=9,
        now=NOW,
    )
    assert result["ownership"] == "agentdeck_owned"
    assert len(probes) == 4
    assert all(pane_id == "%7" for _socket, pane_id in probes)


def test_tmux_return_timeout_persists_unverifiable_reconciliation(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = StateStore(tmp_path)
    write_default_config(tmp_path)
    store.bind_agent(
        AgentRuntimeBinding(
            agent_id="planner",
            pane_id="%7",
            session_name="agentdeck",
            cwd=str(tmp_path),
            status="running",
        )
    )
    monkeypatch.setattr(
        "agentdeck.runtime.tmux.TmuxBackend.pane_exists",
        lambda *_args: True,
    )
    state = store.load()
    state["conversation_sessions"] = [{
        "conversation_id": "cvs_session1", "created_at": NOW.isoformat()
    }]
    state["conversation_state_transitions"] = [
        {
            "transition_id": "cst_created", "conversation_id": "cvs_session1",
            "entity_type": "conversation", "entity_id": "cvs_session1",
            "from_state": None, "to_state": "created", "reason": "started",
            "created_at": NOW.isoformat(),
        },
        {
            "transition_id": "cst_ready", "conversation_id": "cvs_session1",
            "entity_type": "conversation", "entity_id": "cvs_session1",
            "from_state": "created", "to_state": "ready", "reason": "ready",
            "created_at": NOW.isoformat(),
        },
    ]
    store.save(state)
    target = {"agent_id": "planner"}
    preview = apply_worker_ownership_request(
        store, action="takeover", params=target, generation=9, now=NOW
    )
    apply_worker_ownership_request(
        store,
        action="takeover",
        params={**target, "preview_id": preview["preview_id"]},
        generation=9,
        now=NOW,
    )

    timeout = subprocess.TimeoutExpired(["tmux", "display-message"], 5.0)
    monkeypatch.setattr(
        "agentdeck.runtime.tmux.TmuxBackend.pane_exists",
        lambda *_args: (_ for _ in ()).throw(timeout),
    )
    with pytest.raises(ServiceError, match="reconciliation is ambiguous"):
        apply_worker_ownership_request(
            store,
            action="return_control",
            params={
                **target,
                "reported_changes": {"summary": "no changes", "paths": []},
            },
            generation=9,
            now=NOW,
        )

    persisted = store.load()
    assert persisted["worker_takeover_baselines"][0]["state"] == "active"
    decision = persisted["worker_reconciliation_decisions"][-1]
    assert decision["status"] == "ambiguous"
    assert decision["blocker"] == (
        "Worker reconciliation tmux runtime evidence is unverifiable"
    )
    assert "display-message" not in repr(decision)


def test_production_reroute_applies_only_before_attempt_creation(tmp_path) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["conversation_sessions"] = [{
        "conversation_id": "cvs_session1", "created_at": NOW.isoformat()
    }]
    state["conversation_state_transitions"] = [
        {
            "transition_id": "cst_created", "conversation_id": "cvs_session1",
            "entity_type": "conversation", "entity_id": "cvs_session1",
            "from_state": None, "to_state": "created", "reason": "started",
            "created_at": NOW.isoformat(),
        },
        {
            "transition_id": "cst_ready", "conversation_id": "cvs_session1",
            "entity_type": "conversation", "entity_id": "cvs_session1",
            "from_state": "created", "to_state": "ready", "reason": "ready",
            "created_at": NOW.isoformat(),
        },
    ]
    state["missions"] = [{
        "mission_id": "mis_0123456789ab",
        "status": "running",
        "current_step": 0,
        "snapshot_hash": "sha256:" + "a" * 64,
        "execution_snapshot": {
            "mission": {"steps": [{"step_id": "step_1", "position": 1, "agent_id": "worker-a"}]},
            "workers": [{"agent_id": "worker-a", "configured_transport": "acp"}],
        },
    }]
    store.save(state)
    params = {
        "mission_id": "mis_0123456789ab",
        "step_id": "step_1",
        "agent_id": "worker-a",
        "to_transport": "tmux",
    }
    preview = apply_transport_reroute_request(
        store, params, generation=9, now=NOW
    )
    result = apply_transport_reroute_request(
        store,
        {**params, "preview_id": preview["preview_id"]},
        generation=9,
        now=NOW,
    )
    assert result["effective_transport"] == "tmux"
    assert store.load()["mission_transport_reroutes"][0]["to_transport"] == "tmux"
    assert effective_transport_for_step(
        store.load(),
        mission_id="mis_0123456789ab",
        step_id="step_1",
        agent_id="worker-a",
        frozen_transport="acp",
    ) == "tmux"
    corrupted = store.load()
    corrupted["mission_transport_reroutes"][0]["credential"] = "must-not-pass"
    with pytest.raises(GovernanceError, match="lineage"):
        effective_transport_for_step(
            corrupted,
            mission_id="mis_0123456789ab",
            step_id="step_1",
            agent_id="worker-a",
            frozen_transport="acp",
        )
