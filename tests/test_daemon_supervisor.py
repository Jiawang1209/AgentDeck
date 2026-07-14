from __future__ import annotations

import asyncio
import json
from copy import deepcopy

import pytest

from agentdeck.conversation.transports import WorkerRoute
from agentdeck.daemon.supervisor import (
    ArtifactEvidence,
    SubmittedReceipt,
    TransportResult,
    WorkerAdmissionRejected,
    WorkerAttemptError,
    WorkerAttemptSupervisor,
    supervisor_gate,
)
from agentdeck.workflow import build_canonical_handoff, build_compact_handoff
from agentdeck.state import StateStore


def _attempt(transport: str = "acp", **overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "attempt_id": "mat_0123456789ab",
        "mission_id": "mis_0123456789ab",
        "step_id": "step_1",
        "agent_id": "planner",
        "configured_transport": transport,
        "dispatch_key": "dsp_" + "1" * 32,
        "admission_claim_id": None,
        "snapshot_hash": "sha256:" + "2" * 64,
        "state": "prepared",
        "created_at": "2026-07-13T00:00:00+00:00",
        "updated_at": "2026-07-13T00:00:00+00:00",
        "receipt_summary": None,
        "blocker": None,
        "terminal_reason": None,
    }
    record.update(overrides)
    if (
        "admission_claim_id" not in overrides
        and record["state"] in {"admitting", "submitted", "ambiguous"}
    ):
        record["admission_claim_id"] = "adm_" + "1" * 12
    return record


def _route(transport: str = "acp", **overrides: object) -> WorkerRoute:
    values = {
        "agent_id": "planner",
        "configured_transport": transport,
        "effective_transport": transport,
        "ready": True,
        "blocker": None,
        "fallback": {},
        "live_mirror": {},
        "mirror_controls": (),
        "ownership": "agentdeck_owned",
        "automation_allowed": True,
        "prompt_blocker": None,
    }
    values.update(overrides)
    return WorkerRoute(**values)


def _submit_acp(store: StateStore, claimed: dict[str, object]) -> dict[str, object]:
    return store.record_mission_attempt_submitted(
        attempt_id="mat_0123456789ab",
        dispatch_key="dsp_" + "1" * 32,
        expected_claim_id=str(claimed["admission_claim_id"]),
        receipt_summary="session-created",
    )


def _reply(token: str | None = None) -> dict[str, object]:
    return {
        "handoff_token": token or "dsp_" + "1" * 32,
        "status": "completed",
        "summary": "implementation finished",
        "verification": "pytest passed",
        "risks": "none",
        "next_steps": "review",
        "private_reasoning": "must never persist",
    }


def _result(
    *,
    validated: bool = True,
    stop_reason: str = "end_turn",
    reply: dict[str, object] | None = None,
) -> TransportResult:
    return TransportResult(
        stop_reason=stop_reason,
        validated=validated,
        reply=reply or _reply(),
        artifacts=(
            ArtifactEvidence(
                path="reports/result.md",
                content_hash="sha256:" + "a" * 64,
            ),
        ),
        trace_ids=("trn_worker", "rep_worker"),
    )


def _receipt() -> SubmittedReceipt:
    return SubmittedReceipt(
        receipt_id="rcp_worker",
        dispatch_key="dsp_" + "1" * 32,
        summary="accepted",
    )


def _canonical_handoff() -> dict[str, object]:
    return build_canonical_handoff(
        reply=_reply(),
        artifacts=[
            {"path": "reports/result.md", "content_hash": "sha256:" + "a" * 64}
        ],
        trace_ids=["trn_worker", "rep_worker"],
        expected_handoff_token="dsp_" + "1" * 32,
    ).compact()


def _claim_store_attempt(store: StateStore, candidate: dict[str, object]):
    return store.claim_mission_attempt_admission(
        attempt_id=str(candidate["attempt_id"]),
        dispatch_key=str(candidate["dispatch_key"]),
    )


def _release_store_attempt(store: StateStore, candidate: dict[str, object]):
    return store.release_mission_attempt_admission(
        attempt_id=str(candidate["attempt_id"]),
        dispatch_key=str(candidate["dispatch_key"]),
        expected_claim_id=str(candidate["admission_claim_id"]),
    )


def _completion(result: TransportResult | Exception):
    async def complete(_attempt, _receipt) -> TransportResult:
        if isinstance(result, Exception):
            raise result
        return result
    return complete


class _Authority:
    def __init__(self) -> None:
        self.current = _attempt()
        self.authorize_calls = 0
        self.persist_calls = 0
        self.claim_calls = 0

    def authorize(self, candidate: dict[str, object]) -> dict[str, object]:
        self.authorize_calls += 1
        assert candidate["attempt_id"] == self.current["attempt_id"]
        return deepcopy(self.current)

    def claim(self, candidate: dict[str, object]) -> dict[str, object]:
        assert candidate == self.current
        if self.current["state"] != "prepared":
            raise ValueError("already claimed")
        self.claim_calls += 1
        self.current.update(
            {
                "state": "admitting",
                "updated_at": "2026-07-13T00:00:01+00:00",
                "admission_claim_id": f"adm_{self.claim_calls:012x}",
            }
        )
        return deepcopy(self.current)

    def release(self, candidate: dict[str, object]) -> dict[str, object]:
        assert candidate["attempt_id"] == self.current["attempt_id"]
        if self.current["state"] != "admitting":
            raise ValueError("not claimed")
        self.current.update(
            {
                "state": "prepared",
                "updated_at": self.current["created_at"],
                "admission_claim_id": None,
            }
        )
        return deepcopy(self.current)

    async def persist(
        self, candidate: dict[str, object], receipt: SubmittedReceipt
    ) -> None:
        self.persist_calls += 1
        assert receipt.dispatch_key == candidate["dispatch_key"]
        self.current.update(
            {
                "state": "submitted",
                "updated_at": "2026-07-13T00:00:01+00:00",
                "receipt_summary": receipt.summary,
            }
        )


def _supervisor(
    authority: _Authority,
    *,
    acp_admit=None,
    tmux_admit=None,
    acp_complete=None,
    tmux_complete=None,
    mark_ambiguous=None,
    mark_admission_ambiguous=None,
) -> WorkerAttemptSupervisor:
    def default_mark_admission(_candidate, _reason) -> None:
        authority.current.update(
            {
                "state": "ambiguous",
                "updated_at": "2026-07-13T00:00:02+00:00",
                "receipt_summary": "admission outcome unknown",
                "blocker": "admission_outcome_unknown",
                "terminal_reason": "admission_outcome_unknown",
            }
        )

    return WorkerAttemptSupervisor(
        authorize_attempt=authority.authorize,
        claim_admission=authority.claim,
        release_admission=authority.release,
        persist_submitted=authority.persist,
        mark_attempt_ambiguous=mark_ambiguous or (lambda *_: None),
        mark_admission_ambiguous=mark_admission_ambiguous
        or default_mark_admission,
        acp_admit=acp_admit or (lambda _: _receipt()),
        tmux_admit=tmux_admit or (lambda _: _receipt()),
        acp_complete=acp_complete or _completion(_result()),
        tmux_complete=tmux_complete
        or _completion(_result(stop_reason="structured_reply")),
    )


def test_complete_callback_is_not_called_until_async_receipt_persistence_finishes() -> None:
    order: list[str] = []
    authority = _Authority()

    async def persist(candidate, receipt) -> None:
        order.append("persist-start")
        await asyncio.sleep(0)
        order.append("persist-finish")
        await authority.persist(candidate, receipt)

    async def complete(_attempt, _receipt) -> TransportResult:
        order.append("complete")
        return _result()

    supervisor = WorkerAttemptSupervisor(
        authorize_attempt=authority.authorize,
        claim_admission=authority.claim,
        release_admission=authority.release,
        persist_submitted=persist,
        mark_attempt_ambiguous=lambda *_: None,
        mark_admission_ambiguous=lambda *_: None,
        acp_admit=lambda _: _receipt(),
        tmux_admit=lambda _: pytest.fail("tmux must not run"),
        acp_complete=complete,
        tmux_complete=lambda *_: pytest.fail("tmux must not run"),
    )

    asyncio.run(supervisor.execute(_attempt(), _route()))

    assert order == ["persist-start", "persist-finish", "complete"]


def test_admission_record_has_no_completion_injection_surface() -> None:
    async def scenario() -> None:
        hot = asyncio.create_task(asyncio.sleep(0.01, result=_result()))
        receipt = _receipt()
        assert not hasattr(receipt, "completion")
        assert not hasattr(receipt, "completion_factory")
        assert hot.cancelled() is False
        hot.cancel()
        await asyncio.gather(hot, return_exceptions=True)

    asyncio.run(scenario())


def test_supervisor_constructor_never_inspects_or_cancels_caller_hot_tasks() -> None:
    async def scenario() -> None:
        hot = asyncio.create_task(asyncio.sleep(60))
        authority = _Authority()
        _supervisor(
            authority,
            acp_admit=lambda _: _receipt(),
            acp_complete=_completion(_result()),
        )
        assert hot.cancelled() is False
        hot.cancel()
        await asyncio.gather(hot, return_exceptions=True)

    asyncio.run(scenario())


def test_persistence_failure_never_starts_completion_and_is_sanitized() -> None:
    authority = _Authority()
    completion_started = False

    async def persist(*_) -> None:
        raise RuntimeError("secret callback detail")

    async def complete(_attempt, _receipt) -> TransportResult:
        nonlocal completion_started
        completion_started = True
        return _result()

    def mark_ambiguous(*_) -> None:
        authority.current.update(
            {
                "state": "ambiguous",
                "updated_at": "2026-07-13T00:00:01+00:00",
                "receipt_summary": "accepted",
                "blocker": "receipt_persistence_unknown",
                "terminal_reason": "receipt_persistence_unknown",
            }
        )

    supervisor = WorkerAttemptSupervisor(
        authorize_attempt=authority.authorize,
        claim_admission=authority.claim,
        release_admission=authority.release,
        persist_submitted=persist,
        mark_attempt_ambiguous=mark_ambiguous,
        mark_admission_ambiguous=lambda *_: None,
        acp_admit=lambda _: _receipt(),
        tmux_admit=lambda _: pytest.fail("tmux must not run"),
        acp_complete=complete,
        tmux_complete=lambda *_: pytest.fail("tmux must not run"),
    )

    with pytest.raises(WorkerAttemptError) as caught:
        asyncio.run(supervisor.execute(_attempt(), _route()))

    assert str(caught.value) == "Worker submitted receipt persistence failed"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert completion_started is False


