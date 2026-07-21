from pathlib import Path

from agentdeck.adapters.sqlite import SQLiteStore
from product_kernel.fakes import FrozenClock
from product_kernel.test_sqlite_exit_authority import NOW


def test_cursor_and_takeover_authorities_round_trip_transactionally(
    tmp_path: Path,
) -> None:
    store = SQLiteStore.open(tmp_path, clock=FrozenClock(NOW))
    cursor = {
        "cursor_id": "cur_1", "project_id": "prj_1", "sequence": 1,
        "fingerprint": "a" * 64,
    }
    ownership = {
        "attempt_id": "att_1", "generation": 1, "state": "human",
        "cycle_id": "own_1",
    }
    try:
        store.execute_once("cmd_cursor", "cursor_test", lambda transaction: (
            transaction.save_aggregate("observer_cursors", "cur_1", cursor)
            or {"saved": True}
        ))
        store.execute_once("cmd_owner", "ownership_test", lambda transaction: (
            transaction.save_aggregate("takeover_ownership", "att_1", ownership)
            or {"saved": True}
        ))

        assert store.load_aggregate("observer_cursors", "cur_1") == cursor
        assert store.load_aggregate("takeover_ownership", "att_1") == ownership
    finally:
        store.close()
