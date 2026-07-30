from __future__ import annotations

import json
import os
from pathlib import Path

from agentdeck.run_loop_host import (
    RUN_LOOP_HOST_STOPPED_REASONS,
    append_host_log,
    host_dir,
    host_liveness,
    host_log_path,
    host_record_path,
    pid_alive,
    read_host_record,
    write_host_record,
)


def test_paths_are_under_project_agentdeck(tmp_path: Path) -> None:
    assert host_dir(tmp_path) == tmp_path / ".agentdeck" / "run-loop-host"
    assert host_record_path(tmp_path).name == "host.json"
    assert host_log_path(tmp_path).name == "host.log"


def test_stopped_reasons_are_closed_enum() -> None:
    assert RUN_LOOP_HOST_STOPPED_REASONS == (
        "gate_reached",
        "budget_exhausted",
        "policy_revoked",
        "signalled",
        "engine_error",
    )


def test_record_round_trip_and_missing_is_none(tmp_path: Path) -> None:
    assert read_host_record(tmp_path) is None
    record = {"pid": 4242, "plan_id": "pln_x", "wave_count": 0}
    write_host_record(tmp_path, record)
    assert read_host_record(tmp_path) == record
    # 目录自动创建,内容是可读 JSON
    assert json.loads(host_record_path(tmp_path).read_text(encoding="utf-8")) == record


def test_corrupt_record_reads_as_none(tmp_path: Path) -> None:
    host_dir(tmp_path).mkdir(parents=True)
    host_record_path(tmp_path).write_text("{not json", encoding="utf-8")
    assert read_host_record(tmp_path) is None
    host_record_path(tmp_path).write_text('["list"]', encoding="utf-8")
    assert read_host_record(tmp_path) is None


def test_pid_alive_probe() -> None:
    assert pid_alive(os.getpid()) is True
    assert pid_alive(0) is False
    assert pid_alive(-1) is False
    # 极大 pid 几乎不可能存在
    assert pid_alive(4_000_000) is False


def test_host_liveness_three_states(tmp_path: Path) -> None:
    # 无记录
    assert host_liveness(tmp_path) == (None, False, False)
    # 活 pid
    write_host_record(tmp_path, {"pid": os.getpid(), "plan_id": "pln_x"})
    record, running, stale = host_liveness(tmp_path)
    assert record is not None and running is True and stale is False
    # 死 pid → stale
    write_host_record(tmp_path, {"pid": 4_000_000, "plan_id": "pln_x"})
    _record, running, stale = host_liveness(tmp_path)
    assert running is False and stale is True
    # 干净停止(pid 已清)不是 stale
    write_host_record(tmp_path, {"pid": None, "plan_id": "pln_x", "stopped_reason": "gate_reached"})
    _record, running, stale = host_liveness(tmp_path)
    assert running is False and stale is False


def test_append_host_log_is_jsonl_and_appends(tmp_path: Path) -> None:
    append_host_log(tmp_path, {"plan_id": "pln_x", "wave": 1})
    append_host_log(tmp_path, {"plan_id": "pln_x", "wave": 2})
    lines = host_log_path(tmp_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["wave"] for line in lines] == [1, 2]