@pytest.mark.parametrize("failure_path", ["authority", "admit", "completion"])
def test_external_callback_failures_have_no_sensitive_exception_context(
    failure_path: str,
) -> None:
    authority = _Authority()

    def authorize(candidate):
        if failure_path == "authority":
            raise RuntimeError("secret authority")
        return authority.authorize(candidate)

    def admit(_):
        if failure_path == "admit":
            raise RuntimeError("secret admit")
        return _receipt()

    async def complete(_attempt, _receipt):
        if failure_path == "completion":
            raise RuntimeError("secret completion")
        return _result()

    supervisor = WorkerAttemptSupervisor(
        authorize_attempt=authorize,
        claim_admission=authority.claim,
        release_admission=authority.release,
        persist_submitted=authority.persist,
        mark_attempt_ambiguous=lambda *_: None,
        mark_admission_ambiguous=lambda *_: None,
        acp_admit=admit,
        tmux_admit=lambda _: pytest.fail("tmux must not run"),
        acp_complete=complete,
        tmux_complete=lambda *_: pytest.fail("tmux must not run"),
    )

    with pytest.raises(WorkerAttemptError) as caught:
        asyncio.run(supervisor.execute(_attempt(), _route()))

    assert "secret" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_callback_mutation_cannot_change_frozen_attempt_transport_authority() -> None:
    authority = _Authority()
    calls: list[str] = []

    def mutate_and_forge(candidate: dict[str, object]) -> dict[str, object]:
        candidate["configured_transport"] = "tmux"
        return candidate

    supervisor = WorkerAttemptSupervisor(
        authorize_attempt=mutate_and_forge,
        claim_admission=authority.claim,
        release_admission=authority.release,
        persist_submitted=authority.persist,
        mark_attempt_ambiguous=lambda *_: None,
        mark_admission_ambiguous=lambda *_: None,
        acp_admit=lambda _: calls.append("acp") or _receipt(),
        tmux_admit=lambda _: calls.append("tmux") or _receipt(),
        acp_complete=_completion(_result()),
        tmux_complete=_completion(_result(stop_reason="structured_reply")),
    )

    with pytest.raises(WorkerAttemptError, match="Worker attempt authority drift"):
        asyncio.run(supervisor.execute(_attempt(), _route()))

    assert calls == []


def test_executor_mutation_is_isolated_from_canonical_authority() -> None:
    authority = _Authority()
    seen: list[str] = []

    def mutate(candidate: dict[str, object]) -> SubmittedReceipt:
        candidate["configured_transport"] = "tmux"
        candidate["dispatch_key"] = "dsp_" + "9" * 32
        seen.append(str(candidate["configured_transport"]))
        return _receipt()

    outcome = asyncio.run(
        _supervisor(authority, acp_admit=mutate).execute(_attempt(), _route())
    )

    assert outcome.status == "completed"
    assert seen == ["tmux"]
    assert authority.current["configured_transport"] == "acp"
    assert authority.current["dispatch_key"] == "dsp_" + "1" * 32


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mission_id", "mis_wrong"),
        ("step_id", "step_0"),
        ("dispatch_key", "dsp_wrong"),
        ("snapshot_hash", "sha256:wrong"),
        ("state", "submitted"),
    ],
)
def test_full_task7_attempt_authority_is_required(field: str, value: object) -> None:
    authority = _Authority()
    calls: list[str] = []
    supervisor = _supervisor(
        authority,
        acp_admit=lambda _: calls.append("acp") or _receipt(),
    )

    with pytest.raises((ValueError, WorkerAttemptError)):
        asyncio.run(supervisor.execute(_attempt(**{field: value}), _route()))

    assert calls == []


def test_authority_callback_must_return_exact_current_prepared_attempt() -> None:
    authority = _Authority()
    authority.current["snapshot_hash"] = "sha256:" + "f" * 64
    calls: list[str] = []
    supervisor = _supervisor(
        authority,
        acp_admit=lambda _: calls.append("acp") or _receipt(),
    )

    with pytest.raises(WorkerAttemptError, match="Worker attempt authority drift"):
        asyncio.run(supervisor.execute(_attempt(), _route()))

    assert calls == []


def test_same_attempt_cannot_dispatch_twice_after_submitted_receipt() -> None:
    authority = _Authority()
    calls: list[str] = []
    supervisor = _supervisor(
        authority,
        acp_admit=lambda _: calls.append("acp") or _receipt(),
    )

    asyncio.run(supervisor.execute(_attempt(), _route()))
    with pytest.raises(WorkerAttemptError, match="Worker attempt is already submitted"):
        asyncio.run(supervisor.execute(_attempt(), _route()))

    assert calls == ["acp"]
    assert authority.persist_calls == 1


def test_typed_pre_admission_rejection_releases_claim_for_prepared_retry() -> None:
    authority = _Authority()
    calls: list[str] = []

    def admit(_):
        calls.append("acp")
        if len(calls) == 1:
            raise WorkerAdmissionRejected("pre-admission rejection")
        return _receipt()

    supervisor = _supervisor(authority, acp_admit=admit)

    with pytest.raises(WorkerAttemptError, match="rejected admission"):
        asyncio.run(supervisor.execute(_attempt(), _route()))
    result = asyncio.run(supervisor.execute(_attempt(), _route()))

    assert result.status == "completed"
    assert calls == ["acp", "acp"]
    assert supervisor._halted is False
    assert not hasattr(supervisor, "_claimed_dispatch_keys")


def test_unknown_persistence_marks_durable_ambiguity_and_blocks_new_supervisor() -> None:
    authority = _Authority()
    calls: list[str] = []

    async def unknown(*_) -> None:
        raise RuntimeError("unknown")

    def mark_ambiguous(_attempt, receipt, _reason) -> None:
        authority.current.update(
            {
                "state": "ambiguous",
                "updated_at": "2026-07-13T00:00:01+00:00",
                "receipt_summary": receipt.summary,
                "blocker": "receipt_persistence_unknown",
                "terminal_reason": "receipt_persistence_unknown",
            }
        )

    supervisor = WorkerAttemptSupervisor(
        authorize_attempt=authority.authorize,
        claim_admission=authority.claim,
        release_admission=authority.release,
        persist_submitted=unknown,
        mark_attempt_ambiguous=mark_ambiguous,
        mark_admission_ambiguous=lambda *_: None,
        acp_admit=lambda _: calls.append("acp") or _receipt(),
        tmux_admit=lambda _: pytest.fail("tmux must not run"),
        acp_complete=_completion(_result()),
        tmux_complete=lambda *_: pytest.fail("tmux must not run"),
    )

    with pytest.raises(WorkerAttemptError, match="persistence failed"):
        asyncio.run(supervisor.execute(_attempt(), _route()))
    assert calls == ["acp"]
    assert supervisor._halted is False
    assert not hasattr(supervisor, "_claimed_dispatch_keys")
    restarted = _supervisor(
        authority,
        acp_admit=lambda _: calls.append("unexpected") or _receipt(),
    )
    with pytest.raises(WorkerAttemptError, match="must be prepared"):
        asyncio.run(restarted.execute(authority.current, _route()))
    assert calls == ["acp"]


def test_persistence_unknown_survives_supervisor_restart_in_state_store(tmp_path) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["mission_attempts"] = [_attempt()]
    store.save(state)
    calls: list[str] = []

    def authorize(candidate):
        return store.mission_attempt_by_id(str(candidate["attempt_id"]))

    def mark(candidate, receipt, reason):
        return store.mark_mission_attempt_ambiguous(
            attempt_id=str(candidate["attempt_id"]),
            dispatch_key=str(candidate["dispatch_key"]),
            expected_claim_id=str(candidate["admission_claim_id"]),
            observed_dispatch_key=receipt.dispatch_key,
            receipt_summary=receipt.summary,
            reason=reason,
        )

    first = WorkerAttemptSupervisor(
        authorize_attempt=authorize,
        claim_admission=lambda candidate: _claim_store_attempt(store, candidate),
        release_admission=lambda candidate: _release_store_attempt(store, candidate),
        persist_submitted=lambda *_: (_ for _ in ()).throw(RuntimeError("unknown")),
        mark_attempt_ambiguous=mark,
        mark_admission_ambiguous=lambda *_: None,
        acp_admit=lambda _: calls.append("acp") or _receipt(),
        tmux_admit=lambda _: pytest.fail("tmux must not run"),
        acp_complete=_completion(_result()),
        tmux_complete=lambda *_: pytest.fail("tmux must not run"),
    )
    with pytest.raises(WorkerAttemptError, match="persistence failed"):
        asyncio.run(first.execute(_attempt(), _route()))

    persisted = store.mission_attempt_by_id("mat_0123456789ab")
    assert persisted["state"] == "ambiguous"
    assert persisted["terminal_reason"] == "receipt_persistence_unknown"
    assert store.load()["protocol_event_outbox"][-1]["event_type"] == "mission_attempt_ambiguous"

    restarted = WorkerAttemptSupervisor(
        authorize_attempt=authorize,
        claim_admission=lambda *_: pytest.fail("must not claim"),
        release_admission=lambda *_: pytest.fail("must not release"),
        persist_submitted=lambda *_: pytest.fail("must not persist"),
        mark_attempt_ambiguous=mark,
        mark_admission_ambiguous=lambda *_: pytest.fail("must not mark"),
        acp_admit=lambda _: calls.append("unexpected") or _receipt(),
        tmux_admit=lambda _: pytest.fail("tmux must not run"),
        acp_complete=_completion(_result()),
        tmux_complete=lambda *_: pytest.fail("tmux must not run"),
    )
    with pytest.raises(WorkerAttemptError, match="must be prepared"):
        asyncio.run(restarted.execute(persisted, _route()))
    assert calls == ["acp"]


def test_receipt_lineage_drift_uses_canonical_key_and_survives_restart(
    tmp_path,
) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["mission_attempts"] = [_attempt()]
    store.save(state)
    calls: list[str] = []
    expected_dispatch_key = "dsp_" + "1" * 32
    observed_dispatch_key = "dsp_" + "9" * 32
    bad_receipt = SubmittedReceipt(
        receipt_id="rcp_bad_lineage",
        dispatch_key=observed_dispatch_key,
        summary="accepted",
    )

    def authorize(candidate):
        return store.mission_attempt_by_id(str(candidate["attempt_id"]))

    def mark(candidate, receipt, reason):
        return store.mark_mission_attempt_ambiguous(
            attempt_id=str(candidate["attempt_id"]),
            dispatch_key=str(candidate["dispatch_key"]),
            expected_claim_id=str(candidate["admission_claim_id"]),
            observed_dispatch_key=receipt.dispatch_key,
            receipt_summary=receipt.summary,
            reason=reason,
        )

    first = WorkerAttemptSupervisor(
        authorize_attempt=authorize,
        claim_admission=lambda candidate: _claim_store_attempt(store, candidate),
        release_admission=lambda candidate: _release_store_attempt(store, candidate),
        persist_submitted=lambda *_: pytest.fail("must not persist mismatched receipt"),
        mark_attempt_ambiguous=mark,
        mark_admission_ambiguous=lambda *_: pytest.fail("must not mark"),
        acp_admit=lambda _: calls.append("acp") or bad_receipt,
        tmux_admit=lambda _: pytest.fail("tmux must not run"),
        acp_complete=lambda *_: pytest.fail("must not complete"),
        tmux_complete=lambda *_: pytest.fail("tmux must not run"),
    )
    with pytest.raises(WorkerAttemptError, match="receipt lineage drift"):
        asyncio.run(first.execute(_attempt(), _route()))

    persisted = store.mission_attempt_by_id("mat_0123456789ab")
    assert persisted["state"] == "ambiguous"
    event = store.load()["protocol_event_outbox"][-1]
    assert event["event_type"] == "mission_attempt_ambiguous"
    assert event["payload"]["dispatch_key"] == expected_dispatch_key
    assert event["payload"]["admission_claim_id"] == persisted[
        "admission_claim_id"
    ]
    assert event["payload"]["expected_dispatch_key"] == expected_dispatch_key
    assert event["payload"]["observed_dispatch_key"] == observed_dispatch_key

    restarted = WorkerAttemptSupervisor(
        authorize_attempt=authorize,
        claim_admission=lambda *_: pytest.fail("must not claim"),
        release_admission=lambda *_: pytest.fail("must not release"),
        persist_submitted=lambda *_: pytest.fail("must not persist"),
        mark_attempt_ambiguous=mark,
        mark_admission_ambiguous=lambda *_: pytest.fail("must not mark"),
        acp_admit=lambda _: calls.append("unexpected") or bad_receipt,
        tmux_admit=lambda _: pytest.fail("tmux must not run"),
        acp_complete=lambda *_: pytest.fail("must not complete"),
        tmux_complete=lambda *_: pytest.fail("tmux must not run"),
    )
    with pytest.raises(WorkerAttemptError, match="must be prepared"):
        asyncio.run(restarted.execute(persisted, _route()))
    assert calls == ["acp"]


