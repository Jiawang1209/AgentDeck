from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest

from agentdeck.daemon.recovery import (
    RecoveryDecision,
    RecoveryError,
    RecoveryFacts,
    reconcile_gate,
    reconcile_startup,
    recovery_facts_from_persisted_state,
)
from agentdeck.daemon.service import scheduler_facts_from_store
from agentdeck.runtime.protocol import TransportCapabilities
from agentdeck.state import StateStore, canonical_snapshot_hash


MISSION_ID = "mis_aaaaaaaaaaaa"
ATTEMPT_ID = "mat_bbbbbbbbbbbb"


def compact_handoff(token: str) -> dict[str, object]:
    return {
        "handoff_token": token,
        "status": "completed",
        "summary": "implementation finished",
        "verification": "pytest passed",
        "risks": "none",
        "next_steps": "review",
        "artifacts": [],
        "trace_ids": [],
    }


def frozen_snapshot(mission_id: str = MISSION_ID) -> dict[str, object]:
    digest = "sha256:" + "a" * 64
    mission = {
        "mission_id": mission_id,
        "schema_version": "mission/v1",
        "plan_id": "pln_" + "1" * 12,
        "plan_hash": digest,
        "goal_hash": digest,
        "summary_hash": digest,
        "steps": [
            {
                "step_id": "step_1",
                "position": 1,
                "agent_id": "worker",
                "role": "implementation",
                "task_hash": digest,
            },
            {
                "step_id": "step_2",
                "position": 2,
                "agent_id": "reviewer",
                "role": "review",
                "task_hash": digest,
            },
        ],
        "project_scope_hash": digest,
        "action_classes": ["worker_task", "declared_local_verification"],
        "skill_provenance": [],
        "memory_provenance": [],
        "declared_tests_hash": None,
        "acceptance_criteria_hash": None,
    }
    workers = [
        {
            "agent_id": "worker",
            "role": "implementation",
            "provider": "codex",
            "workspace_mode": "shared",
            "configured_transport": "acp",
            "capability_provenance": {
                "source": "project_config",
                "transport": "acp",
                "adapter_configuration": "present",
            },
        },
        {
            "agent_id": "reviewer",
            "role": "review",
            "provider": "claude",
            "workspace_mode": "shared",
            "configured_transport": "tmux",
            "capability_provenance": {
                "source": "project_config",
                "transport": "tmux",
                "adapter_configuration": "not_applicable",
            },
        },
    ]
    policy = {
        "approval_mode": "confirm",
        "autonomous_allowed_agents": [],
        "autonomous_max_approvals": 0,
        "policy_source": "project_config",
    }
    limits = {
        "step_count": 2,
        "timeout_seconds": 60,
        "retry_limit": 0,
        "worker_budget": 2,
    }
    snapshot = {
        "mission": mission,
        "workers": workers,
        "policy": policy,
        "limits": limits,
        "mission_hash": canonical_snapshot_hash(mission),
        "policy_hash": canonical_snapshot_hash(policy),
    }
    snapshot["execution_hash"] = canonical_snapshot_hash(snapshot)
    return snapshot


def facts(**overrides: object) -> RecoveryFacts:
    values: dict[str, object] = {
        "mission_id": MISSION_ID,
        "mission_state": "running",
        "attempt_id": ATTEMPT_ID,
        "attempt_state": "submitted",
        "receipt_state": "recorded",
        "reply_state": "none",
        "handoff_state": "none",
        "permission_state": "none",
        "transport_state": "ready",
        "snapshot_state": "valid",
        "lineage_state": "valid",
        "ownership_state": "agentdeck_owned",
    }
    values.update(overrides)
    return RecoveryFacts(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("evidence", "classification", "transition"),
    [
        (
            facts(
                attempt_state="prepared",
                receipt_state="none",
            ),
            "resumable",
            "dispatch_prepared",
        ),
        (
            facts(attempt_state="admitting", receipt_state="none"),
            "ambiguous",
            None,
        ),
        (
            facts(attempt_state="submitted", receipt_state="none"),
            "ambiguous",
            None,
        ),
        (facts(), "resumable", "await_worker"),
        (
            facts(
                attempt_state="succeeded",
                reply_state="received",
            ),
            "resumable",
            "validate_reply",
        ),
        (
            facts(
                attempt_state="succeeded",
                reply_state="validated",
            ),
            "resumable",
            "record_handoff",
        ),
        (
            facts(
                attempt_state="succeeded",
                reply_state="validated",
                handoff_state="recorded",
            ),
            "resumable",
            "activate_next",
        ),
        (facts(permission_state="pending"), "waiting_human", None),
        (facts(transport_state="missing"), "blocked", None),
        (
            facts(
                mission_state="completed",
                attempt_id=None,
                attempt_state="none",
                receipt_state="none",
            ),
            "terminal",
            None,
        ),
    ],
)
def test_recovery_classifies_from_complete_persisted_evidence(
    evidence: RecoveryFacts, classification: str, transition: str | None
) -> None:
    before = deepcopy(evidence.summary())
    decision = reconcile_gate(evidence)
    assert decision.classification == classification
    assert decision.next_transition == transition
    assert evidence.summary() == before


