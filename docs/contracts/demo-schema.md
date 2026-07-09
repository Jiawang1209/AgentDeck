# Demo Contract

`agentdeck demo golden` returns a read-only guide for running the AgentDeck golden demo. The guide can recommend explicit operator commands, but it never executes them. Mutating commands are recommendations only and require explicit operator execution.

## Discovery

```bash
agentdeck contract demo
agentdeck contract demo --example
```

`agentdeck contract demo --example` includes a GUI-ready `example_golden_demo` payload.

## Golden Demo Command

```bash
agentdeck demo golden
```

## Top-Level Payload Fields

- `ok`
- `mode`
- `demo_name`
- `summary`
- `current_status`
- `next_command`
- `recommended_task`
- `steps`
- `inspection_commands`
- `safety`
- `source_command`

## Step Fields

- `step_id`
- `title`
- `status`
- `command`
- `enabled`
- `blocker`
- `safety`
- `description`
- `checks`

## Step Status Values

- `ready`
- `blocked`
- `waiting_for_input`
- `done`
- `inspect`

## Safety Values

- `inspect`
- `explicit_user`
- `explicit_runtime`

Top-level `safety` must be `inspect`. Step safety can mark whether a command is inspect-only or requires explicit user/runtime action, but no safety value is an execution authorization.
