"""Golden-run slice (b) — domain-typed Agent-Instance provisioning.

Replaces the raw-SQL `agent_instances` seed with a store method that accepts
validated `AgentInstance` domain values and persists them, so the golden run no
longer reaches into private SQL to register its four Agent Instances.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agentdeck.adapters.sqlite import SQLiteStore
from agentdeck.application.session_service import SessionService
from agentdeck.kernel.agents import (
    AgentBackend,
    AgentIdentityError,
    AgentInstance,
    AgentRole,
)

from .fakes import FrozenClock

NOW = datetime(2026, 7, 23, 9, 0, tzinfo=timezone.utc)
_SESSION = "ses_provision"


def _store_with_session(tmp_path):
    clock = FrozenClock(NOW)
    store = SQLiteStore.open(tmp_path, clock=clock)
    SessionService(
        store=store, clock=clock, session_id=_SESSION,
        project_root=str(tmp_path), available_leaders={"fake": ("m",)},
    )
    return store, clock


def _instance(instance_id: str, role: AgentRole, backend_id: str) -> AgentInstance:
    return AgentInstance(
        instance_id=instance_id,
        backend=AgentBackend(backend_id=backend_id, transport="acp", version="1"),
        role=role,
        session_id=_SESSION,
    )


def test_provision_agent_instances_persists_domain_typed_rows(tmp_path) -> None:
    store, clock = _store_with_session(tmp_path)
    try:
        store.provision_agent_instances(
            instances=(
                _instance("ai_impl", AgentRole.IMPLEMENTER, "codex-cli"),
                _instance("ai_review", AgentRole.REVIEWER, "claude-cli"),
            ),
            state="active",
            now=clock.now().isoformat(),
        )
        row = store._require_writer().execute(
            "SELECT backend_id, transport, backend_version, role, state "
            "FROM agent_instances WHERE instance_id='ai_impl'"
        ).fetchone()
        assert tuple(row) == ("codex-cli", "acp", "1", "implementer", "active")
        count = store._require_writer().execute(
            "SELECT count(*) FROM agent_instances WHERE session_id=?", (_SESSION,)
        ).fetchone()[0]
        assert count == 2
    finally:
        store.close()


def test_provision_rejects_duplicate_instance_ids(tmp_path) -> None:
    store, clock = _store_with_session(tmp_path)
    try:
        with pytest.raises(AgentIdentityError, match="ai_dup"):
            store.provision_agent_instances(
                instances=(
                    _instance("ai_dup", AgentRole.IMPLEMENTER, "codex-cli"),
                    _instance("ai_dup", AgentRole.REVIEWER, "claude-cli"),
                ),
                state="active",
                now=clock.now().isoformat(),
            )
    finally:
        store.close()


def test_provision_rejects_non_agent_instance_values(tmp_path) -> None:
    store, clock = _store_with_session(tmp_path)
    try:
        with pytest.raises(TypeError):
            store.provision_agent_instances(
                instances=({"instance_id": "x"},),
                state="active",
                now=clock.now().isoformat(),
            )
    finally:
        store.close()
