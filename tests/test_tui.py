import copy

from agentdeck import cli
from agentdeck.contracts import workbench_example
from agentdeck.tui import TuiModel, render_frame


def test_tui_model_starts_in_overview_mode_reusing_dashboard_render() -> None:
    payload = workbench_example()
    snapshot = copy.deepcopy(payload)

    model = TuiModel(payload)

    assert model.mode == "overview"
    lines = model.overview_lines()
    assert isinstance(lines, list)
    assert any("Role topology" in line for line in lines)
    # the model only reads the payload; it never mutates it
    assert payload == snapshot


def _payload_with_approvals() -> dict:
    payload = copy.deepcopy(workbench_example())
    payload["approval_card"] = {
        "count": 2,
        "approvals": [
            {
                "approval_id": "apv_1", "status": "pending", "agent_id": "planner", "task": "do A",
                "approve_command": "agentdeck approval approve --approval-id apv_1",
                "reject_command": "agentdeck approval reject --approval-id apv_1 --reason <reason>",
                "dispatch_command": "agentdeck approval dispatch --approval-id apv_1",
                "preview_command": "agentdeck approval list",
            },
            {
                "approval_id": "apv_2", "status": "approved", "agent_id": "coder", "task": "do B",
                "approve_command": "agentdeck approval approve --approval-id apv_2",
                "reject_command": "agentdeck approval reject --approval-id apv_2 --reason <reason>",
                "dispatch_command": "agentdeck approval dispatch --approval-id apv_2",
                "preview_command": "agentdeck approval list",
            },
        ],
    }
    return payload


def test_tui_model_approvals_view_navigates_and_shows_status_aware_command() -> None:
    payload = _payload_with_approvals()
    snapshot = copy.deepcopy(payload)

    model = TuiModel(payload)
    model.toggle_approvals()
    assert model.mode == "approvals"

    items = model.approval_items()
    assert [i["approval_id"] for i in items] == ["apv_1", "apv_2"]

    model.move_selection(-100)
    assert model.selected_approval()["approval_id"] == "apv_1"
    # pending -> the footer offers the approve command (read-only: shown, not run)
    assert "agentdeck approval approve --approval-id apv_1" in model.footer_text()

    model.move_selection(1)
    assert model.selected_approval()["approval_id"] == "apv_2"
    # approved -> the footer offers the dispatch command
    assert "agentdeck approval dispatch --approval-id apv_2" in model.footer_text()

    # the model only reads the payload; it never mutates it
    assert payload == snapshot


def test_tui_render_frame_approvals_lists_items() -> None:
    payload = _payload_with_approvals()
    model = TuiModel(payload)
    model.toggle_approvals()

    frame = render_frame(model, 12, 80)
    text = "\n".join(frame)
    assert "approvals" in frame[0]  # title reflects the mode
    assert "planner" in text
    assert "do A" in text
    assert "pending" in text


def test_tui_model_focused_command_reflects_active_view() -> None:
    payload = _payload_with_approvals()
    model = TuiModel(payload)

    # overview: nothing is "focused" for launching
    assert model.focused_command() is None

    model.toggle_approvals()
    model.move_selection(-100)
    assert model.focused_command() == "agentdeck approval approve --approval-id apv_1"

    model.toggle_approvals()  # back to overview
    model.toggle_runtime()
    model.move_selection(-100)
    assert model.focused_command() == "agentdeck agent capture --agent planner --lines 200"

    model.toggle_runtime()  # back to overview
    model.toggle_palette()
    assert model.focused_command() == (model.selected_control() or {}).get("command")


def test_run_tui_returns_focused_command_on_quit() -> None:
    from agentdeck.tui import run_tui

    payload = _payload_with_approvals()
    model = TuiModel(payload)
    # enter the approvals view (selects the first, pending, approval), then quit
    stdscr = _FakeStdscr([ord("a"), ord("q")])
    result = run_tui(stdscr, model, lambda: None)
    assert result == "agentdeck approval approve --approval-id apv_1"


def test_tui_model_runtime_view_navigates_and_shows_status_aware_command() -> None:
    payload = workbench_example()
    snapshot = copy.deepcopy(payload)

    model = TuiModel(payload)
    model.toggle_runtime()
    assert model.mode == "runtime"

    agents = model.runtime_agents()
    assert [a["agent_id"] for a in agents] == ["planner", "coder", "reviewer"]

    model.move_selection(-100)
    assert model.selected_agent()["agent_id"] == "planner"
    # running -> the footer offers the capture command
    assert "agentdeck agent capture --agent planner" in model.footer_text()

    model.move_selection(1)
    assert model.selected_agent()["agent_id"] == "coder"
    # not running -> the footer offers the spawn command
    assert "agentdeck agent spawn --agent coder" in model.footer_text()

    assert payload == snapshot


