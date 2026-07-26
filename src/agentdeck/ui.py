"""Read-only local web shell over the contract-facing CLI surface.

Spec: docs/superpowers/specs/2026-07-26-gui-readonly-web.md. Zero new
dependencies (stdlib ``http.server``); data flows exclusively through
subprocess invocations of the three read-only CLI commands (workbench /
events / controls), so contract validators keep guarding every payload and a
future daemon/remote channel only swaps the transport, not the rendering.
The server binds 127.0.0.1 only, accepts GET only, and never runs a mutating
command — the palette displays commands for copying, never for clicking.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# API 端点 → 只读 CLI argv 白名单（绝无 mutating 命令）。
UI_API_COMMANDS: dict[str, list[str]] = {
    "workbench": ["workbench"],
    "events": ["events", "--limit", "50"],
    "controls": ["controls"],
}


def run_cli_json(root: Path, argv: list[str]) -> object:
    completed = subprocess.run(
        [sys.executable, "-m", "agentdeck", *argv],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return {
            "ok": False,
            "error": f"agentdeck {' '.join(argv)} failed",
            "detail": (completed.stderr or "").strip()[:500],
        }
    return json.loads(completed.stdout)


_PAGE = """<!doctype html>
<meta charset="utf-8">
<title>AgentDeck</title>
<style>
  body { font-family: ui-monospace, monospace; margin: 1.5rem; background: #111; color: #ddd; }
  h1 { font-size: 1.2rem; } h2 { font-size: 1rem; border-bottom: 1px solid #333; padding-bottom: .3rem; }
  table { border-collapse: collapse; width: 100%; font-size: .85rem; }
  td, th { border: 1px solid #333; padding: .25rem .5rem; text-align: left; vertical-align: top; }
  .muted { color: #888; } .ok { color: #7c6; } .warn { color: #ec5; }
  code { background: #222; padding: 0 .3rem; }
  #layout { display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; }
</style>
<h1>AgentDeck <span class="muted">read-only workbench</span></h1>
<div id="layout">
  <div>
    <h2>Overview</h2><div id="overview" class="muted">loading…</div>
    <h2>Agents</h2><table id="agents"></table>
    <h2>Queues</h2><div id="queues" class="muted"></div>
    <h2>Controls <span class="muted">(inspect runnable, others copy only)</span></h2><table id="controls"></table>
  </div>
  <div>
    <h2>Inspect result</h2><pre id="result" class="muted">click a Run button…</pre>
    <h2>Events</h2><table id="events"></table>
  </div>
</div>
<script>
let cursor = null;
const esc = (s) => String(s ?? "").replace(/[&<>]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
async function fetchJson(path) { const r = await fetch(path); return r.json(); }
async function refreshWorkbench() {
  try {
    const wb = await fetchJson("/api/workbench");
    const leader = wb.leader_card || {};
    const recovery = wb.recovery || {};
    document.getElementById("overview").innerHTML =
      `leader: <code>${esc(leader.provider)}</code>/<code>${esc(leader.model)}</code>` +
      ` · mode <code>${esc(leader.approval_mode)}</code>` +
      ` · recovery <code>${esc(recovery.status)}</code>` +
      ` · next <code>${esc(recovery.next_command)}</code>`;
    const agents = ((wb.runtime_card || {}).agents) || [];
    document.getElementById("agents").innerHTML =
      "<tr><th>agent</th><th>role</th><th>status</th><th>pane</th></tr>" +
      agents.map(a => `<tr><td>${esc(a.agent_id)}</td><td>${esc(a.role)}</td>` +
        `<td class="${a.status === "running" ? "ok" : "muted"}">${esc(a.status)}</td>` +
        `<td>${esc(a.pane_id)}</td></tr>`).join("");
    const q = wb.queue_card || {};
    document.getElementById("queues").textContent = JSON.stringify({
      leader_actions: (q.leader_actions || {}).pending_count,
      approvals: (q.approvals || {}).pending_count,
      inbox: (q.inbox || {}).pending_count,
    });
  } catch (e) { document.getElementById("overview").textContent = "workbench unavailable: " + e; }
}
async function refreshControls() {
  try {
    const payload = await fetchJson("/api/controls");
    const items = (payload.items || []).slice(0, 40);
    document.getElementById("controls").innerHTML =
      "<tr><th>scope</th><th>kind</th><th>command</th><th>safety</th><th></th></tr>" +
      items.map(i => `<tr><td>${esc(i.scope)}</td><td>${esc(i.kind)}</td>` +
        `<td><code>${esc(i.command)}</code></td>` +
        `<td class="${i.enabled ? "ok" : "muted"}">${esc(i.safety)}${i.enabled ? "" : " (disabled)"}</td>` +
        `<td>${i.enabled && i.control_id && i.safety === "inspect"
          ? `<button onclick="runInspect('${esc(i.control_id)}')">Run</button>`
          : i.enabled && i.control_id && (i.safety === "explicit_user" || i.safety === "explicit_runtime") && !String(i.command || "").includes("<")
            ? `<button onclick="runExecute('${esc(i.control_id)}', this)">Execute…</button>` : ""}</td></tr>`).join("");
  } catch (e) { /* palette 缺失不致命 */ }
}
async function refreshEvents() {
  try {
    const path = cursor ? `/api/events?since=${encodeURIComponent(cursor)}` : "/api/events";
    const payload = await fetchJson(path);
    const events = payload.events || payload.items || [];
    if (events.length) {
      cursor = events[events.length - 1].event_id || cursor;
      const rows = events.map(e => `<tr><td class="muted">${esc(e.created_at)}</td>` +
        `<td>${esc(e.event_type)}</td><td class="muted">${esc(e.event_id)}</td></tr>`).join("");
      document.getElementById("events").insertAdjacentHTML("afterbegin", rows);
    }
  } catch (e) { /* 轮询失败下轮重试 */ }
}
async function runInspect(controlId) {
  const box = document.getElementById("result");
  box.textContent = "running " + controlId + "…";
  try {
    const response = await fetch("/api/inspect", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({control_id: controlId}),
    });
    const text = await response.text();
    box.textContent = response.ok
      ? JSON.stringify(JSON.parse(text), null, 2)
      : `refused (${response.status}): ${text}`;
  } catch (e) { box.textContent = "inspect failed: " + e; }
}
async function runExecute(controlId, button) {
  const box = document.getElementById("result");
  const command = button.closest("tr").querySelector("code").textContent;
  // 二步确认：对话框展示将执行的完整命令，取消即零执行。
  if (!window.confirm("Execute this command?\\n\\n" + command)) { return; }
  box.textContent = "executing " + controlId + "…";
  try {
    const response = await fetch("/api/execute", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({control_id: controlId, confirmed: true}),
    });
    const text = await response.text();
    box.textContent = response.ok
      ? JSON.stringify(JSON.parse(text), null, 2)
      : `refused (${response.status}): ${text}`;
    refreshWorkbench();
  } catch (e) { box.textContent = "execute failed: " + e; }
}
refreshWorkbench(); refreshControls(); refreshEvents();
setInterval(refreshWorkbench, 5000);
setInterval(refreshEvents, 5000);
setInterval(refreshControls, 30000);
</script>
"""


class _UIRequestHandler(BaseHTTPRequestHandler):
    project_root: Path = Path(".")

    def log_message(self, *_args: object) -> None:  # 静默访问日志
        return

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler naming
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send(200, "text/html; charset=utf-8", _PAGE.encode("utf-8"))
            return
        if parsed.path.startswith("/api/"):
            name = parsed.path[len("/api/"):]
            argv = UI_API_COMMANDS.get(name)
            if argv is None:
                self._send(404, "text/plain; charset=utf-8", b"unknown api")
                return
            argv = list(argv)
            if name == "events":
                since = parse_qs(parsed.query).get("since", [None])[0]
                if since:
                    argv = ["events", "--since", since]
            payload = run_cli_json(self.project_root, argv)
            self._send(
                200,
                "application/json; charset=utf-8",
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            )
            return
        self._send(404, "text/plain; charset=utf-8", b"not found")

    def _lookup_control(self, control_id: str) -> dict[str, object] | None:
        """Re-resolve a control from the live registry — the browser only ever
        sends a control_id; command text comes exclusively from AgentDeck's
        own control registry."""
        payload = run_cli_json(self.project_root, ["controls", "--control-id", control_id])
        items = payload.get("items") if isinstance(payload, dict) else None
        for item in items or []:
            if isinstance(item, dict) and item.get("control_id") == control_id:
                return item
        return None

    def _resolve_inspect_control(self, control_id: str) -> tuple[list[str] | None, tuple[int, str] | None]:
        """Gates for /api/inspect: enabled + safety=inspect + confirm-free."""
        match = self._lookup_control(control_id)
        if match is None:
            return None, (404, "unknown control_id")
        if not match.get("enabled"):
            return None, (403, "control is disabled")
        if match.get("safety") != "inspect":
            return None, (403, "only inspect controls are executable")
        command = str(match.get("command") or "")
        if not command.startswith("agentdeck "):
            return None, (403, "command is not an agentdeck command")
        argv = shlex.split(command)[1:]
        if any(token == "--confirm" for token in argv):
            return None, (403, "confirm-gated commands are not executable")
        return argv, None

    def _resolve_execute_control(
        self, control_id: str, confirmed: object
    ) -> tuple[list[str] | None, tuple[int, str] | None]:
        """Gates for /api/execute (B 档, user-approved): enabled explicit_user /
        explicit_runtime controls only, two-step confirmed in the browser.
        delegated stays copy-only; placeholder templates are never executable.
        Registry commands may legitimately carry --confirm — the human
        confirmed the sanctioned command via the two-step dialog."""
        match = self._lookup_control(control_id)
        if match is None:
            return None, (404, "unknown control_id")
        if not match.get("enabled"):
            return None, (403, "control is disabled")
        if match.get("safety") not in ("explicit_user", "explicit_runtime"):
            return None, (403, "only explicit controls are executable here (inspect uses /api/inspect; delegated is copy-only)")
        command = str(match.get("command") or "")
        if not command.startswith("agentdeck ") or "<" in command:
            return None, (403, "command is a template or not an agentdeck command")
        if confirmed is not True:
            return None, (428, "confirmation required")
        return shlex.split(command)[1:], None

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler naming
        parsed = urlparse(self.path)
        if parsed.path not in ("/api/inspect", "/api/execute"):
            self._send(405, "text/plain; charset=utf-8", b"read-only server: GET only")
            return
        try:
            length = min(int(self.headers.get("Content-Length") or 0), 4096)
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            control_id = body["control_id"]
            if type(control_id) is not str or not control_id or len(control_id) > 200:
                raise ValueError("invalid control_id")
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            self._send(400, "text/plain; charset=utf-8", b"body must be JSON with control_id")
            return
        if parsed.path == "/api/inspect":
            argv, error = self._resolve_inspect_control(control_id)
        else:
            argv, error = self._resolve_execute_control(control_id, body.get("confirmed"))
        if argv is None:
            status, detail = error or (403, "refused")
            self._send(status, "text/plain; charset=utf-8", detail.encode("utf-8"))
            return
        result = run_cli_json(self.project_root, argv)
        self._send(
            200,
            "application/json; charset=utf-8",
            json.dumps(
                {"ok": True, "control_id": control_id, "command": "agentdeck " + " ".join(argv), "result": result},
                ensure_ascii=False,
            ).encode("utf-8"),
        )

    def _reject_non_get(self) -> None:
        self._send(405, "text/plain; charset=utf-8", b"read-only server: GET only")

    do_PUT = _reject_non_get  # noqa: N815 - stdlib handler naming
    do_DELETE = _reject_non_get  # noqa: N815
    do_PATCH = _reject_non_get  # noqa: N815


def build_server(root: Path, port: int) -> ThreadingHTTPServer:
    handler = type("BoundUIRequestHandler", (_UIRequestHandler,), {"project_root": root})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)
