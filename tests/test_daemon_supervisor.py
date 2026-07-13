from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from agentdeck.conversation.transports import WorkerRoute
from agentdeck.daemon.supervisor import (
    ArtifactEvidence,
    SubmittedReceipt,
    TransportExecution,
    TransportResult,
    WorkerAttemptError,
    WorkerAttemptSupervisor,
    supervisor_gate,
)


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


def _execution(result: TransportResult | Exception) -> TransportExecution:
    async def complete() -> TransportResult:
        if isinstance(result, Exception):
            raise result
        return result

    return TransportExecution(
        admission=SubmittedReceipt(
            receipt_id="rcp_worker",
            dispatch_key="dsp_" + "1" * 32,
            summary="accepted",
        ),
        completion_factory=complete,
    )


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
    acp_execute=None,
    tmux_execute=None,
) -> WorkerAttemptSupervisor:
    return WorkerAttemptSupervisor(
        authorize_attempt=authority.authorize,
        persist_submitted=authority.persist,
        acp_execute=acp_execute or (lambda _: _execution(_result())),
        tmux_execute=tmux_execute
        or (lambda _: _execution(_result(stop_reason="structured_reply"))),
    )


def test_completion_factory_cannot_start_before_async_receipt_persistence_finishes() -> None:
    order: list[str] = []
    authority = _Authority()

    async def persist(candidate, receipt) -> None:
        order.append("persist-start")
        await asyncio.sleep(0)
        order.append("persist-finish")
        await authority.persist(candidate, receipt)

    async def complete() -> TransportResult:
        order.append("complete")
        return _result()

    supervisor = WorkerAttemptSupervisor(
        authorize_attempt=authority.authorize,
        persist_submitted=persist,
        acp_execute=lambda _: TransportExecution(
            admission=SubmittedReceipt(
                receipt_id="rcp_worker",
                dispatch_key="dsp_" + "1" * 32,
                summary="accepted",
            ),
            completion_factory=complete,
        ),
        tmux_execute=lambda _: pytest.fail("tmux must not run"),
    )

    asyncio.run(supervisor.execute(_attempt(), _route()))

    assert order == ["persist-start", "persist-finish", "complete"]


def test_transport_execution_rejects_hot_task_or_future() -> None:
    async def scenario() -> None:
        hot = asyncio.create_task(asyncio.sleep(0, result=_result()))
        with pytest.raises((TypeError, ValueError), match="cold completion factory"):
            TransportExecution(
                admission=SubmittedReceipt(
                    receipt_id="rcp_worker",
                    dispatch_key="dsp_" + "1" * 32,
                    summary="accepted",
                ),
                completion_factory=hot,  # type: ignore[arg-type]
            )
        hot.cancel()
        await asyncio.gather(hot, return_exceptions=True)

    asyncio.run(scenario())


def test_persistence_failure_never_starts_completion_and_is_sanitized() -> None:
    authority = _Authority()
    completion_started = False

    async def persist(*_) -> None:
        raise RuntimeError("secret callback detail")

    async def complete() -> TransportResult:
        nonlocal completion_started
        completion_started = True
        return _result()

    supervisor = WorkerAttemptSupervisor(
        authorize_attempt=authority.authorize,
        persist_submitted=persist,
        acp_execute=lambda _: TransportExecution(
            admission=SubmittedReceipt(
                receipt_id="rcp_worker",
                dispatch_key="dsp_" + "1" * 32,
                summary="accepted",
            ),
            completion_factory=complete,
        ),
        tmux_execute=lambda _: pytest.fail("tmux must not run"),
    )

    with pytest.raises(WorkerAttemptError) as caught:
        asyncio.run(supervisor.execute(_attempt(), _route()))

    assert str(caught.value) == "Worker submitted receipt persistence failed"
    assert caught.value.__cause__ is None
    assert completion_started is False


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
        acp_execute=lambda _: calls.append("acp") or _execution(_result()),
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
        acp_execute=lambda _: calls.append("acp") or _execution(_result()),
    )

    with pytest.raises(WorkerAttemptError, match="Worker attempt authority drift"):
        asyncio.run(supervisor.execute(_attempt(), _route()))

    assert calls == []


def test_same_attempt_cannot_dispatch_twice_after_submitted_receipt() -> None:
    authority = _Authority()
    calls: list[str] = []
    supervisor = _supervisor(
        authority,
        acp_execute=lambda _: calls.append("acp") or _execution(_result()),
    )

    asyncio.run(supervisor.execute(_attempt(), _route()))
    with pytest.raises(WorkerAttemptError, match="Worker attempt is already submitted"):
        asyncio.run(supervisor.execute(_attempt(), _route()))

    assert calls == ["acp"]
    assert authority.persist_calls == 1


def test_human_owned_route_cannot_forge_automation_allowed() -> None:
    authority = _Authority()
    calls: list[str] = []
    supervisor = _supervisor(
        authority,
        acp_execute=lambda _: calls.append("acp") or _execution(_result()),
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
    supervisor = _supervisor(authority, acp_execute=lambda _: _execution(result))

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
        acp_execute=lambda _: _execution(_result(reply=reply)),
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
        _supervisor(authority, acp_execute=lambda _: _execution(result)).execute(
            _attempt(), _route()
        )
    )

    reply["summary"] = "mutated"
    object.__setattr__(artifact, "path", "secrets.txt")
    object.__setattr__(result, "trace_ids", ("mutated",))

    assert outcome.compact()["summary"] == "implementation finished"
    assert outcome.compact()["artifacts"][0]["path"] == "reports/result.md"
    assert outcome.compact()["trace_ids"] == ["trn_worker", "rep_worker"]


def test_transport_drift_fails_closed_before_any_dispatch() -> None:
    authority = _Authority()
    calls: list[str] = []
    supervisor = _supervisor(
        authority,
        acp_execute=lambda _: calls.append("acp") or _execution(_result()),
        tmux_execute=lambda _: calls.append("tmux")
        or _execution(_result(stop_reason="structured_reply")),
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
        acp_execute=lambda _: calls.append("acp")
        or _execution(RuntimeError("secret acp")),
        tmux_execute=lambda _: calls.append("tmux")
        or _execution(RuntimeError("secret tmux")),
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
        "status",
        "summary",
        "verification",
        "risks",
        "next_steps",
        "artifacts",
        "trace_ids",
    }


def test_acp_requires_formal_completed_stop_reason() -> None:
    authority = _Authority()
    supervisor = _supervisor(
        authority,
        acp_execute=lambda _: _execution(_result(stop_reason="max_tokens")),
    )

    with pytest.raises(WorkerAttemptError, match="ACP Worker did not complete"):
        asyncio.run(supervisor.execute(_attempt(), _route()))


def test_tmux_requires_a_validated_structured_reply() -> None:
    authority = _Authority()
    authority.current = _attempt("tmux")
    supervisor = _supervisor(
        authority,
        tmux_execute=lambda _: _execution(
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
