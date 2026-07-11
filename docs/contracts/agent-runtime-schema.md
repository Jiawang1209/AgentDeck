# Agent Runtime Contract

`agentdeck contract agent-runtime` is the read-only discovery surface for GUI, TUI, natural-language, and automation clients that need to control visible agent panes through AgentDeck commands.

It does not read `.agentdeck/` state, does not inspect tmux panes, does not spawn or stop agents, and does not send tmux input. It only describes the command templates, stable response fields, and informational transport capabilities that clients can render. Capability metadata is not an authorization control and does not change readiness, pane creation, send, capture, or approval behavior.

## Discovery

```bash
agentdeck contract agent-runtime
agentdeck contract agent-runtime --example
```

The contract command returns:

```json
{
  "schema_version": "project-view/v1",
  "list_command": "agentdeck agent list",
  "ready_command": "agentdeck agent ready",
  "spawn_ready_command": "agentdeck agent spawn-ready --confirm",
  "spawn_command_template": "agentdeck agent spawn --agent <id>",
  "terminal_command_template": "agentdeck agent terminal --agent <id>",
  "capture_command_template": "agentdeck agent capture --agent <id> --lines 200",
  "send_command_template": "agentdeck agent send --agent <id> --text <text>",
  "stop_command_template": "agentdeck agent stop --agent <id>",
  "refresh_command": "agentdeck agent refresh",
  "contract_path": "docs/contracts/agent-runtime-schema.md",
  "contract_exists": true,
  "agent_item_fields": [],
  "capture_response_fields": [],
  "terminal_response_fields": [],
  "refresh_response_fields": [],
  "refresh_agent_fields": [],
  "ready_response_fields": [],
  "spawn_ready_response_fields": [],
  "spawn_ready_result_fields": [],
  "transport_capability_fields": [
    "structured_sessions",
    "streaming_updates",
    "structured_tools",
    "permission_requests",
    "resume_session",
    "observable_terminal"
  ],
  "tmux_fallback_capabilities": {
    "structured_sessions": false,
    "streaming_updates": false,
    "structured_tools": false,
    "permission_requests": false,
    "resume_session": false,
    "observable_terminal": true
  },
  "runtime_control_fields": [],
  "project_view_schema_version": "project-view/v1",
  "project_view_contract": "agentdeck contract project-view",
  "workbench_contract": "agentdeck contract workbench"
}
```

Use `agentdeck contract agent-runtime --example` to include a stable GUI-ready fixture with one running agent, a ready response, a spawn-ready response, a terminal response, a capture response, and reusable runtime controls.

`tmux_fallback_capabilities` declares tmux as an observable terminal fallback only. In particular, it does not claim structured sessions or permission requests, does not claim ACP compatibility, and is never projected into `runtime_control_fields`.

## Agent Item Fields

- `agent_id`: configured agent id.
- `role`: current role assigned in `.agentdeck/config.toml`.
- `provider`: agent provider, such as `codex` or `claude`.
- `workspace_mode`: whether the agent uses the shared workspace or a future isolated mode.
- `runtime`: runtime binding summary from ProjectView, including pane id, session name, cwd, and status.

## Ready Response Fields

`agentdeck agent ready` is a read-only startup card for answering whether the configured multi-agent team is runtime-ready:

```json
{
  "ok": true,
  "mode": "agent_runtime_ready",
  "runtime_backend": "tmux",
  "total_count": 3,
  "running_count": 1,
  "not_running_count": 2,
  "all_running": false,
  "next_command": "agentdeck agent spawn-ready --confirm",
  "spawn_commands": [
    "agentdeck agent spawn --agent coder",
    "agentdeck agent spawn --agent reviewer"
  ],
  "spawn_ready_command": "agentdeck agent spawn-ready --confirm",
  "refresh_command": "agentdeck agent refresh",
  "dispatch_ready_command": "agentdeck approval dispatch-ready --confirm",
  "controls": [],
  "runtime_card": {}
}
```

- `ok`: whether the readiness card was produced.
- `mode`: always `agent_runtime_ready`.
- `runtime_backend`: configured runtime backend, currently `tmux`.
- `total_count`: number of configured agents in the runtime card.
- `running_count`: number of agents with `status=running` and a pane id.
- `not_running_count`: configured agents that still need explicit spawn or repair.
- `all_running`: whether every configured agent has a running pane.
- `next_command`: explicit `agentdeck agent spawn-ready --confirm` when multiple configured agents are not running, the single agent spawn command when exactly one agent is not running, otherwise `agentdeck approval dispatch-ready --confirm`.
- `spawn_commands`: explicit spawn commands for every not-running configured agent.
- `spawn_ready_command`: explicit batch startup command for all not-running configured agents.
- `refresh_command`: explicit runtime reconciliation command.
- `dispatch_ready_command`: explicit batch approval dispatch command for the later step after agents are running.
- `controls`: GUI-ready controls for inspecting readiness, running the current startup/dispatch next command, and refreshing runtime bindings.
- `runtime_card`: the same GUI-ready runtime card shape used by `agentdeck workbench`.

