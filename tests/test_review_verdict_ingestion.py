from __future__ import annotations

import json
from pathlib import Path

from agentdeck import cli
from agentdeck.config import write_default_config
from agentdeck.models import AgentSpec
from agentdeck.state import StateStore

_FAKE_AGENT = AgentSpec(
    agent_id="reviewer", role="review", provider="codex", command="codex"
)


class FakeTmuxBackend:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.output = ""

    def send_input(self, _config, pane_id: str, text: str) -> None:
        self.sent.append((pane_id, text))

    def capture_output(self, _config, pane_id: str, lines: int = 200) -> str:
        return self.output


def prepare_project(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    monkeypatch.chdir(root)
    return root


def bind_planner(root: Path) -> None:
    store = StateStore(root)
    state = store.load()
    state["agents"]["planner"] = {
        "agent_id": "planner",
        "pane_id": "%42",
        "session_name": "agentdeck",
        "cwd": str(root),
        "status": "running",
    }
    store.save(state)


def _verdict() -> dict[str, object]:
    return {
        "schema_version": "review-verdict/v1",
        "criteria": [
            {"criterion": "README 包含新命令", "verdict": "pass"},
            {"criterion": "测试全绿", "verdict": "fail"},
        ],
        "overall": "needs_changes",
        "score": 55,
    }


def _dispatch(root: Path, monkeypatch, capsys) -> str:
    bind_planner(root)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["dispatch", "--from-agent", "coder", "--agent", "planner", "--task", "复核实现"])
    return json.loads(capsys.readouterr().out)["message_id"]


def _events_of_type(store: StateStore, event_type: str) -> list[dict[str, object]]:
    return [
        event
        for event in store.list_events(limit=100)
        if event.get("event_type") == event_type
    ]