def test_ambiguity_failure_halts_supervisor_without_unbounded_claims() -> None:
    authority = _Authority()
    bad_receipt = SubmittedReceipt(
        receipt_id="rcp_bad_lineage",
        dispatch_key="dsp_" + "9" * 32,
        summary="accepted",
    )

    supervisor = WorkerAttemptSupervisor(
        authorize_attempt=authority.authorize,
        claim_admission=authority.claim,
        release_admission=authority.release,
        persist_submitted=lambda *_: pytest.fail("must not persist mismatched receipt"),
        mark_attempt_ambiguous=lambda *_: (_ for _ in ()).throw(
            RuntimeError("sensitive ambiguity failure")
        ),
        mark_admission_ambiguous=lambda *_: pytest.fail("must not mark admission"),
        acp_admit=lambda _: bad_receipt,
        tmux_admit=lambda _: pytest.fail("tmux must not run"),
        acp_complete=lambda *_: pytest.fail("must not complete"),
        tmux_complete=lambda *_: pytest.fail("tmux must not run"),
    )

    with pytest.raises(WorkerAttemptError) as error:
        asyncio.run(supervisor.execute(_attempt(), _route()))
    assert str(error.value) == "Worker ambiguity persistence failed"
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert supervisor._halted is True

    with pytest.raises(WorkerAttemptError) as halted:
        asyncio.run(
            supervisor.execute(
                _attempt(
                    attempt_id="mat_abcdefabcdef",
                    mission_id="mis_abcdefabcdef",
                    dispatch_key="dsp_" + "8" * 32,
                ),
                _route(),
            )
        )
    assert str(halted.value) == "Worker supervisor is halted"
    assert not hasattr(supervisor, "_claimed_dispatch_keys")


def test_state_store_submitted_receipt_precedes_complete_callback(tmp_path) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["mission_attempts"] = [_attempt()]
    store.save(state)
    observed_states: list[str] = []

    def authorize(candidate):
        return store.mission_attempt_by_id(str(candidate["attempt_id"]))

    def persist(candidate, receipt):
        return store.record_mission_attempt_submitted(
            attempt_id=str(candidate["attempt_id"]),
            dispatch_key=str(candidate["dispatch_key"]),
            expected_claim_id=str(candidate["admission_claim_id"]),
            receipt_summary=receipt.summary,
        )

    async def complete(candidate, _receipt):
        assert candidate == store.mission_attempt_by_id(str(candidate["attempt_id"]))
        assert candidate["receipt_summary"] == "accepted"
        observed_states.append(
            str(candidate["state"])
        )
        return _result()

    supervisor = WorkerAttemptSupervisor(
        authorize_attempt=authorize,
        claim_admission=lambda candidate: _claim_store_attempt(store, candidate),
        release_admission=lambda candidate: _release_store_attempt(store, candidate),
        persist_submitted=persist,
        mark_attempt_ambiguous=lambda *_: pytest.fail("must not mark ambiguous"),
        mark_admission_ambiguous=lambda *_: pytest.fail("must not mark admission"),
        acp_admit=lambda _: _receipt(),
        tmux_admit=lambda _: pytest.fail("tmux must not run"),
        acp_complete=complete,
        tmux_complete=lambda *_: pytest.fail("tmux must not run"),
    )
    result = asyncio.run(supervisor.execute(_attempt(), _route()))

    assert result.status == "completed"
    assert observed_states == ["submitted"]
    assert store.load()["protocol_event_outbox"][-1]["event_type"] == "mission_attempt_submitted"


def test_two_supervisors_share_one_atomic_state_store_admission_claim(tmp_path) -> None:
    async def scenario() -> None:
        store = StateStore(tmp_path)
        state = store.load()
        state["mission_attempts"] = [_attempt()]
        store.save(state)
        authorize_count = 0
        both_authorized = asyncio.Event()
        release_admit = asyncio.Event()
        admit_calls: list[str] = []
        complete_calls: list[str] = []

        async def authorize(candidate):
            nonlocal authorize_count
            current = store.mission_attempt_by_id(str(candidate["attempt_id"]))
            authorize_count += 1
            if authorize_count == 2:
                both_authorized.set()
            await both_authorized.wait()
            return current

        def claim(candidate):
            return store.claim_mission_attempt_admission(
                attempt_id=str(candidate["attempt_id"]),
                dispatch_key=str(candidate["dispatch_key"]),
            )

        def release(candidate):
            return store.release_mission_attempt_admission(
                attempt_id=str(candidate["attempt_id"]),
                dispatch_key=str(candidate["dispatch_key"]),
                expected_claim_id=str(candidate["admission_claim_id"]),
            )

        def persist(candidate, receipt):
            return store.record_mission_attempt_submitted(
                attempt_id=str(candidate["attempt_id"]),
                dispatch_key=str(candidate["dispatch_key"]),
                expected_claim_id=str(candidate["admission_claim_id"]),
                receipt_summary=receipt.summary,
            )

        async def admit(_candidate):
            assert _candidate == store.mission_attempt_by_id(
                str(_candidate["attempt_id"])
            )
            assert _candidate["state"] == "admitting"
            assert str(_candidate["admission_claim_id"]).startswith("adm_")
            admit_calls.append("acp")
            await release_admit.wait()
            return _receipt()

        async def complete(candidate, _receipt):
            complete_calls.append(str(candidate["state"]))
            return _result()

        def build() -> WorkerAttemptSupervisor:
            return WorkerAttemptSupervisor(
                authorize_attempt=authorize,
                claim_admission=claim,
                release_admission=release,
                persist_submitted=persist,
                mark_attempt_ambiguous=lambda *_: pytest.fail("must not mark"),
                mark_admission_ambiguous=lambda *_: pytest.fail("must not mark"),
                acp_admit=admit,
                tmux_admit=lambda _: pytest.fail("tmux must not run"),
                acp_complete=complete,
                tmux_complete=lambda *_: pytest.fail("tmux must not run"),
            )

        first_task = asyncio.create_task(build().execute(_attempt(), _route()))
        second_task = asyncio.create_task(build().execute(_attempt(), _route()))
        await both_authorized.wait()
        await asyncio.sleep(0)
        release_admit.set()
        results = await asyncio.gather(first_task, second_task, return_exceptions=True)

        assert admit_calls == ["acp"]
        assert complete_calls == ["submitted"]
        assert sum(isinstance(item, WorkerAttemptError) for item in results) == 1
        assert sum(not isinstance(item, BaseException) for item in results) == 1
        assert store.mission_attempt_by_id("mat_0123456789ab")["state"] == "submitted"

    asyncio.run(scenario())


def test_admission_claim_must_be_reread_as_durable_before_external_call() -> None:
    authority = _Authority()
    external_calls: list[str] = []

    def forged_claim(_candidate):
        return _attempt(
            state="admitting",
            updated_at="2026-07-13T00:00:01+00:00",
        )

    supervisor = WorkerAttemptSupervisor(
        authorize_attempt=authority.authorize,
        claim_admission=forged_claim,
        release_admission=authority.release,
        persist_submitted=authority.persist,
        mark_attempt_ambiguous=lambda *_: pytest.fail("must not mark"),
        mark_admission_ambiguous=lambda *_: pytest.fail("must not mark"),
        acp_admit=lambda _: external_calls.append("acp") or _receipt(),
        tmux_admit=lambda _: pytest.fail("tmux must not run"),
        acp_complete=lambda *_: pytest.fail("must not complete"),
        tmux_complete=lambda *_: pytest.fail("tmux must not run"),
    )

    with pytest.raises(WorkerAttemptError, match="admission claim failed"):
        asyncio.run(supervisor.execute(_attempt(), _route()))
    assert external_calls == []
    assert authority.current["state"] == "prepared"


def test_definite_pre_admission_failure_atomically_releases_durable_claim(
    tmp_path,
) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["mission_attempts"] = [_attempt()]
    store.save(state)

    supervisor = WorkerAttemptSupervisor(
        authorize_attempt=lambda candidate: store.mission_attempt_by_id(
            str(candidate["attempt_id"])
        ),
        claim_admission=lambda candidate: store.claim_mission_attempt_admission(
            attempt_id=str(candidate["attempt_id"]),
            dispatch_key=str(candidate["dispatch_key"]),
        ),
        release_admission=lambda candidate: store.release_mission_attempt_admission(
            attempt_id=str(candidate["attempt_id"]),
            dispatch_key=str(candidate["dispatch_key"]),
            expected_claim_id=str(candidate["admission_claim_id"]),
        ),
        persist_submitted=lambda *_: pytest.fail("must not persist"),
        mark_attempt_ambiguous=lambda *_: pytest.fail("must not mark"),
        mark_admission_ambiguous=lambda *_: pytest.fail("must not mark"),
        acp_admit=lambda _: (_ for _ in ()).throw(
            WorkerAdmissionRejected("definite rejection")
        ),
        tmux_admit=lambda _: pytest.fail("tmux must not run"),
        acp_complete=lambda *_: pytest.fail("must not complete"),
        tmux_complete=lambda *_: pytest.fail("tmux must not run"),
    )

    with pytest.raises(WorkerAttemptError, match="rejected admission"):
        asyncio.run(supervisor.execute(_attempt(), _route()))
    assert store.mission_attempt_by_id("mat_0123456789ab")["state"] == "prepared"
    assert [
        item["event_type"] for item in store.load()["protocol_event_outbox"][-2:]
    ] == ["mission_attempt_admission_claimed", "mission_attempt_admission_released"]


