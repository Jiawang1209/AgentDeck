# Executing Round Loop (`agentdeck run-loop`) — Design (Sub-project 3)

- **Date**: 2026-07-08
- **Status**: Approved (pending spec review)

## Context

Sub-project 3 of 3 in the autonomous-mode goal. Sub-projects 1 (`agentdeck history`) and 2 (bounded autonomous mode: `[autonomous]` policy, `select_auto_approvals`, `policy set-mode --mode autonomous`, `agentdeck approval auto --confirm`) are done.

Today the full cycle — plan → approve → dispatch → capture reply → review → release — is driven by one manual command per step. This sub-project adds the **drive-forward** command that walks that cycle automatically **within the pre-authorized autonomous policy**, stopping at the first gate that needs a human.

Two product decisions were made in brainstorming and are locked:

1. **How far one invocation runs:** run forward until a human is needed, then stop and hand control back.
2. **Worker replies:** the loop does **not** auto-capture replies. When work is dispatched and awaiting a worker reply, it stops and hands the explicit `capture-reply` command to the human. This preserves AgentDeck's existing iron rule: *capturing a worker reply is always an explicit human action; the system never infers task completion.*

### Naming (avoid collision)

The project already ships a **read-only** `agentdeck loop` (loop-once: recommends the next explicit command, mutates nothing) and read-only `agentdeck run --plan-id` (run-progress card). This new command is the **write** counterpart: it actually performs the sanctioned autonomous wave. It is named `agentdeck run-loop` and the spec/docs must state the distinction explicitly so GUI users don't confuse the read-only advisor (`loop`) with the executing engine (`run-loop`).

## Goal

`agentdeck run-loop --plan-id <id> --confirm` drives one plan forward: it performs the sanctioned autonomous actions (auto-approve allowlisted pending approvals within budget, and dispatch approved-and-ready approvals to running panes), then diagnoses where the plan is now stuck and stops there, returning a read-only "loop step card" with the explicit next human command. Every action is audited into `agentdeck history`.

## Non-goals

- No polling/waiting for worker replies; no reading panes to infer completion (locked decision 2).
- No force-spawning agents. Approvals whose target agent has no running pane stay approved-but-blocked and are reported (mirrors `approval dispatch-ready`).
- No new approval semantics: run-loop only *combines* two already-sanctioned actions (`approval auto` + `approval dispatch-ready`), scoped to one plan.
- Not an infinite loop inside one invocation. One invocation performs at most one auto-approve+dispatch wave, then stops at the resulting gate. The human re-runs it after clearing each gate (approve a non-auto item, or capture a reply).
- No autonomous-mode bypass: run-loop requires `--confirm` **and** `config.leader.approval_mode == "autonomous"`, exactly like `approval auto`.

## Design

### 1. Command

`agentdeck run-loop --plan-id <id> --confirm`

- `--plan-id <id>` required; must resolve to a saved plan (else error, no writes).
- `--confirm` required (else reject: "run-loop requires --confirm"; no writes).
- Requires autonomous mode (else reject: "autonomous mode is not enabled; run agentdeck policy set-mode --mode autonomous --confirm --allow-agent <id> --max-approvals <N>"; no writes).
- Validates the plan exists (`store.plan_status(plan_id)`; unknown → error, no writes). It otherwise mirrors its sibling `approval auto` (loads state directly; no separate ProjectView gate — the read-only surfaces it feeds already self-validate).

### 2. The forward wave (write actions — both already sanctioned)

Scoped to the given plan (approvals carry `plan_id`; filter by it, as `approval list --plan-id` already does at cli.py:12301):

1. **Auto-approve** allowlisted pending approvals within budget — reuse `select_auto_approvals(pending_for_plan, policy.allowed_agents, policy.max_approvals)` and the exact per-item path from `approval_auto_command`: `store.decide_approval(id, "approved", reason="autonomous")` + `approval_decided` event with `source="autonomous"`.
2. **Dispatch** every approved-and-ready approval for the plan (whether just auto-approved or previously human-approved) — reuse `_approval_dispatch_preview_card` for the running-pane blocker check and `_dispatch_approved_approval` for the dispatch, exactly as `approval_dispatch_ready_command` does. Non-running targets are recorded as `blocked` (blocker "agent is not running: <id>"), not force-spawned.

Approvals that are pending but **not** allowlisted / over budget are left pending and become the `needs_human_approval` gate below.

### 3. Gate diagnosis (read — reuse `leader review`)

After the wave, determine where the plan is stuck by reusing the existing `leader review` logic for this plan (the single source of truth already used elsewhere; it computes `wait_for_approval`, `wait_for_reply`, all-dispatched-have-replies → summarize/complete). Map the review's `next_action` to a `stopped_reason` + explicit `next_command`:

