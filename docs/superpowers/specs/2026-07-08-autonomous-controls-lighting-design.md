# Light Autonomous Commands into the Control Registry — Design

- **Date**: 2026-07-08
- **Status**: Approved (pending spec review)

## Context

The autonomous-mode goal is complete: `agentdeck approval auto --confirm` and `agentdeck run-loop --plan-id <id> --confirm` are fully functional and audited CLI commands. Both prior specs (sub-projects 2 and 3) deferred surfacing them into the read-only command palette (`control_registry` / `agentdeck controls`) so a future GUI/TUI can render them. This slice does exactly that surfacing — and nothing more.

The workbench `control_registry` is derived generically: `_workbench_control_registry` collects each card's `controls[]` via `_append_workbench_control_registry_items(scope=..., card=..., agent_id=..., controls=...)`, every item gets a deterministic `control_id` (`control_registry_item_id`, a sha1 fingerprint of scope/card/kind/agent_id/label/command), and `validate_workbench_contract` validates each item against `WORKBENCH_CONTROL_REGISTRY_ITEM_FIELDS`. The `agentdeck controls` filters (`--scope`/`--card`/`--query`/`--control-id`/`--enabled-only`), the `filters.active_filter_keys`, `selection`, and `groups[]` are all generic (grouped by scope/card). So a NEW `scope=autonomous` requires no new enum — only well-formed control items.

## Goal

Surface the two autonomous commands as read-only command-palette controls under a new `scope=autonomous`, so `agentdeck workbench`'s `control_registry[]` and `agentdeck controls` (incl. `--scope autonomous`) expose them for GUI/TUI rendering. Rendering is not authorization: a `control_id` is not a token, `enabled` only means "renderable as actionable," and execution still requires the human to run the explicit command with `--confirm`.

## Non-goals

- No natural-language `leader chat` intent for these commands (a separate, larger follow-up).
- No execution, no new authority, no auto-run. Pure read-only derivation: no provider calls, no tmux, no state writes.
- No new top-level card. We extend the existing `control_mode_card` rather than adding a whole card to the workbench payload.

## Design

### 1. `control_mode_card.autonomous_actions[]` (new field)

`_workbench_control_mode_card` gains a new field `autonomous_actions` — a list of exactly two controls derived deterministically from `approval_mode`:

- **approval_auto**: `_control(kind="approval_auto", label="Auto-approve (autonomous)", command="agentdeck approval auto --confirm", safety="delegated", enabled=<approval_mode == "autonomous">, blocker=None if autonomous else "autonomous mode is not enabled")`.
- **run_loop**: `_control(kind="run_loop", label="Run-loop (autonomous)", command="agentdeck run-loop --plan-id <id> --confirm", safety="delegated", enabled=False, blocker="requires --plan-id")` — always a disabled template because of the `<id>` placeholder (consistent with the project's other placeholder controls).

`approval_auto` is enabled iff the current `approval_mode == "autonomous"`; when autonomous mode is off it is disabled with the blocker above. Enabling it means "GUI may render an actionable button"; it does not execute — the human still runs the command.

Add `autonomous_actions` to `WORKBENCH_CONTROL_MODE_CARD_FIELDS` and to the `control_mode_card` branch of `validate_workbench_contract` (each item validated with the same control-field expectations as the existing `active_controls`).

### 2. Registry surfacing under `scope=autonomous`

In `_workbench_control_registry`, add one append call after the existing `policy`/`control_mode_card` append:

```python
_append_workbench_control_registry_items(
    registry,
    scope="autonomous",
    card="control_mode_card",
    agent_id=None,
    controls=control_mode_card.get("autonomous_actions"),
)
```

This flows the two controls into `control_registry[]` (workbench) and, generically, into `agentdeck controls` (filterable by `--scope autonomous`) and its `groups[]`. No changes to the filter/selection/group code are needed (all generic).

### 3. `agentdeck controls` and validators

- `agentdeck controls --scope autonomous` returns the two autonomous controls (generic scope filter — no code change).
- `validate_workbench_contract` already validates every `control_registry[]` item generically against `WORKBENCH_CONTROL_REGISTRY_ITEM_FIELDS`; the two new items inherit that. The only validator change is the `control_mode_card.autonomous_actions` field/item check in §1.
- `validate_control_registry_card_contract` (used by `agentdeck controls`) already validates items generically; no change.

### 4. Docs sync

- `docs/contracts/workbench-schema.md`: document `control_mode_card.autonomous_actions` and the `scope=autonomous` registry group.
- `docs/contracts/controls-schema.md`: note `scope=autonomous` as a discoverable group carrying `kind=approval_auto` / `kind=run_loop`.
- `CLAUDE.md`: extend the control-registry rule to state that `control_registry[]` / `agentdeck controls` must preserve a `scope=autonomous` group derived from `control_mode_card.autonomous_actions[]`, with `kind=approval_auto` (enabled only in autonomous mode, `safety=delegated`) and a disabled `kind=run_loop` template (`requires --plan-id`); rendering these is inspect-only and is not execution authorization. Also note `control_mode_card` now carries `autonomous_actions[]`.
- `README.md` + `HISTORY.md` + `docs/handoff/current-development-state.md`.

## Safety boundary (preserved)

- Read-only: rendering derivation only. No provider calls, no tmux reads/writes, no state mutation, no chat turns.
- A `control_id` / an enabled control is NOT authorization. Both commands still require the human to run them explicitly with `--confirm` (and `run-loop` additionally requires autonomous mode); the disabled `run_loop` template forces the human to supply `--plan-id`.
- The kernel-enforced gates from sub-projects 2 and 3 are unchanged; this slice only makes the commands discoverable.

## Testing

- `control_mode_card.autonomous_actions`: exactly two items; `approval_auto` enabled and blocker `None` when `approval_mode == "autonomous"`, disabled with `"autonomous mode is not enabled"` otherwise; `run_loop` always disabled with `"requires --plan-id"` and command `agentdeck run-loop --plan-id <id> --confirm`. Assert via `agentdeck workbench` in both modes.
- `agentdeck workbench` `control_registry[]` contains two `scope=autonomous` items with the right kinds, commands, safety, and deterministic `control_id`s; `validate_workbench_contract` passes.
- `agentdeck controls --scope autonomous` returns exactly the two items; `--enabled-only` in autonomous mode returns only `approval_auto`.
- Full suite stays green; `agentdeck contract workbench --example` / `agentdeck contract controls --example` still valid.

## Resolved decisions

- Extend `control_mode_card` with `autonomous_actions[]` rather than adding a new workbench card (lighter; semantically these are the autonomous policy's actions).
- New `scope=autonomous` registry group (generic derivation — no enum/allowlist changes).
- `approval_auto` is enabled when autonomous mode is on (renderable actionable button, still not auto-executed); `run_loop` is always a disabled placeholder template.
- Natural-language `leader chat` intent for these commands remains a separate follow-up.
