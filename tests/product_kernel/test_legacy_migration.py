"""Task 37 — explicit legacy JSON→SQLite migration (no silent import).

Legacy `.agentdeck/state.json` + `events.jsonl` are parsed as INERT external
data; they are never imported silently and cannot become a second write
authority. Slice 37.1 covers the read-only parser.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from agentdeck.adapters.legacy_state import LegacyState, parse_legacy_state

_FIXTURE = Path(__file__).parent / "fixtures" / "legacy_state"


def _legacy_project(tmp_path: Path) -> Path:
    agentdeck = tmp_path / ".agentdeck"
    agentdeck.mkdir()
    shutil.copy(_FIXTURE / "state.json", agentdeck / "state.json")
    shutil.copy(_FIXTURE / "events.jsonl", agentdeck / "events.jsonl")
    return tmp_path


def test_parse_legacy_state_reads_inert_records(tmp_path: Path) -> None:
    state = parse_legacy_state(_legacy_project(tmp_path))
    assert isinstance(state, LegacyState)
    assert state.project_id == "prj_legacy0001"
    assert state.resolved_root == "/legacy/example/project"
    assert state.created_at == "2026-07-01T00:00:00+00:00"
    assert state.agent_count == 2
    assert state.message_count == 2
    assert state.job_count == 1
    assert state.event_count == 3
    assert state.content_hash.startswith("sha256:")
    assert {source.kind for source in state.sources} == {"state", "events"}


def test_parse_legacy_state_absent_returns_none(tmp_path: Path) -> None:
    (tmp_path / ".agentdeck").mkdir()
    assert parse_legacy_state(tmp_path) is None


def test_parse_legacy_state_hash_is_content_addressed(tmp_path: Path) -> None:
    first = parse_legacy_state(_legacy_project(tmp_path))
    other = tmp_path / "second"
    other.mkdir()
    second = parse_legacy_state(_legacy_project(other))
    assert first.content_hash == second.content_hash
