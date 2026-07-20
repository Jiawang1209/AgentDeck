from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from agentdeck.application.approval_service import ApprovalContext, ApprovalService
from agentdeck.kernel.permissions import Effect, PermissionProfile, PermissionScope
from agentdeck.ports.approval import ReviewerVerdict
from agentdeck.ports.worker import WorkerEvent, WorkerHandle
from product_kernel.fakes import FrozenClock


NOW = datetime(2026, 7, 19, 2, 0, tzinfo=timezone.utc)


class FakeTransaction:
    def __init__(self, store: "FakeStore") -> None:
        self.store = store
        self.duplicate_result = None

    def save_aggregate(self, kind, identity, snapshot) -> None:
        self.store.timeline.append(("aggregate", snapshot["state"]))
        self.store.aggregates[(kind, identity)] = dict(snapshot)

    def append_event(self, event) -> None:
        self.store.timeline.append(("event", event["kind"]))
        self.store.events.append(event)


class FakeStore:
    def __init__(self) -> None:
        self.commands = {}
        self.aggregates = {}
        self.events = []
        self.timeline = []

    def execute_once(self, command_id, command_kind, callback):
        key = (command_id, command_kind)
        if key not in self.commands:
            self.commands[key] = callback(FakeTransaction(self))
        return dict(self.commands[key])

    def lookup_command(self, command_id, command_kind=None):
        matches = [value for (identity, kind), value in self.commands.items()
                   if identity == command_id and (command_kind is None or kind == command_kind)]
        return None if not matches else dict(matches[0])


class FakeReviewer:
    def __init__(self, reviewer_id: str, *, allowed: bool, fails: bool = False) -> None:
        self.reviewer_id = reviewer_id
        self.allowed = allowed
        self.fails = fails
        self.calls = []

    async def review(self, request):
        self.calls.append(request)
        if self.fails:
            raise RuntimeError("RAW-REVIEWER-MARKER")
        return ReviewerVerdict(allowed=self.allowed, reason="reviewed")


class DriftingReviewer(FakeReviewer):
    def __init__(self) -> None:
        self.allowed = True
        self.fails = False
        self.calls = []
        self.identity_reads = 0

    @property
    def reviewer_id(self) -> str:
        self.identity_reads += 1
        return "agt_reviewer" if self.identity_reads == 1 else "agt_executor"


class HostileIdentityReviewer(FakeReviewer):
    @property
    def reviewer_id(self) -> str:
        raise RuntimeError("RAW-REVIEWER-IDENTITY-MARKER")

    @reviewer_id.setter
    def reviewer_id(self, value: str) -> None:
        pass


class PermissionWorker:
    def __init__(self, store: FakeStore) -> None:
        self.store = store
        self.responses = []

    async def respond_permission(self, handle, *, permission_request_id, allowed, reason):
        assert self.store.timeline[-2:] == [
            ("aggregate", "approved" if allowed else "denied"),
            ("event", "approval_decided"),
        ]
        self.responses.append((permission_request_id, allowed, reason))


class FailingPermissionWorker(PermissionWorker):
    async def respond_permission(self, *args, **kwargs):
        raise RuntimeError("RAW-WORKER-RESPONSE-MARKER")


def handle() -> WorkerHandle:
    return WorkerHandle("ses_1", "agt_executor", "tsk_1", "att_1")


def event(
    effect: Effect | str, request_id: str = "perm_1", risk: str = "bounded risk"
) -> WorkerEvent:
    return WorkerEvent(
        event_id="evt_1", session_id="ses_1", agent_id="agt_executor",
        task_id="tsk_1", attempt_id="att_1", transport="acp", sequence=2,
        kind="permission_requested", timestamp=NOW.isoformat(),
        payload={
            "permission_request_id": request_id, "tool_call_id": "call_1",
            "option_count": 2,
            "effect": effect.value if type(effect) is Effect else effect,
            "risk": risk,
        },
    )


def context(profile: PermissionProfile) -> ApprovalContext:
    return ApprovalContext(
        mission_id="msn_1", mission_version=1,
        permission_scope=PermissionScope.for_profile(profile),
        scope_hash="a" * 64,
    )