def test_submitted_receipt_never_redispatches_worker() -> None:
    decision = reconcile_gate(facts())
    assert decision == RecoveryDecision(
        classification="resumable",
        reason="submitted Worker receipt is waiting for a reply",
        mission_id=MISSION_ID,
        attempt_id=ATTEMPT_ID,
        next_transition="await_worker",
    )
    assert decision.next_transition != "dispatch_prepared"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"snapshot_state": "drift"}, "frozen Mission snapshot drift"),
        ({"lineage_state": "missing"}, "Mission recovery lineage is incomplete"),
        ({"lineage_state": "conflict"}, "Mission recovery lineage conflicts"),
        ({"ownership_state": "human_owned"}, "Worker is human-owned"),
        ({"ownership_state": "conflict"}, "Worker ownership conflicts"),
        ({"transport_state": "invalid"}, "Worker transport is invalid"),
        (
            {"attempt_state": "succeeded", "reply_state": "none"},
            "terminal Worker result has no reply evidence",
        ),
        (
            {"attempt_state": "running", "reply_state": "received"},
            "Worker reply precedes a terminal successful attempt",
        ),
        (
            {"attempt_state": "succeeded", "reply_state": "validated", "handoff_state": "pending"},
            "Mission handoff state is incomplete",
        ),
        ({"attempt_state": "failed"}, "Worker attempt ended as failed"),
    ],
)
def test_recovery_fails_closed_on_corrupt_or_incomplete_evidence(
    overrides: dict[str, object], reason: str
) -> None:
    decision = reconcile_gate(facts(**overrides))
    assert decision.classification == "blocked"
    assert decision.reason == reason
    assert decision.next_transition is None


def test_unknown_admission_outcome_wins_over_pending_permission() -> None:
    decision = reconcile_gate(
        facts(
            attempt_state="admitting",
            receipt_state="none",
            permission_state="pending",
        )
    )
    assert decision.classification == "ambiguous"
    assert decision.reason == "Worker admission outcome is unknown"


def test_terminal_mission_with_active_unknown_effect_is_not_declared_terminal() -> None:
    decision = reconcile_gate(
        facts(
            mission_state="completed",
            attempt_state="admitting",
            receipt_state="none",
        )
    )
    assert decision.classification == "ambiguous"
    assert decision.reason == "Worker admission outcome is unknown"

    ambiguous = reconcile_gate(
        facts(mission_state="completed", attempt_state="ambiguous")
    )
    assert ambiguous.classification == "ambiguous"
    assert ambiguous.reason == "Worker attempt outcome is ambiguous"


def test_handoff_without_validated_reply_fails_closed() -> None:
    decision = reconcile_gate(
        facts(
            attempt_state="prepared",
            receipt_state="none",
            handoff_state="pending",
        )
    )
    assert decision.classification == "blocked"
    assert decision.reason == "Mission handoff lacks validated reply lineage"


def test_recovery_fact_mapping_requires_exact_fields_and_known_states() -> None:
    raw = facts().summary()
    raw["unexpected"] = True
    with pytest.raises(RecoveryError, match="exact fields"):
        RecoveryFacts.from_mapping(raw)
    raw = facts().summary()
    raw["attempt_state"] = "mystery"
    with pytest.raises(RecoveryError, match="unknown state"):
        RecoveryFacts.from_mapping(raw)


def test_recovery_values_are_immutable() -> None:
    evidence = facts()
    decision = reconcile_gate(evidence)
    with pytest.raises(FrozenInstanceError):
        evidence.attempt_state = "prepared"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        decision.classification = "terminal"  # type: ignore[misc]


@pytest.mark.parametrize("transition", [None, "dispatch_worker", "", 1])
def test_resumable_decision_requires_one_allowed_transition(transition: object) -> None:
    with pytest.raises(RecoveryError, match="transition"):
        RecoveryDecision(
            classification="resumable",
            reason="resume",
            mission_id=MISSION_ID,
            attempt_id=ATTEMPT_ID,
            next_transition=transition,  # type: ignore[arg-type]
        )


def test_integrity_and_unknown_effects_precede_human_and_terminal_shortcuts() -> None:
    pending_unknown = reconcile_gate(
        facts(
            mission_state="pending_confirmation",
            attempt_state="admitting",
            receipt_state="none",
        )
    )
    assert pending_unknown.classification == "ambiguous"
    terminal_missing_reply = reconcile_gate(
        facts(mission_state="completed", attempt_state="succeeded", reply_state="none")
    )
    assert terminal_missing_reply.classification == "blocked"
    terminal_drift = reconcile_gate(
        facts(
            mission_state="completed",
            attempt_id=None,
            attempt_state="none",
            receipt_state="none",
            snapshot_state="drift",
        )
    )
    assert terminal_drift.classification == "blocked"


class RecordingStore(StateStore):
    def __init__(self, root: Path, calls: list[str]) -> None:
        super().__init__(root)
        self.calls = calls

    def flush_daemon_event_outbox(self) -> dict[str, int]:
        self.calls.append("flush_daemon")
        return super().flush_daemon_event_outbox()

    def flush_conversation_event_outbox(self) -> int:
        self.calls.append("flush_conversation")
        return super().flush_conversation_event_outbox()

    def flush_protocol_event_outbox(self) -> int:
        self.calls.append("flush_protocol")
        return super().flush_protocol_event_outbox()

    def commit_recovery_decisions(  # type: ignore[no-untyped-def]
        self, decisions, *, expected_recovery_token
    ):
        self.calls.append("persist_recovery")
        return super().commit_recovery_decisions(
            decisions, expected_recovery_token=expected_recovery_token
        )