def test_invalid_admission_result_becomes_durable_ambiguity(tmp_path) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["mission_attempts"] = [_attempt()]
    store.save(state)

    supervisor = WorkerAttemptSupervisor(
        authorize_attempt=lambda candidate: store.mission_attempt_by_id(
            str(candidate["attempt_id"])
        ),
        claim_admission=lambda candidate: _claim_store_attempt(store, candidate),
        release_admission=lambda candidate: _release_store_attempt(store, candidate),
        persist_submitted=lambda *_: pytest.fail("must not persist"),
        mark_attempt_ambiguous=lambda *_: pytest.fail("must not mark"),
        mark_admission_ambiguous=lambda candidate, reason: (
            store.mark_mission_attempt_admission_ambiguous(
                attempt_id=str(candidate["attempt_id"]),
                dispatch_key=str(candidate["dispatch_key"]),
                expected_claim_id=str(candidate["admission_claim_id"]),
                reason=reason,
            )
        ),
        acp_admit=lambda _: object(),
        tmux_admit=lambda _: pytest.fail("tmux must not run"),
        acp_complete=lambda *_: pytest.fail("must not complete"),
        tmux_complete=lambda *_: pytest.fail("tmux must not run"),
    )

    with pytest.raises(WorkerAttemptError, match="admission outcome is unknown"):
        asyncio.run(supervisor.execute(_attempt(), _route()))
    assert supervisor._halted is False
    assert store.mission_attempt_by_id("mat_0123456789ab")["state"] == "ambiguous"
    assert store.load()["protocol_event_outbox"][-1]["event_type"] == (
        "mission_attempt_ambiguous"
    )


def test_timeout_after_external_effect_becomes_durable_ambiguity_and_blocks_restart(
    tmp_path,
) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["mission_attempts"] = [_attempt()]
    store.save(state)
    external_calls: list[str] = []

    def authorize(candidate):
        return store.mission_attempt_by_id(str(candidate["attempt_id"]))

    def mark_admission_unknown(candidate, reason):
        return store.mark_mission_attempt_admission_ambiguous(
            attempt_id=str(candidate["attempt_id"]),
            dispatch_key=str(candidate["dispatch_key"]),
            expected_claim_id=str(candidate["admission_claim_id"]),
            reason=reason,
        )

    def timeout_after_effect(_candidate):
        external_calls.append("acp")
        raise TimeoutError("secret timeout after external effect")

    first = WorkerAttemptSupervisor(
        authorize_attempt=authorize,
        claim_admission=lambda candidate: _claim_store_attempt(store, candidate),
        release_admission=lambda candidate: _release_store_attempt(store, candidate),
        persist_submitted=lambda *_: pytest.fail("must not persist"),
        mark_attempt_ambiguous=lambda *_: pytest.fail("receipt path must not run"),
        mark_admission_ambiguous=mark_admission_unknown,
        acp_admit=timeout_after_effect,
        tmux_admit=lambda _: pytest.fail("tmux must not run"),
        acp_complete=lambda *_: pytest.fail("must not complete"),
        tmux_complete=lambda *_: pytest.fail("tmux must not run"),
    )
    with pytest.raises(WorkerAttemptError) as error:
        asyncio.run(first.execute(_attempt(), _route()))
    assert str(error.value) == "ACP Worker admission outcome is unknown"
    assert error.value.__cause__ is None
    assert error.value.__context__ is None

    persisted = store.mission_attempt_by_id("mat_0123456789ab")
    assert persisted["state"] == "ambiguous"
    assert persisted["terminal_reason"] == "admission_outcome_unknown"
    assert str(persisted["admission_claim_id"]).startswith("adm_")
    event = store.load()["protocol_event_outbox"][-1]
    assert event["event_type"] == "mission_attempt_ambiguous"
    assert event["payload"]["admission_claim_id"] == persisted[
        "admission_claim_id"
    ]
    assert event["payload"]["observed_dispatch_key"] is None
    restarted = WorkerAttemptSupervisor(
        authorize_attempt=authorize,
        claim_admission=lambda *_: pytest.fail("must not claim"),
        release_admission=lambda *_: pytest.fail("must not release"),
        persist_submitted=lambda *_: pytest.fail("must not persist"),
        mark_attempt_ambiguous=lambda *_: pytest.fail("must not mark"),
        mark_admission_ambiguous=lambda *_: pytest.fail("must not mark"),
        acp_admit=lambda _: external_calls.append("unexpected") or _receipt(),
        tmux_admit=lambda _: pytest.fail("tmux must not run"),
        acp_complete=lambda *_: pytest.fail("must not complete"),
        tmux_complete=lambda *_: pytest.fail("tmux must not run"),
    )
    with pytest.raises(WorkerAttemptError, match="must be prepared"):
        asyncio.run(restarted.execute(persisted, _route()))
    assert external_calls == ["acp"]


def test_typed_admission_rejection_releases_claim_and_allows_retry(tmp_path) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["mission_attempts"] = [_attempt()]
    store.save(state)
    calls: list[str] = []

    def authorize(candidate):
        return store.mission_attempt_by_id(str(candidate["attempt_id"]))

    def admit(_candidate):
        calls.append("acp")
        if len(calls) == 1:
            raise WorkerAdmissionRejected("provider rejected before effect")
        return _receipt()

    def persist(candidate, receipt):
        return store.record_mission_attempt_submitted(
            attempt_id=str(candidate["attempt_id"]),
            dispatch_key=str(candidate["dispatch_key"]),
            expected_claim_id=str(candidate["admission_claim_id"]),
            receipt_summary=receipt.summary,
        )

    supervisor = WorkerAttemptSupervisor(
        authorize_attempt=authorize,
        claim_admission=lambda candidate: _claim_store_attempt(store, candidate),
        release_admission=lambda candidate: _release_store_attempt(store, candidate),
        persist_submitted=persist,
        mark_attempt_ambiguous=lambda *_: pytest.fail("must not mark"),
        mark_admission_ambiguous=lambda *_: pytest.fail("must not mark"),
        acp_admit=admit,
        tmux_admit=lambda _: pytest.fail("tmux must not run"),
        acp_complete=_completion(_result()),
        tmux_complete=lambda *_: pytest.fail("tmux must not run"),
    )
    with pytest.raises(WorkerAttemptError) as rejected:
        asyncio.run(supervisor.execute(_attempt(), _route()))
    assert str(rejected.value) == "ACP Worker rejected admission"
    prepared = store.mission_attempt_by_id("mat_0123456789ab")
    assert prepared["state"] == "prepared"
    assert prepared["admission_claim_id"] is None

    result = asyncio.run(supervisor.execute(prepared, _route()))
    assert result.status == "completed"
    assert calls == ["acp", "acp"]


@pytest.mark.parametrize(
    "transition", ["release", "submitted", "receipt_ambiguity", "admission_ambiguity"]
)
def test_stale_admission_claim_generation_is_rejected_with_zero_write(
    tmp_path, transition: str
) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["mission_attempts"] = [_attempt()]
    store.save(state)
    claim1 = store.claim_mission_attempt_admission(
        attempt_id="mat_0123456789ab",
        dispatch_key="dsp_" + "1" * 32,
    )
    stale_claim_id = str(claim1["admission_claim_id"])
    store.release_mission_attempt_admission(
        attempt_id="mat_0123456789ab",
        dispatch_key="dsp_" + "1" * 32,
        expected_claim_id=stale_claim_id,
    )
    claim2 = store.claim_mission_attempt_admission(
        attempt_id="mat_0123456789ab",
        dispatch_key="dsp_" + "1" * 32,
    )
    assert claim2["admission_claim_id"] != stale_claim_id
    before = store.load()

    with pytest.raises(ValueError, match="admission claim drift"):
        if transition == "release":
            store.release_mission_attempt_admission(
                attempt_id="mat_0123456789ab",
                dispatch_key="dsp_" + "1" * 32,
                expected_claim_id=stale_claim_id,
            )
        elif transition == "submitted":
            store.record_mission_attempt_submitted(
                attempt_id="mat_0123456789ab",
                dispatch_key="dsp_" + "1" * 32,
                expected_claim_id=stale_claim_id,
                receipt_summary="accepted",
            )
        elif transition == "receipt_ambiguity":
            store.mark_mission_attempt_ambiguous(
                attempt_id="mat_0123456789ab",
                dispatch_key="dsp_" + "1" * 32,
                expected_claim_id=stale_claim_id,
                observed_dispatch_key="dsp_" + "1" * 32,
                receipt_summary="accepted",
                reason="receipt_persistence_unknown",
            )
        else:
            store.mark_mission_attempt_admission_ambiguous(
                attempt_id="mat_0123456789ab",
                dispatch_key="dsp_" + "1" * 32,
                expected_claim_id=stale_claim_id,
                reason="admission_outcome_unknown",
            )
    assert store.load() == before


def test_released_claim_generation_cannot_be_reused_from_durable_journal(
    tmp_path, monkeypatch
) -> None:
    import agentdeck.state as state_module

    store = StateStore(tmp_path)
    state = store.load()
    state["mission_attempts"] = [_attempt()]
    store.save(state)
    claim1 = store.claim_mission_attempt_admission(
        attempt_id="mat_0123456789ab",
        dispatch_key="dsp_" + "1" * 32,
    )
    claim_id = str(claim1["admission_claim_id"])
    store.release_mission_attempt_admission(
        attempt_id="mat_0123456789ab",
        dispatch_key="dsp_" + "1" * 32,
        expected_claim_id=claim_id,
    )
    store.flush_protocol_event_outbox()
    before_state = store.load()
    before_events = store.events_path.read_bytes()
    original_new_id = state_module.new_id
    monkeypatch.setattr(
        state_module,
        "new_id",
        lambda prefix: claim_id if prefix == "adm" else original_new_id(prefix),
    )

    with pytest.raises(ValueError, match="duplicate mission admission claim identity"):
        store.claim_mission_attempt_admission(
            attempt_id="mat_0123456789ab",
            dispatch_key="dsp_" + "1" * 32,
        )
    assert store.load() == before_state
    assert store.events_path.read_bytes() == before_events


def test_claim_generation_collision_across_attempts_is_zero_write(
    tmp_path, monkeypatch
) -> None:
    import agentdeck.state as state_module

    store = StateStore(tmp_path)
    second = _attempt(
        attempt_id="mat_abcdefabcdef",
        mission_id="mis_abcdefabcdef",
        dispatch_key="dsp_" + "2" * 32,
    )
    state = store.load()
    state["mission_attempts"] = [_attempt(), second]
    store.save(state)
    first = store.claim_mission_attempt_admission(
        attempt_id="mat_0123456789ab",
        dispatch_key="dsp_" + "1" * 32,
    )
    claim_id = str(first["admission_claim_id"])
    before = store.load()
    original_new_id = state_module.new_id
    monkeypatch.setattr(
        state_module,
        "new_id",
        lambda prefix: claim_id if prefix == "adm" else original_new_id(prefix),
    )

    with pytest.raises(ValueError, match="duplicate mission admission claim identity"):
        store.claim_mission_attempt_admission(
            attempt_id="mat_abcdefabcdef",
            dispatch_key="dsp_" + "2" * 32,
        )
    assert store.load() == before


