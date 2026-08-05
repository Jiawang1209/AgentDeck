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
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AgentDeck</title>
<style>
  :root {
    --bg: #1b1a18; --panel: #232220; --line: #35332f; --ink: #e9e6e0;
    --muted: #97918a; --accent: #c96442; --ok: #7fb069; --warn: #d9a441;
    --mono: ui-monospace, SFMono-Regular, Menlo, monospace;
    --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; height: 100vh; display: grid;
    grid-template-columns: 15rem minmax(0,1fr) 24rem;
    background: var(--bg); color: var(--ink); font-family: var(--sans); font-size: 14px;
  }
  @media (max-width: 1100px) { body { grid-template-columns: 13rem minmax(0,1fr); } #rail { display: none; } }
  h1 { font-size: .95rem; font-weight: 600; margin: 0; }
  h2 {
    font-size: .72rem; font-weight: 600; text-transform: uppercase; letter-spacing: .06em;
    color: var(--muted); margin: 1.25rem 0 .5rem;
  }
  h2:first-child { margin-top: 0; }
  .muted { color: var(--muted); } .ok { color: var(--ok); } .warn { color: var(--warn); }
  code { font-family: var(--mono); background: #2c2a27; padding: .1rem .3rem; border-radius: 3px; }

  /* 队伍面板:每个 worker 一张活动卡 */
  .member {
    border: 1px solid var(--line); border-radius: 10px;
    padding: .6rem .7rem; margin-bottom: .5rem; background: var(--panel);
  }
  .member .top { display: flex; align-items: baseline; gap: .45rem; flex-wrap: wrap; }
  .member .who { font-weight: 650; font-size: .92rem; }
  .member .role { color: var(--muted); font-size: .8rem; }
  .member .facts { margin-top: .3rem; color: var(--muted); font-size: .78rem; line-height: 1.5; }
  .member .acts { display: flex; flex-wrap: wrap; gap: .3rem; margin-top: .45rem; }
  .member .acts button {
    font: inherit; font-size: .74rem; padding: .16rem .45rem;
    border: 1px solid var(--line); border-radius: 5px;
    background: transparent; color: var(--ink); cursor: pointer;
  }
  .member .acts button:disabled { opacity: .45; cursor: not-allowed; }
  .chip {
    font-size: .68rem; padding: .08rem .34rem; border-radius: 4px;
    border: 1px solid var(--line); color: var(--muted); white-space: nowrap;
  }
  .chip.acp { border-color: transparent; background: rgba(90,140,220,.18); color: #7ba6e8; }
  .chip.tmux { border-color: transparent; background: rgba(160,160,150,.16); color: #b6b0a6; }
  .chip.busy { border-color: transparent; background: rgba(217,164,65,.18); color: var(--warn); }

  /* 新建任务对话框 */
  #overlay {
    position: fixed; inset: 0; background: rgba(0,0,0,.55);
    display: none; align-items: center; justify-content: center; z-index: 50;
  }
  #overlay.on { display: flex; }
  #dialog {
    width: min(34rem, calc(100vw - 3rem)); background: var(--panel);
    border: 1px solid var(--line); border-radius: 14px; padding: 1.1rem 1.2rem;
  }
  #dialog h3 { margin: 0 0 .3rem; font-size: .95rem; }
  #dialog .hint { color: var(--muted); font-size: .78rem; line-height: 1.55; margin-bottom: .8rem; }
  #goal {
    width: 100%; min-height: 5.5rem; resize: vertical; background: var(--bg);
    color: var(--ink); border: 1px solid var(--line); border-radius: 9px;
    padding: .6rem .75rem; font: inherit; font-size: .85rem;
  }
  #goal:focus { outline: none; border-color: var(--accent); }
  #dialog .acts { display: flex; gap: .5rem; justify-content: flex-end; margin-top: .8rem; }
  #dialog .acts button {
    border-radius: 9px; padding: .45rem 1rem; font: inherit; font-size: .82rem; cursor: pointer;
  }
  #cancel { background: transparent; color: var(--muted); border: 1px solid var(--line); }
  #create { background: var(--accent); color: #fff; border: 0; font-weight: 600; }
  #create:disabled { opacity: .5; cursor: default; }

  /* 侧边栏:任务 */
  #sidebar {
    border-right: 1px solid var(--line); background: #161514;
    display: flex; flex-direction: column; min-width: 0;
  }
  #sidehead { padding: .85rem .9rem; border-bottom: 1px solid var(--line); }
  #newtask {
    width: 100%; background: transparent; color: var(--ink);
    border: 1px solid var(--line); border-radius: 8px; padding: .45rem .7rem;
    font: inherit; font-size: .82rem; cursor: pointer; text-align: left;
  }
  #newtask:hover { border-color: var(--accent); }
  #tasks { flex: 1; overflow-y: auto; padding: .5rem .55rem 1rem; }
  #tasks .cap {
    font-size: .68rem; text-transform: uppercase; letter-spacing: .06em;
    color: var(--muted); padding: .5rem .35rem .3rem;
  }
  .task {
    display: block; width: 100%; text-align: left; background: transparent;
    color: var(--ink); border: 0; border-radius: 7px; padding: .45rem .5rem;
    cursor: pointer; font: inherit;
  }
  .task:hover { background: #201f1d; }
  .task .tt {
    font-size: .79rem; line-height: 1.35;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  }
  .task .tm { font-size: .68rem; color: var(--muted); margin-top: .18rem; font-family: var(--mono); }

  /* 主区:对话 */
  #main { display: flex; flex-direction: column; min-width: 0; }
  #topbar {
    display: flex; align-items: center; gap: .6rem;
    padding: .85rem 1.5rem; border-bottom: 1px solid var(--line);
  }
  #dot { width: .5rem; height: .5rem; border-radius: 50%; background: var(--accent); }
  #whoami { font-size: .78rem; margin-left: auto; font-family: var(--mono); }
  #stream { flex: 1; overflow-y: auto; padding: 1.5rem; }
  #stream:empty::before {
    content: "问点什么，或者说「帮助」"; color: var(--muted);
  }
  .turn { max-width: 46rem; margin: 0 auto 1.5rem; }
  .turn .who {
    font-size: .7rem; text-transform: uppercase; letter-spacing: .06em;
    color: var(--muted); margin-bottom: .4rem;
  }
  .turn.user .bubble {
    background: var(--panel); border: 1px solid var(--line);
    border-radius: 10px; padding: .7rem .9rem; white-space: pre-wrap;
  }
  .turn.agent .bubble { line-height: 1.6; }
  .card {
    margin-top: .75rem; background: var(--panel); border: 1px solid var(--line);
    border-radius: 10px; padding: .75rem .9rem;
  }
  .card .label {
    font-size: .68rem; text-transform: uppercase; letter-spacing: .06em;
    color: var(--muted); margin-bottom: .45rem;
  }
  .card pre {
    margin: 0; font-family: var(--mono); font-size: .78rem; line-height: 1.5;
    white-space: pre-wrap; word-break: break-word; max-height: 22rem; overflow: auto;
  }
  .row { display: flex; gap: .6rem; padding: .22rem 0; font-size: .82rem; }
  .row .k { color: var(--muted); min-width: 9rem; flex: 0 0 auto; font-family: var(--mono); font-size: .76rem; }
  .row .v { min-width: 0; word-break: break-word; font-family: var(--mono); font-size: .76rem; }
  .list { margin-top: .5rem; border-top: 1px solid var(--line); }
  .li { padding: .45rem 0; border-bottom: 1px solid var(--line); font-size: .8rem; }
  .li .t { font-family: var(--mono); font-size: .76rem; }
  .li .s { color: var(--muted); font-size: .72rem; margin-top: .15rem; }
  .btns { display: flex; flex-wrap: wrap; gap: .4rem; margin-top: .7rem; }
  .btn {
    background: #2c2a27; color: var(--ink); border: 1px solid var(--line);
    border-radius: 7px; padding: .3rem .65rem; font-size: .76rem; cursor: pointer;
  }
  .btn:hover:not(:disabled) { border-color: var(--accent); }
  .btn:disabled { opacity: .45; cursor: not-allowed; }
  .btn.run { border-color: var(--accent); }
  .next { margin-top: .6rem; font-family: var(--mono); font-size: .8rem; color: var(--ok); }
  .fallback {
    margin-bottom: .5rem; padding: .5rem .7rem; border-radius: 8px;
    background: #2a2118; border: 1px solid #4a3a22; color: var(--warn);
    font-size: .78rem; line-height: 1.5;
  }

  /* composer */
  #quick {
    display: flex; flex-wrap: wrap; gap: .4rem; justify-content: center;
    padding: 0 1.5rem .2rem;
  }
  #quick button {
    background: transparent; color: var(--muted); border: 1px solid var(--line);
    border-radius: 999px; padding: .28rem .7rem; font-size: .74rem; cursor: pointer;
  }
  #quick button:hover { color: var(--ink); border-color: var(--accent); }
  #composer {
    border-top: 1px solid var(--line); padding: 1rem 1.5rem 1.25rem;
    display: flex; gap: .6rem; align-items: flex-end;
  }
  #composer .wrap { flex: 1; max-width: 46rem; margin: 0 auto; display: flex; gap: .6rem; }
  #message {
    flex: 1; resize: none; min-height: 2.6rem; max-height: 9rem;
    background: var(--panel); color: var(--ink); border: 1px solid var(--line);
    border-radius: 10px; padding: .7rem .9rem; font: inherit;
  }
  #message:focus { outline: none; border-color: var(--accent); }
  #send {
    background: var(--accent); color: #fff; border: 0; border-radius: 10px;
    padding: 0 1.1rem; height: 2.6rem; font: inherit; font-weight: 600; cursor: pointer;
  }
  #provider {
    height: 2.6rem; background: var(--panel); color: var(--ink);
    border: 1px solid var(--line); border-radius: 10px; padding: 0 .6rem;
    font: inherit; font-size: .82rem; max-width: 13rem; cursor: pointer;
  }
  #provider:focus { outline: none; border-color: var(--accent); }
  #send:disabled { opacity: .5; cursor: default; }

  /* 右栏:既有面板 */
  #rail {
    border-left: 1px solid var(--line); background: #191817;
    overflow-y: auto; padding: 1.1rem 1.25rem;
  }
  #layout { display: flex; flex-direction: column; gap: .75rem; }
  /* 右栏由**受保护脚本**渲染内部内容,所以这里只做外层与样式,不碰它一行 JS。
     每段包成卡片,与对话区的 .card 视觉一致。 */
  .panel {
    background: var(--panel); border: 1px solid var(--line);
    border-radius: 10px; padding: .7rem .8rem;
  }
  .panel h2 { margin: 0 0 .5rem; }
  .panel > div, .panel > pre { font-size: .78rem; line-height: 1.55; }
  table { border-collapse: collapse; width: 100%; font-family: var(--mono); font-size: .72rem; }
  td, th { padding: .3rem .35rem; text-align: left; vertical-align: top; }
  tr + tr td { border-top: 1px solid var(--line); }
  th {
    color: var(--muted); font-weight: 500; font-size: .66rem;
    text-transform: uppercase; letter-spacing: .05em; padding-bottom: .35rem;
  }
  .panel table button {
    background: #2c2a27; color: var(--ink); border: 1px solid var(--line);
    border-radius: 6px; padding: .16rem .45rem; font-size: .7rem; cursor: pointer;
  }
  .panel table button:hover { border-color: var(--accent); }
  #result {
    font-family: var(--mono); font-size: .72rem; white-space: pre-wrap;
    word-break: break-word; max-height: 14rem; overflow: auto; margin: 0;
  }
  button { font: inherit; }
