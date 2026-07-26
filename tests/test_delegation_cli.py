from __future__ import annotations

import json
from pathlib import Path

from agentdeck import cli
from agentdeck.config import write_default_config
from agentdeck.state import StateStore


def prepare_project(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    monkeypatch.chdir(root)
    return root


class FakeTmuxBackend:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.output = ""

    def send_input(self, _config, pane_id: str, text: str) -> None:
        self.sent.append((pane_id, text))

    def capture_output(self, _config, pane_id: str, lines: int = 200) -> str:
        return self.output


def bind_coder(root: Path) -> None:
    store = StateStore(root)
    state = store.load()
    state["agents"]["coder"] = {
        "agent_id": "coder",
        "pane_id": "%50",
        "session_name": "agentdeck",
        "cwd": str(root),
        "status": "running",
    }
    store.save(state)


CODEX_AUTH_BOX = (
    "  Would you like to run the following command?\n"
    "  Environment: local\n"
    "  Reason: 验证轮播回归\n"
    "  $ node tests/focus-carousel-tab-order.mjs\n"
    "› 1. Yes, proceed (y)\n"
    "  2. Yes, and don't ask again for commands that start with `node tests/`\n"
    "  3. No, and tell Codex what to do differently (esc)\n"
    "  Press enter to confirm or esc to cancel\n"
)


def _events_text(root: Path) -> str:
    return (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")


def test_delegation_grant_requires_confirm_and_writes_registry(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    # 缺 --confirm：拒绝零写
    assert cli.main(["delegation", "grant", "--agent", "coder", "--prefix", "node tests/"]) == 1
    capsys.readouterr()
    assert StateStore(root).load().get("delegations", []) == []

    assert cli.main(["delegation", "grant", "--agent", "coder", "--prefix", "node tests/", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "delegation_granted"
    assert payload["agent_id"] == "coder"
    assert payload["prefix"] == "node tests/"
    delegation_id = payload["delegation_id"]
    assert delegation_id.startswith("dlg_")
    records = StateStore(root).load()["delegations"]
    assert len(records) == 1
    assert records[0]["delegation_id"] == delegation_id
    assert records[0]["revoked_at"] is None
    assert '"event_type": "delegation_granted"' in _events_text(root)


def test_delegation_grant_rejects_unknown_agent_and_empty_prefix(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    assert cli.main(["delegation", "grant", "--agent", "ghost", "--prefix", "node tests/", "--confirm"]) == 1
    assert "unknown agent" in capsys.readouterr().err
    assert cli.main(["delegation", "grant", "--agent", "coder", "--prefix", "   ", "--confirm"]) == 1
    assert "prefix" in capsys.readouterr().err
    assert StateStore(root).load().get("delegations", []) == []


def test_delegation_grant_rejects_duplicate_active(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    assert cli.main(["delegation", "grant", "--agent", "coder", "--prefix", "node tests/", "--confirm"]) == 0
    capsys.readouterr()

    assert cli.main(["delegation", "grant", "--agent", "coder", "--prefix", "node tests/", "--confirm"]) == 1
    assert "already" in capsys.readouterr().err
    assert len(StateStore(root).load()["delegations"]) == 1


def test_delegation_list_is_read_only(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["delegation", "grant", "--agent", "coder", "--prefix", "node tests/", "--confirm"])
    capsys.readouterr()
    before = StateStore(root).load()

    assert cli.main(["delegation", "list"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "delegation_list"
    assert payload["count"] == 1
    item = payload["items"][0]
    assert item["agent_id"] == "coder"
    assert item["prefix"] == "node tests/"
    assert item["active"] is True
    assert StateStore(root).load() == before


def test_delegation_revoke_lifecycle(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    cli.main(["delegation", "grant", "--agent", "coder", "--prefix", "node tests/", "--confirm"])
    delegation_id = json.loads(capsys.readouterr().out)["delegation_id"]

    # 缺 confirm 拒绝零写
    before = StateStore(root).load()
    assert cli.main(["delegation", "revoke", "--delegation-id", delegation_id]) == 1
    assert StateStore(root).load() == before
    capsys.readouterr()

    assert cli.main(["delegation", "revoke", "--delegation-id", delegation_id, "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "delegation_revoked"
    record = StateStore(root).load()["delegations"][0]
    assert record["revoked_at"] is not None
    assert '"event_type": "delegation_revoked"' in _events_text(root)

    # 二次 revoke 拒绝
    assert cli.main(["delegation", "revoke", "--delegation-id", delegation_id, "--confirm"]) == 1
    capsys.readouterr()

    # revoked 后 list 显示 active=False
    assert cli.main(["delegation", "list"]) == 0
    item = json.loads(capsys.readouterr().out)["items"][0]
    assert item["active"] is False

    # revoked 后可重新 grant 同前缀
    assert cli.main(["delegation", "grant", "--agent", "coder", "--prefix", "node tests/", "--confirm"]) == 0


def test_agent_boxes_is_read_only_detection(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_coder(root)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)

    # 无框：box_present=False
    fake.output = "worker is thinking...\nno prompts here\n"
    assert cli.main(["agent", "boxes", "--agent", "coder"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "agent_boxes"
    assert payload["box_present"] is False
    assert payload["command"] is None
    assert payload["delegated"] is False

    # 有框但无委托：检测到命令，delegated=False
    fake.output = CODEX_AUTH_BOX
    assert cli.main(["agent", "boxes", "--agent", "coder"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["box_present"] is True
    assert payload["command"] == "node tests/focus-carousel-tab-order.mjs"
    assert payload["delegated"] is False
    assert payload["delegation_id"] is None

    # grant 后命中
    cli.main(["delegation", "grant", "--agent", "coder", "--prefix", "node tests/", "--confirm"])
    granted = json.loads(capsys.readouterr().out)
    before = StateStore(root).load()
    assert cli.main(["agent", "boxes", "--agent", "coder"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["delegated"] is True
    assert payload["delegation_id"] == granted["delegation_id"]
    assert payload["release_command"] == "agentdeck agent release-box --agent coder --confirm"
    # 只读：零写、零输入
    assert StateStore(root).load() == before
    assert fake.sent == []


def test_release_box_requires_confirm_and_delegation_match(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_coder(root)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    fake.output = CODEX_AUTH_BOX

    # 缺 confirm：拒绝零发送
    assert cli.main(["agent", "release-box", "--agent", "coder"]) == 1
    assert "confirm" in capsys.readouterr().err
    assert fake.sent == []

    # 无委托命中：拒绝零发送、无事件
    assert cli.main(["agent", "release-box", "--agent", "coder", "--confirm"]) == 1
    assert "no active delegation" in capsys.readouterr().err
    assert fake.sent == []
    assert '"event_type": "auth_box_released"' not in _events_text(root)

    # grant 后释放：发回车 + 审计事件
    cli.main(["delegation", "grant", "--agent", "coder", "--prefix", "node tests/", "--confirm"])
    granted = json.loads(capsys.readouterr().out)
    assert cli.main(["agent", "release-box", "--agent", "coder", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "auth_box_released"
    assert payload["delegation_id"] == granted["delegation_id"]
    assert payload["command"] == "node tests/focus-carousel-tab-order.mjs"
    assert fake.sent == [("%50", "")]
    assert '"event_type": "auth_box_released"' in _events_text(root)

    # revoke 后不再命中
    cli.main(["delegation", "revoke", "--delegation-id", granted["delegation_id"], "--confirm"])
    capsys.readouterr()
    assert cli.main(["agent", "release-box", "--agent", "coder", "--confirm"]) == 1
    capsys.readouterr()
    assert fake.sent == [("%50", "")]


def _enable_autonomous(capsys) -> None:
    cli.main([
        "policy", "set-mode", "--mode", "autonomous", "--confirm",
        "--allow-agent", "coder", "--max-approvals", "3",
    ])
    capsys.readouterr()


class DismissingTmuxBackend(FakeTmuxBackend):
    """发送回车后授权框消失（模拟真实 pane 行为）。"""

    def send_input(self, _config, pane_id: str, text: str) -> None:
        super().send_input(_config, pane_id, text)
        self.output = "worker continues...\n"


def test_boxes_watch_requires_confirm_and_autonomous_mode(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_coder(root)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    fake.output = CODEX_AUTH_BOX
    cli.main(["delegation", "grant", "--agent", "coder", "--prefix", "node tests/", "--confirm"])
    capsys.readouterr()

    # 缺 confirm
    assert cli.main(["boxes", "watch", "--agent", "coder", "--iterations", "1", "--interval", "0"]) == 1
    assert "confirm" in capsys.readouterr().err
    # confirm 但非 autonomous 模式
    assert cli.main(["boxes", "watch", "--agent", "coder", "--confirm", "--iterations", "1", "--interval", "0"]) == 1
    assert "autonomous" in capsys.readouterr().err
    assert fake.sent == []


def test_boxes_watch_releases_delegated_box_and_stops_at_bound(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_coder(root)
    fake = DismissingTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    fake.output = CODEX_AUTH_BOX
    cli.main(["delegation", "grant", "--agent", "coder", "--prefix", "node tests/", "--confirm"])
    granted = json.loads(capsys.readouterr().out)
    _enable_autonomous(capsys)
    fake.sent.clear()

    assert cli.main(["boxes", "watch", "--agent", "coder", "--confirm", "--iterations", "2", "--interval", "0"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "boxes_watch"
    assert payload["iterations"] == 2
    assert payload["released_count"] == 1
    released = payload["released"][0]
    assert released["agent_id"] == "coder"
    assert released["delegation_id"] == granted["delegation_id"]
    assert released["command"] == "node tests/focus-carousel-tab-order.mjs"
    assert fake.sent == [("%50", "")]
    assert '"event_type": "auth_box_released"' in _events_text(root)


def test_boxes_watch_skips_non_delegated_box(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_coder(root)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    fake.output = CODEX_AUTH_BOX.replace("node tests/focus-carousel-tab-order.mjs", "rm -rf /tmp/x")
    cli.main(["delegation", "grant", "--agent", "coder", "--prefix", "node tests/", "--confirm"])
    capsys.readouterr()
    _enable_autonomous(capsys)
    fake.sent.clear()

    assert cli.main(["boxes", "watch", "--agent", "coder", "--confirm", "--iterations", "1", "--interval", "0"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["released_count"] == 0
    assert payload["skipped_count"] == 1
    assert payload["skipped"][0]["reason"] == "no active delegation"
    assert payload["skipped"][0]["command"] == "rm -rf /tmp/x"
    assert fake.sent == []
    assert '"event_type": "auth_box_released"' not in _events_text(root)


def test_contract_delegation_discovery(tmp_path, monkeypatch, capsys) -> None:
    prepare_project(tmp_path, monkeypatch)

    assert cli.main(["contract", "delegation"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["list_command"] == "agentdeck delegation list"
    assert payload["grant_command_template"] == "agentdeck delegation grant --agent <agent_id> --prefix <prefix> --confirm"
    assert payload["revoke_command_template"] == "agentdeck delegation revoke --delegation-id <delegation_id> --confirm"
    assert payload["boxes_command_template"] == "agentdeck agent boxes --agent <agent_id>"
    assert payload["release_box_command_template"] == "agentdeck agent release-box --agent <agent_id> --confirm"
    assert payload["watch_command_template"] == "agentdeck boxes watch --confirm --iterations <n> --interval <seconds>"
    assert "delegation_id" in payload["delegation_item_fields"]
    assert "active" in payload["delegation_item_fields"]
    assert "box_present" in payload["boxes_response_fields"]
    assert "released_count" in payload["watch_response_fields"]
    assert payload["contract_exists"] is True

    assert cli.main(["contract", "delegation", "--example"]) == 0
    example = json.loads(capsys.readouterr().out)
    assert example["example"] is True
    assert example["example_list"]["items"][0]["active"] is True
    assert example["example_boxes"]["box_present"] is True
    assert example["example_watch"]["released_count"] == 1


def test_contract_list_includes_delegation(tmp_path, monkeypatch, capsys) -> None:
    prepare_project(tmp_path, monkeypatch)

    assert cli.main(["contract", "list"]) == 0
    out = capsys.readouterr().out
    assert "agentdeck contract delegation" in out
    assert "delegation-schema.md" in out


def test_release_box_refuses_without_box(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_coder(root)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["delegation", "grant", "--agent", "coder", "--prefix", "node tests/", "--confirm"])
    capsys.readouterr()
    fake.output = "worker is idle at composer\n"

    assert cli.main(["agent", "release-box", "--agent", "coder", "--confirm"]) == 1
    assert "no authorization box" in capsys.readouterr().err
    assert fake.sent == []
