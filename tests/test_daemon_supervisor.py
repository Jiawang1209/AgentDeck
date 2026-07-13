from __future__ import annotations

import asyncio

import pytest

from agentdeck.conversation.transports import WorkerRoute
from agentdeck.daemon.supervisor import (
    ArtifactEvidence,
    AttemptRecord,
    SubmittedReceipt,
    TransportExecution,
    TransportResult,
    WorkerAttemptError,
    WorkerAttemptSupervisor,
    supervisor_gate,
)


def _attempt(transport: str = "acp") -> AttemptRecord:
    return AttemptRecord(
        attempt_id="mat_0123456789ab",
        agent_id="planner",
        configured_transport=transport,
    )


def _route(transport: str = "acp") -> WorkerRoute:
    return WorkerRoute(
        agent_id="planner",
        configured_transport=transport,
        effective_transport=transport,
        ready=True,
        blocker=None,
        fallback={},
        live_mirror={},
        mirror_controls=(),
        ownership="agentdeck_owned",
        automation_allowed=True,
        prompt_blocker=None,
    )


def _result(*, validated: bool = True, stop_reason: str = "end_turn") -> TransportResult:
    return TransportResult(
        stop_reason=stop_reason,
        validated=validated,
        reply={
            "status": "completed",
            "summary": "implementation finished",
            "verification": "pytest passed",
            "risks": "none",
            "next_steps": "review",
            "private_reasoning": "must never persist",
        },
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
        admission=SubmittedReceipt(receipt_id="rcp_worker", summary="accepted"),
        completion=complete(),
    )


def test_transport_drift_fails_closed_before_any_dispatch() -> None:
    calls: list[str] = []
    drifted = _route("acp")
    drifted = WorkerRoute(**{**drifted.__dict__, "effective_transport": "tmux"})
    supervisor = WorkerAttemptSupervisor(
        persist_submitted=lambda *_: calls.append("persist"),
        acp_execute=lambda _: calls.append("acp") or _execution(_result()),
        tmux_execute=lambda _: calls.append("tmux") or _execution(_result()),
    )

    with pytest.raises(WorkerAttemptError, match="Worker transport drift"):
        asyncio.run(supervisor.execute(_attempt("acp"), drifted))

    assert calls == []


def test_acp_failure_never_calls_tmux_fallback() -> None:
    calls: list[str] = []
    supervisor = WorkerAttemptSupervisor(
        persist_submitted=lambda *_: calls.append("submitted"),
        acp_execute=lambda _: calls.append("acp") or _execution(RuntimeError("lost")),
        tmux_execute=lambda _: calls.append("tmux") or _execution(_result()),
    )

    with pytest.raises(WorkerAttemptError, match="ACP Worker failed"):
        asyncio.run(supervisor.execute(_attempt("acp"), _route("acp")))

    assert calls == ["acp", "submitted"]


def test_tmux_failure_never_calls_acp_fallback() -> None:
    calls: list[str] = []
    supervisor = WorkerAttemptSupervisor(
        persist_submitted=lambda *_: calls.append("submitted"),
        acp_execute=lambda _: calls.append("acp") or _execution(_result()),
        tmux_execute=lambda _: calls.append("tmux") or _execution(RuntimeError("lost")),
    )

    with pytest.raises(WorkerAttemptError, match="tmux Worker failed"):
        asyncio.run(supervisor.execute(_attempt("tmux"), _route("tmux")))

    assert calls == ["tmux", "submitted"]


def test_submitted_receipt_is_persisted_before_waiting_for_completion() -> None:
    order: list[str] = []

    async def complete() -> TransportResult:
        order.append("complete")
        return _result()

    supervisor = WorkerAttemptSupervisor(
        persist_submitted=lambda *_: order.append("persist"),
        acp_execute=lambda _: TransportExecution(
            admission=SubmittedReceipt(receipt_id="rcp_worker", summary="accepted"),
            completion=complete(),
        ),
        tmux_execute=lambda _: pytest.fail("tmux must not run"),
    )

    asyncio.run(supervisor.execute(_attempt(), _route()))

    assert order == ["persist", "complete"]


def test_acp_and_validated_tmux_results_map_to_same_compact_outcome() -> None:
    def run(transport: str, result: TransportResult):
        supervisor = WorkerAttemptSupervisor(
            persist_submitted=lambda *_: None,
            acp_execute=lambda _: _execution(result),
            tmux_execute=lambda _: _execution(result),
        )
        return asyncio.run(supervisor.execute(_attempt(transport), _route(transport)))

    acp = run("acp", _result(stop_reason="end_turn"))
    tmux = run("tmux", _result(stop_reason="structured_reply"))

    assert acp.compact() == tmux.compact()
    assert acp.compact() == {
        "status": "completed",
        "summary": "implementation finished",
        "verification": "pytest passed",
        "risks": "none",
        "next_steps": "review",
        "artifacts": [
            {"path": "reports/result.md", "content_hash": "sha256:" + "a" * 64}
        ],
        "trace_ids": ["trn_worker", "rep_worker"],
    }


def test_acp_requires_formal_completed_stop_reason() -> None:
    supervisor = WorkerAttemptSupervisor(
        persist_submitted=lambda *_: None,
        acp_execute=lambda _: _execution(_result(stop_reason="max_tokens")),
        tmux_execute=lambda _: pytest.fail("tmux must not run"),
    )

    with pytest.raises(WorkerAttemptError, match="ACP Worker did not complete"):
        asyncio.run(supervisor.execute(_attempt(), _route()))


def test_tmux_requires_a_validated_structured_reply() -> None:
    supervisor = WorkerAttemptSupervisor(
        persist_submitted=lambda *_: None,
        acp_execute=lambda _: pytest.fail("ACP must not run"),
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
