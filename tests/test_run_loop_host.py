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


def test_plan_awaiting_lists_dispatched_unreplied_approvals_for_this_plan_only() -> None:
    """awaiting 集单一来源:文件通道摄入与宿主人类门判定共用同一份定义。"""
    from agentdeck.cli import _plan_awaiting

    state = {
        "approvals": [
            {"plan_id": "pln_a", "status": "dispatched", "message_id": "msg_1", "agent_id": "coder"},
            {"plan_id": "pln_a", "status": "dispatched", "message_id": "msg_2", "agent_id": "reviewer"},
            {"plan_id": "pln_a", "status": "approved", "message_id": "msg_3", "agent_id": "planner"},
            {"plan_id": "pln_b", "status": "dispatched", "message_id": "msg_4", "agent_id": "coder"},
        ],
        "replies": [{"message_id": "msg_2"}],
    }

    assert _plan_awaiting(state, "pln_a") == [("msg_1", "coder")]


def test_contract_field_tuples_and_examples() -> None:
    from agentdeck.contracts import (
        RUN_LOOP_HOST_START_RESPONSE_FIELDS,
        RUN_LOOP_HOST_STATUS_RESPONSE_FIELDS,
        RUN_LOOP_HOST_STOP_RESPONSE_FIELDS,
        run_loop_host_start_example,
        run_loop_host_status_example,
        run_loop_host_stop_example,
        validate_run_loop_host_start_contract,
        validate_run_loop_host_status_contract,
        validate_run_loop_host_stop_contract,
    )

    for field in ("ok", "mode", "plan_id", "pid", "max_waves", "log_path", "status_command", "stop_command"):
        assert field in RUN_LOOP_HOST_START_RESPONSE_FIELDS
    for field in ("running", "stale", "wave_count", "last_gate", "stopped_reason", "start_command_template"):
        assert field in RUN_LOOP_HOST_STATUS_RESPONSE_FIELDS
    for field in ("ok", "mode", "plan_id", "wave_count", "stopped_reason", "next_command"):
        assert field in RUN_LOOP_HOST_STOP_RESPONSE_FIELDS

    assert validate_run_loop_host_start_contract(run_loop_host_start_example())["ok"] is True
    assert validate_run_loop_host_status_contract(run_loop_host_status_example())["ok"] is True
    assert validate_run_loop_host_stop_contract(run_loop_host_stop_example())["ok"] is True

    # 缺字段 / 错 mode / 非法 stopped_reason 必须被拒
    broken = dict(run_loop_host_status_example())
    broken.pop("running")
    assert validate_run_loop_host_status_contract(broken)["ok"] is False
    wrong_mode = {**run_loop_host_status_example(), "mode": "nope"}
    assert validate_run_loop_host_status_contract(wrong_mode)["ok"] is False
    bad_reason = {**run_loop_host_status_example(), "stopped_reason": "made_up"}
    assert validate_run_loop_host_status_contract(bad_reason)["ok"] is False


def test_contract_is_discoverable(capsys) -> None:
    from agentdeck import cli

    assert cli.main(["contract", "run-loop-host", "--example"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["start_response_fields"]
    assert payload["status_response_fields"]
    assert payload["stop_response_fields"]
    assert payload["stopped_reasons"] == list(RUN_LOOP_HOST_STOPPED_REASONS)
    assert payload["contract_exists"] is True
    assert cli.main(["contract", "list"]) == 0
    index = json.loads(capsys.readouterr().out)
    assert any(item.get("name") == "run-loop-host" for item in index["contracts"])
