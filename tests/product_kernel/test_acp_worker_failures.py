from __future__ import annotations

import asyncio

import pytest

from agentdeck.adapters.acp import ACPWorkerError
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
    ("permission_oversize", "acp_output_oversize", True, False),
    ("secret_output", "acp_sensitive_output_redacted", True, False),
    ("invalid_result", "acp_protocol_mismatch", True, False),
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


def test_cancel_failure_has_one_typed_authoritative_terminal() -> None:
    async def scenario() -> None:
        worker = worker_factory("cancel_failure")()
        handle = await worker.start_task(task_request())
        stream = worker.stream_events(handle).__aiter__()
        assert (await anext(stream)).kind == "started"

        with pytest.raises(ACPWorkerError) as raised:
            await worker.cancel_task(handle, reason="cancelled by test")

        assert raised.value.diagnostic.code == "acp_cancel_failed"
        assert raised.value.diagnostic.outcome_known is False
        assert raised.value.diagnostic.retryable is False
        assert "RAW-CANCEL-BODY" not in repr(raised.value.diagnostic) + str(raised.value)
        events = [event async for event in stream]
        assert [event.kind for event in events] == ["failed"]
        with pytest.raises(ACPWorkerError, match="acp_cancel_failed"):
            await worker.collect_result(handle)

    asyncio.run(scenario())