def test_routine_project_permission_is_durable_before_exact_response() -> None:
    async def scenario() -> None:
        store = FakeStore()
        worker = PermissionWorker(store)
        service = ApprovalService(store=store, clock=FrozenClock(NOW))

        record = await service.handle_permission(
            worker, handle(), event(Effect.WRITE_PROJECT),
            context(PermissionProfile.APPROVE_FOR_ME),
        )

        assert record.state == "approved"
        assert record.request.permission_request_id == "perm_1"
        assert record.request.attempt_id == "att_1"
        assert record.request.agent_id == "agt_executor"
        assert record.decision.reviewer_id == "agentdeck"
        assert worker.responses == [("perm_1", True, "routine_project_effect")]
        assert store.timeline == [
            ("aggregate", "pending"), ("event", "approval_requested"),
            ("aggregate", "approved"), ("event", "approval_decided"),
        ]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("profile", "effect", "reviewer_kind"),
    (
        (PermissionProfile.ASK_FOR_APPROVAL, Effect.WRITE_PROJECT, "human"),
        (PermissionProfile.APPROVE_FOR_ME, Effect.NETWORK, "independent"),
    ),
)
def test_required_reviewer_is_called_only_after_pending_request_is_durable(
    profile: PermissionProfile, effect: Effect, reviewer_kind: str,
) -> None:
    async def scenario() -> None:
        store = FakeStore()
        reviewer = FakeReviewer(
            "human" if reviewer_kind == "human" else "agt_reviewer", allowed=True
        )
        service = ApprovalService(
            store=store, clock=FrozenClock(NOW),
            human_reviewer=reviewer if reviewer_kind == "human" else None,
            independent_reviewer=reviewer if reviewer_kind == "independent" else None,
        )
        worker = PermissionWorker(store)

        record = await service.handle_permission(worker, handle(), event(effect), context(profile))

        assert len(reviewer.calls) == 1
        assert record.state == "approved"
        assert record.decision.reviewer_id == reviewer.reviewer_id
        assert store.timeline[0:2] == [
            ("aggregate", "pending"), ("event", "approval_requested"),
        ]

    asyncio.run(scenario())


def test_executor_cannot_act_as_independent_approval_reviewer() -> None:
    async def scenario() -> None:
        store = FakeStore()
        reviewer = FakeReviewer("agt_executor", allowed=True)
        worker = PermissionWorker(store)
        service = ApprovalService(
            store=store, clock=FrozenClock(NOW), independent_reviewer=reviewer
        )

        record = await service.handle_permission(
            worker, handle(), event(Effect.NETWORK),
            context(PermissionProfile.APPROVE_FOR_ME),
        )

        assert reviewer.calls == []
        assert record.state == "denied"
        assert record.diagnostic_code == "approval_reviewer_not_independent"
        assert worker.responses == [
            ("perm_1", False, "approval_reviewer_not_independent")
        ]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("profile", "reviewer_id", "reviewer_slot"),
    (
        (PermissionProfile.ASK_FOR_APPROVAL, "agt_reviewer", "human"),
        (PermissionProfile.APPROVE_FOR_ME, "human", "independent"),
    ),
)
def test_miswired_reviewer_identity_fails_closed_without_invocation(
    profile: PermissionProfile, reviewer_id: str, reviewer_slot: str,
) -> None:
    async def scenario() -> None:
        store = FakeStore()
        reviewer = FakeReviewer(reviewer_id, allowed=True)
        kwargs = {f"{reviewer_slot}_reviewer": reviewer}
        service = ApprovalService(store=store, clock=FrozenClock(NOW), **kwargs)
        worker = PermissionWorker(store)
        effect = Effect.WRITE_PROJECT if reviewer_slot == "human" else Effect.NETWORK

        record = await service.handle_permission(
            worker, handle(), event(effect), context(profile)
        )

        assert reviewer.calls == []
        assert record.state == "denied"
        assert record.diagnostic_code == "approval_reviewer_invalid"

    asyncio.run(scenario())


def test_independent_reviewer_identity_is_frozen_before_review() -> None:
    async def scenario() -> None:
        store = FakeStore()
        reviewer = DriftingReviewer()
        service = ApprovalService(
            store=store, clock=FrozenClock(NOW), independent_reviewer=reviewer
        )

        record = await service.handle_permission(
            PermissionWorker(store), handle(), event(Effect.NETWORK),
            context(PermissionProfile.APPROVE_FOR_ME),
        )

        assert reviewer.identity_reads == 1
        assert len(reviewer.calls) == 1
        assert record.state == "approved"
        assert record.decision.reviewer_id == "agt_reviewer"

    asyncio.run(scenario())


def test_hostile_reviewer_identity_is_content_free_and_denied() -> None:
    async def scenario() -> None:
        store = FakeStore()
        reviewer = HostileIdentityReviewer("ignored", allowed=True)
        service = ApprovalService(
            store=store, clock=FrozenClock(NOW), independent_reviewer=reviewer
        )

        record = await service.handle_permission(
            PermissionWorker(store), handle(), event(Effect.NETWORK),
            context(PermissionProfile.APPROVE_FOR_ME),
        )

        assert reviewer.calls == []
        assert record.diagnostic_code == "approval_reviewer_invalid"
        assert "RAW-REVIEWER-IDENTITY-MARKER" not in repr(store.commands)
        assert "RAW-REVIEWER-IDENTITY-MARKER" not in repr(store.events)

    asyncio.run(scenario())


def test_full_access_is_auto_audited_and_narrowed_scope_fails_closed() -> None:
    async def scenario() -> None:
        for scope, expected in (
            (PermissionScope.for_profile(PermissionProfile.FULL_ACCESS), True),
            (PermissionScope.for_profile().narrow({Effect.READ}), False),
        ):
            store = FakeStore()
            worker = PermissionWorker(store)
            service = ApprovalService(store=store, clock=FrozenClock(NOW))
            record = await service.handle_permission(
                worker, handle(), event(Effect.PUBLISH),
                ApprovalContext("msn_1", 1, scope, "a" * 64),
            )
            assert record.decision.allowed is expected
            assert store.timeline[0] == ("aggregate", "pending")
            assert store.timeline[-1] == ("event", "approval_decided")
            assert worker.responses[0][1] is expected

    asyncio.run(scenario())


