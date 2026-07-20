from __future__ import annotations

import asyncio

import pytest

from agentdeck.adapters.acp import ACPWorkerError
from agentdeck.ports.worker import WorkerCancellationError
from product_kernel.test_acp_worker_contract import worker_factory
from product_kernel.worker_contract import task_request


async def run_failure(scenario: str) -> ACPWorkerError:
    worker = worker_factory(scenario)()
    try:
        handle = await worker.start_task(task_request())
    except ACPWorkerError as error:
        return error
    events = []
    async for event in worker.stream_events(handle):
        events.append(event)
        if event.kind == "permission_requested":
            await worker.respond_permission(
                handle,
                permission_request_id=event.payload["permission_request_id"],
                allowed=True,
                reason="approved",
            )
    assert events[-1].kind == "failed"
    assert sum(
        event.kind in {"completed", "failed", "cancelled"} for event in events
    ) == 1
    assert [event.sequence for event in events] == sorted(
        {event.sequence for event in events}
    )
    with pytest.raises(ACPWorkerError) as raised:
        await worker.collect_result(handle)
    return raised.value


def _worker_with_cancel_error(error: BaseException):
    worker = worker_factory("cancel_race")()
    agent = worker._agent

    async def cancel(session_id: str, **kwargs) -> None:
        agent.cancelled = True
        agent.cancel_gate.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        raise error

    agent.cancel = cancel
    return worker


def test_worker_cancellation_error_is_closed_and_content_free() -> None:
    assert WorkerCancellationError.ALLOWED_CODES == frozenset({
        "cancel_rejected", "cancel_timeout", "transport_disconnected",
    })
    error = WorkerCancellationError(
        code="transport_disconnected", outcome_known=False,
    )
    assert error.code == "transport_disconnected"
    assert error.outcome_known is False
    assert error.args == ("transport_disconnected", False)
    assert set(error.__dict__) == {"code", "outcome_known"}
    assert error.__cause__ is None
    assert error.__context__ is None
    assert str(error) == "('transport_disconnected', False)"
    assert repr(error) == (
        "WorkerCancellationError('transport_disconnected', False)"
    )


@pytest.mark.parametrize(
    ("code", "outcome_known", "exception"),
    [
        ("unknown", False, ValueError),
        ("cancel_timeout", 0, TypeError),
        ("cancel_timeout", 1, TypeError),
    ],
)
def test_worker_cancellation_error_rejects_open_values(
    code: str, outcome_known: object, exception: type[Exception],
) -> None:
    with pytest.raises(exception):
        WorkerCancellationError(code=code, outcome_known=outcome_known)  # type: ignore[arg-type]


@pytest.mark.parametrize(("scenario", "code", "outcome_known", "retryable"), [
    ("initialization_failure", "acp_initialization_failed", True, False),
    ("session_failure", "acp_session_failed", True, False),
    ("protocol_mismatch", "acp_protocol_mismatch", True, False),
    ("disconnect_before_work", "acp_disconnected_before_effect", True, True),
    ("disconnect_during_effect", "worker_outcome_unknown", False, False),
    ("disconnect_during_read", "acp_disconnected_before_effect", True, True),
    ("disconnect_during_search", "acp_disconnected_before_effect", True, True),
    ("disconnect_during_think", "acp_disconnected_before_effect", True, True),
    ("disconnect_during_other", "worker_outcome_unknown", False, False),
    ("disconnect_during_none", "worker_outcome_unknown", False, False),
    ("disconnect_after_effect", "worker_outcome_unknown", False, False),
    ("duplicate_event", "acp_duplicate_event", True, False),
    ("out_of_order", "acp_sequence_violation", True, False),
    ("oversize", "acp_output_oversize", True, False),
    ("total_oversize", "acp_output_oversize", True, False),
    ("permission_oversize", "acp_output_oversize", False, False),
    ("secret_output", "acp_sensitive_output_redacted", True, False),
    ("invalid_result", "acp_protocol_mismatch", False, False),
    ("effect_sensitive", "acp_sensitive_output_redacted", False, False),
    ("read_sensitive", "acp_sensitive_output_redacted", True, False),
    ("effect_oversize", "acp_output_oversize", False, False),
    ("read_oversize", "acp_output_oversize", True, False),
    ("effect_sequence", "acp_sequence_violation", False, False),
    ("read_sequence", "acp_sequence_violation", True, False),
    ("effect_invalid_result", "acp_protocol_mismatch", False, False),
    ("read_invalid_result", "acp_protocol_mismatch", True, False),
    ("effect_progress_sensitive", "acp_sensitive_output_redacted", False, False),
    ("read_progress_sensitive", "acp_sensitive_output_redacted", True, False),
    ("effect_progress_oversize", "acp_output_oversize", False, False),
    ("read_progress_oversize", "acp_output_oversize", True, False),
    ("effect_progress_sequence", "acp_sequence_violation", False, False),
    ("read_progress_sequence", "acp_sequence_violation", True, False),
    ("effect_progress_invalid_result", "acp_protocol_mismatch", False, False),
    ("read_progress_invalid_result", "acp_protocol_mismatch", True, False),
])
def test_adversarial_acp_scenarios_are_typed(
    scenario: str, code: str, outcome_known: bool, retryable: bool,
) -> None:
    error = asyncio.run(run_failure(scenario))
    assert error.diagnostic.code == code
    assert error.diagnostic.outcome_known is outcome_known
    assert error.diagnostic.retryable is retryable
    rendered = repr(error.diagnostic) + str(error)
    for raw in (
        "secret-token", "RAW-DISCONNECT-BODY", "RAW-INITIALIZATION-BODY",
        "RAW-SESSION-BODY", "RAW-RESULT",
    ):
        assert raw not in rendered