| review next_action | stopped_reason | next_command (explicit, for the human) |
|---|---|---|
| a dispatch errored this run | `error` | inspect: `agentdeck plan status --plan-id <id>` |
| `dispatch_approved` still present after the wave (an approved step could not be dispatched → its agent has no running pane) | `blocked` | `agentdeck agent spawn --agent <id>` |
| `wait_for_approval` (pending, non-auto) | `needs_human_approval` | `agentdeck approval list` |
| `wait_for_reply` | `waiting_for_reply` | `agentdeck capture-reply --agent <id> --message-id <msg_id>` (the head awaiting reply) |
| `summarize` / complete | `complete` | `agentdeck leader summary --plan-id <id>` |
| nothing actionable | `idle` | `agentdeck run --plan-id <id>` (read-only progress) |

Priority: `error` first, then the single `leader review` next_action determines the reason (`dispatch_approved`→`blocked`, `wait_for_approval`→`needs_human_approval`, `wait_for_reply`→`waiting_for_reply`, `summarize`→`complete`, else `idle`). The `blocked` reason covers an approved-but-undispatchable step (agent not running): run-loop does not force-spawn, so the human is handed the explicit spawn command.

### 4. Output — read-only `run_loop_card`

`_print_json` a payload validated by `validate_run_loop_contract()` before printing (fail → non-zero, no half-baked JSON, record `run_loop_contract_failed`), fields:

- `ok`, `mode="run_loop"`, `plan_id`
- `requires_explicit_user=true`, `safety="delegated"`
- `auto_approved` (count), `dispatched` (list: approval_id/agent_id/message_id/trace_command), `blocked` (list: approval_id/agent_id/blocker), `skipped` (list: approval_id/agent_id/reason) — reuse the `approval auto` result shapes
- `stopped_reason` (enum above), `next_command` (explicit human next step)
- `policy` (allowed_agents, max_approvals)

### 5. Audit + history

- Reuse the wave's existing events: `approval_decided` (source=autonomous), `approval_dispatched`.
- Append one summary event `run_loop_advanced` (payload: plan_id, auto_approved, dispatched, blocked, skipped, stopped_reason).
- `src/agentdeck/history.py::_MILESTONES`: add `run_loop_advanced` → "Run-loop advanced · N dispatched, stopped: <reason>".

### 6. Contract discovery (project convention)

Every GUI-consumable command in this project ships a discovery contract. Add, mirroring `run-schema` / `approvals-schema`:

- `docs/contracts/run-loop-schema.md`
- `agentdeck contract run-loop` (+ `--example`) in `src/agentdeck/contracts.py` (payload + stable example fixture + `validate_run_loop_contract()`), registered in `CONTRACT_INDEX_SPECS` and discoverable via `agentdeck contract list`.
- Update the workbench `contracts_card` contract list to include the run-loop entry.

Explicitly **out of scope** for this spec (candidate follow-up, consistent with sub-project 2's deferral): lighting `run-loop` into the workbench `control_registry`/operator card and adding a natural-language `leader chat` intent for it. The command is fully functional and audited without those; the GUI affordance can come later.

## Safety boundary (preserved)

- Requires an explicit `--confirm` **and** pre-authorized autonomous mode; without either, it rejects and writes nothing.
- Only performs actions already sanctioned elsewhere (auto-approve within the stored allowlist+budget; dispatch approved items to running panes). It invents no new authority.
- Never force-spawns; never captures replies; never infers worker completion. It stops at every human gate and hands back an explicit command.
- Every auto-approve and dispatch is its own audit event; a `run_loop_advanced` summary feeds `agentdeck history`. The program kernel enforces the gates — no LLM in the loop.
- All read surfaces stay read-only and contract-gated.

## Testing

- **Reject paths (no writes):** missing `--confirm`; mode != autonomous; unknown/missing `--plan-id`. Assert state unchanged and correct stderr.
- **Forward wave:** with autonomous policy set and a plan with pending approvals — allowlisted+running are auto-approved and dispatched (fake tmux backend); allowlisted+not-running end `blocked`; non-allowlisted stay pending and drive `needs_human_approval`; over-budget stay pending/`skipped`. Assert `approval_decided`(source=autonomous), `approval_dispatched`, and one `run_loop_advanced` event.
- **Gate diagnosis:** each `stopped_reason` maps to the right `next_command` — after dispatch with no reply → `waiting_for_reply` + the head `capture-reply` command; with a captured reply for the only step → `complete` + summary command; with a non-auto pending approval present → `needs_human_approval`.
- **Contract:** `validate_run_loop_contract()` accepts the example and the live payload; `agentdeck contract run-loop` / `--example` reusable without CLI; index/discovery includes it; live `run-loop` output passes the validator.
- **History:** `run_loop_advanced` humanizes to the expected line.
- Full suite stays green.

## Resolved decisions

- Run until a human is needed; one auto-approve+dispatch wave per invocation, then stop at the gate.
- Never auto-capture replies; stop at `waiting_for_reply` with the explicit `capture-reply` command.
- New command `agentdeck run-loop` (not extending `run`); requires `--confirm` + autonomous mode.
- Reuse `select_auto_approvals` + `approval auto` / `dispatch-ready` internals for the wave, and `leader review` for gate diagnosis — no re-implementation.
- Workbench control-registry lighting and a `leader chat` intent for run-loop are deferred follow-ups.
