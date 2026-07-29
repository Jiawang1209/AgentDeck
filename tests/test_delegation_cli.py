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

CODEX_MCP_TOOL_BOX = (
    "  Allow the chrome-devtools MCP server to run tool hover?\n"
    "› 1. Yes, proceed (y)\n"
    "  2. Yes, and don't ask again this session\n"
    "  3. No, and tell Codex what to do differently (esc)\n"
    "  Press enter to confirm or esc to cancel\n"
)

CODEX_MCP_TOOL_BOX_FOLDED = (
    "  Allow the chrome-dev\n"
    "  tools MCP server to run tool\n"
    "  press_key?\n"
    "› 1. Yes, proceed (y)\n"
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


def test_controls_contract_holds_in_all_policy_modes(tmp_path, monkeypatch, capsys) -> None:
    # live 发现（GUI 冒烟）：approve/autonomous 模式下 ask set_mode 控件
    # enabled 且 safety=inspect，违反"enabled set_mode 必须 explicit_user"
    # 的注册表契约——切换 approval_mode 是配置写操作，inspect 语义错误。
    root = prepare_project(tmp_path, monkeypatch)

    for setup in (
        ["policy", "set-mode", "--mode", "approve"],
        ["policy", "set-mode", "--mode", "autonomous", "--confirm",
         "--allow-agent", "coder", "--max-approvals", "3"],
    ):
        cli.main(setup)
        capsys.readouterr()
        assert cli.main(["controls"]) == 0, f"controls failed after {setup}"
        payload = json.loads(capsys.readouterr().out)
        for item in payload["items"]:
            if item.get("scope") == "policy" and item.get("kind") == "set_mode" and item.get("enabled"):
                assert item["safety"] == "explicit_user"


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


COLLAPSED_CODEX_AUTH_BOX = (
    "  Environment: local\n"
    "  Reason: 是否允许在沙箱外启动本机无头\n"
    "  [… 10 lines] ctrl + a view all\n"
    "› 1. Yes, proceed (y)\n"
    "  2. Yes, and don't ask again for commands that start with `node tests/\n"
    "     focus-carousel-tab-order.mjs` (p)\n"
    "  3. No, and tell Codex what to do differently (esc)\n"
    "  Press enter to confirm or esc to cancel or o to open thread\n"
)


def test_boxes_extracts_command_from_collapsed_box_option_text(tmp_path, monkeypatch, capsys) -> None:
    # round 9 live 发现：codex 长框折叠中段，`$ 命令`行不在可见 pane 里，
    # 提取返回 None 导致委托无法匹配；回退=从选项 2 的
    # "commands that start with `…`" 反引号内容（跨行拼接）提取。
    root = prepare_project(tmp_path, monkeypatch)
    bind_coder(root)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    fake.output = COLLAPSED_CODEX_AUTH_BOX
    cli.main(["delegation", "grant", "--agent", "coder", "--prefix", "node tests/", "--confirm"])
    capsys.readouterr()

    assert cli.main(["agent", "boxes", "--agent", "coder"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["box_present"] is True
    assert payload["command"] == "node tests/focus-carousel-tab-order.mjs"
    assert payload["delegated"] is True

    assert cli.main(["agent", "release-box", "--agent", "coder", "--confirm"]) == 0
    capsys.readouterr()
    assert fake.sent == [("%50", "")]


def test_grant_delegation_writer_mcp_pair_and_mutual_exclusion(tmp_path, monkeypatch) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)

    record = store.grant_delegation("coder", mcp_server="chrome-devtools", mcp_tool="hover")
    assert record["kind"] == "mcp_tool"
    assert record["mcp_server"] == "chrome-devtools"
    assert record["mcp_tool"] == "hover"
    assert record["prefix"] is None
    assert record["revoked_at"] is None

    # prefix 记录带显式 kind，且 mcp 字段为 null
    prefix_record = store.grant_delegation("coder", "node tests/")
    assert prefix_record["kind"] == "command_prefix"
    assert prefix_record["mcp_server"] is None
    assert prefix_record["mcp_tool"] is None

    # 重复活跃 (agent, server, tool) 拒绝零写
    try:
        store.grant_delegation("coder", mcp_server="chrome-devtools", mcp_tool="hover")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    assert len(store.load()["delegations"]) == 2

    # 同 server 异 tool 是新委托，不算重复
    second = store.grant_delegation("coder", mcp_server="chrome-devtools", mcp_tool="press_key")
    assert second["mcp_tool"] == "press_key"

    # 二选一：都给或都不给都拒绝
    for kwargs in (
        {"prefix": "node tests/x", "mcp_server": "s", "mcp_tool": "t"},
        {},
        {"mcp_server": "chrome-devtools"},
        {"mcp_tool": "hover"},
    ):
        try:
            store.grant_delegation("coder", **kwargs)
            raise AssertionError(f"expected ValueError for {kwargs}")
        except ValueError:
            pass
    assert len(store.load()["delegations"]) == 3


def test_delegation_grant_mcp_form_and_mutual_exclusion(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    # 互斥：两种形态同时给 / 都不给 / MCP 对不完整 → 拒绝零写
    assert cli.main([
        "delegation", "grant", "--agent", "coder", "--prefix", "node tests/",
        "--mcp-server", "chrome-devtools", "--mcp-tool", "hover", "--confirm",
    ]) == 1
    assert "exactly one" in capsys.readouterr().err
    assert cli.main(["delegation", "grant", "--agent", "coder", "--confirm"]) == 1
    capsys.readouterr()
    assert cli.main([
        "delegation", "grant", "--agent", "coder", "--mcp-server", "chrome-devtools", "--confirm",
    ]) == 1
    capsys.readouterr()
    assert cli.main([
        "delegation", "grant", "--agent", "coder",
        "--mcp-server", "  ", "--mcp-tool", "hover", "--confirm",
    ]) == 1
    capsys.readouterr()
    assert StateStore(root).load().get("delegations", []) == []

    # MCP 形态 happy path：入账 + 审计
    assert cli.main([
        "delegation", "grant", "--agent", "coder",
        "--mcp-server", "chrome-devtools", "--mcp-tool", "hover", "--confirm",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "delegation_granted"
    assert payload["kind"] == "mcp_tool"
    assert payload["mcp_server"] == "chrome-devtools"
    assert payload["mcp_tool"] == "hover"
    assert payload["prefix"] is None
    assert '"event_type": "delegation_granted"' in _events_text(root)
    assert '"mcp_server": "chrome-devtools"' in _events_text(root)


def test_delegation_list_projects_kind_and_legacy_records(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    # 直接写一条旧形态记录（无 kind/mcp 字段）模拟既有数据
    store = StateStore(root)
    state = store.load()
    state.setdefault("delegations", []).append(
        {
            "delegation_id": "dlg_legacy",
            "agent_id": "coder",
            "prefix": "node tests/",
            "created_at": "2026-07-26T00:00:00+00:00",
            "revoked_at": None,
        }
    )
    store.save(state)
    cli.main([
        "delegation", "grant", "--agent", "coder",
        "--mcp-server", "chrome-devtools", "--mcp-tool", "press_key", "--confirm",
    ])
    capsys.readouterr()

    assert cli.main(["delegation", "list"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 2
    legacy = payload["items"][0]
    assert legacy["kind"] == "command_prefix"
    assert legacy["mcp_server"] is None
    assert legacy["mcp_tool"] is None
    mcp = payload["items"][1]
    assert mcp["kind"] == "mcp_tool"
    assert mcp["prefix"] is None
    assert mcp["active"] is True


def test_delegation_contract_exposes_mcp_fields(capsys) -> None:
    assert cli.main(["contract", "delegation", "--example"]) == 0
    payload = json.loads(capsys.readouterr().out)
    for field in ("kind", "mcp_server", "mcp_tool"):
        assert field in payload["delegation_item_fields"]
    assert "mcp_grant_command_template" in payload
    assert "--mcp-server <server>" in payload["mcp_grant_command_template"]
    kinds = {item["kind"] for item in payload["example_list"]["items"]}
    assert kinds == {"command_prefix", "mcp_tool"}


def test_extract_mcp_tool_box_fail_closed() -> None:
    assert cli._extract_mcp_tool_box(CODEX_MCP_TOOL_BOX) == ("chrome-devtools", "hover")
    # token 中间折行:全空白折叠还原
    assert cli._extract_mcp_tool_box(CODEX_MCP_TOOL_BOX_FOLDED) == (
        "chrome-devtools",
        "press_key",
    )
    # 非框文本 / 命令框 / 句尾缺 ? :一律 None(fail-closed)
    assert cli._extract_mcp_tool_box("worker is thinking...\n") is None
    assert cli._extract_mcp_tool_box(CODEX_AUTH_BOX) is None
    assert cli._extract_mcp_tool_box(
        "  Allow the chrome-devtools MCP server to run tool hover\n"
        "  Reason: replay\n"
    ) is None
    # 命令框提取器对 MCP 框返回 None(两类互不干扰)
    assert cli._extract_auth_box_command(CODEX_MCP_TOOL_BOX) is None


def test_match_active_delegation_mcp_arm() -> None:
    state = {
        "delegations": [
            {
                "delegation_id": "dlg_prefix",
                "agent_id": "coder",
                "prefix": "node tests/",
                "revoked_at": None,
            },
            {
                "delegation_id": "dlg_mcp",
                "agent_id": "planner",
                "kind": "mcp_tool",
                "prefix": None,
                "mcp_server": "chrome-devtools",
                "mcp_tool": "hover",
                "revoked_at": None,
            },
        ]
    }
    hit = cli._match_active_delegation(state, "planner", None, ("chrome-devtools", "hover"))
    assert hit["delegation_id"] == "dlg_mcp"
    # 同 server 异 tool / 同 tool 异 server / 异 agent / prefix 记录:都不命中
    assert cli._match_active_delegation(state, "planner", None, ("chrome-devtools", "press_key")) is None
    assert cli._match_active_delegation(state, "planner", None, ("other-server", "hover")) is None
    assert cli._match_active_delegation(state, "coder", None, ("chrome-devtools", "hover")) is None
    # revoked 不命中
    state["delegations"][1]["revoked_at"] = "2026-07-29T00:00:00+00:00"
    assert cli._match_active_delegation(state, "planner", None, ("chrome-devtools", "hover")) is None
    # 旧签名（无 mcp_box）prefix 路径不变
    assert cli._match_active_delegation(state, "coder", "node tests/x.mjs")["delegation_id"] == "dlg_prefix"
    # 双 None 直接 miss
    assert cli._match_active_delegation(state, "coder", None, None) is None


def test_agent_boxes_reports_mcp_tool_box(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_coder(root)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    fake.output = CODEX_MCP_TOOL_BOX

    # 未委托:检测到 MCP 框但 delegated=False,零写零输入
    before = StateStore(root).load()
    assert cli.main(["agent", "boxes", "--agent", "coder"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["box_present"] is True
    assert payload["box_kind"] == "mcp_tool"
    assert payload["command"] is None
    assert payload["mcp_server"] == "chrome-devtools"
    assert payload["mcp_tool"] == "hover"
    assert payload["delegated"] is False
    assert StateStore(root).load() == before
    assert fake.sent == []

    # grant 后命中;命令框路径 box_kind=command 回归
    cli.main([
        "delegation", "grant", "--agent", "coder",
        "--mcp-server", "chrome-devtools", "--mcp-tool", "hover", "--confirm",
    ])
    granted = json.loads(capsys.readouterr().out)
    assert cli.main(["agent", "boxes", "--agent", "coder"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["delegated"] is True
    assert payload["delegation_id"] == granted["delegation_id"]
    fake.output = CODEX_AUTH_BOX
    assert cli.main(["agent", "boxes", "--agent", "coder"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["box_kind"] == "command"
    assert payload["mcp_server"] is None


def test_release_box_releases_delegated_mcp_box_with_audit(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_coder(root)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    fake.output = CODEX_MCP_TOOL_BOX_FOLDED

    # 未委托拒绝,零输入
    assert cli.main(["agent", "release-box", "--agent", "coder", "--confirm"]) == 1
    assert "no active delegation" in capsys.readouterr().err
    assert fake.sent == []

    cli.main([
        "delegation", "grant", "--agent", "coder",
        "--mcp-server", "chrome-devtools", "--mcp-tool", "press_key", "--confirm",
    ])
    capsys.readouterr()
    assert cli.main(["agent", "release-box", "--agent", "coder", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "auth_box_released"
    assert payload["box_kind"] == "mcp_tool"
    assert payload["mcp_server"] == "chrome-devtools"
    assert payload["mcp_tool"] == "press_key"
    assert fake.sent == [("%50", "")]
    events = _events_text(root)
    assert '"event_type": "auth_box_released"' in events
    assert '"mcp_tool": "press_key"' in events


def test_boxes_watch_releases_delegated_mcp_box(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_coder(root)
    fake = DismissingTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    fake.output = CODEX_MCP_TOOL_BOX
    cli.main([
        "delegation", "grant", "--agent", "coder",
        "--mcp-server", "chrome-devtools", "--mcp-tool", "hover", "--confirm",
    ])
    granted = json.loads(capsys.readouterr().out)
    _enable_autonomous(capsys)
    fake.sent.clear()

    assert cli.main(["boxes", "watch", "--agent", "coder", "--confirm", "--iterations", "1", "--interval", "0"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["released_count"] == 1
    released = payload["released"][0]
    assert released["delegation_id"] == granted["delegation_id"]
    assert released["box_kind"] == "mcp_tool"
    assert released["mcp_server"] == "chrome-devtools"
    assert released["mcp_tool"] == "hover"
    assert fake.sent == [("%50", "")]
    assert '"event_type": "auth_box_released"' in _events_text(root)


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