@pytest.mark.parametrize(
    "transition",
    ["claim", "release", "submitted", "receipt_ambiguity", "admission_ambiguity"],
)
def test_all_attempt_transitions_reject_duplicate_active_claim_ids(
    tmp_path, transition: str
) -> None:
    store = StateStore(tmp_path)
    first = _attempt()
    second = _attempt(
        attempt_id="mat_abcdefabcdef",
        mission_id="mis_abcdefabcdef",
        dispatch_key="dsp_" + "2" * 32,
    )
    third = _attempt(
        attempt_id="mat_fedcbafedcba",
        mission_id="mis_fedcbafedcba",
        dispatch_key="dsp_" + "3" * 32,
    )
    duplicate_claim_id = "adm_" + "a" * 12
    for item in (first, second):
        item.update(
            {
                "state": "admitting",
                "updated_at": "2026-07-13T00:00:01+00:00",
                "admission_claim_id": duplicate_claim_id,
            }
        )
    state = store.load()
    state["mission_attempts"] = [first, second, third]
    store.save(state)
    before = store.load()
    target = third if transition == "claim" else first

    with pytest.raises(ValueError, match="duplicate mission admission claim identity"):
        if transition == "claim":
            store.claim_mission_attempt_admission(
                attempt_id=str(target["attempt_id"]),
                dispatch_key=str(target["dispatch_key"]),
            )
        elif transition == "release":
            store.release_mission_attempt_admission(
                attempt_id=str(target["attempt_id"]),
                dispatch_key=str(target["dispatch_key"]),
                expected_claim_id=duplicate_claim_id,
            )
        elif transition == "submitted":
            store.record_mission_attempt_submitted(
                attempt_id=str(target["attempt_id"]),
                dispatch_key=str(target["dispatch_key"]),
                expected_claim_id=duplicate_claim_id,
                receipt_summary="accepted",
            )
        elif transition == "receipt_ambiguity":
            store.mark_mission_attempt_ambiguous(
                attempt_id=str(target["attempt_id"]),
                dispatch_key=str(target["dispatch_key"]),
                expected_claim_id=duplicate_claim_id,
                observed_dispatch_key=str(target["dispatch_key"]),
                receipt_summary="accepted",
                reason="receipt_persistence_unknown",
            )
        else:
            store.mark_mission_attempt_admission_ambiguous(
                attempt_id=str(target["attempt_id"]),
                dispatch_key=str(target["dispatch_key"]),
                expected_claim_id=duplicate_claim_id,
                reason="admission_outcome_unknown",
            )
    assert store.load() == before


@pytest.mark.parametrize(
    "corruption", ["missing_claim", "cross_lineage", "current_history_mismatch"]
)
def test_claim_lifecycle_history_corruption_is_zero_write(
    tmp_path, corruption: str
) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["mission_attempts"] = [_attempt()]
    store.save(state)
    claimed = store.claim_mission_attempt_admission(
        attempt_id="mat_0123456789ab",
        dispatch_key="dsp_" + "1" * 32,
    )
    _submit_acp(store, claimed)
    claim_id = str(claimed["admission_claim_id"])
    state = store.load()
    if corruption == "missing_claim":
        del state["protocol_event_outbox"][-1]["payload"]["admission_claim_id"]
    elif corruption == "cross_lineage":
        state["protocol_event_outbox"].append(
            {
                "event_id": "evt_abcdefabcdef",
                "event_type": "mission_attempt_admission_released",
                "created_at": "2026-07-13T00:00:02+00:00",
                "payload": {
                    "attempt_id": "mat_abcdefabcdef",
                    "mission_id": "mis_abcdefabcdef",
                    "step_id": "step_1",
                    "dispatch_key": "dsp_" + "2" * 32,
                    "admission_claim_id": claim_id,
                },
            }
        )
    else:
        state["mission_attempts"][0]["admission_claim_id"] = "adm_" + "b" * 12
    store.save(state)
    before = store.load()

    with pytest.raises(ValueError, match="admission claim history"):
        store.release_mission_attempt_admission(
            attempt_id="mat_0123456789ab",
            dispatch_key="dsp_" + "1" * 32,
            expected_claim_id=str(before["mission_attempts"][0]["admission_claim_id"]),
        )
    assert store.load() == before


def test_claim_lifecycle_allows_claimed_submitted_ambiguous_sequence(tmp_path) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["mission_attempts"] = [_attempt()]
    store.save(state)
    claimed = store.claim_mission_attempt_admission(
        attempt_id="mat_0123456789ab",
        dispatch_key="dsp_" + "1" * 32,
    )
    claim_id = str(claimed["admission_claim_id"])
    store.record_mission_attempt_submitted(
        attempt_id="mat_0123456789ab",
        dispatch_key="dsp_" + "1" * 32,
        expected_claim_id=claim_id,
        receipt_summary="accepted",
    )
    ambiguous = store.mark_mission_attempt_ambiguous(
        attempt_id="mat_0123456789ab",
        dispatch_key="dsp_" + "1" * 32,
        expected_claim_id=claim_id,
        observed_dispatch_key="dsp_" + "1" * 32,
        receipt_summary="accepted",
        reason="receipt_persistence_unknown",
    )

    assert ambiguous["state"] == "ambiguous"
    assert [
        item["event_type"] for item in store.load()["protocol_event_outbox"]
    ] == [
        "mission_attempt_admission_claimed",
        "mission_attempt_submitted",
        "mission_attempt_ambiguous",
    ]


def test_acp_completion_persists_receipt_result_and_reply_with_one_atomic_save(
    tmp_path, monkeypatch
) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["mission_attempts"] = [_attempt()]
    store.save(state)
    claimed = store.claim_mission_attempt_admission(
        attempt_id="mat_0123456789ab",
        dispatch_key="dsp_" + "1" * 32,
    )
    _submit_acp(store, claimed)
    save_calls = 0
    original_save = store._atomic_save

    def counted_save(candidate):
        nonlocal save_calls
        save_calls += 1
        original_save(candidate)

    monkeypatch.setattr(store, "_atomic_save", counted_save)
    result = store.record_acp_mission_attempt_completion(
        attempt_id="mat_0123456789ab",
        dispatch_key="dsp_" + "1" * 32,
        expected_claim_id=str(claimed["admission_claim_id"]),
        receipt_summary="session-created",
        succeeded=True,
        summary="implementation finished",
        canonical_handoff=_canonical_handoff(),
    )

    persisted = store.load()
    assert save_calls == 1
    assert result["attempt"]["state"] == "succeeded"
    assert result["reply"]["state"] == "received"
    assert result["reply"]["canonical_handoff"] == _canonical_handoff()
    assert "private_reasoning" not in json.dumps(result["reply"])
    assert persisted["mission_attempts"][0]["state"] == "succeeded"
    assert persisted["mission_worker_replies"][0]["state"] == "received"
    assert [item["event_type"] for item in persisted["protocol_event_outbox"]] == [
        "mission_attempt_admission_claimed",
        "mission_attempt_submitted",
        "mission_attempt_result_recorded",
        "mission_reply_evidence_recorded",
    ]


def test_failed_acp_completion_is_one_terminal_save_without_reply(tmp_path) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["mission_attempts"] = [_attempt()]
    store.save(state)
    claimed = store.claim_mission_attempt_admission(
        attempt_id="mat_0123456789ab", dispatch_key="dsp_" + "1" * 32,
    )
    _submit_acp(store, claimed)
    result = store.record_acp_mission_attempt_completion(
        attempt_id="mat_0123456789ab",
        dispatch_key="dsp_" + "1" * 32,
        expected_claim_id=str(claimed["admission_claim_id"]),
        receipt_summary="session-created",
        succeeded=False,
        summary="Worker transport failed",
    )

    persisted = store.load()
    assert result["attempt"]["state"] == "failed"
    assert result["reply"] is None
    assert persisted["mission_worker_replies"] == []
    assert [item["event_type"] for item in persisted["protocol_event_outbox"]] == [
        "mission_attempt_admission_claimed",
        "mission_attempt_submitted",
        "mission_attempt_result_recorded",
    ]


@pytest.mark.parametrize(
    "completion_stage", ["prompt", "update", "parse", "finish", "cleanup"]
)
def test_submitted_acp_completion_failure_is_stage_specific_ambiguity(
    tmp_path, completion_stage: str
) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["mission_attempts"] = [_attempt()]
    store.save(state)
    claimed = store.claim_mission_attempt_admission(
        attempt_id="mat_0123456789ab", dispatch_key="dsp_" + "1" * 32,
    )
    _submit_acp(store, claimed)
    submitted = store.record_mission_attempt_submitted(
        attempt_id="mat_0123456789ab",
        dispatch_key="dsp_" + "1" * 32,
        expected_claim_id=str(claimed["admission_claim_id"]),
        receipt_summary="session-created",
    )

    result = store.mark_acp_mission_attempt_completion_ambiguous(
        attempt_id="mat_0123456789ab",
        dispatch_key="dsp_" + "1" * 32,
        expected_claim_id=str(claimed["admission_claim_id"]),
        receipt_summary=str(submitted["receipt_summary"]),
        completion_stage=completion_stage,
    )

    expected = f"acp_completion_{completion_stage}_outcome_unknown"
    assert result["state"] == "ambiguous"
    assert result["blocker"] == expected
    assert result["terminal_reason"] == expected
    assert result["receipt_summary"] == "session-created"
    persisted = store.load()
    assert persisted["mission_worker_replies"] == []
    assert persisted["protocol_event_outbox"][-1]["payload"]["reason"] == expected
    assert "session-created" not in persisted["protocol_event_outbox"][-1]["payload"]


def test_acp_completion_ambiguity_rejects_unknown_stage_without_writing(tmp_path) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["mission_attempts"] = [_attempt()]
    store.save(state)
    claimed = store.claim_mission_attempt_admission(
        attempt_id="mat_0123456789ab", dispatch_key="dsp_" + "1" * 32,
    )
    store.record_mission_attempt_submitted(
        attempt_id="mat_0123456789ab",
        dispatch_key="dsp_" + "1" * 32,
        expected_claim_id=str(claimed["admission_claim_id"]),
        receipt_summary="session-created",
    )
    before = store.state_path.read_bytes()

    with pytest.raises(ValueError, match="completion stage"):
        store.mark_acp_mission_attempt_completion_ambiguous(
            attempt_id="mat_0123456789ab",
            dispatch_key="dsp_" + "1" * 32,
            expected_claim_id=str(claimed["admission_claim_id"]),
            receipt_summary="session-created",
            completion_stage="/private/tmp/secret",
        )

    assert store.state_path.read_bytes() == before


