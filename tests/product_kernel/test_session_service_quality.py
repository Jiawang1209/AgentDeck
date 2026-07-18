from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import datetime, timedelta, timezone, tzinfo
from hashlib import sha256
import json
from pathlib import Path
import traceback
from types import MappingProxyType

import pytest

from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.application.session_service import SessionService, SessionServiceError

from .fakes import FrozenClock
from .test_session_service import AVAILABLE_LEADERS, NOW, _service


@pytest.mark.parametrize("goal", [
    "OPENAI_API_KEY=sk-sensitive-value",
    "Authorization: Bearer sensitive-value",
    "Bearer sensitive-value",
    "-----BEGIN PRIVATE KEY-----\nsensitive-value",
    "https://example.test/callback?access_token=sensitive-value",
    "API key: sensitive-value",
    "[password=sensitive-value]",
    "credentials=sensitive-value",
    "accessToken=sensitive-value",
    "refreshToken=sensitive-value",
    "idToken=sensitive-value",
    "bearerToken=sensitive-value",
    "sk-proj-RAWSESSIONTOKEN123456789",
    "sk-ant-RAWSESSIONTOKEN123456789",
    "ghp_RAWSESSIONTOKEN12345678901234567890",
    "github_pat_RAWSESSIONTOKEN12345678901234567890",
    "AKIARAWSESSIONTOKEN1",
    "AIzaRAWSESSIONTOKEN12345678901234567890",
])
def test_credential_shaped_goal_is_rejected_content_free_before_writes(
    tmp_path: Path, goal: str
) -> None:
    service, store = _service(tmp_path)
    tables = ("commands", "product_sessions", "conversation_turns", "events")
    before = tuple(store.count(table) for table in tables)
    try:
        with pytest.raises(ValueError, match="credential material") as error:
            service.accept_text(goal, command_id="cmd_sensitive")
        assert "sensitive-value" not in str(error.value)
        assert "RAWSESSIONTOKEN" not in str(error.value)
        assert goal not in str(error.value)
        assert tuple(store.count(table) for table in tables) == before
        assert store.lookup_command("cmd_sensitive") is None
    finally:
        store.close()


@pytest.mark.parametrize("goal", [
    "Improve token_count=512 handling",
    "Document API key rotation without including a value",
    "Read credentials from the approved store",
    "Document the sk-proj- prefix without a credential value",
    "Recognize github_pat_ token names in documentation",
    "Explain AKIA access-key prefixes",
])
def test_ordinary_goal_language_is_not_misclassified_as_credentials(
    tmp_path: Path, goal: str
) -> None:
    service, store = _service(tmp_path)
    try:
        assert service.accept_text(goal).accepted is True
    finally:
        store.close()


@pytest.mark.parametrize(("field", "value"), [
    ("extra", "unexpected"),
    ("mode", "ready"),
    ("accepted", False),
    ("goal", "different goal"),
])
def test_accept_replay_rejects_nonexact_or_drifted_result(
    tmp_path: Path, field: str, value: object
) -> None:
    service, store = _service(tmp_path)
    service.accept_text("Durable goal", command_id="cmd_replay")
    row = store._writer.execute(
        "SELECT canonical_result_facts FROM commands WHERE command_id='cmd_replay'"
    ).fetchone()
    forged = json.loads(row[0])
    forged[field] = value
    store._writer.execute(
        "UPDATE commands SET canonical_result_facts=? WHERE command_id='cmd_replay'",
        (json.dumps(forged, sort_keys=True, separators=(",", ":")),),
    )
    store._writer.commit()
    try:
        with pytest.raises(SessionServiceError, match="accept result"):
            service.accept_text("Durable goal", command_id="cmd_replay")
    finally:
        store.close()


