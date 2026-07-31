from __future__ import annotations

import json
from pathlib import Path

from agentdeck import cli
from agentdeck.config import write_default_config
from agentdeck.state import StateStore

# pytest 默认 importmode=prepend 会把 tests/ 加进 sys.path,同目录直接导入
from test_review_iteration import _state, _verdict


def prepare_seeded_project(tmp_path: Path, monkeypatch, overall: str = "fail") -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    store = StateStore(root)
    state = store.load()
    seed = _state(overall)
    for key in ("plans", "approvals", "messages", "replies"):
        state[key] = seed[key]
    store.save(state)
    monkeypatch.chdir(root)
    return root


def test_rework_gate_matrix_refuses_with_zero_writes(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_seeded_project(tmp_path, monkeypatch, "fail")
    store = StateStore(root)
    before = store.load()

    assert cli.main(["plan", "rework", "--plan-id", "pln_1"]) == 1
    assert "confirm" in capsys.readouterr().err
    assert cli.main(["plan", "rework", "--plan-id", "pln_ghost", "--confirm"]) == 1
    assert "no_plan" in capsys.readouterr().err
    assert store.load() == before


def test_rework_refuses_on_pass_verdict(tmp_path, monkeypatch, capsys) -> None:
    prepare_seeded_project(tmp_path, monkeypatch, "pass")
    assert cli.main(["plan", "rework", "--plan-id", "pln_1", "--confirm"]) == 1
    assert "verdict_pass" in capsys.readouterr().err


def test_rework_appends_and_reports(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_seeded_project(tmp_path, monkeypatch, "fail")
    assert cli.main(["plan", "rework", "--plan-id", "pln_1", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "plan_rework"
    assert payload["ok"] is True
    assert payload["plan_id"] == "pln_1"
    assert payload["round"] == 1
    assert payload["steps"] == [3, 4]
    assert len(payload["approval_ids"]) == 2
    assert payload["next_command"] == "agentdeck approval list"
    assert payload["requires_explicit_user"] is True
    assert payload["safety"] == "explicit_user"
    steps = StateStore(root).load()["plans"][0]["plan"]["steps"]
    assert len(steps) == 4
    # 同一 reply 第二次拒绝
    assert cli.main(["plan", "rework", "--plan-id", "pln_1", "--confirm"]) == 1
    assert "already_triggered" in capsys.readouterr().err


def test_rework_respects_budget(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_seeded_project(tmp_path, monkeypatch, "fail")
    config_path = root / ".agentdeck" / "config.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + "\n[autonomous]\nmax_review_rounds = 0\n",
        encoding="utf-8",
    )
    assert cli.main(["plan", "rework", "--plan-id", "pln_1", "--confirm"]) == 1
    assert "rounds_exhausted" in capsys.readouterr().err


def _enable_autonomous(capsys) -> None:
    cli.main([
        "policy", "set-mode", "--mode", "autonomous", "--confirm",
        "--allow-agent", "coder", "--allow-agent", "reviewer", "--max-approvals", "8",
    ])
    capsys.readouterr()


def test_run_loop_wave_appends_iteration_on_fail_verdict(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_seeded_project(tmp_path, monkeypatch, "fail")
    _enable_autonomous(capsys)
    assert cli.main(["run-loop", "--plan-id", "pln_1", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    iterations = payload["review_iterations"]
    assert iterations[0]["round"] == 1
    assert iterations[0]["steps"] == [3, 4]
    state = StateStore(root).load()
    assert len(state["plans"][0]["plan"]["steps"]) == 4
    # 追加的审批当 wave 未被 auto-approve(选取先于追加),下一 wave 接手
    new_pending = [a for a in state["approvals"] if a["status"] == "pending"]
    assert len(new_pending) == 2


def test_run_loop_wave_reports_rounds_exhausted(tmp_path, monkeypatch, capsys) -> None:
    prepare_seeded_project(tmp_path, monkeypatch, "fail")
    _enable_autonomous(capsys)
    assert cli.main([
        "run-loop", "--plan-id", "pln_1", "--confirm", "--max-review-rounds", "0",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "review_iterations" not in payload  # 0 = 关闭,逐字节同现状
    assert cli.main(["run-loop", "--plan-id", "pln_1", "--confirm"]) == 0
    capsys.readouterr()
    # 第一次已消费 reply;造第二个 fail reply 并把预算压到 1 → exhausted
    store = StateStore(Path.cwd())
    state = store.load()
    state["approvals"].append({
        "approval_id": "apv_4", "plan_id": "pln_1", "step": 4, "agent_id": "reviewer",
        "role": "review", "task": "review the widget", "risk": "low",
        "status": "dispatched", "message_id": "msg_rev2",
    })
    state["messages"].append({"message_id": "msg_rev2", "worktree_branch": None})
    state["replies"].append({
        "reply_id": "rep_round2", "message_id": "msg_rev2", "from_agent": "reviewer",
        "text": "still failing", "verdict": _verdict("fail"),
    })
    store.save(state)
    assert cli.main([
        "run-loop", "--plan-id", "pln_1", "--confirm", "--max-review-rounds", "1",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["review_iterations"] == [{"skipped": "rounds_exhausted"}]


def test_run_loop_wave_without_verdict_is_byte_stable(tmp_path, monkeypatch, capsys) -> None:
    prepare_seeded_project(tmp_path, monkeypatch, "pass")
    _enable_autonomous(capsys)
    assert cli.main(["run-loop", "--plan-id", "pln_1", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "review_iterations" not in payload


def test_run_loop_all_plan_item_carries_review_iterations(tmp_path, monkeypatch, capsys) -> None:
    prepare_seeded_project(tmp_path, monkeypatch, "fail")
    _enable_autonomous(capsys)
    assert cli.main(["run-loop", "--all", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "run_loop_all"
    item = next(p for p in payload["plans"] if p["plan_id"] == "pln_1")
    assert item["review_iterations"][0]["round"] == 1


def test_run_loop_all_triggers_review_iteration_for_already_complete_plan(
    tmp_path, monkeypatch, capsys
) -> None:
    """Important-fix (2026-07-31): --all's pre-gate gate0==complete skip
    must not swallow an already-ingested fail verdict -- the single-plan
    engine never pre-gate-skips, so with every step already replied (gate0
    == complete) --all must still trigger the hook instead of silently
    treating the plan as done."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    store = StateStore(root)
    state = store.load()
    seed = _state("fail")
    # every step -- not just the review step -- needs a reply so gate0 ==
    # "complete" before the hook runs, exercising the pre-gate branch.
    seed["replies"].append({
        "reply_id": "rep_impl", "message_id": "msg_impl", "from_agent": "coder",
        "text": "status: completed\nsummary: done",
    })
    for key in ("plans", "approvals", "messages", "replies"):
        state[key] = seed[key]
    store.save(state)
    monkeypatch.chdir(root)
    _enable_autonomous(capsys)

    assert cli.main(["run-loop", "--all", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "run_loop_all"
    item = next((p for p in payload["plans"] if p["plan_id"] == "pln_1"), None)
    assert item is not None  # not silently pre-gate-skipped
    assert item["review_iterations"][0]["round"] == 1
