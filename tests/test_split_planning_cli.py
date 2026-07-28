from __future__ import annotations

import json
from pathlib import Path

from agentdeck import cli
from agentdeck.config import write_default_config
from agentdeck.providers.fake import FakeLeaderProvider
from agentdeck.providers.planner_brief import PLANNER_BRIEF_SCHEMA_VERSION
from agentdeck.state import StateStore


def prepare_project(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    config_path = root / ".agentdeck" / "config.toml"
    config_text = config_path.read_text(encoding="utf-8")
    config_text = config_text.replace('provider = "deepseek"', 'provider = "fake"', 1)
    config_text = config_text.replace('model = "deepseek-chat"', 'model = "fake-plan"', 1)
    config_path.write_text(config_text, encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def _enable_split(root: Path) -> None:
    config_path = root / ".agentdeck" / "config.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + "\n[leader.planner]\n"
        + 'provider = "fake"\n'
        + 'model = "fake-planner"\n'
        + "\n[leader.orchestrator]\n"
        + 'provider = "fake"\n'
        + 'model = "fake-orchestrator"\n',
        encoding="utf-8",
    )


def _events_of_type(store: StateStore, event_type: str) -> list[dict[str, object]]:
    return [
        event
        for event in store.list_events(limit=100)
        if event.get("event_type") == event_type
    ]


def test_leader_plan_split_records_three_provenance_fields(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    _enable_split(root)

    exit_code = cli.main(["leader", "plan", "--task", "拆分验证"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    record = StateStore(root).load()["plans"][0]
    for source in (payload, record):
        assert source["provider"] == "fake"
        assert source["model"] == "fake-orchestrator"
        assert source["leader_backend"]["model"] == "fake-orchestrator"
    assert record["planner_backend"]["provider"] == "fake"
    assert record["planner_backend"]["model"] == "fake-planner"
    assert record["planner_backend"]["runtime_kind"] == "logical_leader"
    assert record["orchestrator_backend"]["provider"] == "fake"
    assert record["orchestrator_backend"]["model"] == "fake-orchestrator"
    brief = record["planner_brief"]
    assert brief["schema_version"] == PLANNER_BRIEF_SCHEMA_VERSION
    assert isinstance(brief["content_hash"], str) and len(brief["content_hash"]) == 64
    assert brief["acceptance_criteria"]
    assert all(step["requires_approval"] is True for step in record["plan"]["steps"])


def test_leader_plan_without_split_keeps_record_shape(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    exit_code = cli.main(["leader", "plan", "--task", "无拆分基线"])

    assert exit_code == 0
    record = StateStore(root).load()["plans"][0]
    assert "planner_backend" not in record
    assert "orchestrator_backend" not in record
    assert "planner_brief" not in record


def test_leader_plan_explicit_override_bypasses_split(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    _enable_split(root)

    exit_code = cli.main(
        ["leader", "plan", "--task", "dry run", "--provider", "fake", "--model", "fake-plan"]
    )

    assert exit_code == 0
    record = StateStore(root).load()["plans"][0]
    assert record["model"] == "fake-plan"
    assert "planner_backend" not in record
    assert "planner_brief" not in record


def test_planner_stage_failure_is_audited_with_stage(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    _enable_split(root)

    def _boom(self, **_kwargs):
        raise RuntimeError("planner backend down")

    monkeypatch.setattr(FakeLeaderProvider, "plan_brief", _boom)

    exit_code = cli.main(["leader", "plan", "--task", "拆分失败"])

    assert exit_code == 1
    assert "leader provider failed" in capsys.readouterr().err
    store = StateStore(root)
    assert store.load()["plans"] == []
    events = _events_of_type(store, "leader_provider_failed")
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["stage"] == "planner"
    assert payload["provider"] == "fake"
    assert payload["model"] == "fake-planner"


def test_orchestrator_stage_failure_is_audited_with_stage(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    _enable_split(root)

    def _boom(self, request):
        raise RuntimeError("orchestrator backend down")

    monkeypatch.setattr(FakeLeaderProvider, "plan", _boom)

    exit_code = cli.main(["leader", "plan", "--task", "拆分失败"])

    assert exit_code == 1
    store = StateStore(root)
    assert store.load()["plans"] == []
    payload = _events_of_type(store, "leader_provider_failed")[0]["payload"]
    assert payload["stage"] == "orchestrator"
    assert payload["model"] == "fake-orchestrator"


def test_single_stage_failure_event_keeps_shape_without_stage(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    def _boom(self, request):
        raise RuntimeError("provider down")

    monkeypatch.setattr(FakeLeaderProvider, "plan", _boom)

    exit_code = cli.main(["leader", "plan", "--task", "单段失败"])

    assert exit_code == 1
    payload = _events_of_type(StateStore(root), "leader_provider_failed")[0]["payload"]
    assert "stage" not in payload


def test_run_task_split_records_provenance(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    _enable_split(root)

    exit_code = cli.main(["run", "--task", "run 拆分验证"])

    assert exit_code == 0
    record = StateStore(root).load()["plans"][0]
    assert record["model"] == "fake-orchestrator"
    assert record["planner_brief"]["schema_version"] == PLANNER_BRIEF_SCHEMA_VERSION
    assert record["orchestrator_backend"]["model"] == "fake-orchestrator"
