# Loop Contract

`agentdeck loop once` is the deterministic, read-only programmatic loop surface for AgentDeck.

It validates ProjectView, derives the same `continue_card` used by `agentdeck continue`, and wraps it with loop metadata so CLI, GUI, TUI, or a future scheduler can decide what the next explicit human command should be. It does not call a Leader provider, inspect tmux panes, send tmux input, approve work, dispatch work, capture replies, acknowledge inbox items, or write state.

## Command

```bash
conda activate agentdeck
agentdeck loop once
```

## Response Fields

- `ok`: always `true` when emitted.
- `mode`: always `loop_once`.
- `loop_id`: stable id for this deterministic one-shot loop, `run_once`.
- `iteration`: always `1`.
- `max_iterations`: always `1`.
- `source_command`: exactly `agentdeck loop once`.
- `project_view_command`: read-only source command, `agentdeck status`.
- `continue_command`: read-only recovery card command, `agentdeck continue`.
- `workbench_command`: read-only workbench command, `agentdeck workbench`.
- `status`: copied from the embedded continue card.
- `reason`: copied from the embedded continue card.
- `recovery`: validated ProjectView recovery object.
- `continue_card`: embedded `agentdeck continue` payload.
- `recommended_action`: copied from `continue_card.recommended_action`.
- `next_command`: copied from `continue_card.next_command`.
- `stop_reason`: `requires_human_command` when a next command exists, otherwise `idle`.
- `will_execute`: always `false`.
- `requires_explicit_user`: `true` when `next_command` exists.
- `safety`: copied from `recommended_action.safety` when available.
- `controls`: GUI-ready inspect controls plus an `execute_next` control that points at `next_command` but is never executed by `loop once`.

## Boundaries

- The command is read-only and state-driven.
- It is a programmatic shell around recovery/continue, not a model call.
- It must pass `validate_project_view_contract()`, `validate_continue_contract()`, and `validate_loop_once_contract()` before printing JSON.
- It may recommend explicit commands such as `agentdeck approval list`, `agentdeck approval dispatch-ready --confirm`, `agentdeck capture-reply ...`, or `agentdeck doctor`, but it never runs them.
- GUI or automation clients may render `controls[]`, but must keep execution behind a human action or a later explicit policy gate.

Use `agentdeck contract loop --example` for a stable example payload.
