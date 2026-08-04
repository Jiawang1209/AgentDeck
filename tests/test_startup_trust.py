from __future__ import annotations

from agentdeck.startup_trust import (
    TRUST_STATES,
    TRUST_UNKNOWN_REASONS,
    classify_startup_trust,
)


def test_codex_recorded_as_trusted_is_trusted() -> None:
    result = classify_startup_trust(provider="codex", recorded=True)

    assert result["state"] == "trusted"
    assert result["reason"] is None


def test_codex_absent_from_its_project_list_is_untrusted() -> None:
    # 首次进入一个新目录:codex 会弹 "Do you trust the contents of this
    # directory?" 并停在那儿,pane 起来了但 REPL 永远没进。
    result = classify_startup_trust(provider="codex", recorded=False)

    assert result["state"] == "untrusted"
    assert result["reason"] is None


def test_claude_is_resolved_the_same_way() -> None:
    assert classify_startup_trust(provider="claude", recorded=True)["state"] == "trusted"
    assert (
        classify_startup_trust(provider="claude", recorded=False)["state"] == "untrusted"
    )


def test_unknown_provider_is_unknown_never_trusted() -> None:
    # 自定义 command 的 agent:我们不知道它把 trust 存在哪,甚至不知道它有没有
    # 这个概念。那是"查不了",不是"没问题"。
    result = classify_startup_trust(provider="my-own-agent", recorded=None)

    assert result["state"] == "unknown"
    assert result["reason"] == "provider_not_recognized"


def test_recognized_provider_with_unreadable_config_is_unknown_not_untrusted() -> None:
    # 配置文件读不出来时绝不能塌成 untrusted——那会让一个早就信任过的目录
    # 被报成"要去按框",人按预检去 attach 却发现无框可按,预检就此失去信用。
    result = classify_startup_trust(provider="codex", recorded=None)

    assert result["state"] == "unknown"
    assert result["reason"] == "trust_state_unreadable"


def test_states_and_reasons_are_closed_enums() -> None:
    assert set(TRUST_STATES) == {"trusted", "untrusted", "unknown"}
    assert set(TRUST_UNKNOWN_REASONS) == {
        "provider_not_recognized",
        "trust_state_unreadable",
    }


def test_unknown_never_reads_as_a_clean_result() -> None:
    # 与 review_digest / branch_custody 同一条纪律。
    unknown = classify_startup_trust(provider="my-own-agent", recorded=None)

    assert unknown["state"] != "trusted"
    assert not any(value is False for value in unknown.values())


# ---- CLI:显式写入两个 CLI 自己的信任记录 ----

import json
import pathlib

from agentdeck import cli
from agentdeck.config import write_default_config


def _project_with_clean_home(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    monkeypatch.chdir(root)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return root, home


def test_agent_trust_requires_confirm_and_writes_nothing(tmp_path, monkeypatch, capsys) -> None:
    _root, home = _project_with_clean_home(tmp_path, monkeypatch)

    assert cli.main(["agent", "trust"]) == 1
    capsys.readouterr()
    assert not (home / ".codex").exists()
    assert not (home / ".claude.json").exists()


def test_agent_trust_appends_codex_entry_without_touching_existing_bytes(
    tmp_path, monkeypatch, capsys
) -> None:
    # codex 的 config.toml 有注释和上百条记录:只能追加,绝不解析后重写。
    root, home = _project_with_clean_home(tmp_path, monkeypatch)
    codex = home / ".codex"
    codex.mkdir()
    original = '# my notes\nmodel = "gpt-5.6-sol"\n\n[projects."/somewhere/else"]\ntrust_level = "trusted"\n'
    (codex / "config.toml").write_text(original, encoding="utf-8")

    assert cli.main(["agent", "trust", "--confirm"]) == 0
    capsys.readouterr()

    written = (codex / "config.toml").read_text(encoding="utf-8")
    assert written.startswith(original)          # 原字节逐字保留
    assert f'[projects."{root}"]' in written
    assert 'trust_level = "trusted"' in written.split(original, 1)[1]
    # 写前备份
    assert list(codex.glob("config.toml.agentdeck-backup-*"))


def test_agent_trust_marks_claude_project_without_disturbing_others(
    tmp_path, monkeypatch, capsys
) -> None:
    root, home = _project_with_clean_home(tmp_path, monkeypatch)
    claude = home / ".claude.json"
    claude.write_text(
        json.dumps({"projects": {"/other": {"hasTrustDialogAccepted": True, "keep": 1}}, "topLevel": "x"}),
        encoding="utf-8",
    )

    assert cli.main(["agent", "trust", "--confirm"]) == 0
    capsys.readouterr()

    data = json.loads(claude.read_text(encoding="utf-8"))
    assert data["projects"][str(root)]["hasTrustDialogAccepted"] is True
    # 其它项目与顶层键一字未动
    assert data["projects"]["/other"] == {"hasTrustDialogAccepted": True, "keep": 1}
    assert data["topLevel"] == "x"
    assert list(home.glob(".claude.json.agentdeck-backup-*"))


def test_agent_trust_reports_unknown_provider_instead_of_claiming_success(
    tmp_path, monkeypatch, capsys
) -> None:
    # 自定义 command 的 agent:我们不知道它把 trust 存在哪,绝不能报成已处理。
    root, _home = _project_with_clean_home(tmp_path, monkeypatch)
    path = root / ".agentdeck" / "config.toml"
    path.write_text(
        path.read_text(encoding="utf-8").replace('provider = "claude"', 'provider = "my-own-agent"'),
        encoding="utf-8",
    )

    assert cli.main(["agent", "trust", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)

    states = {item["agent_id"]: item["outcome"] for item in payload["items"]}
    assert states["reviewer"] == "unsupported"
    assert payload["unsupported_count"] == 1