def test_accept_replay_requires_its_deterministic_durable_turn(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    command_id = "cmd_missing_turn"
    turn_digest = sha256(f"ses_1:{command_id}".encode()).hexdigest()[:32]

    def seed_session(transaction: object) -> dict[str, object]:
        transaction.save_session({  # type: ignore[attr-defined]
            "session_id": "ses_1", "state": "setup", "pending_goal": "Durable goal",
        })
        return {"seeded": True}

    store.execute_once("cmd_seed_goal", "seed_goal", seed_session)
    store.execute_once(command_id, "accept_session_text", lambda transaction: {
        "accepted": True, "goal": "Durable goal", "mode": "setup_required",
        "session_id": "ses_1", "turn_id": f"trn_{turn_digest}", "turn_ordinal": 1,
        "turn_occurred_at": NOW.isoformat(),
    })
    try:
        with pytest.raises(SessionServiceError, match="durable turn"):
            service.accept_text("Durable goal", command_id=command_id)
    finally:
        store.close()


def test_accept_replay_rejects_durable_turn_ordinal_drift(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    service.accept_text("Durable goal", command_id="cmd_replay")
    store._writer.execute(
        "UPDATE conversation_turns SET ordinal=2 WHERE session_id='ses_1'"
    )
    store._writer.commit()
    try:
        with pytest.raises(SessionServiceError, match="durable turn"):
            service.accept_text("Durable goal", command_id="cmd_replay")
    finally:
        store.close()


def test_accept_replay_rejects_durable_turn_time_drift(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    service.accept_text("Durable goal", command_id="cmd_replay")
    store._writer.execute(
        "UPDATE conversation_turns SET occurred_at=? WHERE session_id='ses_1'",
        ("2026-07-19T08:10:10+00:00",),
    )
    store._writer.commit()
    try:
        with pytest.raises(SessionServiceError, match="durable turn"):
            service.accept_text("Durable goal", command_id="cmd_replay")
    finally:
        store.close()


class _HostileLeaderMapping(Mapping[str, tuple[str, ...]]):
    def __getitem__(self, key: str) -> tuple[str, ...]:
        raise RuntimeError("sensitive-mapping-content")

    def __iter__(self) -> Iterator[str]:
        raise RuntimeError("sensitive-mapping-content")

    def __len__(self) -> int:
        raise RuntimeError("sensitive-mapping-content")


class _ForgedLengthDict(dict[str, tuple[str, ...]]):
    def __len__(self) -> int:
        return 0


def test_available_leaders_rejects_hostile_mapping_without_inspection(
    tmp_path: Path,
) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        with pytest.raises(TypeError, match="safe snapshot") as error:
            SessionService(
                store=store, clock=FrozenClock(NOW), session_id="ses_1",
                project_root=str(tmp_path), available_leaders=_HostileLeaderMapping(),
            )
        assert "sensitive-mapping-content" not in str(error.value)
        assert store.count("commands") == 0
    finally:
        store.close()


def test_available_leaders_cannot_forge_length_past_actual_limit(
    tmp_path: Path,
) -> None:
    forged = _ForgedLengthDict(
        {f"leader-{index}": ("native-default",) for index in range(257)}
    )
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        with pytest.raises(TypeError, match="safe snapshot"):
            SessionService(
                store=store, clock=FrozenClock(NOW), session_id="ses_1",
                project_root=str(tmp_path), available_leaders=forged,
            )
        assert store.count("commands") == 0
    finally:
        store.close()


def test_mapping_proxy_leader_snapshot_is_accepted(tmp_path: Path) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        service = SessionService(
            store=store, clock=FrozenClock(NOW), session_id="ses_1",
            project_root=str(tmp_path),
            available_leaders=MappingProxyType(dict(AVAILABLE_LEADERS)),
        )
        assert service.current().state.value == "setup"
    finally:
        store.close()


def test_command_id_is_validated_before_string_conversion(tmp_path: Path) -> None:
    class Trap:
        converted = False

        def __str__(self) -> str:
            self.converted = True
            raise RuntimeError("sensitive-command-content")

    service, store = _service(tmp_path)
    command_id = Trap()
    before = store.count("commands")
    try:
        with pytest.raises(TypeError, match="command_id must be a string") as error:
            service.accept_text("Safe goal", command_id=command_id)  # type: ignore[arg-type]
        assert command_id.converted is False
        assert "sensitive-command-content" not in str(error.value)
        assert store.count("commands") == before
    finally:
        store.close()


@pytest.mark.parametrize("command_id", ["", "\ud800", "x" * 256])
def test_command_id_requires_bounded_strict_utf8_before_writes(
    tmp_path: Path, command_id: str
) -> None:
    service, store = _service(tmp_path)
    before = store.count("commands")
    try:
        with pytest.raises(ValueError, match="command_id"):
            service.accept_text("Safe goal", command_id=command_id)
        assert store.count("commands") == before
    finally:
        store.close()


def test_non_utc_aware_clock_is_normalized_before_turn_persistence(
    tmp_path: Path,
) -> None:
    local_now = datetime(2026, 7, 19, 16, 9, 10, tzinfo=timezone(timedelta(hours=8)))
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(local_now))
    service = SessionService(
        store=store, clock=FrozenClock(local_now), session_id="ses_1",
        project_root=str(tmp_path), available_leaders=AVAILABLE_LEADERS,
    )
    try:
        service.accept_text("Safe goal")
        assert store.connection.execute(
            "SELECT occurred_at FROM conversation_turns"
        ).fetchone() == ("2026-07-19T08:09:10+00:00",)
    finally:
        store.close()


class _HostileTimezone(tzinfo):
    def utcoffset(self, value: datetime | None) -> timedelta:
        raise RuntimeError("sensitive-timezone-content")

    def dst(self, value: datetime | None) -> timedelta:
        return timedelta(0)


def test_hostile_timezone_fails_with_fixed_content_free_error(
    tmp_path: Path,
) -> None:
    hostile_now = datetime(2026, 7, 19, 8, 9, 10, tzinfo=_HostileTimezone())
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        with pytest.raises(
            SessionServiceError, match="clock must return a timezone-aware datetime"
        ) as error:
            SessionService(
                store=store, clock=FrozenClock(hostile_now), session_id="ses_1",
                project_root=str(tmp_path), available_leaders=AVAILABLE_LEADERS,
            )
        rendered = "".join(traceback.format_exception(error.value))
        assert "sensitive-timezone-content" not in rendered
        assert error.value.__cause__ is None
        assert error.value.__context__ is None
        assert store.count("commands") == 0
        assert store.count("product_sessions") == 0
        assert store.count("events") == 0
    finally:
        store.close()


def test_nul_project_root_fails_with_fixed_error_before_writes(tmp_path: Path) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        with pytest.raises(SessionServiceError, match="project root is unavailable"):
            SessionService(
                store=store, clock=FrozenClock(NOW), session_id="ses_1",
                project_root="\0sensitive-root", available_leaders=AVAILABLE_LEADERS,
            )
        assert store.count("commands") == 0
        assert store.count("product_sessions") == 0
        assert store.count("events") == 0
    finally:
        store.close()


def test_same_project_root_spelling_resolves_to_one_durable_identity(
    tmp_path: Path,
) -> None:
    alias = tmp_path.parent / f"{tmp_path.name}-alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    first = SessionService(
        store=store, clock=FrozenClock(NOW), session_id="ses_1",
        project_root=str(alias), available_leaders=AVAILABLE_LEADERS,
    )
    assert first.current().project_root == str(tmp_path.resolve())
    store.close()

    reopened = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    try:
        restored, _ = _service(tmp_path, store=reopened)
        assert restored.current().project_root == str(tmp_path.resolve())
    finally:
        reopened.close()


def test_generic_load_aggregate_returns_complete_conversation_turn(
    tmp_path: Path,
) -> None:
    service, store = _service(tmp_path)
    try:
        service.accept_text("Safe goal", command_id="cmd_turn")
        turn_id = store.connection.execute(
            "SELECT turn_id FROM conversation_turns"
        ).fetchone()[0]
        assert store.load_aggregate("conversation_turns", turn_id) == {
            "turn_id": turn_id, "session_id": "ses_1", "ordinal": 1,
            "actor_role": "human", "sanitized_content": "Safe goal",
            "occurred_at": NOW.isoformat(),
        }
    finally:
        store.close()