def test_reviewer_failure_is_content_free_and_denied() -> None:
    async def scenario() -> None:
        store = FakeStore()
        reviewer = FakeReviewer("human", allowed=True, fails=True)
        worker = PermissionWorker(store)
        service = ApprovalService(
            store=store, clock=FrozenClock(NOW), human_reviewer=reviewer
        )
        record = await service.handle_permission(
            worker, handle(), event(Effect.WRITE_PROJECT),
            context(PermissionProfile.ASK_FOR_APPROVAL),
        )
        assert record.state == "denied"
        assert record.diagnostic_code == "approval_reviewer_failed"
        assert "RAW-REVIEWER-MARKER" not in repr(store.commands)
        assert "RAW-REVIEWER-MARKER" not in repr(store.events)

    asyncio.run(scenario())


def test_unclassified_permission_effect_fails_before_persistence_or_response() -> None:
    async def scenario() -> None:
        store = FakeStore()
        worker = PermissionWorker(store)
        service = ApprovalService(store=store, clock=FrozenClock(NOW))

        with pytest.raises(ValueError, match="permission event facts are invalid") as raised:
            await service.handle_permission(
                worker, handle(), event("unclassified"),
                context(PermissionProfile.FULL_ACCESS),
            )
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None
        assert store.commands == {}
        assert worker.responses == []

    asyncio.run(scenario())


def test_worker_response_failure_is_content_free_after_durable_decision() -> None:
    async def scenario() -> None:
        store = FakeStore()
        worker = FailingPermissionWorker(store)
        service = ApprovalService(store=store, clock=FrozenClock(NOW))
        with pytest.raises(RuntimeError, match="^approval_response_failed$") as raised:
            await service.handle_permission(
                worker, handle(), event(Effect.READ),
                context(PermissionProfile.APPROVE_FOR_ME),
            )
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None
        assert store.timeline[-2:] == [
            ("aggregate", "approved"), ("event", "approval_decided"),
        ]
        assert "RAW-WORKER-RESPONSE-MARKER" not in repr(store.commands)

    asyncio.run(scenario())


def test_fail_safe_unknown_tool_risk_is_denied_even_under_full_access() -> None:
    async def scenario() -> None:
        store = FakeStore()
        worker = PermissionWorker(store)
        service = ApprovalService(store=store, clock=FrozenClock(NOW))
        record = await service.handle_permission(
            worker, handle(),
            event(Effect.DESTRUCTIVE, risk="unclassified_tool_effect"),
            context(PermissionProfile.FULL_ACCESS),
        )
        assert record.state == "denied"
        assert record.diagnostic_code == "permission_effect_unclassified"
        assert worker.responses == [
            ("perm_1", False, "permission_effect_unclassified")
        ]

    asyncio.run(scenario())


def test_decision_replay_does_not_call_reviewer_twice() -> None:
    async def scenario() -> None:
        store = FakeStore()
        reviewer = FakeReviewer("human", allowed=True)
        clock = FrozenClock(NOW)
        service = ApprovalService(store=store, clock=clock, human_reviewer=reviewer)
        first = PermissionWorker(store)
        second = PermissionWorker(store)

        one = await service.handle_permission(
            first, handle(), event(Effect.WRITE_PROJECT),
            context(PermissionProfile.ASK_FOR_APPROVAL),
        )
        clock.value = NOW + timedelta(hours=1)
        two = await service.handle_permission(
            second, handle(), event(Effect.WRITE_PROJECT),
            context(PermissionProfile.ASK_FOR_APPROVAL),
        )

        assert one == two
        assert len(reviewer.calls) == 1
        assert len(store.commands) == 2
        assert second.responses == [("perm_1", True, "reviewed")]

    asyncio.run(scenario())


def test_stream_self_cancellation_does_not_send_worker_cancel() -> None:
    class SelfCancelledWorker:
        def __init__(self) -> None:
            self.cancel_calls = 0

        async def _events(self):
            raise asyncio.CancelledError("worker stream self-cancelled")
            if False:
                yield

        def stream_events(self, _handle):
            return self._events()

        async def cancel_task(self, *_args, **_kwargs):
            self.cancel_calls += 1

    async def scenario() -> None:
        worker = SelfCancelledWorker()
        service = ApprovalService(store=FakeStore(), clock=FrozenClock(NOW))

        with pytest.raises(asyncio.CancelledError, match="self-cancelled"):
            await service.bridge_attempt(
                worker, handle(), context(PermissionProfile.APPROVE_FOR_ME)
            )

        assert asyncio.current_task().cancelling() == 0
        assert worker.cancel_calls == 0

    asyncio.run(scenario())