def test_unknown_handle_and_permission_never_cross_lineage() -> None:
    async def scenario() -> None:
        worker = worker_factory()()
        handle = await worker.start_task(task_request())
        stream = worker.stream_events(handle).__aiter__()
        assert (await anext(stream)).kind == "started"
        permission = await anext(stream)
        assert permission.kind == "tool_started"
        permission = await anext(stream)
        assert permission.kind == "permission_requested"
        with pytest.raises(ValueError, match="not pending"):
            await worker.respond_permission(
                handle, permission_request_id="perm_unknown", allowed=True,
                reason="approved",
            )
        await worker.cancel_task(handle, reason="cancelled by test")

    asyncio.run(scenario())


def test_known_agent_refusal_maps_to_one_failed_terminal_result() -> None:
    async def scenario() -> None:
        worker = worker_factory("refusal")()
        handle = await worker.start_task(task_request())
        events = []
        async for event in worker.stream_events(handle):
            events.append(event)
            if event.kind == "permission_requested":
                await worker.respond_permission(
                    handle,
                    permission_request_id=event.payload["permission_request_id"],
                    allowed=True,
                    reason="approved",
                )
        assert [event.kind for event in events].count("failed") == 1
        result = await worker.collect_result(handle)
        assert result.status == "failed"
        assert result.payload["stop_reason"] == "refusal"

    asyncio.run(scenario())


def test_cancel_intent_wins_prompt_completion_race() -> None:
    async def scenario() -> None:
        worker = worker_factory("cancel_race")()
        handle = await worker.start_task(task_request())
        stream = worker.stream_events(handle).__aiter__()
        assert (await anext(stream)).kind == "started"

        await worker.cancel_task(handle, reason="cancelled by test")

        events = [event async for event in stream]
        assert [event.kind for event in events] == ["cancelled"]
        assert (await worker.collect_result(handle)).status == "cancelled"

    asyncio.run(scenario())


def test_acp_worker_preserves_exact_cancellation_failure_and_closes_stream() -> None:
    async def scenario() -> None:
        expected = WorkerCancellationError(
            code="cancel_timeout", outcome_known=False,
        )
        worker = _worker_with_cancel_error(expected)
        handle = await worker.start_task(task_request())
        stream = worker.stream_events(handle).__aiter__()
        assert (await anext(stream)).kind == "started"

        with pytest.raises(WorkerCancellationError) as raised:
            await worker.cancel_task(handle, reason="cancelled by test")

        events = [event async for event in stream]
        assert (raised.value.code, raised.value.outcome_known) == (
            "cancel_timeout", False,
        )
        assert events == []
        assert worker._run is not None and worker._run.queue.empty()
        assert worker._run.error is raised.value
        assert "raw-acp-session" not in repr(worker._run.error.__dict__)
        with pytest.raises(WorkerCancellationError) as collected:
            await worker.collect_result(handle)
        assert collected.value is raised.value

    asyncio.run(scenario())


def test_acp_worker_sanitizes_unexpected_cancel_exception_without_event() -> None:
    async def scenario() -> None:
        hostile = "ghp_" + ("B" * 36)
        worker = _worker_with_cancel_error(RuntimeError(hostile))
        handle = await worker.start_task(task_request())
        stream = worker.stream_events(handle).__aiter__()
        assert (await anext(stream)).kind == "started"

        with pytest.raises(WorkerCancellationError) as raised:
            await worker.cancel_task(handle, reason="cancelled by test")

        events = [event async for event in stream]
        assert (raised.value.code, raised.value.outcome_known) == (
            "transport_disconnected", False,
        )
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None
        assert hostile not in repr(raised.value) + repr(raised.value.__dict__)
        assert events == []
        assert worker._run is not None and worker._run.queue.empty()

    asyncio.run(scenario())


def test_end_turn_cannot_complete_while_permission_is_pending() -> None:
    async def scenario() -> None:
        worker = worker_factory("terminal_with_pending_permission")()
        agent = worker._agent
        handle = await worker.start_task(task_request())
        events = [event async for event in worker.stream_events(handle)]

        terminals = [
            event for event in events
            if event.kind in {"completed", "failed", "cancelled"}
        ]
        assert [event.kind for event in terminals] == ["failed"]
        assert terminals[0].payload == {
            "diagnostic_code": "acp_sequence_violation",
            "outcome_known": False,
        }
        assert agent.cancelled is True
        assert agent.permission_outcomes == []
        assert agent.pending_permission_task is not None
        await asyncio.sleep(0)
        assert agent.pending_permission_task.cancelled()
        with pytest.raises(ACPWorkerError) as raised:
            await worker.collect_result(handle)
        assert raised.value.diagnostic.code == "acp_sequence_violation"
        assert raised.value.diagnostic.outcome_known is False
        assert raised.value.diagnostic.retryable is False
        rendered = repr(raised.value.diagnostic) + str(raised.value)
        assert "RAW-PENDING-PERMISSION-BODY" not in rendered
        with pytest.raises(ValueError, match="terminal"):
            await worker.respond_permission(
                handle, permission_request_id="perm_1", allowed=True,
                reason="late approval must not cross terminal authority",
            )

    asyncio.run(scenario())