def test_structured_blocked_acp_completion_preserves_canonical_reply(tmp_path) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["mission_attempts"] = [_attempt()]
    store.save(state)
    claimed = store.claim_mission_attempt_admission(
        attempt_id="mat_0123456789ab", dispatch_key="dsp_" + "1" * 32,
    )
    _submit_acp(store, claimed)
    canonical = _canonical_handoff()
    canonical["status"] = "blocked"
    canonical["summary"] = "blocked by missing input"
    result = store.record_acp_mission_attempt_completion(
        attempt_id="mat_0123456789ab",
        dispatch_key="dsp_" + "1" * 32,
        expected_claim_id=str(claimed["admission_claim_id"]),
        receipt_summary="session-created",
        succeeded=False,
        summary="blocked by missing input",
        canonical_handoff=canonical,
    )

    assert result["attempt"]["state"] == "failed"
    assert result["reply"]["state"] == "received"
    assert result["reply"]["canonical_handoff"] == canonical
    before = store.state_path.read_bytes()
    repeated = store.record_acp_mission_attempt_completion(
        attempt_id="mat_0123456789ab",
        dispatch_key="dsp_" + "1" * 32,
        expected_claim_id=str(claimed["admission_claim_id"]),
        receipt_summary="session-created",
        succeeded=False,
        summary="blocked by missing input",
        canonical_handoff=canonical,
    )
    assert repeated == result
    assert store.state_path.read_bytes() == before
    conflicting = deepcopy(canonical)
    conflicting["summary"] = "different blocked result"
    with pytest.raises(ValueError, match="conflict"):
        store.record_acp_mission_attempt_completion(
            attempt_id="mat_0123456789ab",
            dispatch_key="dsp_" + "1" * 32,
            expected_claim_id=str(claimed["admission_claim_id"]),
            receipt_summary="session-created",
            succeeded=False,
            summary="blocked by missing input",
            canonical_handoff=conflicting,
        )
    assert store.state_path.read_bytes() == before


def test_tmux_completion_atomically_persists_result_and_canonical_reply(
    tmp_path, monkeypatch
) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["mission_attempts"] = [_attempt("tmux")]
    store.save(state)
    claimed = store.claim_mission_attempt_admission(
        attempt_id="mat_0123456789ab", dispatch_key="dsp_" + "1" * 32,
    )
    store.record_mission_attempt_submitted(
        attempt_id="mat_0123456789ab",
        dispatch_key="dsp_" + "1" * 32,
        expected_claim_id=str(claimed["admission_claim_id"]),
        receipt_summary="pane accepted",
    )
    save_calls = 0
    original_save = store._atomic_save

    def counted_save(candidate):
        nonlocal save_calls
        save_calls += 1
        original_save(candidate)

    monkeypatch.setattr(store, "_atomic_save", counted_save)
    result = store.record_tmux_mission_attempt_completion(
        attempt_id="mat_0123456789ab",
        dispatch_key="dsp_" + "1" * 32,
        succeeded=True,
        summary="implementation finished",
        canonical_handoff=_canonical_handoff(),
    )

    assert save_calls == 1
    assert result["attempt"]["state"] == "succeeded"
    assert result["reply"]["canonical_handoff"] == _canonical_handoff()


def test_acp_completion_wrong_claim_is_zero_write(tmp_path) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["mission_attempts"] = [_attempt()]
    store.save(state)
    claimed = store.claim_mission_attempt_admission(
        attempt_id="mat_0123456789ab", dispatch_key="dsp_" + "1" * 32,
    )
    _submit_acp(store, claimed)
    before = store.state_path.read_bytes()
    with pytest.raises(ValueError, match="authority"):
        store.record_acp_mission_attempt_completion(
            attempt_id="mat_0123456789ab",
            dispatch_key="dsp_" + "1" * 32,
            expected_claim_id="adm_" + "f" * 12,
            receipt_summary="session-created",
            succeeded=True,
            summary="done",
            canonical_handoff=_canonical_handoff(),
        )
    assert store.state_path.read_bytes() == before


def test_identical_acp_completion_retry_is_idempotent_and_adds_no_events(
    tmp_path,
) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["mission_attempts"] = [_attempt()]
    store.save(state)
    claimed = store.claim_mission_attempt_admission(
        attempt_id="mat_0123456789ab", dispatch_key="dsp_" + "1" * 32,
    )
    _submit_acp(store, claimed)
    arguments = {
        "attempt_id": "mat_0123456789ab",
        "dispatch_key": "dsp_" + "1" * 32,
        "expected_claim_id": str(claimed["admission_claim_id"]),
        "receipt_summary": "session-created",
        "succeeded": True,
        "summary": "done",
        "canonical_handoff": _canonical_handoff(),
    }
    first = store.record_acp_mission_attempt_completion(**arguments)
    before = store.state_path.read_bytes()
    second = store.record_acp_mission_attempt_completion(**arguments)

    assert second == first
    assert store.state_path.read_bytes() == before


def test_identical_acp_completion_retry_remains_idempotent_after_reply_validation(
    tmp_path,
) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["mission_attempts"] = [_attempt()]
    store.save(state)
    claimed = store.claim_mission_attempt_admission(
        attempt_id="mat_0123456789ab", dispatch_key="dsp_" + "1" * 32,
    )
    _submit_acp(store, claimed)
    arguments = {
        "attempt_id": "mat_0123456789ab",
        "dispatch_key": "dsp_" + "1" * 32,
        "expected_claim_id": str(claimed["admission_claim_id"]),
        "receipt_summary": "session-created",
        "succeeded": True,
        "summary": "done",
        "canonical_handoff": _canonical_handoff(),
    }
    completed = store.record_acp_mission_attempt_completion(**arguments)
    reply = completed["reply"]
    assert isinstance(reply, dict)
    from dataclasses import asdict
    from agentdeck.models import EventRecord

    state = store.load()
    state["mission_worker_replies"][0]["state"] = "validated"
    state["protocol_event_outbox"].append(
        asdict(EventRecord.create(
            "mission_reply_evidence_recorded",
            {
                "attempt_id": "mat_0123456789ab",
                "mission_id": "mis_0123456789ab",
                "reply_id": reply["reply_id"],
                "state": "validated",
            },
        ))
    )
    store.save(state)
    before = store.state_path.read_bytes()
    repeated = store.record_acp_mission_attempt_completion(**arguments)

    assert repeated["attempt"] == completed["attempt"]
    assert repeated["reply"]["state"] == "validated"
    assert store.state_path.read_bytes() == before


def test_conflicting_acp_completion_retry_is_zero_write(tmp_path) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["mission_attempts"] = [_attempt()]
    store.save(state)
    claimed = store.claim_mission_attempt_admission(
        attempt_id="mat_0123456789ab", dispatch_key="dsp_" + "1" * 32,
    )
    _submit_acp(store, claimed)
    store.record_acp_mission_attempt_completion(
        attempt_id="mat_0123456789ab",
        dispatch_key="dsp_" + "1" * 32,
        expected_claim_id=str(claimed["admission_claim_id"]),
        receipt_summary="session-created",
        succeeded=True,
        summary="done",
        canonical_handoff=_canonical_handoff(),
    )
    before = store.state_path.read_bytes()
    with pytest.raises(ValueError, match="conflict"):
        store.record_acp_mission_attempt_completion(
            attempt_id="mat_0123456789ab",
            dispatch_key="dsp_" + "1" * 32,
            expected_claim_id=str(claimed["admission_claim_id"]),
            receipt_summary="session-created",
            succeeded=False,
            summary="Worker transport failed",
        )
    assert store.state_path.read_bytes() == before


@pytest.mark.parametrize(
    "overlap_stage", ["claimed", "released", "submitted", "ambiguous"]
)
def test_claim_lifecycle_accepts_identical_journal_outbox_replay_overlap(
    tmp_path, overlap_stage: str
) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["mission_attempts"] = [_attempt()]
    store.save(state)
    claimed = store.claim_mission_attempt_admission(
        attempt_id="mat_0123456789ab",
        dispatch_key="dsp_" + "1" * 32,
    )
    claim_id = str(claimed["admission_claim_id"])
    if overlap_stage == "released":
        store.release_mission_attempt_admission(
            attempt_id="mat_0123456789ab",
            dispatch_key="dsp_" + "1" * 32,
            expected_claim_id=claim_id,
        )
    elif overlap_stage in {"submitted", "ambiguous"}:
        store.record_mission_attempt_submitted(
            attempt_id="mat_0123456789ab",
            dispatch_key="dsp_" + "1" * 32,
            expected_claim_id=claim_id,
            receipt_summary="accepted",
        )
        if overlap_stage == "ambiguous":
            store.mark_mission_attempt_ambiguous(
                attempt_id="mat_0123456789ab",
                dispatch_key="dsp_" + "1" * 32,
                expected_claim_id=claim_id,
                observed_dispatch_key="dsp_" + "1" * 32,
                receipt_summary="accepted",
                reason="receipt_persistence_unknown",
            )

    state = store.load()
    store.events_path.write_text(
        "".join(
            json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
            for item in state["protocol_event_outbox"]
        ),
        encoding="utf-8",
    )

    if overlap_stage == "claimed":
        released = store.release_mission_attempt_admission(
            attempt_id="mat_0123456789ab",
            dispatch_key="dsp_" + "1" * 32,
            expected_claim_id=claim_id,
        )
        assert released["state"] == "prepared"
    elif overlap_stage == "released":
        reclaimed = store.claim_mission_attempt_admission(
            attempt_id="mat_0123456789ab",
            dispatch_key="dsp_" + "1" * 32,
        )
        assert reclaimed["state"] == "admitting"
    elif overlap_stage == "submitted":
        ambiguous = store.mark_mission_attempt_ambiguous(
            attempt_id="mat_0123456789ab",
            dispatch_key="dsp_" + "1" * 32,
            expected_claim_id=claim_id,
            observed_dispatch_key="dsp_" + "1" * 32,
            receipt_summary="accepted",
            reason="receipt_persistence_unknown",
        )
        assert ambiguous["state"] == "ambiguous"
    else:
        state["mission_attempts"].append(
            _attempt(
                attempt_id="mat_abcdefabcdef",
                mission_id="mis_abcdefabcdef",
                dispatch_key="dsp_" + "2" * 32,
            )
        )
        store.save(state)
        next_claim = store.claim_mission_attempt_admission(
            attempt_id="mat_abcdefabcdef",
            dispatch_key="dsp_" + "2" * 32,
        )
        assert next_claim["state"] == "admitting"


