from __future__ import annotations

import pytest

from agentdeck.adapters.sqlite_validation import _attempt_record


NOW = "2026-07-19T03:00:00+00:00"


def _snapshot(acp_session_id=None, **changes) -> dict[str, object]:
    snapshot = {
        "attempt_id": "att_1", "task_id": "tsk_1",
        "agent_instance_id": "agt_1", "ordinal": 1, "state": "running",
        "retryable": False, "acp_session_id": acp_session_id,
        "effect_observed": False,
    }
    snapshot.update(changes)
    return snapshot


def test_running_attempt_binds_one_exact_acp_session_from_null() -> None:
    initial = _attempt_record(_snapshot(), None, NOW)

    bound = _attempt_record(_snapshot("ses_acp_1"), initial, NOW)
    replay = _attempt_record(_snapshot("ses_acp_1"), bound, NOW)

    assert bound[8] == "ses_acp_1"
    assert replay[8] == "ses_acp_1"


@pytest.mark.parametrize("replacement", [None, "ses_acp_2"])
def test_bound_acp_session_cannot_be_removed_or_drifted(replacement) -> None:
    initial = _attempt_record(_snapshot(), None, NOW)
    bound = _attempt_record(_snapshot("ses_acp_1"), initial, NOW)

    with pytest.raises(ValueError, match="ACP session|immutable lineage"):
        _attempt_record(_snapshot(replacement), bound, NOW)


def test_first_acp_session_binding_must_be_nonempty() -> None:
    initial = _attempt_record(_snapshot(), None, NOW)

    with pytest.raises(ValueError, match="acp_session_id"):
        _attempt_record(_snapshot(" "), initial, NOW)


def test_first_acp_session_binding_cannot_skip_running_state() -> None:
    initial = _attempt_record(_snapshot(), None, NOW)

    with pytest.raises(ValueError, match="ACP session"):
        _attempt_record(
            _snapshot("ses_acp_1", state="completed", result_summary="done"),
            initial, NOW,
        )
