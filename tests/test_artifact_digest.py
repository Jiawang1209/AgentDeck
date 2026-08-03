"""Artifact content digests: the capability CCB already has.

CCB's kernel records digest/actor/job/timestamp when it commits an artifact,
makes re-import idempotent for the same digest, and FAILS CLOSED on a
conflicting one. AgentDeck recorded only path/kind/status, so evidence could be
rewritten afterwards and nothing noticed.

See docs/superpowers/specs/2026-08-03-review-digest-binding-design.md.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from agentdeck.state import StateStore


def _store(tmp_path: Path) -> StateStore:
    root = tmp_path / "repo"
    (root / ".agentdeck" / "state").mkdir(parents=True)
    return StateStore(root)


def _dispatch(store: StateStore) -> str:
    records = store.create_dispatch_records(
        "leader", "coder", "do the thing", "prompt", "%1"
    )
    return str(records["message"]["message_id"])


def _reply_text(path: Path) -> str:
    return f"status: completed\nsummary: done\nfull_output_path: {path}\n"


def test_artifact_records_content_hash_and_byte_count(tmp_path: Path) -> None:
    store = _store(tmp_path)
    message_id = _dispatch(store)
    artifact = tmp_path / "out.md"
    artifact.write_text("hello evidence\n", encoding="utf-8")

    result = store.record_reply("coder", message_id, _reply_text(artifact))

    recorded = result["artifacts"][0]
    assert recorded["digest_status"] == "recorded"
    assert recorded["content_hash"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert recorded["byte_count"] == artifact.stat().st_size


def test_missing_file_is_recorded_as_missing_not_as_hashed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    message_id = _dispatch(store)

    result = store.record_reply("coder", message_id, _reply_text(tmp_path / "absent.md"))

    recorded = result["artifacts"][0]
    assert recorded["digest_status"] == "file_missing"
    assert recorded["content_hash"] is None
    assert recorded["byte_count"] is None


def test_reregistering_the_same_content_is_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    message_id = _dispatch(store)
    artifact = tmp_path / "out.md"
    artifact.write_text("stable\n", encoding="utf-8")

    store.record_reply("coder", message_id, _reply_text(artifact))
    second = store.record_reply("coder", message_id, _reply_text(artifact))

    assert second["artifacts"] == []
    assert second.get("artifact_conflicts") in (None, [])
    assert len([a for a in store.load()["artifacts"] if a["message_id"] == message_id]) == 1


def test_conflicting_content_fails_closed_and_keeps_the_original(tmp_path: Path) -> None:
    store = _store(tmp_path)
    message_id = _dispatch(store)
    artifact = tmp_path / "out.md"
    artifact.write_text("first\n", encoding="utf-8")
    store.record_reply("coder", message_id, _reply_text(artifact))
    original = store.load()["artifacts"][0]["content_hash"]

    artifact.write_text("rewritten after the fact\n", encoding="utf-8")
    second = store.record_reply("coder", message_id, _reply_text(artifact))

    # rejected: not registered, original untouched, conflict named
    assert second["artifacts"] == []
    conflict = second["artifact_conflicts"][0]
    assert conflict["path"] == str(artifact)
    assert conflict["recorded_hash"] == original
    assert conflict["observed_hash"] != original
    stored = [a for a in store.load()["artifacts"] if a["message_id"] == message_id]
    assert len(stored) == 1
    assert stored[0]["content_hash"] == original
    assert any(e["event_type"] == "artifact_digest_conflict" for e in store.list_events(200))


def test_a_conflict_does_not_block_the_reply_itself(tmp_path: Path) -> None:
    # A reply is a fact; registering evidence is a judgement. Same split as an
    # invalid verdict not blocking its reply.
    store = _store(tmp_path)
    message_id = _dispatch(store)
    artifact = tmp_path / "out.md"
    artifact.write_text("first\n", encoding="utf-8")
    store.record_reply("coder", message_id, _reply_text(artifact))

    artifact.write_text("second\n", encoding="utf-8")
    second = store.record_reply("coder", message_id, _reply_text(artifact))

    assert second["reply_id"]
    assert any(r["reply_id"] == second["reply_id"] for r in store.load()["replies"])