def test_claim_lifecycle_rejects_conflicting_journal_outbox_replay_overlap(
    tmp_path,
) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["mission_attempts"] = [_attempt()]
    store.save(state)
    claimed = store.claim_mission_attempt_admission(
        attempt_id="mat_0123456789ab",
        dispatch_key="dsp_" + "1" * 32,
    )
    claim_id = str(claimed["admission_claim_id"])
    state = store.load()
    conflicting = deepcopy(state["protocol_event_outbox"][0])
    conflicting["payload"]["mission_id"] = "mis_abcdefabcdef"
    store.events_path.write_text(
        json.dumps(conflicting, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    before_state = store.load()
    before_events = store.events_path.read_bytes()

    with pytest.raises(ValueError, match="replay conflict"):
        store.release_mission_attempt_admission(
            attempt_id="mat_0123456789ab",
            dispatch_key="dsp_" + "1" * 32,
            expected_claim_id=claim_id,
        )
    assert store.load() == before_state
    assert store.events_path.read_bytes() == before_events


def test_admission_ambiguity_cannot_follow_submitted_even_with_fixed_summary(
    tmp_path,
) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["mission_attempts"] = [_attempt()]
    store.save(state)
    claimed = store.claim_mission_attempt_admission(
        attempt_id="mat_0123456789ab",
        dispatch_key="dsp_" + "1" * 32,
    )
    claim_id = str(claimed["admission_claim_id"])
    store.record_mission_attempt_submitted(
        attempt_id="mat_0123456789ab",
        dispatch_key="dsp_" + "1" * 32,
        expected_claim_id=claim_id,
        receipt_summary="admission outcome unknown",
    )
    before_state = store.load()
    before_events = (
        store.events_path.read_bytes() if store.events_path.exists() else None
    )

    with pytest.raises(ValueError, match="transition"):
        store.mark_mission_attempt_admission_ambiguous(
            attempt_id="mat_0123456789ab",
            dispatch_key="dsp_" + "1" * 32,
            expected_claim_id=claim_id,
            reason="admission_outcome_unknown",
        )
    assert store.load() == before_state
    assert (
        store.events_path.read_bytes() if store.events_path.exists() else None
    ) == before_events


def test_history_replay_rejects_admission_ambiguity_after_submitted(tmp_path) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["mission_attempts"] = [_attempt()]
    store.save(state)
    claimed = store.claim_mission_attempt_admission(
        attempt_id="mat_0123456789ab",
        dispatch_key="dsp_" + "1" * 32,
    )
    claim_id = str(claimed["admission_claim_id"])
    store.record_mission_attempt_submitted(
        attempt_id="mat_0123456789ab",
        dispatch_key="dsp_" + "1" * 32,
        expected_claim_id=claim_id,
        receipt_summary="admission outcome unknown",
    )
    state = store.load()
    state["mission_attempts"][0].update(
        {
            "state": "ambiguous",
            "blocker": "admission_outcome_unknown",
            "terminal_reason": "admission_outcome_unknown",
        }
    )
    state["mission_attempts"].append(
        _attempt(
            attempt_id="mat_abcdefabcdef",
            mission_id="mis_abcdefabcdef",
            dispatch_key="dsp_" + "2" * 32,
        )
    )
    state["protocol_event_outbox"].append(
        {
            "event_id": "evt_abcdefabcdef",
            "event_type": "mission_attempt_ambiguous",
            "created_at": "2026-07-13T00:00:03+00:00",
            "payload": {
                "attempt_id": "mat_0123456789ab",
                "mission_id": "mis_0123456789ab",
                "step_id": "step_1",
                "dispatch_key": "dsp_" + "1" * 32,
                "admission_claim_id": claim_id,
                "reason": "admission_outcome_unknown",
                "expected_dispatch_key": "dsp_" + "1" * 32,
                "observed_dispatch_key": None,
            },
        }
    )
    store.save(state)
    before = store.load()

    with pytest.raises(ValueError, match="admission claim history"):
        store.claim_mission_attempt_admission(
            attempt_id="mat_abcdefabcdef",
            dispatch_key="dsp_" + "2" * 32,
        )
    assert store.load() == before


@pytest.mark.parametrize(
    ("source_stage", "reason"),
    [
        ("claimed", "admission_outcome_unknown"),
        ("claimed", "receipt_persistence_unknown"),
        ("submitted", "receipt_persistence_unknown"),
    ],
)
def test_claim_lifecycle_allows_reason_specific_ambiguity_stages(
    tmp_path, source_stage: str, reason: str
) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["mission_attempts"] = [_attempt()]
    store.save(state)
    claimed = store.claim_mission_attempt_admission(
        attempt_id="mat_0123456789ab",
        dispatch_key="dsp_" + "1" * 32,
    )
    claim_id = str(claimed["admission_claim_id"])
    receipt_summary = "accepted"
    if source_stage == "submitted":
        store.record_mission_attempt_submitted(
            attempt_id="mat_0123456789ab",
            dispatch_key="dsp_" + "1" * 32,
            expected_claim_id=claim_id,
            receipt_summary=receipt_summary,
        )

    if reason == "admission_outcome_unknown":
        ambiguous = store.mark_mission_attempt_admission_ambiguous(
            attempt_id="mat_0123456789ab",
            dispatch_key="dsp_" + "1" * 32,
            expected_claim_id=claim_id,
            reason=reason,
        )
    else:
        ambiguous = store.mark_mission_attempt_ambiguous(
            attempt_id="mat_0123456789ab",
            dispatch_key="dsp_" + "1" * 32,
            expected_claim_id=claim_id,
            observed_dispatch_key="dsp_" + "1" * 32,
            receipt_summary=receipt_summary,
            reason=reason,
        )
    assert ambiguous["state"] == "ambiguous"


def test_submitted_reread_rejects_forged_claim_generation_before_complete() -> None:
    authority = _Authority()
    completion_calls: list[str] = []

    def forge_submitted(candidate, receipt) -> None:
        authority.current.update(
            {
                "state": "submitted",
                "updated_at": "2026-07-13T00:00:02+00:00",
                "admission_claim_id": "adm_" + "f" * 12,
                "receipt_summary": receipt.summary,
            }
        )

    supervisor = WorkerAttemptSupervisor(
        authorize_attempt=authority.authorize,
        claim_admission=authority.claim,
        release_admission=authority.release,
        persist_submitted=forge_submitted,
        mark_attempt_ambiguous=lambda *_: pytest.fail("must not mark stale claim"),
        mark_admission_ambiguous=lambda *_: pytest.fail("must not mark admission"),
        acp_admit=lambda _: _receipt(),
        tmux_admit=lambda _: pytest.fail("tmux must not run"),
        acp_complete=lambda *_: completion_calls.append("complete") or _result(),
        tmux_complete=lambda *_: pytest.fail("tmux must not run"),
    )

    with pytest.raises(WorkerAttemptError) as error:
        asyncio.run(supervisor.execute(_attempt(), _route()))
    assert str(error.value) == "Worker submitted receipt claim drift"
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert completion_calls == []
    assert supervisor._halted is True


@pytest.mark.parametrize(
    "contradiction",
    [
        "receipt_public_admission_reason",
        "admission_public_receipt_reason",
        "receipt_private_missing_evidence",
        "admission_private_forged_receipt",
    ],
)
def test_ambiguity_reason_and_evidence_shapes_cannot_cross(
    tmp_path, contradiction: str
) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["mission_attempts"] = [_attempt()]
    store.save(state)
    claimed = store.claim_mission_attempt_admission(
        attempt_id="mat_0123456789ab",
        dispatch_key="dsp_" + "1" * 32,
    )
    claim_id = str(claimed["admission_claim_id"])
    before = store.load()

    with pytest.raises(ValueError, match="ambiguity"):
        if contradiction == "receipt_public_admission_reason":
            store.mark_mission_attempt_ambiguous(
                attempt_id="mat_0123456789ab",
                dispatch_key="dsp_" + "1" * 32,
                expected_claim_id=claim_id,
                observed_dispatch_key=None,
                receipt_summary="admission outcome unknown",
                reason="admission_outcome_unknown",
            )
        elif contradiction == "admission_public_receipt_reason":
            store.mark_mission_attempt_admission_ambiguous(
                attempt_id="mat_0123456789ab",
                dispatch_key="dsp_" + "1" * 32,
                expected_claim_id=claim_id,
                reason="receipt_persistence_unknown",
            )
        elif contradiction == "receipt_private_missing_evidence":
            store._transition_mission_attempt_receipt(
                attempt_id="mat_0123456789ab",
                dispatch_key="dsp_" + "1" * 32,
                expected_claim_id=claim_id,
                observed_dispatch_key=None,
                receipt_summary="accepted",
                target_state="ambiguous",
                reason="receipt_persistence_unknown",
            )
        else:
            store._transition_mission_attempt_receipt(
                attempt_id="mat_0123456789ab",
                dispatch_key="dsp_" + "1" * 32,
                expected_claim_id=claim_id,
                observed_dispatch_key="dsp_" + "1" * 32,
                receipt_summary="forged",
                target_state="ambiguous",
                reason="admission_outcome_unknown",
            )
    assert store.load() == before


@pytest.mark.parametrize(
    "transition", ["claim", "release", "submitted", "ambiguity"]
)
def test_all_attempt_transitions_reject_unrelated_duplicate_attempt_ids(
    tmp_path, transition: str
) -> None:
    store = StateStore(tmp_path)
    claim_id = "adm_" + "a" * 12
    target = _attempt(admission_claim_id=None)
    duplicate1 = _attempt(
        attempt_id="mat_abcdefabcdef",
        mission_id="mis_abcdefabcdef",
        dispatch_key="dsp_" + "3" * 32,
        admission_claim_id=None,
    )
    duplicate2 = _attempt(
        attempt_id="mat_abcdefabcdef",
        mission_id="mis_fedcbafedcba",
        dispatch_key="dsp_" + "4" * 32,
        admission_claim_id=None,
    )
    if transition != "claim":
        target.update(
            {
                "state": "admitting",
                "updated_at": "2026-07-13T00:00:01+00:00",
                "admission_claim_id": claim_id,
            }
        )
    state = store.load()
    state["mission_attempts"] = [target, duplicate1, duplicate2]
    store.save(state)
    before = store.load()

    with pytest.raises(ValueError, match="duplicate mission attempt identity"):
        if transition == "claim":
            store.claim_mission_attempt_admission(
                attempt_id="mat_0123456789ab",
                dispatch_key="dsp_" + "1" * 32,
            )
        elif transition == "release":
            store.release_mission_attempt_admission(
                attempt_id="mat_0123456789ab",
                dispatch_key="dsp_" + "1" * 32,
                expected_claim_id=claim_id,
            )
        elif transition == "submitted":
            store.record_mission_attempt_submitted(
                attempt_id="mat_0123456789ab",
                dispatch_key="dsp_" + "1" * 32,
                expected_claim_id=claim_id,
                receipt_summary="accepted",
            )
        else:
            store.mark_mission_attempt_ambiguous(
                attempt_id="mat_0123456789ab",
                dispatch_key="dsp_" + "1" * 32,
                expected_claim_id=claim_id,
                observed_dispatch_key="dsp_" + "1" * 32,
                receipt_summary="accepted",
                reason="receipt_persistence_unknown",
            )
    assert store.load() == before


def test_submitted_receipt_cannot_bypass_durable_admission_claim(tmp_path) -> None:
    store = StateStore(tmp_path)
    state = store.load()
    state["mission_attempts"] = [_attempt()]
    store.save(state)
    before = store.load()

    with pytest.raises(ValueError, match="admission claim drift"):
        store.record_mission_attempt_submitted(
            attempt_id="mat_0123456789ab",
            dispatch_key="dsp_" + "1" * 32,
            expected_claim_id="adm_" + "f" * 12,
            receipt_summary="accepted",
        )
    assert store.load() == before


@pytest.mark.parametrize(
    "transition", ["claim", "release", "submitted", "ambiguous"]
)
def test_attempt_transitions_reject_duplicate_global_dispatch_lineage(
    tmp_path, transition: str
) -> None:
    store = StateStore(tmp_path)
    duplicate = _attempt(
        attempt_id="mat_abcdefabcdef",
        mission_id="mis_abcdefabcdef",
    )
    state = store.load()
    state["mission_attempts"] = [_attempt(), duplicate]
    if transition != "claim":
        for item in state["mission_attempts"]:
            item["state"] = "admitting"
            item["updated_at"] = "2026-07-13T00:00:01+00:00"
            item["admission_claim_id"] = "adm_" + "a" * 12
    store.save(state)
    before = store.load()

    with pytest.raises(ValueError, match="duplicate mission attempt dispatch key"):
        if transition == "claim":
            store.claim_mission_attempt_admission(
                attempt_id="mat_0123456789ab",
                dispatch_key="dsp_" + "1" * 32,
            )
        elif transition == "release":
            store.release_mission_attempt_admission(
                attempt_id="mat_0123456789ab",
                dispatch_key="dsp_" + "1" * 32,
                expected_claim_id="adm_" + "a" * 12,
            )
        elif transition == "submitted":
            store.record_mission_attempt_submitted(
                attempt_id="mat_0123456789ab",
                dispatch_key="dsp_" + "1" * 32,
                expected_claim_id="adm_" + "a" * 12,
                receipt_summary="accepted",
            )
        else:
            store.mark_mission_attempt_ambiguous(
                attempt_id="mat_0123456789ab",
                dispatch_key="dsp_" + "1" * 32,
                expected_claim_id="adm_" + "a" * 12,
                observed_dispatch_key="dsp_" + "9" * 32,
                receipt_summary="accepted",
                reason="receipt_persistence_unknown",
            )
    assert store.load() == before


def test_concurrent_same_dispatch_uses_one_transport_and_releases_claim() -> None:
    async def scenario() -> None:
        authority = _Authority()
        calls: list[str] = []

        async def persist(candidate, receipt) -> None:
            await asyncio.sleep(0)
            await authority.persist(candidate, receipt)

        def admit(_):
            calls.append("acp")
            return _receipt()

        supervisor = WorkerAttemptSupervisor(
            authorize_attempt=authority.authorize,
            claim_admission=authority.claim,
            release_admission=authority.release,
            persist_submitted=persist,
            mark_attempt_ambiguous=lambda *_: None,
            mark_admission_ambiguous=lambda *_: None,
            acp_admit=admit,
            tmux_admit=lambda _: pytest.fail("tmux must not run"),
            acp_complete=_completion(_result()),
            tmux_complete=lambda *_: pytest.fail("tmux must not run"),
        )
        results = await asyncio.gather(
            supervisor.execute(_attempt(), _route()),
            supervisor.execute(_attempt(), _route()),
            return_exceptions=True,
        )

        assert calls == ["acp"]
        assert sum(isinstance(item, WorkerAttemptError) for item in results) == 1
        assert sum(not isinstance(item, BaseException) for item in results) == 1
        assert supervisor._halted is False
        assert not hasattr(supervisor, "_claimed_dispatch_keys")

    asyncio.run(scenario())


def test_human_owned_route_cannot_forge_automation_allowed() -> None:
    authority = _Authority()
    calls: list[str] = []
    supervisor = _supervisor(
        authority,
        acp_admit=lambda _: calls.append("acp") or _receipt(),
    )

    with pytest.raises(WorkerAttemptError, match="Worker ownership is not AgentDeck"):
        asyncio.run(
            supervisor.execute(
                _attempt(),
                _route(ownership="human_owned", automation_allowed=True),
            )
        )

    assert calls == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda result: object.__setattr__(result, "validated", 1),
        lambda result: object.__setattr__(result, "trace_ids", ["trn_worker"]),
        lambda result: object.__setattr__(result, "artifacts", [result.artifacts[0]]),
    ],
)
def test_result_strict_types_fail_closed(mutation) -> None:
    authority = _Authority()
    result = _result()
    mutation(result)
    supervisor = _supervisor(authority, acp_complete=_completion(result))

    with pytest.raises(WorkerAttemptError, match="ACP Worker result is invalid"):
        asyncio.run(supervisor.execute(_attempt(), _route()))


