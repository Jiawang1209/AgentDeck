from __future__ import annotations

import asyncio
import json

import pytest

import agentdeck.adapters.sqlite as sqlite_module
from agentdeck.application.execution_runtime import ExecutionBindingError

from .test_product_exit_acp_integration import (
    ExitHarness, InProcessWorker, async_test,
)


def _rendered(result) -> str:
    assert result.diagnostic is not None
    return json.dumps(result.diagnostic.__dict__, default=list, sort_keys=True)


@async_test
async def test_lookup_failure_after_cancel_replays_stable_pending_without_io(
    tmp_path, monkeypatch,
):
    harness = ExitHarness(tmp_path)
    try:
        harness.bind()
        original_append = sqlite_module._SQLiteCommandTransaction.append_event

        def fail_callback(transaction, event):
            raise RuntimeError("raw-callback-marker")

        monkeypatch.setattr(
            sqlite_module._SQLiteCommandTransaction, "append_event", fail_callback
        )
        first = await harness.coordinator.confirm(
            harness.request.request_id, harness.request.attempt_hash
        )
        monkeypatch.setattr(
            sqlite_module._SQLiteCommandTransaction, "append_event", original_append
        )
        original_lookup = harness.store.lookup_command

        def fail_lookup(command_id, command_kind=None):
            if command_kind == "confirm_product_exit":
                raise RuntimeError("raw-lookup-marker")
            return original_lookup(command_id, command_kind)

        monkeypatch.setattr(harness.store, "lookup_command", fail_lookup)
        second = await harness.coordinator.confirm(
            harness.request.request_id, harness.request.attempt_hash
        )
        assert first.diagnostic == second.diagnostic
        assert first.diagnostic.code == "exit_persistence_pending"
        assert harness.worker.cancel_count == 1
        assert harness.runtime.status().state == "fenced_pending"
        rendered = _rendered(second).lower()
        assert "raw-lookup-marker" not in rendered
        assert "raw-callback-marker" not in rendered
    finally:
        harness.close()


@async_test
async def test_lookup_failure_without_matching_fence_is_content_free(
    tmp_path, monkeypatch,
):
    harness = ExitHarness(tmp_path)
    try:
        harness.bind()

        def fail_lookup(command_id, command_kind=None):
            raise RuntimeError("raw-lookup-marker")

        monkeypatch.setattr(harness.store, "lookup_command", fail_lookup)
        result = await harness.coordinator.confirm(
            harness.request.request_id, harness.request.attempt_hash
        )
        assert result.diagnostic.code == "exit_authority_invalid"
        assert harness.worker.cancel_count == 0
        assert "raw-lookup-marker" not in _rendered(result).lower()
    finally:
        harness.close()


@pytest.mark.parametrize("boundary", ["callback", "precommit", "postcommit"])
@async_test
async def test_persistence_failure_boundaries_retry_without_second_cancel(
    tmp_path, monkeypatch, boundary,
):
    harness = ExitHarness(tmp_path)
    try:
        harness.bind()
        before = harness.database_facts()
        if boundary == "callback":
            original = sqlite_module._SQLiteCommandTransaction.append_event

            def fail(transaction, event):
                raise RuntimeError("raw-callback-marker")

            target, attribute = sqlite_module._SQLiteCommandTransaction, "append_event"
        elif boundary == "precommit":
            original = sqlite_module._require_state_identity

            def fail(state, identity):
                if harness.store._writer.in_transaction:
                    raise RuntimeError("raw-precommit-marker")
                return original(state, identity)

            target, attribute = sqlite_module, "_require_state_identity"
        else:
            original = sqlite_module._after_command_commit

            def fail(path):
                raise RuntimeError("raw-postcommit-marker")

            target, attribute = sqlite_module, "_after_command_commit"
        monkeypatch.setattr(target, attribute, fail)
        first = await harness.coordinator.confirm(
            harness.request.request_id, harness.request.attempt_hash
        )
        assert first.diagnostic.code == "exit_persistence_pending"
        assert first.diagnostic.outcome_known is False
        assert "raw-" not in _rendered(first).lower()
        assert harness.worker.cancel_count == 1
        if boundary == "postcommit":
            assert harness.session_state() == "paused"
            assert harness.attempt_state() == "interrupted"
        else:
            assert harness.database_facts() == before
        monkeypatch.setattr(target, attribute, original)
        second = await harness.coordinator.confirm(
            harness.request.request_id, harness.request.attempt_hash
        )
        assert second.mode == "project_paused" and second.should_exit is True
        assert harness.worker.cancel_count == 1
    finally:
        harness.close()


class BlockingCancellationWorker(InProcessWorker):
    def __init__(self) -> None:
        super().__init__()
        self.cancel_entered = asyncio.Event()
        self.cancel_blocker = asyncio.Event()

    async def cancel_task(self, handle, *, reason):
        self.cancel_calls.append((handle, reason))
        self.cancel_entered.set()
        await self.cancel_blocker.wait()


@async_test
async def test_real_caller_cancellation_closes_unknown_and_retries_once(tmp_path):
    harness = ExitHarness(tmp_path)
    try:
        harness.worker = BlockingCancellationWorker()
        harness.bind()
        task = asyncio.create_task(harness.coordinator.confirm(
            harness.request.request_id, harness.request.attempt_hash
        ))
        await harness.worker.cancel_entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        replay = await harness.coordinator.confirm(
            harness.request.request_id, harness.request.attempt_hash
        )
        assert replay.diagnostic.code == "transport_disconnected"
        assert replay.diagnostic.outcome_known is False
        assert harness.worker.cancel_count == 1
        assert "cancelled" not in _rendered(replay).lower()
    finally:
        harness.close()


@async_test
async def test_durable_success_replay_retries_only_runtime_settlement(
    tmp_path, monkeypatch,
):
    harness = ExitHarness(tmp_path)
    try:
        harness.bind()
        original = harness.runtime.settle_exit_cancellation
        monkeypatch.setattr(
            harness.runtime, "settle_exit_cancellation",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                ExecutionBindingError("synthetic settlement drift")
            ),
        )
        first = await harness.coordinator.confirm(
            harness.request.request_id, harness.request.attempt_hash
        )
        assert first.diagnostic.code == "exit_runtime_convergence_failed"
        monkeypatch.setattr(
            harness.runtime, "settle_exit_cancellation", original
        )
        second = await harness.coordinator.confirm(
            harness.request.request_id, harness.request.attempt_hash
        )
        assert second.mode == "project_paused" and second.should_exit is True
        assert harness.worker.cancel_count == 1
    finally:
        harness.close()