def _seed_missions(store: StateStore) -> None:
    state = store.load()
    snapshot = frozen_snapshot()
    state["missions"] = [
        {
            "mission_id": MISSION_ID,
            "status": "running",
            "snapshot_hash": snapshot["execution_hash"],
            "execution_snapshot": snapshot,
        },
        {"mission_id": "mis_cccccccccccc", "status": "completed"},
    ]
    state["mission_attempts"] = [
        {
            "attempt_id": ATTEMPT_ID,
            "mission_id": MISSION_ID,
            "step_id": "step_1",
            "agent_id": "worker",
            "configured_transport": "acp",
            "dispatch_key": "dsp_" + "d" * 32,
            "admission_claim_id": "adm_" + "e" * 12,
            "snapshot_hash": snapshot["execution_hash"],
            "state": "submitted",
            "created_at": "2026-07-13T01:00:00+00:00",
            "updated_at": "2026-07-13T01:01:00+00:00",
            "receipt_summary": "admitted",
            "blocker": None,
            "terminal_reason": None,
        }
    ]
    state["protocol_event_outbox"] = [
        {
            "event_id": "evt_" + "1" * 24,
            "event_type": "mission_attempt_admission_claimed",
            "created_at": "2026-07-13T01:00:30+00:00",
            "payload": {
                "attempt_id": ATTEMPT_ID,
                "mission_id": MISSION_ID,
                "step_id": "step_1",
                "dispatch_key": "dsp_" + "d" * 32,
                "admission_claim_id": "adm_" + "e" * 12,
            },
        },
        {
            "event_id": "evt_" + "2" * 24,
            "event_type": "mission_attempt_submitted",
            "created_at": "2026-07-13T01:01:00+00:00",
            "payload": {
                "attempt_id": ATTEMPT_ID,
                "mission_id": MISSION_ID,
                "step_id": "step_1",
                "dispatch_key": "dsp_" + "d" * 32,
                "admission_claim_id": "adm_" + "e" * 12,
                "reason": None,
            },
        },
    ]
    state["mission_recovery_evidence"] = [
        {
            "mission_id": MISSION_ID,
            "attempt_id": ATTEMPT_ID,
            "agent_id": "worker",
        }
    ]
    store.save(state)


