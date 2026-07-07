"""Read-only interactive curses TUI over the ``agentdeck workbench`` contract.

Design: the pure navigation/state lives in :class:`TuiModel` (fully testable, no
curses). The curses I/O is a thin shell in :func:`run_tui` that renders the model
and translates key presses into model updates. The TUI is strictly a *viewer* of
the read-only workbench contract — selecting a control only surfaces the exact
explicit command for the human to run; the TUI never executes anything, writes
state, calls a provider, or touches tmux.
"""

from __future__ import annotations

from typing import Any

from .dashboard import render_workbench_dashboard


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clamp(value: int, low: int, high: int) -> int:
    if high < low:
        return low
    return max(low, min(high, value))


class TuiModel:
    """Pure navigation state for the interactive dashboard (no curses)."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.mode = "overview"
        self.scroll = 0
        self.selected_index = 0
        self.filter_text = ""
        self._set_payload(payload)

    def _set_payload(self, payload: dict[str, Any]) -> None:
        self._payload = _as_dict(payload)
        self._overview_lines = render_workbench_dashboard(self._payload).splitlines()
        self._controls = [
            item for item in _as_list(self._payload.get("control_registry")) if isinstance(item, dict)
        ]
        self._reclamp()

    def _filtered_controls(self) -> list[dict[str, Any]]:
        needle = self.filter_text.strip().lower()
        if not needle:
            return self._controls
        matches = []
        for control in self._controls:
            haystack = (
                f"{control.get('scope')}{control.get('kind')}"
                f"{control.get('label')}{control.get('command')}"
            ).lower()
            if needle in haystack:
                matches.append(control)
        return matches

    def _reclamp(self) -> None:
        self.selected_index = _clamp(self.selected_index, 0, max(0, len(self._filtered_controls()) - 1))
        self.scroll = _clamp(self.scroll, 0, max(0, len(self._overview_lines) - 1))

    def set_filter(self, text: str) -> None:
        self.filter_text = text
        self._reclamp()

    # --- overview mode ---

    def overview_lines(self) -> list[str]:
        return list(self._overview_lines)

    def scroll_by(self, delta: int, viewport_height: int) -> None:
        max_scroll = max(0, len(self._overview_lines) - max(1, viewport_height))
        self.scroll = _clamp(self.scroll + delta, 0, max_scroll)

    # --- palette mode ---

    def control_items(self) -> list[dict[str, Any]]:
        return list(self._filtered_controls())

    def selected_control(self) -> dict[str, Any] | None:
        controls = self._filtered_controls()
        if not controls:
            return None
        index = _clamp(self.selected_index, 0, len(controls) - 1)
        return controls[index]

    def move_selection(self, delta: int) -> None:
        controls = self._filtered_controls()
        self.selected_index = _clamp(self.selected_index + delta, 0, max(0, len(controls) - 1))

    # --- shared ---

    def toggle_palette(self) -> None:
        self.mode = "palette" if self.mode == "overview" else "overview"

    def refresh(self, payload: dict[str, Any]) -> None:
        self._set_payload(payload)

    def footer_text(self) -> str:
        if self.mode == "palette":
            controls = self._filtered_controls()
            filter_hint = f"  filter:'{self.filter_text}'" if self.filter_text else ""
            control = self.selected_control()
            if control is None:
                return (
                    f"palette: no controls{filter_hint}"
                    "  |  [/] filter  [tab] overview  [r] refresh  [q] quit"
                )
            command = control.get("command")
            command_text = str(command) if command else "(disabled — no command)"
            enabled = "enabled" if control.get("enabled") else "disabled"
            blocker = control.get("blocker")
            detail = (
                f"[{control.get('scope')}] {control.get('safety')} · {enabled}"
                f"{' · ' + str(blocker) if blocker else ''}"
            )
            return (
                f"{self.selected_index + 1}/{len(controls)}{filter_hint}  {detail}\n"
                f"run: {command_text}  |  [/] filter  [tab] overview  [r] refresh  [q] quit"
            )
        return (
            f"overview  line {self.scroll + 1}/{len(self._overview_lines)}"
            "  |  [tab] palette  [r] refresh  [q] quit"
        )


def _fit(text: str, width: int) -> str:
    return text[:width]


def _palette_rows(model: "TuiModel", body_height: int, width: int) -> list[str]:
    controls = model.control_items()
    if not controls:
        return ["(no controls in this snapshot)"]
    # scroll a window of controls so the selected row stays visible
    top = _clamp(model.selected_index - body_height // 2, 0, max(0, len(controls) - body_height))
    rows: list[str] = []
    for index in range(top, min(len(controls), top + body_height)):
        control = _as_dict(controls[index])
        marker = ">" if index == model.selected_index else " "
        state = "x" if control.get("enabled") else " "
        row = (
            f"{marker} [{state}] {str(control.get('scope') or ''):<16} "
            f"{str(control.get('kind') or ''):<16} {control.get('label') or ''}"
        )
        rows.append(_fit(row, width))
    return rows


def render_frame(model: "TuiModel", height: int, width: int) -> list[str]:
    """Return exactly ``height`` lines representing the visible screen (pure)."""
    height = max(4, height)
    width = max(10, width)
    title = _fit(f"AgentDeck TUI — {model.mode}", width)
    footer_lines = [_fit(line, width) for line in model.footer_text().split("\n")]
    body_height = height - 1 - len(footer_lines)
    if model.mode == "palette":
        body = _palette_rows(model, body_height, width)
    else:
        lines = model.overview_lines()
        body = [_fit(line, width) for line in lines[model.scroll : model.scroll + body_height]]
    body = body[:body_height]
    while len(body) < body_height:
        body.append("")
    frame = [title, *body, *footer_lines]
    return frame[:height]


def _read_filter(stdscr: Any, model: "TuiModel") -> None:
    """Read a filter string from a simple prompt (curses shell; Enter applies, Esc cancels)."""
    import curses

    buffer = model.filter_text
    while True:
        height, width = stdscr.getmaxyx()
        prompt = _fit(f"filter: {buffer}_  [Enter] apply  [Esc] cancel", width)
        try:
            stdscr.addstr(height - 1, 0, " " * (width - 1))
            stdscr.addstr(height - 1, 0, prompt)
            stdscr.refresh()
        except Exception:
            pass
        key = stdscr.getch()
        if key in (curses.KEY_ENTER, 10, 13):
            model.set_filter(buffer)
            return
        if key == 27:  # Esc
            return
        if key in (curses.KEY_BACKSPACE, 127, 8):
            buffer = buffer[:-1]
        elif 32 <= key <= 126:
            buffer += chr(key)
        model.set_filter(buffer)


def run_tui(stdscr: Any, model: "TuiModel", fetch: Any) -> None:
    """Thin curses shell: render the model and translate keys into model updates.

    Read-only: it never runs a command. ``fetch`` returns a fresh validated
    workbench payload (or ``None``) for the refresh key.
    """
    import curses

    try:
        curses.curs_set(0)
    except Exception:
        pass
    while True:
        height, width = stdscr.getmaxyx()
        frame = render_frame(model, height, width)
        stdscr.erase()
        for row, line in enumerate(frame):
            try:
                stdscr.addstr(row, 0, line)
            except Exception:
                pass
        stdscr.refresh()
        key = stdscr.getch()
        if key in (ord("q"), ord("Q")):
            return
        if key == ord("/"):
            if model.mode != "palette":
                model.toggle_palette()
            _read_filter(stdscr, model)
        elif key in (ord("\t"), ord("p"), ord("P")):
            model.toggle_palette()
        elif key in (ord("r"), ord("R")):
            fresh = fetch()
            if isinstance(fresh, dict):
                model.refresh(fresh)
        elif key in (curses.KEY_DOWN, ord("j")):
            if model.mode == "palette":
                model.move_selection(1)
            else:
                model.scroll_by(1, max(1, stdscr.getmaxyx()[0] - 3))
        elif key in (curses.KEY_UP, ord("k")):
            if model.mode == "palette":
                model.move_selection(-1)
            else:
                model.scroll_by(-1, max(1, stdscr.getmaxyx()[0] - 3))
        elif key in (curses.KEY_NPAGE, ord(" ")):
            if model.mode == "palette":
                model.move_selection(10)
            else:
                model.scroll_by(10, max(1, stdscr.getmaxyx()[0] - 3))
        elif key == curses.KEY_PPAGE:
            if model.mode == "palette":
                model.move_selection(-10)
            else:
                model.scroll_by(-10, max(1, stdscr.getmaxyx()[0] - 3))
