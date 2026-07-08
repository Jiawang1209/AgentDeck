# Plan Board Contract

`agentdeck plan board` is a **read-only** multi-plan overview. It lists every saved plan with its derived gate and the explicit per-plan next command, plus active/done counts, so the operator can see and drive multiple plans at once ("A waits on my approval, B waits on a reply, C is done"). It is the first slice of the multi-plan-parallel lane.

It reuses the existing read-only `store.leader_review(plan_id)` and the pure `run_loop_gate(review, has_error=False, plan_id)` (`src/agentdeck/autonomy.py`) to derive each row's gate + next command — no new mapping.

## Safety boundary

- Pure read-only aggregation of existing per-plan facts. No provider call, no tmux read/write, no state mutation, no chat turn/event.
- Every `next_command` is an explicit human command (the same ones the operator already runs); the board only surfaces them, it never executes.
- An empty project (no plans) returns `plan_count=0`, `plans=[]`, `active_count=0` — valid, exit 0 (not an error).

## Discovery

```bash
agentdeck contract plans
agentdeck contract plans --example
```

Reusable helpers live in `src/agentdeck/contracts.py`:

- `plan_board_contract_payload()`
- `plan_board_contract_response()`
- `plan_board_example()`
- `validate_plan_board_contract()`

The pure gate-diagnosis function `run_loop_gate(review, has_error, plan_id)` lives in `src/agentdeck/autonomy.py`.

## Response Fields

`plan_board_response_fields` describes the live `agentdeck plan board` response:

- `ok`
- `mode` — must be `plan_board`
- `board_command` — must be `agentdeck plan board`
- `plan_count` — must equal `len(plans)`
- `active_count` — must equal the number of items with `active == true`
- `plans`

`plan_board_item_fields` describes each `plans[]` item:

- `plan_id`, `task`, `provider_backend`, `created_at`, `status` (from the plan record)
- `gate`, `next_command` (from `run_loop_gate`)
- `active` (bool; `true` iff `gate != "complete"`)
- `counts` (from `leader_review.counts`)

## Gates

`gates` enumerates the `gate` values, each mapped to an explicit per-plan `next_command` for the human (derived by `run_loop_gate`, always with `has_error=False`):

| `leader review` next_action | `gate` | `next_command` |
|---|---|---|
| `dispatch_approved` (approved step could not dispatch — agent not running) | `blocked` | `agentdeck agent spawn --agent <id>` |
| `wait_for_approval` | `needs_human_approval` | `agentdeck approval list` |
| `wait_for_reply` | `waiting_for_reply` | `agentdeck capture-reply --agent <id> --message-id <id>` |
| `summarize` / complete | `complete` | `agentdeck leader summary --plan-id <id>` |
| nothing actionable | `idle` | `agentdeck run --plan-id <id>` |

## Validation

The live `plan board` payload is validated by `validate_plan_board_contract()` before printing. On failure it returns non-zero and prints no half-baked JSON. The validator checks the top-level fields, that `mode`/`board_command` are exact, that `plan_count == len(plans)`, that each item carries every `plan_board_item_field`, that each `gate` is one of the enum, that each `next_command` is a non-empty string, and that `active_count` equals the number of active items.
