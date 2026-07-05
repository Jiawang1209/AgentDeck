# Agent Runtime Contract

`agentdeck contract agent-runtime` is the read-only discovery surface for GUI, TUI, natural-language, and automation clients that need to control visible agent panes through AgentDeck commands.

It does not read `.agentdeck/` state, does not inspect tmux panes, does not spawn or stop agents, and does not send tmux input. It only describes the command templates and stable response fields that clients can render as human-triggered controls.

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
  "spawn_command_template": "agentdeck agent spawn --agent <id>",
  "capture_command_template": "agentdeck agent capture --agent <id> --lines 200",
  "send_command_template": "agentdeck agent send --agent <id> --text <text>",
  "stop_command_template": "agentdeck agent stop --agent <id>",
  "contract_path": "docs/contracts/agent-runtime-schema.md",
  "contract_exists": true,
  "agent_item_fields": [],
  "capture_response_fields": [],
  "runtime_control_fields": [],
  "project_view_schema_version": "project-view/v1",
  "project_view_contract": "agentdeck contract project-view",
  "workbench_contract": "agentdeck contract workbench"
}
```

Use `agentdeck contract agent-runtime --example` to include a stable GUI-ready fixture with one running agent, a capture response, and reusable runtime controls.

## Agent Item Fields

- `agent_id`: configured agent id.
- `role`: current role assigned in `.agentdeck/config.toml`.
- `provider`: agent provider, such as `codex` or `claude`.
- `workspace_mode`: whether the agent uses the shared workspace or a future isolated mode.
- `runtime`: runtime binding summary from ProjectView, including pane id, session name, cwd, and status.

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

## Runtime Controls

`runtime_control_fields` reuses the same control item fields as the workbench runtime card:

- `kind`
- `label`
- `command`
- `safety`
- `enabled`
- `blocker`

`capture` controls are inspect-only. `spawn`, `send`, and `stop` controls are explicit runtime actions and must be triggered by a human or an equivalent explicit user command. GUI clients must never treat this contract as permission to auto-send text or auto-kill panes.

## Related Surfaces

- `agentdeck agent list` returns the current ProjectView, where `agents[]` contains the live runtime binding for each configured agent.
- `agentdeck workbench` embeds a render-ready `runtime_card` and `contracts_card.agent_runtime_contract`.
- `agentdeck contract workbench` publishes the workbench runtime card fields and the same runtime control fields.
