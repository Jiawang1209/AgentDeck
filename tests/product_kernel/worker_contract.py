from __future__ import annotations

from collections.abc import Callable

import pytest

from agentdeck.ports.worker import TaskRequest


WORKER_EVENT_KINDS = set(
    "started progress tool_started tool_completed permission_requested "
    "artifact_changed message completed failed cancelled".split()
)


def task_request() -> TaskRequest:
    return TaskRequest(
        agent_id="agt_1",
        task_id="tsk_1",
        attempt_id="att_1",
        instruction="Implement the frozen task.",
    )


async def assert_worker_contract(factory: Callable[[], object]) -> None:
    worker = factory()
    handle = await worker.start_task(task_request())
    events = []
    async for event in worker.stream_events(handle):
        events.append(event)
        if event.kind == "permission_requested":
            request_id = event.payload["permission_request_id"]
            assert type(request_id) is str and request_id.startswith("perm_") and request_id[5:]
            for invalid_id in ("", "perm_unknown"):
                with pytest.raises(ValueError):
                    await worker.respond_permission(
                        handle, permission_request_id=invalid_id, allowed=True,
                        reason="contract approval",
                    )
            with pytest.raises(ValueError) as raised:
                await worker.respond_permission(
                    handle, permission_request_id=request_id, allowed=True,
                    reason="token=RAW-CONTRACT-MARKER",
                )
            assert "RAW-CONTRACT-MARKER" not in str(raised.value)
            with pytest.raises(ValueError):
                await worker.respond_permission(
                    handle, permission_request_id=request_id, allowed=True,
                    reason="x" * (64 * 1024 + 1),
                )
            await worker.respond_permission(
                handle, permission_request_id=request_id, allowed=True,
                reason="contract approval",
            )

    assert events[0].kind == "started"
    assert events[-1].kind == "completed"
    assert [event.kind for event in events].count("permission_requested") == 1
    assert sum(event.kind in {"completed", "failed", "cancelled"} for event in events) == 1
    assert {event.kind for event in events} <= WORKER_EVENT_KINDS
    assert all(event.agent_id == "agt_1" for event in events)
    assert all(event.task_id == "tsk_1" for event in events)
    assert all(event.attempt_id == "att_1" for event in events)
    assert all(event.session_id == handle.session_id for event in events)
    assert all(event.transport == "acp" for event in events)
    assert [event.sequence for event in events] == sorted(
        {event.sequence for event in events}
    )

    result = await worker.collect_result(handle)
    assert result.status == "completed"
    assert result.agent_id == handle.agent_id
    assert result.task_id == handle.task_id
    assert result.attempt_id == handle.attempt_id
    assert result.session_id == handle.session_id
    with pytest.raises(ValueError):
        await worker.cancel_task(handle, reason="late cancellation")
    assert (await worker.collect_result(handle)).status == "completed"

    cancelled_worker = factory()
    cancelled_handle = await cancelled_worker.start_task(task_request())
    stream = cancelled_worker.stream_events(cancelled_handle).__aiter__()
    first = await anext(stream)
    assert first.kind == "started"
    with pytest.raises(ValueError) as raised:
        await cancelled_worker.cancel_task(
            cancelled_handle, reason="token=RAW-CANCEL-MARKER"
        )
    assert "RAW-CANCEL-MARKER" not in str(raised.value)
    await cancelled_worker.cancel_task(cancelled_handle, reason="contract cancellation")
    remaining = [event async for event in stream]
    assert [event.kind for event in remaining] == ["cancelled"]
    cancelled_events = [first, *remaining]
    assert all(event.session_id == cancelled_handle.session_id for event in cancelled_events)
    assert all(event.agent_id == cancelled_handle.agent_id for event in cancelled_events)
    assert all(event.task_id == cancelled_handle.task_id for event in cancelled_events)
    assert all(event.attempt_id == cancelled_handle.attempt_id for event in cancelled_events)
    assert all(event.transport == cancelled_handle.transport == "acp" for event in cancelled_events)
    sequences = [event.sequence for event in cancelled_events]
    assert all(left < right for left, right in zip(sequences, sequences[1:]))
    assert sum(event.kind in {"completed", "failed", "cancelled"} for event in cancelled_events) == 1
    result = await cancelled_worker.collect_result(cancelled_handle)
    assert result.status == "cancelled"
    assert result.session_id == cancelled_handle.session_id
    assert result.agent_id == cancelled_handle.agent_id
    assert result.task_id == cancelled_handle.task_id
    assert result.attempt_id == cancelled_handle.attempt_id
