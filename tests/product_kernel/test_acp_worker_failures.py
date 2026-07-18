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


@pytest.mark.parametrize(("scenario", "code", "outcome_known"), [
    ("protocol_mismatch", "acp_protocol_mismatch", True),
    ("disconnect_before_work", "acp_disconnected_before_effect", True),
    ("disconnect_after_effect", "worker_outcome_unknown", False),
    ("duplicate_event", "acp_duplicate_event", True),
    ("out_of_order", "acp_sequence_violation", True),
    ("oversize", "acp_output_oversize", True),
    ("total_oversize", "acp_output_oversize", True),
    ("permission_oversize", "acp_output_oversize", True),
    ("secret_output", "acp_sensitive_output_redacted", True),
    ("invalid_result", "acp_protocol_mismatch", True),
])
def test_adversarial_acp_scenarios_are_typed(
    scenario: str, code: str, outcome_known: bool,
) -> None:
    error = asyncio.run(run_failure(scenario))
    assert error.diagnostic.code == code
    assert error.diagnostic.outcome_known is outcome_known
    rendered = repr(error.diagnostic) + str(error)
    assert "secret-token" not in rendered
    assert "RAW-DISCONNECT-BODY" not in rendered
    assert "RAW-RESULT" not in rendered


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