</style>

<div id="overlay">
  <div id="dialog">
    <h3>新建任务</h3>
    <div class="hint">
      交给 Leader 拆解成计划，并为每一步创建<strong>待审批项</strong>。
      到此为止——不会派发给任何 worker，派发要你逐条批准。
    </div>
    <textarea id="goal" placeholder="想让它做什么？例如：给页面加一个深色模式开关，并补上对应的结构测试"></textarea>
    <div class="acts">
      <button id="cancel" type="button">取消</button>
      <button id="create" type="button">生成计划</button>
    </div>
  </div>
</div>

<div id="sidebar">
  <div id="sidehead"><button id="newtask" type="button">＋  新建任务</button></div>
  <div id="tasks"><div class="cap">任务</div></div>
</div>

<div id="main">
  <div id="topbar">
    <span id="dot"></span><h1>AgentDeck</h1>
    <span id="whoami" class="muted">Leader · 载入中…</span>
  </div>
  <div id="stream"></div>
  <div id="quick"></div>
  <form id="composer" autocomplete="off">
    <div class="wrap">
      <textarea id="message" rows="1" placeholder="说点什么…（Enter 发送，Shift+Enter 换行）"></textarea>
      <select id="provider" title="Leader 推理后端"></select>
      <button id="send" type="submit">发送</button>
    </div>
  </form>
