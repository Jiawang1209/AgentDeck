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
)
from agentdeck.state import StateStore


MISSION_ID = "mis_aaaaaaaaaaaa"
ATTEMPT_ID = "mat_bbbbbbbbbbbb"


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
    state["missions"] = [
        {
            "mission_id": MISSION_ID,
            "status": "running",
            "snapshot_hash": "sha256:" + "f" * 64,
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
            "snapshot_hash": "sha256:" + "f" * 64,
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
            "reply": None,
            "handoff": None,
            "permission": None,
            "route": {
                "configured_transport": "acp",
                "transport_state": "ready",
                "snapshot_hash": "sha256:" + "f" * 64,
                "ownership_state": "agentdeck_owned",
            },
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


def test_startup_rejects_persisted_handoff_without_reply_record(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    _seed_missions(store)
    state = store.load()
    state["mission_recovery_evidence"][0]["handoff"] = {
        "handoff_id": "hof_" + "3" * 12,
        "reply_id": "mrp_" + "4" * 12,
        "state": "recorded",
    }
    store.save(state)
    with pytest.raises(RecoveryError, match="durable recovery evidence"):
        reconcile_startup(store, enable_scheduler=lambda: pytest.fail("enabled"))
    assert store.load().get("recovery_decisions", []) == []


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
            **deepcopy(state["mission_recovery_evidence"][0]),
            "mission_id": "mis_777777777777",
            "attempt_id": None,
            "route": {"corrupt": True},
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
