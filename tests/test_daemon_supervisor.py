from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from agentdeck.conversation.transports import WorkerRoute
from agentdeck.daemon.supervisor import (
    ArtifactEvidence,
    SubmittedReceipt,
    TransportResult,
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
        "snapshot_hash": "sha256:" + "2" * 64,
        "state": "prepared",
        "created_at": "2026-07-13T00:00:00+00:00",
        "updated_at": "2026-07-13T00:00:00+00:00",
        "receipt_summary": None,
        "blocker": None,
        "terminal_reason": None,
    }
    record.update(overrides)
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

    def authorize(self, candidate: dict[str, object]) -> dict[str, object]:
        self.authorize_calls += 1
        assert candidate["attempt_id"] == self.current["attempt_id"]
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
) -> WorkerAttemptSupervisor:
    return WorkerAttemptSupervisor(
        authorize_attempt=authority.authorize,
        persist_submitted=authority.persist,
        mark_attempt_ambiguous=mark_ambiguous or (lambda *_: None),
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
        persist_submitted=persist,
        mark_attempt_ambiguous=lambda *_: None,
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
        persist_submitted=persist,
        mark_attempt_ambiguous=mark_ambiguous,
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
        persist_submitted=authority.persist,
        mark_attempt_ambiguous=lambda *_: None,
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
        persist_submitted=authority.persist,
        mark_attempt_ambiguous=lambda *_: None,
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


def test_pre_admission_failure_releases_claim_for_prepared_retry() -> None:
    authority = _Authority()
    calls: list[str] = []

    def admit(_):
        calls.append("acp")
        if len(calls) == 1:
            raise RuntimeError("pre-admission")
        return _receipt()

    supervisor = _supervisor(authority, acp_admit=admit)

    with pytest.raises(WorkerAttemptError, match="failed before admission"):
        asyncio.run(supervisor.execute(_attempt(), _route()))
    result = asyncio.run(supervisor.execute(_attempt(), _route()))

    assert result.status == "completed"
    assert calls == ["acp", "acp"]
    assert supervisor._claimed_dispatch_keys == set()


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
        persist_submitted=unknown,
        mark_attempt_ambiguous=mark_ambiguous,
        acp_admit=lambda _: calls.append("acp") or _receipt(),
        tmux_admit=lambda _: pytest.fail("tmux must not run"),
        acp_complete=_completion(_result()),
        tmux_complete=lambda *_: pytest.fail("tmux must not run"),
    )

    with pytest.raises(WorkerAttemptError, match="persistence failed"):
        asyncio.run(supervisor.execute(_attempt(), _route()))
    assert calls == ["acp"]
    assert supervisor._claimed_dispatch_keys == set()
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
            dispatch_key=receipt.dispatch_key,
            receipt_summary=receipt.summary,
            reason=reason,
        )

    first = WorkerAttemptSupervisor(
        authorize_attempt=authorize,
        persist_submitted=lambda *_: (_ for _ in ()).throw(RuntimeError("unknown")),
        mark_attempt_ambiguous=mark,
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
        persist_submitted=lambda *_: pytest.fail("must not persist"),
        mark_attempt_ambiguous=mark,
        acp_admit=lambda _: calls.append("unexpected") or _receipt(),
        tmux_admit=lambda _: pytest.fail("tmux must not run"),
        acp_complete=_completion(_result()),
        tmux_complete=lambda *_: pytest.fail("tmux must not run"),
    )
    with pytest.raises(WorkerAttemptError, match="must be prepared"):
        asyncio.run(restarted.execute(persisted, _route()))
    assert calls == ["acp"]


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
            dispatch_key=receipt.dispatch_key,
            receipt_summary=receipt.summary,
        )

    async def complete(candidate, _receipt):
        observed_states.append(
            store.mission_attempt_by_id(str(candidate["attempt_id"]))["state"]
        )
        return _result()

    supervisor = WorkerAttemptSupervisor(
        authorize_attempt=authorize,
        persist_submitted=persist,
        mark_attempt_ambiguous=lambda *_: pytest.fail("must not mark ambiguous"),
        acp_admit=lambda _: _receipt(),
        tmux_admit=lambda _: pytest.fail("tmux must not run"),
        acp_complete=complete,
        tmux_complete=lambda *_: pytest.fail("tmux must not run"),
    )
    result = asyncio.run(supervisor.execute(_attempt(), _route()))

    assert result.status == "completed"
    assert observed_states == ["submitted"]
    assert store.load()["protocol_event_outbox"][-1]["event_type"] == "mission_attempt_submitted"


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
            persist_submitted=persist,
            mark_attempt_ambiguous=lambda *_: None,
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
        assert supervisor._claimed_dispatch_keys == set()

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

    with pytest.raises(WorkerAttemptError, match=f"{'ACP' if transport == 'acp' else 'tmux'} Worker failed") as caught:
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
            "reply_state": reply_state,
            "handoff_state": handoff_state,
            "next_worker": "reviewer",
        }
    )
    assert decision.next_worker is None


def test_worker_b_starts_only_after_agentdeck_validates_and_records_worker_a() -> None:
    decision = supervisor_gate(
        {
            "reply_state": "validated",
            "handoff_state": "recorded",
            "next_worker": "reviewer",
        }
    )
    assert decision.next_worker == "reviewer"
