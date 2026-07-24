# Run Loop Contract

`agentdeck run-loop --plan-id <id> --confirm` is the **write** counterpart to the read-only `agentdeck loop`. It drives one plan forward within the pre-authorized autonomous policy: it auto-approves allowlisted pending approvals within budget, dispatches every approved-and-ready approval to a running pane, then diagnoses where the plan is now stuck and stops there with an explicit next human command. Every action is audited into `agentdeck history`.

`agentdeck run-loop` is distinct from two read-only siblings:

- `agentdeck loop` — a read-only advisor that recommends the next explicit command and mutates nothing.
- `agentdeck run --plan-id <id>` — a read-only `run_progress` card.

`run-loop` is the only one of the three that performs the sanctioned autonomous wave.

## Safety boundary

- Requires an explicit `--confirm` **and** `config.leader.approval_mode == "autonomous"`. Without either, it rejects and writes nothing.
- Only performs actions already sanctioned elsewhere: auto-approve within the stored allowlist + count budget (reusing `select_auto_approvals`), and dispatch approved items to running panes (reusing the `approval dispatch-ready` internals). It invents no new authority.
- Never force-spawns an agent. Approvals whose target agent has no running pane stay approved-but-`blocked` and are reported (the human is handed the explicit `agentdeck agent spawn --agent <id>` command).
- Never captures worker replies and never infers task completion. When work is dispatched and awaiting a reply, it stops at the `waiting_for_reply` gate and hands the explicit `agentdeck capture-reply --agent <id> --message-id <id>` command to the human. In that stop state the response additionally carries an optional read-only derived field `reply_file_ready` (bool): `true` when the awaited file-channel reply `.agentdeck/replies/<message_id>.reply.txt` already exists on disk. It is a signal for humans and GUI clients that running the explicit capture command will succeed — run-loop still never reads or ingests the file itself, and the field is absent for every other `stopped_reason` (`RUN_LOOP_RESPONSE_FIELDS`, the required set, is unchanged).
- One invocation performs at most one auto-approve + dispatch wave, then stops at the resulting human gate. The human re-runs it after clearing each gate.
- Every auto-approve and dispatch is its own audit event (`approval_decided` with `source=autonomous`, `approval_dispatched`); a `run_loop_advanced` summary event feeds `agentdeck history`. The program kernel enforces the gates — no LLM is in the loop.

## Discovery

```bash
agentdeck contract run-loop
agentdeck contract run-loop --example
```

Reusable helpers live in `src/agentdeck/contracts.py`:

- `run_loop_contract_payload()`
- `run_loop_contract_response()`
- `run_loop_example()`
- `validate_run_loop_contract()`

The pure gate-diagnosis function `run_loop_gate(review, has_error, plan_id)` lives in `src/agentdeck/autonomy.py`.

## Response Fields

`run_loop_response_fields` describes the live `agentdeck run-loop --plan-id <id> --confirm` response:

- `ok`
- `mode`
- `plan_id`
- `requires_explicit_user`
- `safety`
- `auto_approved`
- `dispatched`
- `blocked`
- `skipped`
- `stopped_reason`
- `next_command`
- `policy`

`mode` must be `run_loop`. `safety` must be `delegated`, and `requires_explicit_user` must be `true`. `auto_approved` is the count of pending approvals auto-approved this wave. `dispatched[]`, `blocked[]`, and `skipped[]` reuse the `agentdeck approval auto` result shapes (`dispatched` items carry `approval_id`/`agent_id`/`message_id`/`trace_command`; `blocked` items carry `approval_id`/`agent_id`/`blocker`; `skipped` items carry `approval_id`/`agent_id`/`reason`). `policy` echoes the stored `allowed_agents` and `max_approvals`.

## Stop reasons

`stop_reasons` enumerates the `stopped_reason` values, each mapped to an explicit `next_command` for the human. Priority: `error` first, then the single `leader review` `next_action` determines the reason.

| `leader review` next_action | `stopped_reason` | `next_command` |
|---|---|---|
| a dispatch errored this run | `error` | `agentdeck plan status --plan-id <id>` |
| `dispatch_approved` still present (approved step could not dispatch — agent not running) | `blocked` | `agentdeck agent spawn --agent <id>` |
| `wait_for_approval` (pending, non-auto) | `needs_human_approval` | `agentdeck approval list` |
| `wait_for_reply` | `waiting_for_reply` | `agentdeck capture-reply --agent <id> --message-id <id>` |
| `summarize` / complete | `complete` | `agentdeck leader summary --plan-id <id>` |
| nothing actionable | `idle` | `agentdeck run --plan-id <id>` |

## Validation

The live `run-loop` payload is validated by `validate_run_loop_contract()` before printing. On failure it returns non-zero, prints no half-baked JSON, and records a `run_loop_contract_failed` event.
