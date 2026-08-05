from __future__ import annotations

from agentdeck.dispatch_receptive import (
    RECEPTIVE_STATES,
    RECEPTIVE_REASONS,
    classify_pane_receptive,
)


def test_a_normal_composer_is_receptive() -> None:
    result = classify_pane_receptive(
        pane_text="› Improve documentation in @filename\n  gpt-5.6-sol · ~/proj · main\n"
    )

    assert result["state"] == "receptive"
    assert result["reason"] is None


def test_a_pane_on_a_directory_trust_dialog_is_blocked() -> None:
    # 2026-08-04 现场:pane 冻在首次目录信任框上,派发照常发送,按键落进对话框,
    # 任务丢失,记录却写着 dispatched,宿主对着永不会来的回复等了 300 个 wave。
    result = classify_pane_receptive(
        pane_text=(
            "  Do you trust the contents of this directory?\n"
            "› 1. Yes, continue\n"
            "  2. No, quit\n"
            "  Press enter to continue\n"
        )
    )

    assert result["state"] == "blocked"
    assert result["reason"] == "directory_trust"


def test_a_pane_on_a_pending_authorization_box_is_blocked() -> None:
    # 待批授权框同理:此刻发进去的按键会被那道框吃掉。
    result = classify_pane_receptive(
        pane_text=(
            "  Would you like to run the following command?\n"
            "  $ node tests/x.mjs\n"
            "› 1. Yes, proceed (y)\n"
            "  Press enter to confirm or esc to cancel\n"
        )
    )

    assert result["state"] == "blocked"
    assert result["reason"] == "pending_box"


def test_an_answered_box_in_scrollback_does_not_block() -> None:
    # 已答复的框折进历史后不再渲染活动选择器字形——它不该拦住派发。
    result = classify_pane_receptive(
        pane_text=(
            "  Do you trust the contents of this directory?\n"
            "  -> Yes, continue\n"
            "\n"
            "› Improve documentation in @filename\n"
            "  gpt-5.6-sol · ~/proj · main\n"
        )
    )

    assert result["state"] == "receptive"


def test_unreadable_pane_is_unverifiable_and_never_blocked() -> None:
    # capture 失败是既有的 runtime 抖动,本仓库一贯当抖动处理。把它判成 blocked
    # 会让一次读取失败停掉正常派发;判成 receptive 又是在陈述没确立的事实。
    result = classify_pane_receptive(pane_text=None)

    assert result["state"] == "unverifiable"
    assert result["reason"] == "pane_unreadable"


def test_states_and_reasons_are_closed_enums() -> None:
    assert set(RECEPTIVE_STATES) == {"receptive", "blocked", "unverifiable"}
    # reason 横跨 blocked(前两个)与 unverifiable(最后一个)两态,故不叫
    # BLOCKED_REASONS——那会暗示 pane_unreadable 是一种"被拦住",而它是"没查成"。
    assert set(RECEPTIVE_REASONS) == {
        "directory_trust",
        "pending_box",
        "pane_unreadable",
    }


def test_unverifiable_never_reads_as_receptive() -> None:
    unverifiable = classify_pane_receptive(pane_text=None)
    receptive = classify_pane_receptive(pane_text="› ready\n")

    assert unverifiable["state"] != receptive["state"]
    assert not any(value is False for value in unverifiable.values())
