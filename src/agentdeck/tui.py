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
        self._prev_mode = "overview"
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
        self._approvals = [
            item
            for item in _as_list(_as_dict(self._payload.get("approval_card")).get("approvals"))
            if isinstance(item, dict)
        ]
        self._runtime_agents = [
            item
            for item in _as_list(_as_dict(self._payload.get("runtime_card")).get("agents"))
            if isinstance(item, dict)
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
        if self.mode == "approvals":
            length = len(self._approvals)
        elif self.mode == "runtime":
            length = len(self._runtime_agents)
        else:
            length = len(self._filtered_controls())
        self.selected_index = _clamp(self.selected_index + delta, 0, max(0, length - 1))

    # --- runtime mode ---

    def runtime_agents(self) -> list[dict[str, Any]]:
        return list(self._runtime_agents)

    def selected_agent(self) -> dict[str, Any] | None:
        if not self._runtime_agents:
            return None
        index = _clamp(self.selected_index, 0, len(self._runtime_agents) - 1)
        return self._runtime_agents[index]

    def selected_agent_command(self) -> str | None:
        agent = self.selected_agent()
        if agent is None:
            return None
        if agent.get("status") == "running":
            return agent.get("capture_command")
        return agent.get("spawn_command")

    def toggle_runtime(self) -> None:
        if self.mode == "help":
            self.mode = self._prev_mode
        self.mode = "runtime" if self.mode != "runtime" else "overview"
        if self.mode == "runtime":
            self.selected_index = 0

    # --- approvals mode ---

    def approval_items(self) -> list[dict[str, Any]]:
        return list(self._approvals)

    def selected_approval(self) -> dict[str, Any] | None:
        if not self._approvals:
            return None
        index = _clamp(self.selected_index, 0, len(self._approvals) - 1)
        return self._approvals[index]

    def selected_approval_command(self) -> str | None:
        approval = self.selected_approval()
        if approval is None:
            return None
        status = approval.get("status")
        if status == "approved":
            return approval.get("dispatch_command")
        if status == "pending":
            return approval.get("approve_command")
        return approval.get("preview_command") or approval.get("approve_command")

    def toggle_approvals(self) -> None:
        if self.mode == "help":
            self.mode = self._prev_mode
        self.mode = "approvals" if self.mode != "approvals" else "overview"
        if self.mode == "approvals":
            self.selected_index = 0

    # --- shared ---

    def _focus_next_command(self) -> None:
        next_command = self._payload.get("next_command")
        if not next_command:
            return
        for index, control in enumerate(self._filtered_controls()):
            if control.get("command") == next_command:
                self.selected_index = index
                return

    def toggle_palette(self) -> None:
        if self.mode == "help":
            self.mode = self._prev_mode
        self.mode = "palette" if self.mode == "overview" else "overview"
        if self.mode == "palette":
            self._focus_next_command()

    def toggle_help(self) -> None:
        if self.mode == "help":
            self.mode = self._prev_mode
        else:
            self._prev_mode = self.mode
            self.mode = "help"

    def help_lines(self) -> list[str]:
        return [
            "Keys",
            "  [tab] / [p]    toggle overview <-> command palette",
            "  [a]            toggle overview <-> approvals view",
            "  [g]            toggle overview <-> runtime (agents) view",
            "  [up]/[down] ([k]/[j])   scroll overview or move palette/approvals selection",
            "  [PgUp]/[PgDn] ([space]) jump by 10",
            "  [/]            filter the palette (Enter apply, Esc cancel)",
            "  [r]            refresh (re-fetch + validate workbench snapshot)",
            "  [?] / [h]      toggle this help",
            "  [q]            quit",
            "",
            "Read-only viewer: selecting a control only shows the exact command",
            "to run (run: ...); the TUI never executes, writes state, or touches tmux.",
        ]

    def refresh(self, payload: dict[str, Any]) -> None:
        self._set_payload(payload)

    def footer_text(self) -> str:
        if self.mode == "help":
            return "help  |  [?] back  [q] quit"
        if self.mode == "runtime":
            agents = self._runtime_agents
            if not agents:
                return "runtime: no agents  |  [g] overview  [r] refresh  [q] quit"
            agent = self.selected_agent() or {}
            command = self.selected_agent_command()
            command_text = str(command) if command else "(no command)"
            status = str(agent.get("status") or "")
            agent_id = str(agent.get("agent_id") or "")
            return (
                f"{self.selected_index + 1}/{len(agents)}  [{status}] {agent_id}\n"
                f"run: {command_text}  |  [g] overview  [r] refresh  [q] quit"
            )
        if self.mode == "approvals":
            approvals = self._approvals
            if not approvals:
                return "approvals: none pending  |  [a] overview  [r] refresh  [q] quit"
            approval = self.selected_approval() or {}
            command = self.selected_approval_command()
            command_text = str(command) if command else "(no command)"
            status = str(approval.get("status") or "")
            agent = str(approval.get("agent_id") or "")
            return (
                f"{self.selected_index + 1}/{len(approvals)}  [{status}] {agent}\n"
                f"run: {command_text}  |  [a] overview  [r] refresh  [q] quit"
            )
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


def _palette_window(model: "TuiModel", body_height: int) -> list[int]:
    controls = model.control_items()
    if not controls:
        return []
    # scroll a window of controls so the selected row stays visible
    top = _clamp(model.selected_index - body_height // 2, 0, max(0, len(controls) - body_height))
    return list(range(top, min(len(controls), top + max(1, body_height))))


def _palette_rows(model: "TuiModel", body_height: int, width: int) -> list[str]:
    controls = model.control_items()
    if not controls:
        return ["(no controls in this snapshot)"]
    rows: list[str] = []
    for index in _palette_window(model, body_height):
        control = _as_dict(controls[index])
        marker = ">" if index == model.selected_index else " "
        state = "x" if control.get("enabled") else " "
        row = (
            f"{marker} [{state}] {str(control.get('scope') or ''):<16} "
            f"{str(control.get('kind') or ''):<16} {control.get('label') or ''}"
        )
        rows.append(_fit(row, width))
    return rows


def _approval_window(model: "TuiModel", body_height: int) -> list[int]:
    approvals = model.approval_items()
    if not approvals:
        return []
    top = _clamp(model.selected_index - body_height // 2, 0, max(0, len(approvals) - body_height))
    return list(range(top, min(len(approvals), top + max(1, body_height))))


def _approval_rows(model: "TuiModel", body_height: int, width: int) -> list[str]:
    approvals = model.approval_items()
    if not approvals:
        return ["(no pending approvals in this snapshot)"]
    rows: list[str] = []
    for index in _approval_window(model, body_height):
        approval = _as_dict(approvals[index])
        marker = ">" if index == model.selected_index else " "
        status = str(approval.get("status") or "")
        agent = str(approval.get("agent_id") or "")
        task = str(approval.get("task") or "")
        row = f"{marker} {status:<10} {agent:<12} {task}"
        rows.append(_fit(row, width))
    return rows


def _runtime_window(model: "TuiModel", body_height: int) -> list[int]:
    agents = model.runtime_agents()
    if not agents:
        return []
    top = _clamp(model.selected_index - body_height // 2, 0, max(0, len(agents) - body_height))
    return list(range(top, min(len(agents), top + max(1, body_height))))


def _runtime_rows(model: "TuiModel", body_height: int, width: int) -> list[str]:
    agents = model.runtime_agents()
    if not agents:
        return ["(no agents in this snapshot)"]
    rows: list[str] = []
    for index in _runtime_window(model, body_height):
        agent = _as_dict(agents[index])
        marker = ">" if index == model.selected_index else " "
        status = str(agent.get("status") or "")
        agent_id = str(agent.get("agent_id") or "")
        role = str(agent.get("role") or "")
        pane_id = agent.get("pane_id")
        tail = f"  pane:{pane_id}" if pane_id else ""
        row = f"{marker} {status:<10} {agent_id:<12} {role:<14}{tail}"
        rows.append(_fit(row, width))
    return rows


def palette_row_style(control: dict[str, Any], is_selected: bool) -> str:
    """Pure styling decision for a palette row: selected > enabled > disabled."""
    if is_selected:
        return "selected"
    return "enabled" if _as_dict(control).get("enabled") else "disabled"


def palette_row_styles(model: "TuiModel", body_height: int) -> list[str]:
    """Style token per visible palette row, parallel to :func:`_palette_rows`."""
    controls = model.control_items()
    if not controls:
        return ["disabled"]
    return [
        palette_row_style(_as_dict(controls[index]), index == model.selected_index)
        for index in _palette_window(model, body_height)
    ]


def render_frame(model: "TuiModel", height: int, width: int) -> list[str]:
    """Return exactly ``height`` lines representing the visible screen (pure)."""
    height = max(4, height)
    width = max(10, width)
    title = _fit(f"AgentDeck TUI — {model.mode}", width)
    footer_lines = [_fit(line, width) for line in model.footer_text().split("\n")]
    body_height = height - 1 - len(footer_lines)
    if model.mode == "help":
        body = [_fit(line, width) for line in model.help_lines()]
    elif model.mode == "palette":
        body = _palette_rows(model, body_height, width)
    elif model.mode == "approvals":
        body = _approval_rows(model, body_height, width)
    elif model.mode == "runtime":
        body = _runtime_rows(model, body_height, width)
    else:
        lines = model.overview_lines()
        body = [_fit(line, width) for line in lines[model.scroll : model.scroll + body_height]]
    body = body[:body_height]
    while len(body) < body_height:
        body.append("")
    frame = [title, *body, *footer_lines]
    return frame[:height]


def _frame_row_attributes(model: "TuiModel", height: int, frame_len: int) -> list[int]:
    """Map palette-body rows to curses attributes (selected reverse, disabled dim)."""
    import curses

    attrs = [0] * frame_len
    if model.mode not in ("palette", "approvals"):
        return attrs
    footer_count = len(model.footer_text().split("\n"))
    body_height = max(1, height - 1 - footer_count)
    if model.mode == "approvals":
        for offset, index in enumerate(_approval_window(model, body_height)):
            row = 1 + offset
            if row < frame_len and index == model.selected_index:
                attrs[row] = curses.A_REVERSE
        return attrs
    if model.mode == "runtime":
        for offset, index in enumerate(_runtime_window(model, body_height)):
            row = 1 + offset
            if row < frame_len and index == model.selected_index:
                attrs[row] = curses.A_REVERSE
        return attrs
    style_attr = {
        "selected": curses.A_REVERSE,
        "disabled": curses.A_DIM,
        "enabled": curses.A_NORMAL,
    }
    for offset, style in enumerate(palette_row_styles(model, body_height)):
        row = 1 + offset  # body starts after the title row
        if row < frame_len:
            attrs[row] = style_attr.get(style, 0)
    return attrs


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
        row_attrs = _frame_row_attributes(model, height, len(frame))
        stdscr.erase()
        for row, line in enumerate(frame):
            attr = row_attrs[row] if row < len(row_attrs) else 0
            try:
                stdscr.addstr(row, 0, line, attr)
            except Exception:
                try:
                    stdscr.addstr(row, 0, line)
                except Exception:
                    pass
        stdscr.refresh()
        key = stdscr.getch()
        if key in (ord("q"), ord("Q")):
            return
        if key in (ord("?"), ord("h"), ord("H")):
            model.toggle_help()
        elif key == ord("/"):
            if model.mode != "palette":
                model.toggle_palette()
            _read_filter(stdscr, model)
        elif key in (ord("\t"), ord("p"), ord("P")):
            model.toggle_palette()
        elif key in (ord("a"), ord("A")):
            model.toggle_approvals()
        elif key in (ord("g"), ord("G")):
            model.toggle_runtime()
        elif key in (ord("r"), ord("R")):
            fresh = fetch()
            if isinstance(fresh, dict):
                model.refresh(fresh)
        elif key in (curses.KEY_DOWN, ord("j")):
            if model.mode in ("palette", "approvals", "runtime"):
                model.move_selection(1)
            else:
                model.scroll_by(1, max(1, stdscr.getmaxyx()[0] - 3))
        elif key in (curses.KEY_UP, ord("k")):
            if model.mode in ("palette", "approvals", "runtime"):
                model.move_selection(-1)
            else:
                model.scroll_by(-1, max(1, stdscr.getmaxyx()[0] - 3))
        elif key in (curses.KEY_NPAGE, ord(" ")):
            if model.mode in ("palette", "approvals", "runtime"):
                model.move_selection(10)
            else:
                model.scroll_by(10, max(1, stdscr.getmaxyx()[0] - 3))
        elif key == curses.KEY_PPAGE:
            if model.mode in ("palette", "approvals", "runtime"):
                model.move_selection(-10)
            else:
                model.scroll_by(-10, max(1, stdscr.getmaxyx()[0] - 3))
