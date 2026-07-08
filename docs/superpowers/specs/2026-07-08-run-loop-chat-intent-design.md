# Natural-language `run-loop` preview intent (`leader chat`) — Design

- **Date**: 2026-07-08
- **Status**: Approved (pending spec review)

## Context

Final GUI-mainline follow-up for the autonomous-mode goal. `agentdeck run-loop --plan-id <id> --confirm` (sub-project 3) and its `scope=autonomous` command-palette group (previous slice) exist. This slice adds a **read-only** natural-language entry so a human can type "推进计划 pln_xxx" and get the explicit `run-loop` command surfaced — without the chat ever executing it.

The hard invariant this preserves: **`agentdeck leader chat` never executes a runtime/dispatch action.** `run-loop` dispatches, so chat must not run it. Chat *suggests* the explicit command; the human (or a future GUI button) runs it. This mirrors the existing read-only preview intents (`run_progress`, `role_assign`, dispatch-preview): the chat turn is read-only, but the recommended next step is an explicit-user runtime command.

## Goal

`agentdeck leader chat --message "推进计划 pln_xxx"` (and English/synonym variants) returns `mode=run_loop_preview`, embeds a read-only `run_loop_preview_card`, and hands back the explicit `agentdeck run-loop --plan-id pln_xxx --confirm` command (requires autonomous mode). It records only the chat turn + audit event; it does not call a provider, read/write tmux, auto-approve, dispatch, or mutate approval/runtime/plan state.

## Non-goals

- No execution of the run-loop wave from chat (invariant above).
- No new run-loop behavior; this only surfaces the existing command via natural language.
- No changes to the `run-loop` command, its contract, or the `scope=autonomous` controls.

## Design

### 1. Intent detection

Mirror `_chat_wants_run_progress` / `_chat_run_progress_plan_id`:

- `_chat_wants_run_loop_preview(message) -> bool`: matches "推进计划", "推进 pln", "往前推", "驱动计划", "run-loop", "run loop", "推进这个计划" etc. Guard against colliding with existing intents (it requires an explicit run-loop/推进 phrasing plus, for routing, a plan id).
- `_chat_run_loop_preview_plan_id(message) -> str | None`: returns the `pln_...` match iff `_chat_wants_run_loop_preview` is true, else `None`. (A run-loop intent with no plan id is rejected with a clear error — unlike `run_progress`, run-loop is plan-scoped and must not guess a plan.)

Routing precedence: place the `run_loop_preview` branch so it does not shadow or get shadowed by `run_progress` (both mention plans). The run-loop detector requires the stronger "推进/run-loop/往前推/驱动" verbs; "进度/progress" stays with `run_progress`.

### 2. `_run_loop_preview_card(store, config, plan_id)` — read-only card

Validates the plan exists (`store.plan_status(plan_id)`; unknown → error, no chat turn/plan). Fields:

- `mode` = `"run_loop_preview"`
- `plan_id`
- `command` = `f"agentdeck run-loop --plan-id {plan_id} --confirm"`
- `autonomous_enabled` = `config.leader.approval_mode == "autonomous"`
- `safety` = `"delegated"`
- `requires_explicit_user` = `True`
- `blocker` = `None` if autonomous else `"autonomous mode is not enabled"`
- `enable_command` = `None` if autonomous else `"agentdeck policy set-mode --mode autonomous --confirm --allow-agent <id> --max-approvals <N>"`
- `controls[]`: one `kind=run_loop` control (command = the concrete `run-loop` command; `safety=delegated`; `enabled` = autonomous_enabled; `blocker` = the autonomous blocker when off) plus one `kind=inspect` control (`agentdeck run --plan-id <plan_id>`, read-only progress).

Register `LEADER_CHAT_RUN_LOOP_PREVIEW_CARD_FIELDS` and expose it via `agentdeck contract leader-chat` (add `run_loop_preview_card_fields`); add a stable example to the leader-chat example fixture.

### 3. Response assembly (mirror `run_progress`, with `role_assign`-style explicit next step)

The chat response includes the standard leader-chat envelope plus:

