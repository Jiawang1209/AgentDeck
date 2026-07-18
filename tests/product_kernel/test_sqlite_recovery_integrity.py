from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.application.recovery_service import (
    ReconnectStatus,
    RecoveryService,
)
from agentdeck.ports.store import RunningAttempt

from .fakes import FrozenClock
from .test_recovery_service import (
    CorruptingTransport,
    _seed_sqlite_running_attempt,
)


NOW = datetime(2026, 7, 19, 4, 5, 6, tzinfo=timezone.utc)
NOW_TEXT = "2026-07-19T04:05:06+00:00"


def _seed_second_agent(store: SQLiteStore) -> None:
    store._writer.execute(
        "INSERT INTO agent_instances VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "agt_2", "ses_1", "codex", "acp", "1", "implementer", "acp_2",
            "active", NOW_TEXT, NOW_TEXT,
        ),
    )


@pytest.mark.parametrize("fingerprint", ["a" * 63, "A" * 64, 7])
def test_running_attempt_fingerprint_is_exact_lower_hex(fingerprint: object) -> None:
    with pytest.raises((TypeError, ValueError), match="durable_fingerprint"):
        RunningAttempt(
            "att_1", "tsk_1", "acp_1", False, fingerprint  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("assignment", "column", "expected"),
    [
        ("agent_instance_id='agt_2'", "agent_instance_id", "agt_2"),
        ("ordinal=2", "ordinal", 2),
    ],
)
def test_recovery_rejects_any_valid_full_attempt_drift_after_transport(
    tmp_path: Path, assignment: str, column: str, expected: object,
) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    _seed_sqlite_running_attempt(store)
    _seed_second_agent(store)
    assert len(store.list_running_attempts()[0].durable_fingerprint or "") == 64
    transport = CorruptingTransport(store, ReconnectStatus.LOST, assignment)
    try:
        with pytest.raises((RuntimeError, ValueError), match="drift|changed"):
            RecoveryService(
                store, transport, FrozenClock(NOW), "restart_full_drift"
            ).reconcile()
        assert transport.calls == ["acp_1"]
        assert store.connection.execute(
            "SELECT count(*) FROM commands WHERE command_id LIKE 'recover:%'"
        ).fetchone() == (0,)
        assert store.connection.execute(
            "SELECT count(*) FROM events WHERE kind='attempt_recovered'"
        ).fetchone() == (0,)
        assert store.connection.execute(
            f"SELECT state,{column} FROM attempts WHERE attempt_id='att_1'"
        ).fetchone() == ("running", expected)
    finally:
        store.close()
