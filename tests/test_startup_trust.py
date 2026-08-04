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