@pytest.mark.parametrize("token", [None, "dsp_" + "9" * 32])
def test_reply_requires_exact_attempt_dispatch_handoff_token(token: str | None) -> None:
    authority = _Authority()
    reply = _reply(token) if token is not None else _reply()
    if token is None:
        reply.pop("handoff_token")
    supervisor = _supervisor(
        authority,
        acp_complete=_completion(_result(reply=reply)),
    )

    with pytest.raises(WorkerAttemptError, match="ACP Worker result is invalid"):
        asyncio.run(supervisor.execute(_attempt(), _route()))


def test_validated_outcome_is_detached_from_mutable_transport_result() -> None:
    authority = _Authority()
    reply = _reply()
    artifact = ArtifactEvidence(
        path="reports/result.md", content_hash="sha256:" + "a" * 64
    )
    result = TransportResult(
        stop_reason="end_turn",
        validated=True,
        reply=reply,
        artifacts=(artifact,),
        trace_ids=("trn_worker", "rep_worker"),
    )
    outcome = asyncio.run(
        _supervisor(authority, acp_complete=_completion(result)).execute(
            _attempt(), _route()
        )
    )

    reply["summary"] = "mutated"
    object.__setattr__(artifact, "path", "secrets.txt")
    object.__setattr__(result, "trace_ids", ("mutated",))

    assert outcome.compact()["summary"] == "implementation finished"
    assert outcome.compact()["artifacts"][0]["path"] == "reports/result.md"
    assert outcome.compact()["trace_ids"] == ["trn_worker", "rep_worker"]


def test_ignored_reply_extra_value_is_never_deepcopied_or_accessed() -> None:
    class ExplodingExtra:
        def __deepcopy__(self, _memo):
            raise AssertionError("extra reply value was accessed")

    authority = _Authority()
    reply = _reply()
    reply["unapproved_extra"] = ExplodingExtra()
    outcome = asyncio.run(
        _supervisor(
            authority,
            acp_complete=_completion(_result(reply=reply)),
        ).execute(_attempt(), _route())
    )

    assert outcome.summary == "implementation finished"


def test_transport_drift_fails_closed_before_any_dispatch() -> None:
    authority = _Authority()
    calls: list[str] = []
    supervisor = _supervisor(
        authority,
        acp_admit=lambda _: calls.append("acp") or _receipt(),
        tmux_admit=lambda _: calls.append("tmux") or _receipt(),
    )

    with pytest.raises(WorkerAttemptError, match="Worker transport drift"):
        asyncio.run(
            supervisor.execute(_attempt("acp"), _route("acp", effective_transport="tmux"))
        )

    assert calls == []


@pytest.mark.parametrize("transport", ["acp", "tmux"])
def test_transport_failure_never_calls_other_fallback(transport: str) -> None:
    authority = _Authority()
    authority.current = _attempt(transport)
    calls: list[str] = []
    supervisor = _supervisor(
        authority,
        acp_admit=lambda _: calls.append("acp")
        or (_ for _ in ()).throw(RuntimeError("secret acp")),
        tmux_admit=lambda _: calls.append("tmux")
        or (_ for _ in ()).throw(RuntimeError("secret tmux")),
    )

    with pytest.raises(
        WorkerAttemptError,
        match=f"{'ACP' if transport == 'acp' else 'tmux'} Worker admission outcome is unknown",
    ) as caught:
        asyncio.run(supervisor.execute(_attempt(transport), _route(transport)))

    assert calls == [transport]
    assert "secret" not in str(caught.value)


def test_acp_and_validated_tmux_results_map_to_same_compact_outcome() -> None:
    acp_authority = _Authority()
    tmux_authority = _Authority()
    tmux_authority.current = _attempt("tmux")
    acp = asyncio.run(
        _supervisor(acp_authority).execute(_attempt(), _route())
    )
    tmux = asyncio.run(
        _supervisor(tmux_authority).execute(_attempt("tmux"), _route("tmux"))
    )

    assert acp.compact() == tmux.compact()
    assert set(acp.compact()) == {
        "handoff_token",
        "status",
        "summary",
        "verification",
        "risks",
        "next_steps",
        "artifacts",
        "trace_ids",
    }


def test_legacy_workflow_handoff_is_projection_of_same_canonical_record() -> None:
    reply = _reply()
    canonical = build_canonical_handoff(
        reply=reply,
        expected_handoff_token="dsp_" + "1" * 32,
        artifacts=[{"path": "reports/result.md", "content_hash": None}],
        trace_ids=["rep_worker"],
        require_artifact_hashes=False,
    )
    legacy = build_compact_handoff(
        step=1,
        agent_id="planner",
        reply=reply,
        reply_id="rep_worker",
        artifact_paths=["reports/result.md"],
    )

    for field in ("status", "summary", "verification", "risks", "next_steps"):
        assert legacy[field] == canonical.compact()[field]
    assert legacy["artifact_paths"] == [item.path for item in canonical.artifacts]
    assert legacy["trace_command"] == "agentdeck trace --id " + canonical.trace_ids[0]


def test_acp_requires_formal_completed_stop_reason() -> None:
    authority = _Authority()
    supervisor = _supervisor(
        authority,
        acp_complete=_completion(_result(stop_reason="max_tokens")),
    )

    with pytest.raises(WorkerAttemptError, match="ACP Worker did not complete"):
        asyncio.run(supervisor.execute(_attempt(), _route()))


def test_tmux_requires_a_validated_structured_reply() -> None:
    authority = _Authority()
    authority.current = _attempt("tmux")
    supervisor = _supervisor(
        authority,
        tmux_complete=_completion(
            _result(validated=False, stop_reason="structured_reply")
        ),
    )

    with pytest.raises(WorkerAttemptError, match="tmux Worker reply is not validated"):
        asyncio.run(supervisor.execute(_attempt("tmux"), _route("tmux")))


@pytest.mark.parametrize(
    ("reply_state", "handoff_state"),
    [("none", "none"), ("received", "none"), ("validated", "none")],
)
def test_worker_b_cannot_start_before_validated_handoff_is_recorded(
    reply_state: str, handoff_state: str
) -> None:
    decision = supervisor_gate(
        {
            "attempt_state": "succeeded",
            "result_status": "completed",
            "reply_state": reply_state,
            "handoff_state": handoff_state,
            "next_worker": "reviewer",
        }
    )
    assert decision.next_worker is None


def test_worker_b_starts_only_after_agentdeck_validates_and_records_worker_a() -> None:
    decision = supervisor_gate(
        {
            "attempt_state": "succeeded",
            "result_status": "completed",
            "reply_state": "validated",
            "handoff_state": "recorded",
            "next_worker": "reviewer",
        }
    )
    assert decision.next_worker == "reviewer"


@pytest.mark.parametrize(
    ("attempt_state", "result_status"),
    [
        ("failed", "completed"),
        ("cancelled", "completed"),
        ("interrupted", "completed"),
        ("ambiguous", "completed"),
        ("completed", "failed"),
        ("succeeded", "blocked"),
    ],
)
def test_supervisor_gate_rejects_non_success_attempt_or_result(
    attempt_state: str, result_status: str
) -> None:
    decision = supervisor_gate(
        {
            "attempt_state": attempt_state,
            "result_status": result_status,
            "reply_state": "validated",
            "handoff_state": "recorded",
            "next_worker": "reviewer",
        }
    )
    assert decision.next_worker is None


def test_supervisor_gate_rejects_unknown_fact_fields() -> None:
    decision = supervisor_gate(
        {
            "attempt_state": "succeeded",
            "result_status": "completed",
            "reply_state": "validated",
            "handoff_state": "recorded",
            "next_worker": "reviewer",
            "validated": True,
        }
    )
    assert decision.next_worker is None
