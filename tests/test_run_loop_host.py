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


# 屏上原文提示是 _detect_waiting_for_input 返回的 **marker 行**,不是选项行
# (终审 2026-08-01 F4)。live 证据与 delegation contract 示例都是这一句。
_REAL_WAITING_HINT = "Press enter to confirm or esc to cancel"


def test_stopped_reasons_are_closed_enum() -> None:
    assert RUN_LOOP_HOST_STOPPED_REASONS == (
        "gate_reached",
        "budget_exhausted",
        "policy_revoked",
        "signalled",
        "engine_error",
        "human_gate",
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


def test_human_gate_is_a_closed_stopped_reason() -> None:
    assert "human_gate" in RUN_LOOP_HOST_STOPPED_REASONS
    assert len(RUN_LOOP_HOST_STOPPED_REASONS) == 6
    assert len(set(RUN_LOOP_HOST_STOPPED_REASONS)) == 6


def test_human_gate_candidate_matches_undelegated_box_on_awaited_agent() -> None:
    from agentdeck.run_loop_host import human_gate_candidate

    skipped = [{
        "agent_id": "planner", "command": "playwright_cli.sh open file:///x",
        "box_kind": "command", "mcp_server": None, "mcp_tool": None,
        "waiting_hint": _REAL_WAITING_HINT, "box_pending": True,
        "reason": "no active delegation",
    }]
    assert human_gate_candidate(skipped, {"planner"}) == {
        "agent_id": "planner",
        "box_kind": "command",
        "command": "playwright_cli.sh open file:///x",
        "mcp_server": None,
        "mcp_tool": None,
        "waiting_hint": _REAL_WAITING_HINT,
    }


def test_human_gate_candidate_requires_a_pending_box_on_screen() -> None:
    """终审 2026-08-01 F1/F2:已答复的折叠框绝不能停掉一个健康的走开段。

    marker "Would you like to run" 在 `… ? -> Yes` 这种已答复残留上同样命中,
    因此 `box_pending` 是必需的正证明,而非可选补充。
    """
    from agentdeck.run_loop_host import human_gate_candidate

    answered = {
        "agent_id": "planner", "command": "deploy.sh", "box_kind": "command",
        "mcp_server": None, "mcp_tool": None,
        "waiting_hint": "Would you like to run the following command? -> Yes",
        "box_pending": False, "reason": "no active delegation",
    }
    assert human_gate_candidate([answered], {"planner"}) is None
    # 缺失该键(旧形状)同样 fail-open,绝不按"没说不行就是行"处理
    assert human_gate_candidate([{k: v for k, v in answered.items()
                                  if k != "box_pending"}], {"planner"}) is None


def test_human_gate_candidate_requires_a_parsed_box_identity() -> None:
    """spec 冻结条款:解析不出框一律不判定。

    全 None 身份恒等于自身,若放行会让 debounce 必然确认 —— 一次误停。
    """
    from agentdeck.run_loop_host import human_gate_candidate, same_human_gate

    unparsed = {
        "agent_id": "planner", "command": None, "box_kind": None,
        "mcp_server": None, "mcp_tool": None,
        "waiting_hint": "Would you like to run the following command? -> Yes",
        "box_pending": True, "reason": "no active delegation",
    }
    assert human_gate_candidate([unparsed], {"planner"}) is None
    # 说明为什么这条必须挡在候选阶段:全 None 身份自等
    all_none = {key: None for key in
                ("agent_id", "box_kind", "command", "mcp_server", "mcp_tool")}
    assert same_human_gate(all_none, all_none) is True


def test_human_gate_candidate_ignores_agents_outside_the_awaiting_set() -> None:
    from agentdeck.run_loop_host import human_gate_candidate

    skipped = [{"agent_id": "idle_bot", "command": "x", "box_kind": "command",
                "mcp_server": None, "mcp_tool": None, "waiting_hint": "h",
                "reason": "no active delegation"}]
    assert human_gate_candidate(skipped, {"planner"}) is None


def test_human_gate_candidate_ignores_pane_capture_failures() -> None:
    from agentdeck.run_loop_host import human_gate_candidate

    skipped = [{"agent_id": "planner", "command": None,
                "reason": "pane capture failed", "iteration": 3}]
    assert human_gate_candidate(skipped, {"planner"}) is None


def test_human_gate_candidate_returns_none_for_empty_scan() -> None:
    from agentdeck.run_loop_host import human_gate_candidate

    assert human_gate_candidate([], {"planner"}) is None


def test_same_human_gate_compares_by_agent_and_box_identity() -> None:
    from agentdeck.run_loop_host import same_human_gate

    a = {"agent_id": "planner", "box_kind": "command", "command": "x",
         "mcp_server": None, "mcp_tool": None, "waiting_hint": "h1"}
    b = {"agent_id": "planner", "box_kind": "command", "command": "x",
         "mcp_server": None, "mcp_tool": None, "waiting_hint": "h2"}
    c = {"agent_id": "planner", "box_kind": "command", "command": "y",
         "mcp_server": None, "mcp_tool": None, "waiting_hint": "h1"}

    assert same_human_gate(a, b) is True   # hint 变化不影响身份
    assert same_human_gate(a, c) is False  # 命令不同 = 不同的框
    assert same_human_gate(None, a) is False
    assert same_human_gate(a, None) is False


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


def test_status_contract_pins_the_full_field_list_in_order() -> None:
    """契约扩张也必须逐字段钉死顺序,避免 GUI 面悄悄漂移。"""
    from agentdeck.contracts import RUN_LOOP_HOST_STATUS_RESPONSE_FIELDS

    assert RUN_LOOP_HOST_STATUS_RESPONSE_FIELDS == (
        "ok",
        "mode",
        "running",
        "stale",
        "pid",
        "plan_id",
        "wave_count",
        "max_waves",
        "interval",
        "last_gate",
        "last_wave_at",
        "stopped_reason",
        "log_path",
        "start_command_template",
        "stop_command",
        "human_gate",
    )


def test_status_validator_guards_the_human_gate_evidence() -> None:
    from agentdeck.contracts import (
        run_loop_host_status_example,
        validate_run_loop_host_status_contract,
    )
    from agentdeck.run_loop_host import HUMAN_GATE_FIELDS

    example = run_loop_host_status_example()
    # 无人类门时 null 合法
    assert example["human_gate"] is None
    assert validate_run_loop_host_status_contract(example)["ok"] is True

    gate = {field: "x" for field in HUMAN_GATE_FIELDS}
    stopped = {
        **example,
        "running": False,
        "stopped_reason": "human_gate",
        "human_gate": gate,
    }
    assert validate_run_loop_host_status_contract(stopped)["ok"] is True

    # stopped_reason=human_gate 但没有证据 → 必须拒
    missing = {**stopped, "human_gate": None}
    result = validate_run_loop_host_status_contract(missing)
    assert result["ok"] is False
    assert any("human_gate is required" in error for error in result["errors"])

    # 证据不是对象 → 必须拒
    not_object = {**stopped, "human_gate": ["planner"]}
    assert validate_run_loop_host_status_contract(not_object)["ok"] is False

    # 证据缺任一字段 → 必须逐个点名
    for field in HUMAN_GATE_FIELDS:
        partial = {key: value for key, value in gate.items() if key != field}
        result = validate_run_loop_host_status_contract({**stopped, "human_gate": partial})
        assert result["ok"] is False
        assert any(field in error for error in result["errors"])


def test_contract_discovery_exposes_the_human_gate_fields() -> None:
    from agentdeck.contracts import run_loop_host_contract_payload
    from agentdeck.run_loop_host import HUMAN_GATE_FIELDS

    payload = run_loop_host_contract_payload(Path("docs/contracts/run-loop-host-schema.md"))
    assert payload["human_gate_fields"] == list(HUMAN_GATE_FIELDS)
    assert "human_gate" in payload["status_response_fields"]


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