</div>

<div id="rail">
  <div id="layout">
    <section class="panel"><h2>概览</h2><div id="overview" class="muted">载入中…</div></section>
    <section class="panel"><h2>队伍</h2><div id="team" class="muted">载入中…</div></section>
    <section class="panel"><h2>Agents</h2><table id="agents"></table></section>
    <section class="panel"><h2>队列</h2><div id="queues" class="muted"></div></section>
    <section class="panel"><h2>命令面板</h2><table id="controls"></table></section>
    <section class="panel"><h2>检查结果</h2><pre id="result" class="muted">点右侧 Run 按钮…</pre></section>
    <section class="panel"><h2>事件</h2><table id="events"></table></section>
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

<script>
  // ④a:对话回路。卡片此刻按原样 JSON 呈现——结构化渲染是 ④b,
  // 在那之前先把形态跑通,免得对着猜出来的数据形状做样式。
  (function () {
    const stream = document.getElementById("stream");
    const form = document.getElementById("composer");
    const input = document.getElementById("message");
    const send = document.getElementById("send");

    function turn(who, label) {
      const el = document.createElement("div");
      el.className = "turn " + who;
      const w = document.createElement("div");
      w.className = "who";
      w.textContent = label;
      el.appendChild(w);
      stream.appendChild(el);
      stream.scrollTop = stream.scrollHeight;
      return el;
    }
    function bubble(el, text) {
      const b = document.createElement("div");
      b.className = "bubble";
      b.textContent = text;
      el.appendChild(b);
      return b;
    }
    function card(el, label, text) {
      const c = document.createElement("div");
      c.className = "card";
      const l = document.createElement("div");
      l.className = "label";
      l.textContent = label;
      const p = document.createElement("pre");
      p.textContent = text;
      c.appendChild(l);
      c.appendChild(p);
      el.appendChild(c);
    }

    // 通用结构化渲染:**不给每种 card 硬编码**。标量成行、数组成列表、
    // controls[] 成按钮。未知卡片也能读,新增 mode 不用改前端——这正是
    // "GUI 只做契约消费方"的落地方式。
    const SKIP = ["mode", "title", "controls", "schema_version"];

    function scalar(v) {
      return v === null || ["string", "number", "boolean"].indexOf(typeof v) >= 0;
    }
    function row(parent, k, v) {
      const r = document.createElement("div");
      r.className = "row";
      const kk = document.createElement("span");
      kk.className = "k";
      kk.textContent = k;
      const vv = document.createElement("span");
      vv.className = "v";
      vv.textContent = v === null ? "—" : String(v);
      if (v === false) { vv.classList.add("muted"); }
      r.appendChild(kk);
      r.appendChild(vv);
      parent.appendChild(r);
    }
    function itemLine(parent, obj) {
      const li = document.createElement("div");
      li.className = "li";
      const t = document.createElement("div");
      t.className = "t";
      t.textContent = obj.label || obj.command || obj.agent_id || obj.name
        || obj.approval_id || obj.plan_id || obj.event_type || JSON.stringify(obj).slice(0, 80);
      li.appendChild(t);
      const bits = ["status", "state", "role", "blocker", "task", "summary", "safety"]
        .filter(function (k) { return obj[k]; })
        .map(function (k) { return k + ": " + obj[k]; });
      if (bits.length) {
        const s = document.createElement("div");
        s.className = "s";
        s.textContent = bits.join("   ");
        li.appendChild(s);
      }
      // 控件多半嵌在 item 里而不是卡片顶层(runtime_card 的 agents、
      // approval_card 的 approvals 都是如此),这里必须一并渲染,
      // 否则界面看得见状态却按不动任何东西。
      if (Array.isArray(obj.controls) && obj.controls.length) {
        controlButtons(li, obj.controls);
      }
      parent.appendChild(li);
    }
    // 按钮的 enabled / blocker / safety **一律照抄 contract**,前端不判断。
    // 只有带 control_id 的才可点(没有 id 就无法经 /api/execute 执行);
    // inspect 走 /api/inspect,explicit_* 走 /api/execute 的二步确认。
    function controlButtons(parent, controls) {
      const box = document.createElement("div");
      box.className = "btns";
      controls.forEach(function (ctl) {
        const b = document.createElement("button");
        b.className = "btn" + (ctl.safety === "inspect" ? " run" : "");
        b.textContent = ctl.label || ctl.kind || "control";
        b.title = ctl.command || "";
        const id = ctl.control_id || registryByCommand[ctl.command];
        const runnable = ctl.enabled && id
          && ["inspect", "explicit_user", "explicit_runtime"].indexOf(ctl.safety) >= 0;
        b.disabled = !runnable;
        if (ctl.blocker) { b.title = ctl.blocker; }
        if (runnable) {
          b.addEventListener("click", function () {
            runControl(Object.assign({}, ctl, { control_id: id }));
          });
        }
        box.appendChild(b);
      });
      if (box.children.length) { parent.appendChild(box); }
    }
    async function runControl(ctl) {
      const inspect = ctl.safety === "inspect";
      if (!inspect && !window.confirm("执行这条命令？\\n\\n" + ctl.command)) { return; }
      const res = await fetch(inspect ? "/api/inspect" : "/api/execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          inspect ? { control_id: ctl.control_id } : { control_id: ctl.control_id, confirmed: true }
        ),
      });
      const el = turn("agent", "AgentDeck");
      if (res.ok) {
        const data = await res.json();
        bubble(el, ctl.label || ctl.command);
        renderCard(el, "result", data.result !== undefined ? data.result : data);
      } else {
        bubble(el, "被拒绝：HTTP " + res.status).classList.add("warn");
      }
      if (typeof refreshAll === "function") { refreshAll(); }
      stream.scrollTop = stream.scrollHeight;
    }

    function renderCard(el, label, obj) {
      if (!obj || typeof obj !== "object") { return card(el, label, String(obj)); }
      const c = document.createElement("div");
      c.className = "card";
      const l = document.createElement("div");
      l.className = "label";
      l.textContent = obj.title || label;
      c.appendChild(l);
      Object.keys(obj).forEach(function (k) {
        if (SKIP.indexOf(k) >= 0) { return; }
        const v = obj[k];
        if (scalar(v)) { row(c, k, v); }
      });
      Object.keys(obj).forEach(function (k) {
        const v = obj[k];
        if (!Array.isArray(v) || !v.length || k === "controls") { return; }
        const h = document.createElement("div");
        h.className = "label";
        h.style.marginTop = ".7rem";
        h.textContent = k + " (" + v.length + ")";
        c.appendChild(h);
        const list = document.createElement("div");
        list.className = "list";
        v.slice(0, 12).forEach(function (item) {
          if (scalar(item)) { row(list, "", item); } else { itemLine(list, item); }
        });
        c.appendChild(list);
      });
      if (Array.isArray(obj.controls)) { controlButtons(c, obj.controls); }
      el.appendChild(c);
    }

    async function ask(text) {
      bubble(turn("user", "你"), text);
      const el = turn("agent", "AgentDeck");
      const b = bubble(el, "…");
      try {
        const res = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: text }),
        });
        if (!res.ok) {
          b.textContent = "请求被拒绝：HTTP " + res.status;
          b.classList.add("warn");
          return;
        }
        const data = await res.json();
        // 未匹配到已知意图时如实说出来。`route_source` 区分两者:`local_rule`
        // 是短语命中了某个 mode,`state_review` 是什么都没匹配上、于是按当前
        // 状态给了个默认回复。把后者当成"对你这句话的答复"呈现,就是把一件
        // 不成立的事说成成立——这个项目在别处一直在消灭这类事。
        const ic = data.intent_card || {};
        if (ic.route_source === "state_review") {
          const warn = document.createElement("div");
          warn.className = "fallback";
          warn.textContent = "没有匹配到已知意图。下面不是对这句话的回答，"
            + "而是按当前项目状态给出的默认汇报。试试「帮助」看可用的说法。";
          el.insertBefore(warn, b);
        }
        b.textContent = (data.leader_explanation && data.leader_explanation.summary)
          || ("mode: " + (data.mode || "?"));
        const embedded = data.intent_card && data.intent_card.embedded_card;
        if (embedded && data[embedded]) {
          renderCard(el, embedded, data[embedded]);
        }
        if (data.next_command) {
          const n = document.createElement("div");
          n.className = "next";
          n.textContent = "下一步  " + data.next_command;
          el.appendChild(n);
        }
        if (typeof refreshAll === "function") { refreshAll(); }
      } catch (err) {
        b.textContent = "请求失败：" + err;
        b.classList.add("warn");
      } finally {
        stream.scrollTop = stream.scrollHeight;
      }
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      const text = input.value.trim();
      if (!text) { return; }
      input.value = "";
      send.disabled = true;
      ask(text).finally(function () { send.disabled = false; input.focus(); });
    });
    input.addEventListener("keydown", function (event) {
      // 输入法组字中的 Enter 是**选词上屏**,不是发送。吃掉它,中文用户的每
      // 一句话都会卡在输入框里:词上不了屏,消息也发不出去。
      // `isComposing` 是标准信号;keyCode 229 是 Safari/旧 Chrome 的说法。
      if (event.isComposing || event.keyCode === 229) { return; }
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        form.requestSubmit();
      }
    });

    // Leader 后端选择器。**全部来自契约**:选项来自控件注册表的
    // scope=provider / kind=set_provider(它们带 control_id,可经 /api/execute
    // 执行),当前项来自 provider_health.provider。前端不判断谁可用、不硬编码
    // 任何 provider 名单——那是 contract 的职责。
    //
    // 只收 set_provider:guarded_set_provider 是"预检后切"的另一种意图,
    // setup_provider 是 `export ...`(不以 agentdeck 开头,/api/execute 按设计
    // 拒绝),二者都不该混进一个"选后端"的下拉里。
    const picker = document.getElementById("provider");
    let providerOptions = [];
    // 命令原文 → control_id 的映射。卡片里的 controls[] **不带 control_id**
    // (实测:runtime_card.agents[].controls 全是 None),而注册表里同一条命令
    // 带 id——注册表本就是 control_id 的权威来源,查它不是绕过契约,正是消费它。
    // 没有这座桥,所有卡片按钮都点不动,界面就只是展示面。
    let registryByCommand = {};

    async function loadProviders() {
      try {
        const [controls, workbench] = await Promise.all([
          fetch("/api/controls").then(function (r) { return r.json(); }),
          fetch("/api/workbench").then(function (r) { return r.json(); }),
        ]);
        const health = workbench.provider_health || {};
        // 你在和谁说话:Leader 是逻辑角色(agent_id=leader、无 pane),
        // worker 是它派发的对象。这条今天就成立,只是界面没说出来。
        const who = document.getElementById("whoami");
        if (who) {
          who.textContent = "Leader · " + (health.provider || "?")
            + " · " + (health.model || "?")
            + (health.ready === false ? " · 未就绪" : "");
          who.classList.toggle("warn", health.ready === false);
        }
        registryByCommand = {};
        (controls.items || []).forEach(function (i) {
          if (i.command && i.control_id && !registryByCommand[i.command]) {
            registryByCommand[i.command] = i.control_id;
          }
        });
        providerOptions = (controls.items || []).filter(function (i) {
          return i.scope === "provider" && i.kind === "set_provider" && i.control_id;
        });
        picker.innerHTML = "";
        const current = document.createElement("option");
        current.value = "";
        current.textContent = (health.provider || "?") + " · " + (health.model || "?");
        picker.appendChild(current);
        providerOptions.forEach(function (item) {
          const opt = document.createElement("option");
          opt.value = item.control_id;
          opt.textContent = item.label || item.command;
          if (!item.enabled) { opt.disabled = true; opt.textContent += " — " + (item.blocker || ""); }
          picker.appendChild(opt);
        });
      } catch (err) {
        picker.innerHTML = "<option>后端列表不可用</option>";
      }
    }

    picker.addEventListener("change", async function () {
      const id = picker.value;
      if (!id) { return; }
      const item = providerOptions.find(function (i) { return i.control_id === id; });
      picker.value = "";
      if (!item) { return; }
      // 切换 Leader 后端是配置写操作(safety=explicit_user),走既有的二步确认:
      // 对话框展示**完整命令原文**,取消即零执行。
      if (!window.confirm("执行这条命令？\\n\\n" + item.command)) { return; }
      const res = await fetch("/api/execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ control_id: id, confirmed: true }),
      });
      const el = turn("agent", "AgentDeck");
      if (res.ok) {
        bubble(el, "已切换 Leader 后端");
        card(el, "command", item.command);
      } else {
        const b = bubble(el, "切换被拒绝：HTTP " + res.status);
        b.classList.add("warn");
      }
      loadProviders();
      if (typeof refreshAll === "function") { refreshAll(); }
    });

    // 快捷入口:每一条都是一句**自然语言**,走的还是同一条 /api/chat。
    // 它不是新功能面,是让人不必记命令就能进到既有的各个 mode——
    // "全部能力都在对话里用"的第一步。
    const QUICK = [
      "帮助",
      "查看运行进度",
      "查看审批",
      "查看 runtime",
      "查看队列",
      "查看账本",
      "查看产物",
      "打开工作台",
    ];
    const quick = document.getElementById("quick");
    QUICK.forEach(function (text) {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = text;
      b.addEventListener("click", function () {
        send.disabled = true;
        ask(text).finally(function () { send.disabled = false; input.focus(); });
      });
      quick.appendChild(b);
    });

    // 侧边栏任务列表。标题取 `project_view.plans.items[].task` ——实测确认
    // 字段名是 `task` 而非 `goal`(设计简报原先记错),因此**不需要动端点
    // 白名单**,workbench 载荷里已经有它。
    // 队伍面板:每个 worker 此刻在做什么。
    //
    // 数据**全部来自已有契约**,一个新端点都没加:`worker_lifecycle_card` 给
    // 生命周期阶段与当前任务,`worker_transport_card` 给配置的传输与就绪度。
    // 2026-08-06 之前 GUI 只画 `runtime_card.agents[]`——那张卡只懂 tmux,于是
    // 把两个刚跑完七步的 ACP worker 画成灰的"未运行"。显示了不成立的事。
    const teamBox = document.getElementById("team");

    function chip(parent, text, cls) {
      const s = document.createElement("span");
      s.className = "chip" + (cls ? " " + cls : "");
      s.textContent = text;
      parent.appendChild(s);
      return s;
    }

    async function loadTeam() {
      try {
        const wb = await fetch("/api/workbench").then(function (r) { return r.json(); });
        const life = ((wb.worker_lifecycle_card || {}).items) || [];
        const trans = ((wb.worker_transport_card || {}).items) || [];
        const byId = {};
        trans.forEach(function (item) { byId[item.agent_id] = item; });
        teamBox.className = "";
        teamBox.innerHTML = "";
        if (!life.length) {
          teamBox.className = "muted";
          teamBox.textContent = "还没有配置 worker";
          return;
        }
        life.forEach(function (m) {
          const tr = byId[m.agent_id] || {};
          const card = document.createElement("div");
          card.className = "member";

          const top = document.createElement("div");
          top.className = "top";
          const who = document.createElement("span");
          who.className = "who";
          who.textContent = m.agent_id;
          top.appendChild(who);
          const role = document.createElement("span");
          role.className = "role";
          role.textContent = m.role || "";
          top.appendChild(role);
          // transport 照抄契约,不猜:配了什么就显示什么。
          const tp = tr.configured_transport || "tmux";
          chip(top, tp, tp === "acp" ? "acp" : "tmux");
          if (m.lifecycle_stage) {
            chip(top, m.lifecycle_stage, m.lifecycle_stage === "idle" ? "" : "busy");
          }
          card.appendChild(top);

          const facts = document.createElement("div");
          facts.className = "facts";
          const bits = [];
          if (m.provider) { bits.push(m.provider); }
          if (m.active_message_id) { bits.push("在做 " + m.active_message_id.slice(0, 12)); }
          if (m.pending_inbox_count) { bits.push("待确认 " + m.pending_inbox_count); }
          if (m.artifact_count) { bits.push("产物 " + m.artifact_count); }
          // ACP worker 没有 pane,这不是缺陷——别把它渲染成缺陷。
          if (tp === "tmux") { bits.push(m.pane_id ? "pane " + m.pane_id : "未启动"); }
          if (tr.readiness) { bits.push("传输 " + tr.readiness); }
          facts.textContent = bits.join(" · ");
          card.appendChild(facts);

          // blocker 照抄,不改写措辞。
          (tr.blockers || []).forEach(function (b) {
            const w = document.createElement("div");
            w.className = "facts warn";
            w.textContent = b;
            card.appendChild(w);
          });

          const acts = document.createElement("div");
          acts.className = "acts";
          card.appendChild(acts);
          controlButtons(acts, m.controls || []);
          teamBox.appendChild(card);
        });
        // 旧的 Agents 表只懂 tmux。元素留着(受保护的脚本块仍会写它),
        // 但不再展示两份互相矛盾的说法。
        const legacy = document.getElementById("agents");
        if (legacy && legacy.closest("section")) {
          legacy.closest("section").style.display = "none";
        }
      } catch (err) {
        teamBox.className = "muted";
        teamBox.textContent = "队伍状态不可用";
      }
    }

    const tasksBox = document.getElementById("tasks");

    async function loadTasks() {
      try {
        const wb = await fetch("/api/workbench").then(function (r) { return r.json(); });
        const items = ((wb.project_view || {}).plans || {}).items || [];
        tasksBox.innerHTML = "";
        const cap = document.createElement("div");
        cap.className = "cap";
        cap.textContent = "任务 (" + items.length + ")";
        tasksBox.appendChild(cap);
        items.slice().reverse().forEach(function (plan) {
          const b = document.createElement("button");
          b.className = "task";
          b.type = "button";
          const tt = document.createElement("div");
          tt.className = "tt";
          tt.textContent = plan.task || plan.plan_id;
          const tm = document.createElement("div");
          tm.className = "tm";
          tm.textContent = (plan.status || "?") + " · " + (plan.plan_id || "").slice(0, 12);
          b.appendChild(tt);
          b.appendChild(tm);
          b.title = plan.plan_id || "";
          // 点任务 = 问它的进度。走的还是同一条 /api/chat,不新增动作面。
          b.addEventListener("click", function () {
            send.disabled = true;
            ask("查看运行进度 " + plan.plan_id)
              .finally(function () { send.disabled = false; });
          });
          tasksBox.appendChild(b);
        });
      } catch (err) {
        tasksBox.innerHTML = "<div class='cap'>任务列表不可用</div>";
      }
    }

    // 新建任务:走自然语言的 run_start mode——它调 Leader 出计划并创建
    // **待审批项**,停在审批门前,不派发。审批门一条不减,GUI 只是把入口
    // 摆出来,不改变它背后发生什么。
    const overlay = document.getElementById("overlay");
    const goal = document.getElementById("goal");
    const create = document.getElementById("create");

    function closeDialog() { overlay.classList.remove("on"); }

    document.getElementById("newtask").addEventListener("click", function () {
      goal.value = "";
      overlay.classList.add("on");
      goal.focus();
    });
    document.getElementById("cancel").addEventListener("click", closeDialog);
    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) { closeDialog(); }
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && overlay.classList.contains("on")) { closeDialog(); }
    });
    create.addEventListener("click", function () {
      const text = goal.value.trim();
      if (!text) { goal.focus(); return; }
      closeDialog();
      send.disabled = true;
      create.disabled = true;
      ask("开始运行 " + text).finally(function () {
        send.disabled = false;
        create.disabled = false;
        loadTasks();
      });
    });

    loadTasks();
    // 队伍面板要等注册表拉完才画:控件按命令原文换 control_id,换不到就画成
    // 禁用——这是既有的诚实规则,不能因为先画了而绕过它。
    loadProviders().then(loadTeam, loadTeam);
    setInterval(loadTeam, 5000);
  })();
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
        if parsed.path not in ("/api/inspect", "/api/execute", "/api/chat"):
            self._send(405, "text/plain; charset=utf-8", b"read-only server: GET only")
            return
        try:
            length = min(int(self.headers.get("Content-Length") or 0), 4096)
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send(400, "text/plain; charset=utf-8", b"body must be JSON")
            return
        if parsed.path == "/api/chat":
            # 与另两条 POST 的本质差异:浏览器在这里发的是**自由文本**。
            # 缓解不是限制文本,而是文本从不进入命令位置——它作为单个 argv
            # 元素传给 `leader chat --message <text>`(与 events --since 同一
            # 处理,无 shell),所以它是数据不是命令。
            #
            # 不做 confirmed 二步确认:chat 契约本身就不执行 runtime 动作,
            # 二步确认在这里只是噪音,还会让人误以为这是个执行面。
            message = body.get("message") if isinstance(body, dict) else None
            if type(message) is not str or not message.strip() or len(message) > 4000:
                self._send(
                    400, "text/plain; charset=utf-8", b"body must be JSON with a non-empty message"
                )
                return
            result = run_cli_json(
                self.project_root, ["leader", "chat", "--message", message]
            )
            # 原样透传:chat 响应本身就是契约载荷(intent_card / mode /
            # embedded_card / controls),再包一层只会让浏览器多剥一次。
            self._send(
                200,
                "application/json; charset=utf-8",
                json.dumps(result, ensure_ascii=False).encode("utf-8"),
            )
            return
        try:
            control_id = body["control_id"]
            if type(control_id) is not str or not control_id or len(control_id) > 200:
                raise ValueError("invalid control_id")
        except (ValueError, KeyError, TypeError):
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
