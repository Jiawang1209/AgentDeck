from __future__ import annotations

import pytest

from agentdeck.product.presenter import ExitPresentation
from agentdeck.product.renderer import render


REQUEST_ID = "xrt_" + "1" * 32
ATTEMPT_HASH = "a" * 64


def test_exit_request_renders_copyable_exact_commands() -> None:
    text = render(ExitPresentation(
        summary="The active Attempt must be interrupted before exit.",
        active_attempts=("att_1",),
        requires_confirmation=True,
        request_id=REQUEST_ID,
        attempt_hash=ATTEMPT_HASH,
    ))

    assert f"/exit confirm {REQUEST_ID} {ATTEMPT_HASH}" in text
    assert f"/exit decline {REQUEST_ID} {ATTEMPT_HASH}" in text
    assert "{" not in text


def test_idle_exit_cannot_carry_request_authority() -> None:
    with pytest.raises(ValueError):
        ExitPresentation(
            summary="safe",
            active_attempts=(),
            requires_confirmation=False,
            request_id=REQUEST_ID,
            attempt_hash=ATTEMPT_HASH,
        )


@pytest.mark.parametrize(
    "values",
    (
        {"request_id": REQUEST_ID},
        {"attempt_hash": ATTEMPT_HASH},
        {"request_id": "xrt_" + "A" * 32, "attempt_hash": ATTEMPT_HASH},
        {"request_id": REQUEST_ID, "attempt_hash": "A" * 64},
    ),
)
def test_exit_confirmation_requires_one_exact_closed_authority_pair(
    values: dict[str, str],
) -> None:
    with pytest.raises(ValueError):
        ExitPresentation(
            summary="active",
            active_attempts=("att_1",),
            requires_confirmation=True,
            **values,
        )
