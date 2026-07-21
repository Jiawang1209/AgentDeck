from dataclasses import replace

import pytest


def test_product_observer_exports_the_shared_cursor_type() -> None:
    from agentdeck.ports.observer import ObserverCursor
    from agentdeck.product.observer import ObserverCursor as ProductCursor

    assert ProductCursor is ObserverCursor


def test_acknowledgement_requires_exact_cursor_and_project_binding() -> None:
    from agentdeck.ports.observer import ObserverAcknowledgement, ObserverCursor

    cursor = ObserverCursor(
        "prj_1", "ses_1", "agt_1", "tsk_1", "att_1", "acp",
        1, "evt_1", "a" * 64,
    )

    assert ObserverAcknowledgement(cursor).cursor is cursor
    with pytest.raises(ValueError, match="project"):
        replace(cursor, project_id="project_1")


@pytest.mark.parametrize(
    "field,value",
    (
        ("transport", "pty"),
        ("sequence", 0),
        ("fingerprint", "A" * 64),
        ("event_id", "evt_bad value"),
    ),
)
def test_cursor_rejects_non_closed_authority(field: str, value: object) -> None:
    from agentdeck.ports.observer import ObserverCursor

    cursor = ObserverCursor(
        "prj_1", "ses_1", "agt_1", "tsk_1", "att_1", "acp",
        1, "evt_1", "a" * 64,
    )

    with pytest.raises((TypeError, ValueError)):
        replace(cursor, **{field: value})
