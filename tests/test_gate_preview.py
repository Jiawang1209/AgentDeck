from __future__ import annotations

import pytest

from agentdeck.gate_preview import (
    GATE_PREVIEW_CANDIDATE_FIELDS,
    GATE_PREVIEW_LADDER_CAP,
    GATE_PREVIEW_SOURCES,
    derive_gate_preview,
    prefix_ladder,
    render_gate_preview,
)

# Round 14 的真实框:一道 Playwright 框卡了两天,846 wave 里 834 个空转在它上面。
ROUND14_COMMAND = (
    "/Users/x/.codex/skills/playwright/scripts/playwright_cli.sh "
    "open file:///Users/x/proj/index.html"
)


def test_prefix_ladder_descends_from_narrowest_to_widest() -> None:
    ladder = prefix_ladder(ROUND14_COMMAND)
    assert [item["prefix"] for item in ladder] == [
        ROUND14_COMMAND,
        "/Users/x/.codex/skills/playwright/scripts/playwright_cli.sh open",
        "/Users/x/.codex/skills/playwright/scripts/playwright_cli.sh",
    ]
    assert [item["unpinned_tail"] for item in ladder] == [
        "",
        "file:///Users/x/proj/index.html",
        "open file:///Users/x/proj/index.html",
    ]
    assert [item["is_widest"] for item in ladder] == [False, False, True]
    assert [item["index"] for item in ladder] == [1, 2, 3]
    for item in ladder:
        assert set(item) == set(GATE_PREVIEW_CANDIDATE_FIELDS)


def test_prefix_ladder_single_token_is_one_widest_entry() -> None:
    ladder = prefix_ladder("pytest")
    assert len(ladder) == 1
    assert ladder[0]["prefix"] == "pytest"
    assert ladder[0]["unpinned_tail"] == ""
    assert ladder[0]["is_widest"] is True


def test_prefix_ladder_caps_at_five_keeping_both_ends() -> None:
    command = " ".join(f"t{index}" for index in range(1, 9))
    ladder = prefix_ladder(command)
    assert len(ladder) == GATE_PREVIEW_LADDER_CAP == 5
    # 最窄一条是整条命令,最宽一条是裸首 token:两端必在。
    assert ladder[0]["prefix"] == command
    assert ladder[0]["is_widest"] is False
    assert ladder[-1]["prefix"] == "t1"
    assert ladder[-1]["is_widest"] is True
    # 严格递减且各不相同。
    lengths = [len(item["prefix"]) for item in ladder]
    assert lengths == sorted(lengths, reverse=True)
    assert len({item["prefix"] for item in ladder}) == len(ladder)


def test_prefix_ladder_preserves_original_inner_spacing() -> None:
    ladder = prefix_ladder("node   tests/a.mjs  --flag")
    assert [item["prefix"] for item in ladder] == [
        "node   tests/a.mjs  --flag",
        "node   tests/a.mjs",
        "node",
    ]
    assert ladder[1]["unpinned_tail"] == "--flag"


def test_prefix_ladder_empty_command_has_no_ladder() -> None:
    assert prefix_ladder("") == []
    assert prefix_ladder("   ") == []


def test_derive_gate_preview_for_command_box_never_marks_a_choice() -> None:
    payload = derive_gate_preview(
        agent_id="planner",
        box_kind="command",
        command=ROUND14_COMMAND,
        mcp_server=None,
        mcp_tool=None,
        waiting_hint="Press enter to confirm or esc to cancel",
        source="host_record",
    )
    assert payload["mode"] == "delegation_gate_preview"
    assert payload["source"] == "host_record"
    assert payload["candidate_count"] == 3 == len(payload["candidates"])
    assert payload["release_command"] == (
        "agentdeck agent release-box --agent planner --confirm"
    )
    # grant 命令含占位符 → 对应 control 必须 disabled(宽度只能由人选)。
    grant = next(c for c in payload["controls"] if c["kind"] == "grant")
    assert "<prefix>" in str(grant["command"])
    assert grant["enabled"] is False
    assert grant["blocker"]
    # 绝不推荐:没有任何一条候选带"被选中/被偏好"标记。
    marker_keys = {"recommended", "selected", "preferred", "suggested", "default"}
    for candidate in payload["candidates"]:
        assert not marker_keys & set(candidate)
        assert candidate["grant_command"].startswith(
            "agentdeck delegation grant --agent planner --prefix "
        )
        assert candidate["grant_command"].endswith(" --confirm")


def test_derive_gate_preview_for_mcp_box_has_no_ladder() -> None:
    payload = derive_gate_preview(
        agent_id="planner",
        box_kind="mcp_tool",
        command=None,
        mcp_server="chrome-devtools",
        mcp_tool="hover",
        waiting_hint="enter to submit | esc to cancel",
        source="agent_scan",
    )
    assert payload["candidates"] == []
    assert payload["candidate_count"] == 0
    grant = next(c for c in payload["controls"] if c["kind"] == "grant")
    assert grant["command"] == (
        "agentdeck delegation grant --agent planner "
        "--mcp-server chrome-devtools --mcp-tool hover --confirm"
    )
    assert grant["enabled"] is True
    assert grant["blocker"] is None


def test_derive_gate_preview_rejects_unknown_source() -> None:
    assert set(GATE_PREVIEW_SOURCES) == {"host_record", "agent_scan"}
    with pytest.raises(ValueError):
        derive_gate_preview(
            agent_id="planner",
            box_kind="command",
            command="pytest",
            mcp_server=None,
            mcp_tool=None,
            waiting_hint=None,
            source="guesswork",
        )


def test_render_gate_preview_never_recommends() -> None:
    for payload in (
        derive_gate_preview(
            agent_id="planner",
            box_kind="command",
            command=ROUND14_COMMAND,
            mcp_server=None,
            mcp_tool=None,
            waiting_hint="Press enter to confirm or esc to cancel",
            source="host_record",
        ),
        derive_gate_preview(
            agent_id="planner",
            box_kind="mcp_tool",
            command=None,
            mcp_server="chrome-devtools",
            mcp_tool="hover",
            waiting_hint=None,
            source="agent_scan",
        ),
    ):
        text = render_gate_preview(payload).lower()
        for word in ("建议", "推荐", "recommend", "safe", "✅"):
            assert word not in text
        # 两步闭环必须完整摊开。
        assert "agentdeck delegation grant" in text
        assert "agentdeck agent release-box --agent planner --confirm" in text
        # 无法核验命令性质这句话必须出现一次(没有警告不代表无害)。
        assert "无法核验" in text


def test_render_gate_preview_marks_only_the_widest_entry() -> None:
    payload = derive_gate_preview(
        agent_id="planner",
        box_kind="command",
        command=ROUND14_COMMAND,
        mcp_server=None,
        mcp_tool=None,
        waiting_hint=None,
        source="host_record",
    )
    text = render_gate_preview(payload)
    assert text.count("最宽") == 1
