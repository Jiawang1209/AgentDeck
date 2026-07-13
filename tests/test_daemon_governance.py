from __future__ import annotations

from datetime import datetime, timedelta, timezone
import asyncio

import pytest

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
from agentdeck.state import StateStore


NOW = datetime(2026, 7, 14, 2, 0, tzinfo=timezone.utc)


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
    state["missions"] = [{
        "mission_id": "mis_0123456789ab",
        "status": "running",
        "stop_reason": None,
        "current_step": 1,
        "snapshot_hash": "sha256:" + "a" * 64,
        "updated_at": NOW.isoformat(),
        "can_start": True,
        "blockers": [],
        "workflow_run_id": "run_1",
        "confirmed_at": NOW.isoformat(),
    }]
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
    state["missions"] = [{
        "mission_id": "mis_0123456789ab",
        "status": "running",
        "stop_reason": None,
        "current_step": 1,
        "snapshot_hash": "sha256:" + "a" * 64,
        "updated_at": NOW.isoformat(),
        "can_start": True,
        "blockers": [],
        "workflow_run_id": "run_1",
        "confirmed_at": NOW.isoformat(),
    }]
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
    state["missions"] = [{
        "mission_id": "mis_0123456789ab",
        "status": initial,
        "stop_reason": "human_pause" if initial == "stopped" else None,
        "current_step": 1,
        "snapshot_hash": "sha256:" + "a" * 64,
        "updated_at": NOW.isoformat(),
    }]
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


def test_production_takeover_and_return_control_are_preview_bound(tmp_path) -> None:
    store = StateStore(tmp_path)
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