def test_startup_flushes_outboxes_then_atomically_persists_before_enable(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    store = RecordingStore(tmp_path, calls)
    _seed_missions(store)

    result = reconcile_startup(
        store,
        enable_scheduler=lambda: calls.append("enable_scheduler"),
    )

    assert calls == [
        "flush_daemon",
        "flush_conversation",
        "flush_protocol",
        "persist_recovery",
        "enable_scheduler",
    ]
    assert result[0]["classification"] == "resumable"
    state = store.load()
    assert state["recovery_decisions"] == result
    event = state["protocol_event_outbox"][-1]
    assert event["event_type"] == "mission_recovery_classified"
    assert event["payload"] == {
        "attempt_id": ATTEMPT_ID,
        "classification": "resumable",
        "mission_id": MISSION_ID,
        "next_transition": "await_worker",
        "reason": "submitted Worker receipt is waiting for a reply",
    }


def test_startup_requires_durable_evidence_for_every_nonterminal_mission(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    state = store.load()
    state["mission_recovery_evidence"] = []
    store.save(state)
    with pytest.raises(RecoveryError, match="durable recovery evidence"):
        reconcile_startup(store, enable_scheduler=lambda: pytest.fail("enabled"))
    assert store.load().get("recovery_decisions", []) == []


def test_startup_with_only_terminal_missions_enables_after_empty_atomic_commit(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["missions"] = [
        {"mission_id": "mis_cccccccccccc", "status": "completed"}
    ]
    store.save(state)
    enabled: list[bool] = []
    assert reconcile_startup(
        store, enable_scheduler=lambda: enabled.append(True)
    ) == []
    assert enabled == [True]
    assert store.load()["recovery_decisions"] == []


def test_startup_accepts_no_caller_facts_that_can_forge_validated_handoff(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    enabled: list[bool] = []
    with pytest.raises(TypeError):
        reconcile_startup(
            store,
            [facts(reply_state="validated", handoff_state="recorded")],  # type: ignore[arg-type]
            enable_scheduler=lambda: enabled.append(True),
        )
    assert enabled == []
    assert store.load().get("recovery_decisions", []) == []


def test_startup_rejects_attempt_evidence_that_does_not_match_persisted_record(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    state = store.load()
    state["mission_recovery_evidence"][0]["attempt_id"] = "mat_" + "9" * 12
    store.save(state)
    with pytest.raises(RecoveryError, match="durable recovery evidence"):
        reconcile_startup(store, enable_scheduler=lambda: pytest.fail("enabled"))
    assert store.load().get("recovery_decisions", []) == []


def test_startup_outbox_failure_prevents_classification_and_scheduler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    before = store.state_path.read_bytes()
    monkeypatch.setattr(
        store,
        "flush_protocol_event_outbox",
        lambda: (_ for _ in ()).throw(OSError("disk unavailable")),
    )
    enabled: list[bool] = []
    with pytest.raises(RecoveryError, match="pending outbox flush failed"):
        reconcile_startup(store, enable_scheduler=lambda: enabled.append(True))
    assert enabled == []
    assert store.state_path.read_bytes() == before


def test_reconcile_gate_and_startup_do_not_call_runtime_surfaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("recovery called a runtime surface")

    for name in ("subprocess", "socket", "time"):
        monkeypatch.setitem(__import__("sys").modules, name, forbidden)
    result = reconcile_startup(store, enable_scheduler=lambda: None)
    assert result[0]["next_transition"] == "await_worker"


def test_recovery_persistence_rejects_malformed_existing_state_zero_write(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    state = store.load()
    state["recovery_decisions"] = {"bad": True}
    store.save(state)
    before = store.state_path.read_bytes()
    with pytest.raises((TypeError, ValueError), match="recovery_decisions"):
        _snapshot, token = store.load_recovery_snapshot()
        store.commit_recovery_decisions(
            [reconcile_gate(facts())], expected_recovery_token=token
        )
    assert store.state_path.read_bytes() == before


def test_recovery_event_and_state_are_one_atomic_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    saves: list[dict[str, object]] = []
    original = store._atomic_save

    def capture(state: dict[str, object]) -> None:
        saves.append(deepcopy(state))
        original(state)

    monkeypatch.setattr(store, "_atomic_save", capture)
    _snapshot, token = store.load_recovery_snapshot()
    store.commit_recovery_decisions(
        [reconcile_gate(facts())], expected_recovery_token=token
    )
    assert len(saves) == 1
    assert saves[0]["recovery_decisions"]
    assert saves[0]["protocol_event_outbox"]
    json.dumps(saves[0], allow_nan=False)


def test_state_store_rejects_caller_forged_recovery_decision_with_valid_token(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    store.flush_protocol_event_outbox()
    _snapshot, token = store.load_recovery_snapshot()
    forged = RecoveryDecision(
        classification="resumable",
        reason="skip directly to handoff",
        mission_id=MISSION_ID,
        attempt_id=ATTEMPT_ID,
        next_transition="record_handoff",
    )
    before = store.state_path.read_bytes()
    with pytest.raises(ValueError, match="recovery decision evidence drift"):
        store.commit_recovery_decisions(
            [forged], expected_recovery_token=token
        )
    assert store.state_path.read_bytes() == before


def test_startup_rejects_multiple_active_attempts_before_classification(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    state = store.load()
    duplicate = deepcopy(state["mission_attempts"][0])
    duplicate.update(
        {
            "attempt_id": "mat_" + "8" * 12,
            "dispatch_key": "dsp_" + "8" * 32,
            "admission_claim_id": None,
            "state": "prepared",
            "created_at": "2026-07-13T01:02:00+00:00",
            "updated_at": "2026-07-13T01:02:00+00:00",
            "receipt_summary": None,
        }
    )
    state["mission_attempts"].append(duplicate)
    store.save(state)
    enabled: list[bool] = []
    with pytest.raises(RecoveryError, match="durable recovery evidence"):
        reconcile_startup(store, enable_scheduler=lambda: enabled.append(True))
    assert enabled == []
    assert store.load().get("recovery_decisions", []) == []


def test_startup_rejects_post_admission_attempt_with_cleared_claim(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    state = store.load()
    state["mission_attempts"][0]["state"] = "running"
    state["mission_attempts"][0]["admission_claim_id"] = None
    store.save(state)
    enabled: list[bool] = []
    with pytest.raises(RecoveryError, match="durable recovery evidence"):
        reconcile_startup(store, enable_scheduler=lambda: enabled.append(True))
    assert enabled == []
    assert store.load().get("recovery_decisions", []) == []


def test_startup_rejects_two_submitted_claim_generations_for_one_attempt(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    state = store.load()
    claim_b = "adm_" + "b" * 12
    state["mission_attempts"][0]["admission_claim_id"] = claim_b
    base = {
        "attempt_id": ATTEMPT_ID,
        "mission_id": MISSION_ID,
        "step_id": "step_1",
        "dispatch_key": "dsp_" + "d" * 32,
        "admission_claim_id": claim_b,
    }
    state["protocol_event_outbox"].extend([
        {"event_id": "evt_" + "b" * 24, "event_type": "mission_attempt_admission_claimed", "created_at": "2026-07-13T01:02:00+00:00", "payload": dict(base)},
        {"event_id": "evt_" + "c" * 24, "event_type": "mission_attempt_submitted", "created_at": "2026-07-13T01:03:00+00:00", "payload": {**base, "reason": None}},
    ])
    store.save(state)
    with pytest.raises(RecoveryError, match="durable recovery evidence"):
        reconcile_startup(store, enable_scheduler=lambda: pytest.fail("enabled"))


def test_startup_rejects_terminal_mission_with_submitted_attempt(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    state = store.load()
    state["missions"][0]["status"] = "completed"
    store.save(state)
    enabled: list[bool] = []
    with pytest.raises(RecoveryError, match="durable recovery evidence"):
        reconcile_startup(store, enable_scheduler=lambda: enabled.append(True))
    assert enabled == []
    assert store.load().get("recovery_decisions", []) == []


def test_startup_rejects_newer_terminal_attempt_hiding_older_active_attempt(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    state = store.load()
    newer = deepcopy(state["mission_attempts"][0])
    newer.update(
        {
            "attempt_id": "mat_" + "c" * 12,
            "dispatch_key": "dsp_" + "c" * 32,
            "admission_claim_id": "adm_" + "c" * 12,
            "state": "succeeded",
            "created_at": "2026-07-13T01:02:00+00:00",
            "updated_at": "2026-07-13T01:03:00+00:00",
            "receipt_summary": "completed",
        }
    )
    state["mission_attempts"].append(newer)
    state["mission_recovery_evidence"][0].update(
        {"attempt_id": newer["attempt_id"], "agent_id": "worker"}
    )
    reply_id = "mrp_" + "c" * 12
    handoff_id = "hof_" + "c" * 12
    state["mission_worker_replies"] = [{
        "mission_id": MISSION_ID,
        "attempt_id": newer["attempt_id"],
        "reply_id": reply_id,
        "dispatch_key": newer["dispatch_key"],
        "state": "validated",
        "canonical_handoff": compact_handoff(newer["dispatch_key"]),
    }]
    state["mission_handoffs"] = [{
        "mission_id": MISSION_ID,
        "attempt_id": newer["attempt_id"],
        "handoff_id": handoff_id,
        "reply_id": reply_id,
        "state": "recorded",
        "canonical_handoff": compact_handoff(newer["dispatch_key"]),
    }]
    def event(suffix: str, event_type: str, payload: dict[str, object]):
        return {
            "event_id": "evt_" + suffix * 24,
            "event_type": event_type,
            "created_at": "2026-07-13T01:03:00+00:00",
            "payload": payload,
        }
    state["protocol_event_outbox"].extend([
        event("3", "mission_attempt_admission_claimed", {
            "attempt_id": newer["attempt_id"], "mission_id": MISSION_ID,
            "step_id": "step_1", "dispatch_key": newer["dispatch_key"],
            "admission_claim_id": newer["admission_claim_id"],
        }),
        event("4", "mission_attempt_submitted", {
            "attempt_id": newer["attempt_id"], "mission_id": MISSION_ID,
            "step_id": "step_1", "dispatch_key": newer["dispatch_key"],
            "admission_claim_id": newer["admission_claim_id"], "reason": None,
        }),
        event("5", "mission_reply_evidence_recorded", {
            "attempt_id": newer["attempt_id"], "mission_id": MISSION_ID,
            "reply_id": reply_id, "state": "received",
        }),
        event("6", "mission_reply_evidence_recorded", {
            "attempt_id": newer["attempt_id"], "mission_id": MISSION_ID,
            "reply_id": reply_id, "state": "validated",
        }),
        event("7", "mission_handoff_evidence_recorded", {
            "attempt_id": newer["attempt_id"], "mission_id": MISSION_ID,
            "handoff_id": handoff_id, "reply_id": reply_id, "state": "pending",
        }),
        event("8", "mission_handoff_evidence_recorded", {
            "attempt_id": newer["attempt_id"], "mission_id": MISSION_ID,
            "handoff_id": handoff_id, "reply_id": reply_id, "state": "recorded",
        }),
    ])
    store.save(state)
    enabled: list[bool] = []
    with pytest.raises(RecoveryError, match="durable recovery evidence"):
        reconcile_startup(store, enable_scheduler=lambda: enabled.append(True))
    assert enabled == []
    assert store.load().get("recovery_decisions", []) == []


@pytest.mark.parametrize("orphan", ["attempt", "evidence"])
def test_startup_rejects_orphan_attempt_or_evidence(
    tmp_path: Path, orphan: str
) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    state = store.load()
    if orphan == "attempt":
        state["mission_attempts"][0]["mission_id"] = "mis_" + "6" * 12
    else:
        state["mission_recovery_evidence"][0]["mission_id"] = "mis_" + "6" * 12
    store.save(state)
    with pytest.raises(RecoveryError, match="durable recovery evidence"):
        reconcile_startup(store, enable_scheduler=lambda: pytest.fail("enabled"))


def test_startup_rejects_cross_mission_attempt_binding(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    state = store.load()
    other = deepcopy(state["mission_attempts"][0])
    other.update(
        {
            "attempt_id": "mat_" + "6" * 12,
            "mission_id": "mis_cccccccccccc",
            "dispatch_key": "dsp_" + "6" * 32,
            "admission_claim_id": None,
            "state": "failed",
            "receipt_summary": None,
            "blocker": None,
            "terminal_reason": "failed",
        }
    )
    state["mission_attempts"].append(other)
    state["mission_recovery_evidence"][0]["attempt_id"] = other["attempt_id"]
    store.save(state)
    with pytest.raises(RecoveryError, match="durable recovery evidence"):
        reconcile_startup(store, enable_scheduler=lambda: pytest.fail("enabled"))


def test_startup_rejects_persisted_handoff_without_reply_record(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    state = store.load()
    state["mission_handoffs"] = [{
        "mission_id": MISSION_ID,
        "attempt_id": ATTEMPT_ID,
        "handoff_id": "hof_" + "3" * 12,
        "reply_id": "mrp_" + "4" * 12,
        "state": "recorded",
    }]
    store.save(state)
    with pytest.raises(RecoveryError, match="durable recovery evidence"):
        reconcile_startup(store, enable_scheduler=lambda: pytest.fail("enabled"))
    assert store.load().get("recovery_decisions", []) == []


def test_startup_rejects_permission_binding_without_authoritative_request(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    state = store.load()
    state["mission_permission_bindings"] = [
        {
            "mission_id": MISSION_ID,
            "attempt_id": ATTEMPT_ID,
            "permission_id": "prm_" + "5" * 12,
        }
    ]
    store.save(state)
    with pytest.raises(RecoveryError, match="durable recovery evidence"):
        reconcile_startup(store, enable_scheduler=lambda: pytest.fail("enabled"))


@pytest.mark.parametrize("corruption", ["bad_dict", "orphan", "broken_chain"])
def test_startup_rejects_complete_protocol_transition_history_corruption(
    tmp_path: Path, corruption: str
) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    capabilities = TransportCapabilities(True, True, True, True, True, False)
    session = store.record_agent_session(
        "other", "codex", "acp", "native", str(tmp_path), capabilities
    )
    state = store.load()
    if corruption == "bad_dict":
        state["protocol_state_transitions"] = [{}]
    elif corruption == "orphan":
        turn = store.record_protocol_turn(session["session_id"], "msg_orphan")
        permission = store.record_permission_request(
            session["session_id"], turn["turn_id"], "shell", str(tmp_path), "low"
        )
        store.record_protocol_transition(
            "permission", permission["permission_id"], "pending", "approved", None, {}
        )
        state = store.load()
        state["protocol_state_transitions"][0]["entity_id"] = "prm_orphan"
    else:
        store.record_protocol_transition(
            "session", session["session_id"], "created", "ready", None, {}
        )
        store.record_protocol_transition(
            "session", session["session_id"], "ready", "busy", None, {}
        )
        state = store.load()
        state["protocol_state_transitions"][1].update(
            {"from_state": "connecting", "to_state": "ready"}
        )
    store.save(state)
    enabled: list[bool] = []
    with pytest.raises(RecoveryError, match="durable recovery evidence"):
        reconcile_startup(store, enable_scheduler=lambda: enabled.append(True))
    assert enabled == []
    assert store.load().get("recovery_decisions", []) == []


def test_startup_rejects_reply_evidence_without_controlled_audit_history(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    state = store.load()
    state["mission_attempts"][0]["state"] = "succeeded"
    state["mission_worker_replies"] = [
        {
            "mission_id": MISSION_ID,
            "attempt_id": ATTEMPT_ID,
            "reply_id": "mrp_" + "4" * 12,
            "dispatch_key": "dsp_" + "d" * 32,
            "state": "received",
        }
    ]
    store.save(state)
    with pytest.raises(RecoveryError, match="durable recovery evidence"):
        reconcile_startup(store, enable_scheduler=lambda: pytest.fail("enabled"))


def test_controlled_permission_binding_tracks_authoritative_status_transitions(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    capabilities = TransportCapabilities(True, True, True, True, True, False)
    session = store.record_agent_session(
        "worker", "codex", "acp", "native", str(tmp_path), capabilities
    )
    turn = store.record_protocol_turn(session["session_id"], "msg_permission")
    permission = store.record_permission_request(
        session["session_id"], turn["turn_id"], "shell", str(tmp_path), "high"
    )
    binding = store.bind_mission_permission_evidence(
        attempt_id=ATTEMPT_ID, permission_id=permission["permission_id"]
    )
    assert binding["mission_id"] == MISSION_ID
    waiting = reconcile_startup(store, enable_scheduler=lambda: None)
    assert waiting[0]["classification"] == "waiting_human"

    store.record_protocol_transition(
        "permission",
        permission["permission_id"],
        "pending",
        "approved",
        "approved",
        {},
    )
    resumed = reconcile_startup(store, enable_scheduler=lambda: None)
    assert resumed[0]["classification"] == "resumable"
    assert resumed[0]["next_transition"] == "await_worker"


@pytest.mark.parametrize("forged_status", ["approved", "denied", "expired"])
def test_startup_rejects_forged_terminal_permission_base_state(
    tmp_path: Path, forged_status: str
) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    capabilities = TransportCapabilities(True, True, True, True, True, False)
    session = store.record_agent_session(
        "worker", "codex", "acp", "native", str(tmp_path), capabilities
    )
    turn = store.record_protocol_turn(session["session_id"], "msg_permission")
    permission = store.record_permission_request(
        session["session_id"], turn["turn_id"], "shell", str(tmp_path), "high"
    )
    store.bind_mission_permission_evidence(
        attempt_id=ATTEMPT_ID, permission_id=permission["permission_id"]
    )
    state = store.load()
    state["permission_requests"][0]["status"] = forged_status
    assert state["permission_requests"][0]["decision"] is None
    store.save(state)
    enabled: list[bool] = []

    with pytest.raises(RecoveryError, match="durable recovery evidence"):
        reconcile_startup(store, enable_scheduler=lambda: enabled.append(True))

    assert enabled == []
    assert store.load().get("recovery_decisions", []) == []


@pytest.mark.parametrize("forged_field", ["configured_transport", "ownership_state"])
def test_startup_rejects_forged_route_or_ownership_projection(
    tmp_path: Path, forged_field: str
) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    state = store.load()
    state["mission_recovery_evidence"][0][forged_field] = (
        "tmux" if forged_field == "configured_transport" else "agentdeck_owned"
    )
    store.save(state)
    with pytest.raises(RecoveryError, match="durable recovery evidence"):
        reconcile_startup(store, enable_scheduler=lambda: pytest.fail("enabled"))


def test_startup_multi_mission_corruption_disables_scheduler_and_writes_no_classification(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    state = store.load()
    state["missions"].append(
        {
            "mission_id": "mis_777777777777",
            "status": "running",
            "snapshot_hash": "sha256:" + "7" * 64,
        }
    )
    state["mission_recovery_evidence"].append(
        {
            "mission_id": "mis_777777777777",
            "attempt_id": None,
            "agent_id": "forged",
        }
    )
    store.save(state)
    enabled: list[bool] = []
    with pytest.raises(RecoveryError, match="durable recovery evidence"):
        reconcile_startup(store, enable_scheduler=lambda: enabled.append(True))
    assert enabled == []
    assert store.load().get("recovery_decisions", []) == []


def test_startup_compare_and_swap_rejects_state_drift_before_scheduler(
    tmp_path: Path,
) -> None:
    class DriftingStore(StateStore):
        def commit_recovery_decisions(  # type: ignore[no-untyped-def]
            self, decisions, *, expected_recovery_token
        ):
            state = self.load()
            state["missions"][0]["status"] = "preparing"
            self.save(state)
            return super().commit_recovery_decisions(
                decisions, expected_recovery_token=expected_recovery_token
            )

    store = DriftingStore(tmp_path)
    _seed_missions(store)
    enabled: list[bool] = []
    with pytest.raises(RecoveryError, match="recovery authority drift"):
        reconcile_startup(store, enable_scheduler=lambda: enabled.append(True))
    assert enabled == []
    assert store.load().get("recovery_decisions", []) == []


def test_recovery_token_covers_protocol_transition_authority(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    capabilities = TransportCapabilities(True, True, True, True, True, False)
    session = store.record_agent_session(
        "other", "codex", "acp", "native", str(tmp_path), capabilities
    )
    turn = store.record_protocol_turn(session["session_id"], "msg_other")
    permission = store.record_permission_request(
        session["session_id"], turn["turn_id"], "shell", str(tmp_path), "low"
    )
    store.record_protocol_transition(
        "permission", permission["permission_id"], "pending", "approved", None, {}
    )
    store.flush_protocol_event_outbox()
    snapshot, token = store.load_recovery_snapshot()
    decision = reconcile_gate(
        recovery_facts_from_persisted_state(snapshot, MISSION_ID)
    )
    state = store.load()
    state["protocol_state_transitions"][0]["details"] = {"drift": "same outcome"}
    store.save(state)
    with pytest.raises(ValueError, match="recovery authority drift"):
        store.commit_recovery_decisions([decision], expected_recovery_token=token)


def test_recovery_token_covers_complete_protocol_turn_authority(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    capabilities = TransportCapabilities(True, True, True, True, True, False)
    session = store.record_agent_session(
        "other", "codex", "acp", "native", str(tmp_path), capabilities
    )
    store.record_protocol_turn(session["session_id"], "msg_before")
    store.flush_protocol_event_outbox()
    snapshot, token = store.load_recovery_snapshot()
    decision = reconcile_gate(
        recovery_facts_from_persisted_state(snapshot, MISSION_ID)
    )
    state = store.load()
    state["protocol_turns"][0]["message_id"] = "msg_after"
    store.save(state)

    _new_snapshot, new_token = store.load_recovery_snapshot()
    assert new_token != token
    with pytest.raises(ValueError, match="recovery authority drift"):
        store.commit_recovery_decisions([decision], expected_recovery_token=token)


def test_recovery_rejects_noncanonical_protocol_turn_record(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    capabilities = TransportCapabilities(True, True, True, True, True, False)
    session = store.record_agent_session(
        "other", "codex", "acp", "native", str(tmp_path), capabilities
    )
    store.record_protocol_turn(session["session_id"], "msg_before")
    state = store.load()
    state["protocol_turns"][0]["projection_only"] = True
    store.save(state)

    with pytest.raises(ValueError, match="protocol turn record is invalid"):
        store.load_recovery_snapshot()


@pytest.mark.parametrize(
    ("record_type", "corruption"),
    [
        ("session", "missing"),
        ("session", "extra"),
        ("session", "invalid"),
        ("permission", "missing"),
        ("permission", "extra"),
        ("permission", "invalid"),
        ("permission", "invalid_combo"),
    ],
)
def test_startup_rejects_noncanonical_session_and_permission_authority(
    tmp_path: Path, record_type: str, corruption: str
) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    capabilities = TransportCapabilities(True, True, True, True, True, False)
    session = store.record_agent_session(
        "worker", "codex", "acp", "native", str(tmp_path), capabilities
    )
    turn = store.record_protocol_turn(session["session_id"], "msg_permission")
    permission = store.record_permission_request(
        session["session_id"], turn["turn_id"], "shell", str(tmp_path), "high"
    )
    state = store.load()
    record = state[
        "agent_sessions" if record_type == "session" else "permission_requests"
    ][0]
    if corruption == "missing":
        record.pop("provider" if record_type == "session" else "target")
    elif corruption == "extra":
        record["projection_only"] = True
    elif record_type == "session":
        record["capabilities"]["structured_sessions"] = "yes"
    elif corruption == "invalid_combo":
        record["decision"] = "approve"
    else:
        record["decision"] = 42
    store.save(state)
    enabled: list[bool] = []

    with pytest.raises(RecoveryError, match="durable recovery evidence"):
        reconcile_startup(store, enable_scheduler=lambda: enabled.append(True))

    assert enabled == []
    assert store.load().get("recovery_decisions", []) == []


def test_controlled_reply_and_handoff_transitions_drive_recovery(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    store.flush_protocol_event_outbox()
    state = store.load()
    state["mission_attempts"][0].update(
        {
            "state": "succeeded",
            "updated_at": "2026-07-13T01:02:00+00:00",
        }
    )
    store.save(state)
    received = store.record_mission_reply_evidence(
        attempt_id=ATTEMPT_ID,
        dispatch_key="dsp_" + "d" * 32,
        state="received",
        canonical_handoff=compact_handoff("dsp_" + "d" * 32),
    )
    validated = store.record_mission_reply_evidence(
        attempt_id=ATTEMPT_ID,
        dispatch_key="dsp_" + "d" * 32,
        state="validated",
        expected_reply_id=received["reply_id"],
    )
    pending = store.record_mission_handoff_evidence(
        attempt_id=ATTEMPT_ID,
        reply_id=validated["reply_id"],
        state="pending",
    )
    store.record_mission_handoff_evidence(
        attempt_id=ATTEMPT_ID,
        reply_id=validated["reply_id"],
        state="recorded",
        expected_handoff_id=pending["handoff_id"],
    )
    result = reconcile_startup(store, enable_scheduler=lambda: None)
    assert result[0]["classification"] == "resumable"
    assert result[0]["next_transition"] == "activate_next"


def test_reply_evidence_rejects_success_without_durable_receipt_zero_write(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    state = store.load()
    state["mission_attempts"][0].update(
        {"state": "succeeded", "receipt_summary": None}
    )
    store.save(state)
    before = store.state_path.read_bytes()
    with pytest.raises(ValueError, match="mission attempt state invalid"):
        store.record_mission_reply_evidence(
            attempt_id=ATTEMPT_ID,
            dispatch_key="dsp_" + "d" * 32,
            state="received",
            canonical_handoff=compact_handoff("dsp_" + "d" * 32),
        )
    assert store.state_path.read_bytes() == before


def test_handoff_rechecks_reply_receipt_lineage_zero_write(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    store.flush_protocol_event_outbox()
    state = store.load()
    state["mission_attempts"][0]["state"] = "succeeded"
    store.save(state)
    received = store.record_mission_reply_evidence(
        attempt_id=ATTEMPT_ID,
        dispatch_key="dsp_" + "d" * 32,
        state="received",
        canonical_handoff=compact_handoff("dsp_" + "d" * 32),
    )
    validated = store.record_mission_reply_evidence(
        attempt_id=ATTEMPT_ID,
        dispatch_key="dsp_" + "d" * 32,
        state="validated",
        expected_reply_id=received["reply_id"],
    )
    state = store.load()
    state["mission_attempts"][0]["receipt_summary"] = None
    store.save(state)
    before = store.state_path.read_bytes()
    with pytest.raises(ValueError, match="mission attempt state invalid"):
        store.record_mission_handoff_evidence(
            attempt_id=ATTEMPT_ID,
            reply_id=validated["reply_id"],
            state="pending",
        )
    assert store.state_path.read_bytes() == before


def test_mission_daemon_admission_is_durable_idempotent_and_drift_safe(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    state = store.load()
    mission = state["missions"][0]
    mission["status"] = "preparing"
    mission["confirmed_at"] = "2026-07-13T01:00:00+00:00"
    state["mission_attempts"] = []
    state["mission_recovery_evidence"] = [
        {"mission_id": MISSION_ID, "attempt_id": None, "agent_id": None}
    ]
    state["protocol_event_outbox"] = []
    mission["daemon_admission"] = {
        "state": "confirmed_not_admitted",
        "snapshot_hash": mission["snapshot_hash"],
        "blocker": "verified project daemon is unavailable",
        "recovery_command": f"agentdeck mission run --mission-id {MISSION_ID} --confirm",
        "updated_at": "2026-07-13T01:01:00+00:00",
    }
    store.save(state)

    accepted = store.admit_mission_execution(
        MISSION_ID, snapshot_hash=str(mission["snapshot_hash"])
    )
    assert accepted["state"] == "admitted"
    assert accepted["blocker"] is None
    assert accepted["recovery_command"] is None
    before = store.state_path.read_bytes()
    assert store.admit_mission_execution(
        MISSION_ID, snapshot_hash=str(mission["snapshot_hash"])
    ) == accepted
    assert store.state_path.read_bytes() == before

    with pytest.raises(ValueError, match="Mission admission drift"):
        store.admit_mission_execution(
            MISSION_ID, snapshot_hash="sha256:" + "9" * 64
        )
    assert store.state_path.read_bytes() == before


def test_confirmed_not_admitted_is_persisted_with_recovery_control(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    state = store.load()
    state["missions"][0]["status"] = "preparing"
    state["missions"][0]["confirmed_at"] = "2026-07-13T01:00:00+00:00"
    store.save(state)
    record = store.record_mission_not_admitted(
        MISSION_ID,
        snapshot_hash=str(state["missions"][0]["snapshot_hash"]),
        blocker="verified project daemon is unavailable",
    )
    assert record["state"] == "confirmed_not_admitted"
    assert store.load()["missions"][0]["daemon_admission"] == record
    assert record["recovery_command"].endswith(
        f"--mission-id {MISSION_ID} --confirm"
    )
    assert scheduler_facts_from_store(store) is None


def test_admitted_mission_projects_real_scheduler_facts(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    state = store.load()
    mission = state["missions"][0]
    mission["status"] = "preparing"
    mission["confirmed_at"] = "2026-07-13T01:00:00+00:00"
    mission["current_step"] = 0
    mission["daemon_admission"] = {
        "state": "admitted", "snapshot_hash": mission["snapshot_hash"],
        "blocker": None, "recovery_command": f"agentdeck mission run --mission-id {MISSION_ID} --confirm",
        "updated_at": "2026-07-13T01:01:00+00:00",
    }
    state["mission_attempts"] = []
    state["mission_recovery_evidence"] = [
        {"mission_id": MISSION_ID, "attempt_id": None, "agent_id": None}
    ]
    store.save(state)
    projected = scheduler_facts_from_store(store)
    assert projected is not None
    assert projected.mission_id == MISSION_ID
    assert projected.step_id == "step_1"
    assert projected.step_state == "pending"
    assert projected.attempt_state == "none"