- Top-level `next_command` = the concrete `agentdeck run-loop --plan-id <plan_id> --confirm`.
- `leader_explanation`: `mode=run_loop_preview`, `action_kind=run_loop_preview`, `safety=explicit_runtime`, `requires_explicit_user=True`, `next_command` = the concrete command, `summary`/`reason` describing a read-only preview that hands back the explicit command.
- `intent_card`: `embedded_card=run_loop_preview_card`, `read_only=True`, `requires_explicit_user=True`, `next_command` = the concrete command; its `kind=next` control uses `safety=explicit_runtime` (NOT inspect, per the intent-card rule that a requires-explicit-user main button must not be inspect) and is `enabled` only when autonomous mode is on (else disabled with blocker "autonomous mode is not enabled"). Include a `kind=inspect` control pointing at `agentdeck run --plan-id <plan_id>`. `secondary_embedded_cards` includes `control_registry_card`.
- Embedded `control_registry_card` filtered to `scope=autonomous`, `selection` pointing at the **disabled** `run_loop` template control (the palette shows the `--plan-id <id>` template; the concrete command lives only in top-level `next_command` + the intent next control — exactly the `role_assign` pattern). Reuse the `scope=autonomous` group from the previous slice.

### 4. Validation (`validate_leader_chat_contract`)

Add `run_loop_preview` to the mode handling: require the `run_loop_preview_card` (validated against its field list), require `intent_card.embedded_card == "run_loop_preview_card"`, require top-level `next_command` == card `command`, and require the embedded `control_registry_card` (validated by `validate_control_registry_card_contract`) filtered to `scope=autonomous`. Keep the existing global intent-card rules (next-control safety not inspect when requires_explicit_user).

### 5. Docs sync (per the CLAUDE.md "新增 chat mode" rule)

- `docs/contracts/leader-chat-schema.md` + `agentdeck contract leader-chat` discovery (`run_loop_preview_card_fields`) + example fixture.
- `CLAUDE.md`: add the `mode=run_loop_preview` rule — read-only; embeds `run_loop_preview_card` + `scope=autonomous` `control_registry_card`; hands back explicit `agentdeck run-loop --plan-id <id> --confirm`; requires a plan id (no guessing); records only chat turn + audit; does NOT call a provider, read/write tmux, auto-approve, dispatch, or mutate approval/runtime/plan state.
- `README.md`, `HISTORY.md`, `docs/handoff/current-development-state.md`.

## Safety boundary (preserved)

- Read-only chat turn: no provider call, no tmux read/write, no auto-approve/dispatch, no approval/runtime/plan mutation. Only a `leader_chat_turn` + audit event.
- The recommended next step is an explicit-user command the human must run; when autonomous mode is off, the next control is disabled with a blocker and the card surfaces the enable command. A `control_id` is not authorization.
- Unknown/missing plan id → error, no chat turn, no plan — never guess a plan for a runtime-affecting suggestion.

## Testing

- "推进计划 pln_xxx" → `mode=run_loop_preview`; card `command` == `agentdeck run-loop --plan-id pln_xxx --confirm`; top-level `next_command` matches; `intent_card.embedded_card=run_loop_preview_card`; the intent next control has `safety=explicit_runtime` and `requires_explicit_user=True`.
- Autonomous off: card `blocker` == "autonomous mode is not enabled", `enable_command` set, intent next control disabled with that blocker. Autonomous on: `blocker=None`, intent next control enabled.
- Embedded `control_registry_card` is filtered to `scope=autonomous` and its `selection` points at the disabled `run_loop` template control.
- No-plan-id run-loop phrasing → non-zero error, no chat turn / plan created; unknown plan id → error, nothing written.
- Read-only proof: state (plans/approvals/messages/events except the chat turn + its audit event) unchanged; no provider/tmux interaction.
- `validate_leader_chat_contract` accepts the live response and the example; full suite green.

## Resolved decisions

- Read-only preview only; chat never executes run-loop (project invariant).
- Requires an explicit plan id; never guesses (unlike `run_progress`).
- Reuses the `scope=autonomous` control-registry group; follows the `role_assign` pattern (disabled template in the palette, concrete filled command in `next_command` + intent next control).
- `safety=explicit_runtime`, `requires_explicit_user=True`; next control disabled with a blocker when autonomous mode is off.