def test_tui_render_frame_runtime_lists_agents() -> None:
    payload = workbench_example()
    model = TuiModel(payload)
    model.toggle_runtime()

    frame = render_frame(model, 12, 80)
    text = "\n".join(frame)
    assert "runtime" in frame[0]
    assert "planner" in text
    assert "running" in text


def test_tui_model_toggles_to_palette_and_navigates_controls() -> None:
    payload = workbench_example()
    model = TuiModel(payload)

    model.toggle_palette()
    assert model.mode == "palette"
    items = model.control_items()
    assert items == payload["control_registry"]
    assert len(items) > 1

    # move to a known start (top), then verify stepwise navigation
    model.move_selection(-10_000)
    assert model.selected_index == 0
    assert model.selected_control() == items[0]

    model.move_selection(1)
    assert model.selected_index == 1
    assert model.selected_control() == items[1]

    # navigation is clamped to the list bounds
    model.move_selection(-100)
    assert model.selected_index == 0
    model.move_selection(10_000)
    assert model.selected_index == len(items) - 1


def test_tui_model_selected_control_command_is_read_only_display() -> None:
    payload = workbench_example()
    model = TuiModel(payload)
    model.toggle_palette()

    control = model.selected_control()
    # the footer surfaces the exact command + its safety/enabled state, for the human to run
    footer = model.footer_text()
    assert str(control["command"]) in footer or control["command"] is None
    assert control["safety"] in footer


def test_tui_model_overview_scroll_is_clamped() -> None:
    payload = workbench_example()
    model = TuiModel(payload)
    height = 5

    assert model.scroll == 0
    model.scroll_by(-10, height)
    assert model.scroll == 0
    model.scroll_by(10_000, height)
    max_scroll = max(0, len(model.overview_lines()) - height)
    assert model.scroll == max_scroll


def test_tui_model_refresh_replaces_payload_and_reclamps() -> None:
    payload = workbench_example()
    model = TuiModel(payload)
    model.toggle_palette()
    model.move_selection(10_000)
    assert model.selected_index == len(model.control_items()) - 1

    smaller = workbench_example()
    smaller["control_registry"] = smaller["control_registry"][:3]
    model.refresh(smaller)

    assert model.control_items() == smaller["control_registry"]
    assert model.selected_index == 2  # re-clamped to the smaller list
    assert model.mode == "palette"  # refresh preserves the current mode


def test_render_frame_fills_exact_height_with_title_and_footer() -> None:
    payload = workbench_example()
    model = TuiModel(payload)
    height, width = 24, 80

    frame = render_frame(model, height, width)

    assert len(frame) == height
    assert all(len(line) <= width for line in frame)
    assert "AgentDeck" in frame[0]
    # overview mode shows dashboard content and the footer hint
    # (assert on an early section that stays within a 24-line viewport)
    joined = "\n".join(frame)
    assert "Run progress" in joined
    assert "[tab] palette" in joined


def test_render_frame_palette_marks_selected_control() -> None:
    payload = workbench_example()
    model = TuiModel(payload)
    model.toggle_palette()
    model.move_selection(1)
    selected = model.selected_control()

    frame = render_frame(model, 24, 100)
    joined = "\n".join(frame)

    # the selected control row is marked and its command surfaced in the footer
    assert ">" in joined
    assert str(selected["label"]) in joined
    assert "run:" in joined


def test_tui_command_requires_a_tty(tmp_path, monkeypatch, capsys) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    from agentdeck.config import write_default_config

    write_default_config(root)
    monkeypatch.chdir(root)
    from agentdeck.state import StateStore

    state_before = StateStore(root).load()

    # pytest capture makes stdout a non-tty, so the interactive TUI must decline cleanly
    exit_code = cli.main(["tui"])

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "interactive terminal" in err
    assert "agentdeck dashboard" in err
    state_after = StateStore(root).load()
    assert state_after == state_before


def test_tui_model_palette_filter_narrows_controls() -> None:
    payload = workbench_example()
    model = TuiModel(payload)
    model.toggle_palette()
    total = len(model.control_items())

    model.set_filter("provider")
    filtered = model.control_items()

    assert 0 < len(filtered) < total
    for control in filtered:
        haystack = (
            f"{control.get('scope')}{control.get('kind')}"
            f"{control.get('label')}{control.get('command')}"
        ).lower()
        assert "provider" in haystack
    # selection is re-clamped into the filtered list and the filter shows in the footer
    assert model.selected_index < len(filtered)
    assert "provider" in model.footer_text()

    # clearing the filter restores the full list
    model.set_filter("")
    assert len(model.control_items()) == total


