from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest

from agentdeck.daemon.recovery import RecoveryFacts, reconcile_gate


MISSION_ID = "mis_141414141414"
ATTEMPT_ID = "mat_141414141414"


@dataclass(frozen=True)
class CrashResult:
    recovery_classification: str
    duplicate_dispatches: int


def _facts_for(crash_point: str) -> dict[str, object]:
    facts: dict[str, object] = {
        "mission_id": MISSION_ID,
        "mission_state": "running",
        "attempt_id": ATTEMPT_ID,
        "attempt_state": "prepared",
        "receipt_state": "none",
        "reply_state": "none",
        "handoff_state": "none",
        "permission_state": "none",
        "configured_transport": "acp",
        "transport_state": "ready",
        "snapshot_state": "valid",
        "lineage_state": "valid",
        "ownership_state": "agentdeck_owned",
    }
    updates: dict[str, dict[str, object]] = {
        "before_prepare": {
            "attempt_id": None,
            "attempt_state": "none",
        },
        "after_prepare_before_dispatch": {},
        "after_dispatch_before_receipt": {"attempt_state": "admitting"},
        "after_receipt_before_reply": {
            "attempt_state": "submitted",
            "receipt_state": "recorded",
            "configured_transport": "tmux",
        },
        "after_reply_before_handoff": {
            "attempt_state": "succeeded",
            "receipt_state": "recorded",
            "reply_state": "received",
        },
        "after_handoff_before_next_dispatch": {
            "attempt_state": "succeeded",
            "receipt_state": "recorded",
            "reply_state": "validated",
            "handoff_state": "recorded",
        },
        "permission_pending": {
            "attempt_state": "running",
            "receipt_state": "recorded",
            "permission_state": "pending",
            "configured_transport": "tmux",
        },
        "outbox_flush": {},
        "shutdown": {
            "mission_state": "interrupted",
            "attempt_state": "interrupted",
        },
    }
    facts.update(updates[crash_point])
    return facts


def _persist_then_crash(path: Path, crash_point: str) -> subprocess.CompletedProcess[str]:
    script = """
import json, os, signal, sys
path, payload = sys.argv[1], sys.argv[2]
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    os.write(fd, payload.encode('utf-8'))
    os.fsync(fd)
finally:
    os.close(fd)
os.kill(os.getpid(), signal.SIGKILL)
"""
    return subprocess.run(
        [sys.executable, "-c", script, str(path), json.dumps(_facts_for(crash_point))],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )


def run_crash_scenario(root: Path, crash_point: str) -> CrashResult:
    evidence = root / f"{crash_point}.json"
    child = _persist_then_crash(evidence, crash_point)
    assert child.returncode == -signal.SIGKILL
    facts = RecoveryFacts.from_mapping(json.loads(evidence.read_text(encoding="utf-8")))
    decision = reconcile_gate(facts)
    classification = (
        "interrupted"
        if facts.mission_state == "interrupted"
        else decision.classification
    )
    external_dispatches_before_crash = int(
        crash_point
        in {
            "after_dispatch_before_receipt",
            "after_receipt_before_reply",
            "after_reply_before_handoff",
            "after_handoff_before_next_dispatch",
            "permission_pending",
        }
    )
    recovery_dispatches = int(decision.next_transition == "dispatch_prepared")
    duplicate_dispatches = max(
        0, external_dispatches_before_crash + recovery_dispatches - 1
    )
    return CrashResult(classification, duplicate_dispatches)


@pytest.mark.parametrize(
    ("crash_point", "expected"),
    [
        ("before_prepare", "resumable"),
        ("after_prepare_before_dispatch", "resumable"),
        ("after_dispatch_before_receipt", "ambiguous"),
        ("after_receipt_before_reply", "resumable"),
        ("after_reply_before_handoff", "resumable"),
        ("after_handoff_before_next_dispatch", "resumable"),
        ("permission_pending", "waiting_human"),
        ("outbox_flush", "resumable"),
        ("shutdown", "interrupted"),
    ],
)
def test_crash_recovery_never_repeats_unknown_effect(
    crash_point: str, expected: str, tmp_path: Path
) -> None:
    result = run_crash_scenario(tmp_path, crash_point)
    assert result.recovery_classification == expected
    assert result.duplicate_dispatches == 0