The command does not inspect tmux, create panes, refresh bindings, send input, write events, or dispatch approvals.

## Spawn-Ready Response Fields

`agentdeck agent spawn-ready --confirm` is the explicit batch startup command for configured agents that are not already running:

```json
{
  "ok": true,
  "mode": "agent_spawn_ready",
  "requires_explicit_user": true,
  "safety": "explicit_runtime",
  "spawned_count": 2,
  "skipped_count": 1,
  "results": [
    {
      "agent_id": "planner",
      "status": "skipped",
      "previous_status": "running",
      "pane_id": "%42",
      "spawn_command": "agentdeck agent spawn --agent planner",
      "blocker": "agent already running"
    },
    {
      "agent_id": "coder",
      "status": "spawned",
      "previous_status": "configured",
      "pane_id": "%43",
      "spawn_command": "agentdeck agent spawn --agent coder",
      "blocker": null
    }
  ],
  "ready_command": "agentdeck agent ready"
}
```

- `ok`: whether the batch command completed.
- `mode`: always `agent_spawn_ready`.
- `requires_explicit_user`: always `true`.
- `safety`: always `explicit_runtime`.
- `spawned_count`: number of agents spawned in this run.
- `skipped_count`: number of already-running agents skipped.
- `results`: per-agent result list with agent id, previous status, pane id, explicit single-agent spawn command, and blocker.
- `ready_command`: command to re-open the readiness card after startup.

The command requires `--confirm`; without it, the command exits non-zero and must not write state or create panes.

## Terminal Response Fields

`agentdeck agent terminal --agent <id>` is a read-only terminal card for opening or locating a visible tmux pane:

```json
{
  "ok": true,
  "mode": "agent_terminal",
  "agent_id": "planner",
  "role": "planning",
  "provider": "codex",
  "workspace_mode": "shared",
  "status": "running",
  "pane_id": "%42",
  "session_name": "agentdeck",
  "cwd": "/workspace/project",
  "attach_command": "tmux -L agentdeck-example attach -t agentdeck",
  "select_pane_command": "tmux -L agentdeck-example select-pane -t %42",
  "capture_command": "agentdeck agent capture --agent planner --lines 200",
  "send_command_template": "agentdeck agent send --agent planner --text <text>",
  "stop_command": "agentdeck agent stop --agent planner",
  "inbox_command": "agentdeck inbox --agent planner",
  "refresh_command": "agentdeck agent refresh",
  "controls": []
}
```

- `attach_command`: explicit tmux command for attaching to the configured project session.
- `select_pane_command`: explicit tmux command for selecting the agent pane.
- `capture_command`: read-only output capture command.
- `send_command_template`: explicit input template that still requires text.
- `stop_command`: explicit pane stop command.
- `inbox_command`: read-only mailbox command for the same agent.
- `controls`: the same runtime controls used by workbench runtime agents.

The command does not attach to tmux, read pane output, send input, write state, or append events.

## Capture Response Fields

`agentdeck agent capture --agent <id> --lines 200` returns:

```json
{
  "agent_id": "planner",
  "pane_id": "%42",
  "output": "status: completed\n"
}
```

- `agent_id`: captured agent id.
- `pane_id`: tmux pane id bound to the agent.
- `output`: captured pane output.

## Refresh Response Fields

`agentdeck agent refresh` explicitly checks stored `running` pane bindings against tmux and marks missing panes as `stale`:

```json
{
  "ok": true,
  "agents": [
    {
      "agent_id": "planner",
      "previous_status": "running",
      "status": "stale",
      "pane_id": "%42",
      "pane_exists": false,
      "changed": true
    }
  ],
  "stale_count": 1,
  "running_count": 0
}
```

- `ok`: whether the refresh command completed.
- `agents`: per-agent refresh summaries.
- `stale_count`: number of running bindings changed to `stale`.
- `running_count`: number of running bindings confirmed to still have a live pane.

Refresh agent items contain:

- `agent_id`: configured agent id.
- `previous_status`: status before refresh.
- `status`: status after refresh.
- `pane_id`: pane id checked for that agent, if any.
- `pane_exists`: `true` or `false` for checked running panes; `null` when no pane was checked.
- `changed`: whether refresh mutated that agent binding.

## Runtime Controls

`runtime_control_fields` reuses the same control item fields as the workbench runtime card:

- `kind`
- `label`
- `command`
- `safety`
- `enabled`
- `blocker`

`capture` controls are inspect-only. `refresh` is an explicit runtime reconciliation command. `spawn`, `send`, and `stop` controls are explicit runtime actions and must be triggered by a human or an equivalent explicit user command. GUI clients must never treat this contract as permission to auto-send text or auto-kill panes.

## Related Surfaces

- `agentdeck agent list` returns the current ProjectView, where `agents[]` contains the live runtime binding for each configured agent.
- `agentdeck workbench` embeds a render-ready `runtime_card` and `contracts_card.agent_runtime_contract`.
- `agentdeck contract workbench` publishes the workbench runtime card fields and the same runtime control fields.