def test_tui_palette_focuses_recovery_next_command_on_open() -> None:
    payload = workbench_example()
    model = TuiModel(payload)

    model.toggle_palette()

    control = model.selected_control()
    assert control is not None
    assert control["command"] == payload["next_command"]


def test_tui_help_mode_shows_key_legend_and_restores_previous_mode() -> None:
    payload = workbench_example()
    model = TuiModel(payload)
    model.toggle_palette()

    model.toggle_help()
    assert model.mode == "help"
    frame = render_frame(model, 20, 80)
    joined = "\n".join(frame)
    assert "Keys" in joined
    assert "[tab]" in joined
    assert "[/]" in joined
    assert "quit" in joined

    # leaving help returns to whatever mode was active before
    model.toggle_help()
    assert model.mode == "palette"


def test_palette_row_styles_mark_selected_enabled_disabled() -> None:
    from agentdeck.tui import palette_row_style, palette_row_styles

    assert palette_row_style({"enabled": True}, is_selected=True) == "selected"
    assert palette_row_style({"enabled": True}, is_selected=False) == "enabled"
    assert palette_row_style({"enabled": False}, is_selected=False) == "disabled"
    # selection wins over enabled/disabled
    assert palette_row_style({"enabled": False}, is_selected=True) == "selected"

    payload = workbench_example()
    model = TuiModel(payload)
    model.toggle_palette()
    model.move_selection(-10_000)  # top of the list
    body_height = 8

    styles = palette_row_styles(model, body_height)
    rows = [
        line
        for line in __import__("agentdeck.tui", fromlist=["_palette_rows"])._palette_rows(
            model, body_height, 120
        )
    ]
    assert len(styles) == len(rows)
    # the first visible row is the selected one
    assert styles[0] == "selected"
    # every style is one of the known tokens
    assert set(styles) <= {"selected", "enabled", "disabled"}


class _FakeStdscr:
    """Minimal fake curses window that replays scripted keys for run_tui tests."""

    def __init__(self, keys, height=24, width=90):
        self._keys = list(keys)
        self._height = height
        self._width = width
        self.drawn: list[tuple] = []

    def getmaxyx(self):
        return (self._height, self._width)

    def getch(self):
        if self._keys:
            return self._keys.pop(0)
        return ord("q")  # safety: quit when the script is exhausted

    def erase(self):
        self.drawn.append(("erase",))

    def refresh(self):
        pass

    def addstr(self, row, col, text, *attr):
        self.drawn.append((row, col, text))


def test_run_tui_navigates_palette_refreshes_and_quits() -> None:
    from agentdeck.tui import run_tui

    payload = workbench_example()
    model = TuiModel(payload)
    calls = {"fetch": 0}

    def fetch():
        calls["fetch"] += 1
        return workbench_example()

    # open palette, move down twice, refresh, then quit
    stdscr = _FakeStdscr([ord("p"), ord("j"), ord("j"), ord("r"), ord("q")])
    run_tui(stdscr, model, fetch)

    assert model.mode == "palette"
    assert calls["fetch"] == 1
    assert stdscr.drawn  # it rendered at least one frame


def test_run_tui_refresh_ignores_none_payload() -> None:
    from agentdeck.tui import run_tui

    model = TuiModel(workbench_example())
    before = model.control_items()

    # a failing refresh (fetch -> None) must not crash or blank the model
    stdscr = _FakeStdscr([ord("p"), ord("r"), ord("q")])
    run_tui(stdscr, model, lambda: None)

    assert model.control_items() == before


def test_run_tui_help_toggle_and_quit() -> None:
    from agentdeck.tui import run_tui

    model = TuiModel(workbench_example())
    stdscr = _FakeStdscr([ord("?"), ord("q")])
    run_tui(stdscr, model, lambda: None)

    assert model.mode == "help"


def test_run_tui_filter_prompt_applies_filter() -> None:
    from agentdeck.tui import run_tui

    model = TuiModel(workbench_example())
    # '/' opens the filter prompt; type "role", Enter applies; then quit
    keys = [ord("/"), ord("r"), ord("o"), ord("l"), ord("e"), 10, ord("q")]
    run_tui(_FakeStdscr(keys), model, lambda: None)

    assert model.mode == "palette"
    assert model.filter_text == "role"
    for control in model.control_items():
        haystack = (
            f"{control.get('scope')}{control.get('kind')}"
            f"{control.get('label')}{control.get('command')}"
        ).lower()
        assert "role" in haystack
