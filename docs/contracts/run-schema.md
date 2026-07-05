# Run Start Contract

`agentdeck run --task <text>` starts an approval-gated multi-agent run. It asks the configured Leader provider for a plan, persists that plan, creates pending approval items for approval-required steps, and returns a GUI-ready `run_start` card.

The command does not approve, dispatch, capture pane output, acknowledge inbox items, or send tmux input. Runtime work still requires explicit follow-up commands from the returned controls.

## Discovery

```bash
agentdeck contract run
agentdeck contract run --example
```

Reusable helpers live in `src/agentdeck/contracts.py`:

- `run_start_contract_payload()`
- `run_start_contract_response()`
- `run_start_example()`
- `validate_run_start_contract()`

## Response Fields

`response_fields` describes the live `agentdeck run --task <text>` response:

- `schema_version`
- `ok`
- `mode`
- `task`
- `plan_id`
- `provider`
- `model`
- `approval_count`
- `pending_approval_count`
- `plan`
- `approval_card`
- `next_command`
- `approve_next_command`
- `review_command`
- `continue_command`
- `workbench_command`
- `controls`
- `safety`
- `requires_explicit_user`

`mode` must be `run_start`. `safety` must be `approval_gated`, and `requires_explicit_user` must be `true`.

## Controls

Each item in `controls[]` uses:

- `kind`
- `label`
- `command`
- `safety`
- `enabled`
- `blocker`

The first command should be `agentdeck approval list`. Approving or dispatching work remains explicit and separate from `agentdeck run`.

## Embedded Approval Card

`approval_card` reuses the `agentdeck approval list` queue shape. Clients should validate it against:

```bash
agentdeck contract approvals
```