def test_reply_with_verdict_line_records_verdict_and_event(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    message_id = _dispatch(root, monkeypatch, capsys)
    text = (
        "status: completed\n"
        "summary: 复核完成\n"
        f"verdict: {json.dumps(_verdict(), ensure_ascii=False)}"
    )

    exit_code = cli.main(["reply", "--agent", "planner", "--message-id", message_id, "--text", text])

    assert exit_code == 0
    capsys.readouterr()
    store = StateStore(root)
    reply = store.load()["replies"][0]
    assert reply["verdict"] == _verdict()
    events = _events_of_type(store, "review_verdict_recorded")
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["reply_id"] == reply["reply_id"]
    assert payload["overall"] == "needs_changes"
    assert payload["criteria_count"] == 2
    assert payload["score"] == 55
    assert _events_of_type(store, "review_verdict_invalid") == []


def test_reply_without_verdict_line_keeps_record_shape(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    message_id = _dispatch(root, monkeypatch, capsys)

    exit_code = cli.main(
        [
            "reply",
            "--agent",
            "planner",
            "--message-id",
            message_id,
            "--text",
            "status: completed\nsummary: 无判定",
        ]
    )

    assert exit_code == 0
    capsys.readouterr()
    store = StateStore(root)
    reply = store.load()["replies"][0]
    assert "verdict" not in reply
    assert _events_of_type(store, "review_verdict_recorded") == []
    assert _events_of_type(store, "review_verdict_invalid") == []


def test_invalid_verdict_never_blocks_reply_ingestion(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    message_id = _dispatch(root, monkeypatch, capsys)
    text = "status: completed\nsummary: 判定坏了\nverdict: {not valid json"

    exit_code = cli.main(["reply", "--agent", "planner", "--message-id", message_id, "--text", text])

    assert exit_code == 0
    capsys.readouterr()
    store = StateStore(root)
    state = store.load()
    reply = state["replies"][0]
    assert "verdict" not in reply
    assert reply["text"] == text
    assert state["messages"][0]["status"] == "replied"
    invalid_events = _events_of_type(store, "review_verdict_invalid")
    assert len(invalid_events) == 1
    assert invalid_events[0]["payload"]["message_id"] == message_id
    assert _events_of_type(store, "review_verdict_recorded") == []


def test_project_view_and_trace_expose_reply_verdict(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    message_id = _dispatch(root, monkeypatch, capsys)
    text = f"status: completed\nverdict: {json.dumps(_verdict(), ensure_ascii=False)}"
    cli.main(["reply", "--agent", "planner", "--message-id", message_id, "--text", text])
    capsys.readouterr()

    assert cli.main(["status"]) == 0
    view = json.loads(capsys.readouterr().out)
    assert view["replies"]["items"][0]["verdict"] == _verdict()

    reply_id = StateStore(root).load()["replies"][0]["reply_id"]
    assert cli.main(["trace", "--id", reply_id]) == 0
    trace = json.loads(capsys.readouterr().out)
    assert trace["replies"][0]["verdict"] == _verdict()


def _enable_split(root: Path) -> None:
    config_path = root / ".agentdeck" / "config.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + '\n[leader.planner]\nprovider = "fake"\nmodel = "fake-planner"\n'
        + '\n[leader.orchestrator]\nprovider = "fake"\nmodel = "fake-orchestrator"\n',
        encoding="utf-8",
    )


def _bind_agent(root: Path, agent_id: str, pane_id: str) -> None:
    store = StateStore(root)
    state = store.load()
    state["agents"][agent_id] = {
        "agent_id": agent_id,
        "pane_id": pane_id,
        "session_name": "agentdeck",
        "cwd": str(root),
        "status": "running",
    }
    store.save(state)


def _run_plan_to_replies(
    root: Path, monkeypatch, capsys, verdict_text: str | None
) -> tuple[str, list[str]]:
    """Plan → approve-plan → dispatch each step → reply each; last reply may carry a verdict."""
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    fake_config = root / ".agentdeck" / "config.toml"
    fake_config.write_text(
        fake_config.read_text(encoding="utf-8").replace('provider = "deepseek"', 'provider = "fake"', 1).replace(
            'model = "deepseek-chat"', 'model = "fake-plan"', 1
        ),
        encoding="utf-8",
    )
    assert cli.main(["leader", "plan", "--task", "量化验收目标"]) == 0
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    assert cli.main(["approval", "create-from-plan", "--plan-id", plan_id]) == 0
    capsys.readouterr()
    assert cli.main(["approval", "approve-plan", "--plan-id", plan_id, "--confirm"]) == 0
    capsys.readouterr()
    state = StateStore(root).load()
    approvals = [item for item in state["approvals"] if item.get("plan_id") == plan_id]
    message_ids: list[str] = []
    for index, approval in enumerate(approvals):
        agent_id = approval["agent_id"]
        _bind_agent(root, agent_id, f"%{40 + index}")
        assert cli.main(["approval", "dispatch", "--approval-id", approval["approval_id"]]) == 0
        message_id = json.loads(capsys.readouterr().out)["message_id"]
        message_ids.append(message_id)
        is_last = index == len(approvals) - 1
        text = "status: completed\nsummary: 完成"
        if is_last and verdict_text is not None:
            text += f"\nverdict: {verdict_text}"
        assert cli.main(["reply", "--agent", agent_id, "--message-id", message_id, "--text", text]) == 0
        capsys.readouterr()
    return plan_id, message_ids


def _expected_summary(task: str) -> dict[str, object]:
    return {
        "criteria_total": 2,
        "passed": 1,
        "failed": 1,
        "unknown": 0,
        "overall": "needs_changes",
        "score": 70,
        "unverified": ["全部相关验证通过"],
        "extra": ["额外检查项"],
    }


def _summary_verdict(task: str) -> dict[str, object]:
    return {
        "schema_version": "review-verdict/v1",
        "criteria": [
            {"criterion": f"任务 '{task}' 的产出已生成并通过检查", "verdict": "pass"},
            {"criterion": "额外检查项", "verdict": "fail"},
        ],
        "overall": "needs_changes",
        "score": 70,
    }


def test_leader_review_and_run_progress_expose_verdict_summary(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    _enable_split(root)
    task = "量化验收目标"
    verdict = _summary_verdict(task)
    plan_id, _ = _run_plan_to_replies(
        root, monkeypatch, capsys, json.dumps(verdict, ensure_ascii=False)
    )

    assert cli.main(["leader", "review", "--plan-id", plan_id]) == 0
    review = json.loads(capsys.readouterr().out)
    assert review["verdict_summary"] == _expected_summary(task)

    assert cli.main(["run", "--plan-id", plan_id]) == 0
    progress = json.loads(capsys.readouterr().out)
    assert progress["verdict_summary"] == _expected_summary(task)


def test_leader_summary_exposes_verdict_summary(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    _enable_split(root)
    task = "量化验收目标"
    verdict = _summary_verdict(task)
    plan_id, _ = _run_plan_to_replies(
        root, monkeypatch, capsys, json.dumps(verdict, ensure_ascii=False)
    )

    assert cli.main(["leader", "summary", "--plan-id", plan_id]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["verdict_summary"] == _expected_summary(task)


def test_verdict_summary_null_without_any_verdict(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    plan_id, _ = _run_plan_to_replies(root, monkeypatch, capsys, None)

    assert cli.main(["leader", "review", "--plan-id", plan_id]) == 0
    review = json.loads(capsys.readouterr().out)
    assert "verdict_summary" in review
    assert review["verdict_summary"] is None

    assert cli.main(["run", "--plan-id", plan_id]) == 0
    progress = json.loads(capsys.readouterr().out)
    assert progress["verdict_summary"] is None


def _enable_autonomous(root: Path) -> None:
    config_path = root / ".agentdeck" / "config.toml"
    text = config_path.read_text(encoding="utf-8").replace(
        'approval_mode = "confirm"', 'approval_mode = "autonomous"', 1
    )
    text += '\n[autonomous]\nallowed_agents = ["planner", "coder", "reviewer"]\nmax_approvals = 5\n'
    config_path.write_text(text, encoding="utf-8")


def test_merge_on_complete_withheld_when_verdict_not_pass(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    _enable_split(root)
    _enable_autonomous(root)
    verdict = _summary_verdict("量化验收目标")  # overall=needs_changes
    plan_id, _ = _run_plan_to_replies(
        root, monkeypatch, capsys, json.dumps(verdict, ensure_ascii=False)
    )

    exit_code = cli.main(
        ["run-loop", "--plan-id", plan_id, "--confirm", "--follow", "--max-waves", "2", "--interval", "1", "--merge-on-complete"]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["waves"][-1]["stopped_reason"] == "complete"
    merge = payload["plan_merge"]
    assert merge["mode"] == "verdict_blocked"
    assert merge["ok"] is False
    assert "needs_changes" in merge["blocker"]
    assert merge["next_command"] == f"agentdeck worktree merge-plan --plan-id {plan_id} --confirm"


def test_merge_on_complete_proceeds_when_verdict_pass(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    _enable_split(root)
    _enable_autonomous(root)
    verdict = _summary_verdict("量化验收目标")
    verdict["overall"] = "pass"
    plan_id, _ = _run_plan_to_replies(
        root, monkeypatch, capsys, json.dumps(verdict, ensure_ascii=False)
    )

    exit_code = cli.main(
        ["run-loop", "--plan-id", plan_id, "--confirm", "--follow", "--max-waves", "2", "--interval", "1", "--merge-on-complete"]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    merge = payload["plan_merge"]
    assert merge["mode"] == "worktree_merge_plan"


def test_merge_on_complete_unchanged_without_verdict(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    _enable_autonomous(root)
    plan_id, _ = _run_plan_to_replies(root, monkeypatch, capsys, None)

    exit_code = cli.main(
        ["run-loop", "--plan-id", plan_id, "--confirm", "--follow", "--max-waves", "2", "--interval", "1", "--merge-on-complete"]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["plan_merge"]["mode"] == "worktree_merge_plan"


def _init_real_git(root: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "tester"], check=True)
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "init"], check=True)


def test_build_dispatch_prompt_injects_review_criteria_and_verdict_format() -> None:
    prompt = cli.build_dispatch_prompt(
        _FAKE_AGENT,
        "复核实现产物",
        review_criteria=["README 包含新命令", "测试全绿"],
    )
    assert "验收标准" in prompt
    assert "README 包含新命令" in prompt
    assert "测试全绿" in prompt
    assert "review-verdict/v1" in prompt
    assert "verdict:" in prompt


def test_build_dispatch_prompt_without_criteria_is_unchanged() -> None:
    baseline = cli.build_dispatch_prompt(_FAKE_AGENT, "普通任务")
    assert "review-verdict/v1" not in baseline
    assert cli.build_dispatch_prompt(_FAKE_AGENT, "普通任务", review_criteria=None) == baseline
    assert cli.build_dispatch_prompt(_FAKE_AGENT, "普通任务", review_criteria=[]) == baseline


def test_review_step_dispatch_prompt_carries_criteria_for_split_plan(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    _enable_split(root)
    _init_real_git(root)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    fake_config = root / ".agentdeck" / "config.toml"
    fake_config.write_text(
        fake_config.read_text(encoding="utf-8")
        .replace('provider = "deepseek"', 'provider = "fake"', 1)
        .replace('model = "deepseek-chat"', 'model = "fake-plan"', 1),
        encoding="utf-8",
    )
    task = "量化验收目标"
    assert cli.main(["leader", "plan", "--task", task]) == 0
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    assert cli.main(["approval", "create-from-plan", "--plan-id", plan_id]) == 0
    capsys.readouterr()
    assert cli.main(["approval", "approve-plan", "--plan-id", plan_id, "--confirm"]) == 0
    capsys.readouterr()
    approvals = [
        item
        for item in StateStore(root).load()["approvals"]
        if item.get("plan_id") == plan_id
    ]
    prompts: list[str] = []
    for index, approval in enumerate(approvals):
        agent_id = approval["agent_id"]
        _bind_agent(root, agent_id, f"%{60 + index}")
        assert cli.main(["approval", "dispatch", "--approval-id", approval["approval_id"]]) == 0
        capsys.readouterr()
        prompts.append(fake.sent[-1][1])

    assert "review-verdict/v1" not in prompts[0]
    criterion = f"任务 '{task}' 的产出已生成并通过检查"
    review_prompts = [prompt for prompt in prompts if "review-verdict/v1" in prompt]
    assert review_prompts
    assert all(criterion in prompt for prompt in review_prompts)


def test_project_view_reply_without_verdict_projects_null(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    message_id = _dispatch(root, monkeypatch, capsys)
    cli.main(["reply", "--agent", "planner", "--message-id", message_id, "--text", "status: completed"])
    capsys.readouterr()

    assert cli.main(["status"]) == 0
    view = json.loads(capsys.readouterr().out)
    item = view["replies"]["items"][0]
    assert "verdict" in item
    assert item["verdict"] is None
