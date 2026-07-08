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
    joined = "\n".join(frame)
    assert "Role topology" in joined
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
