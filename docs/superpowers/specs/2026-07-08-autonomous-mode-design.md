# Autonomous Mode + `approval auto` — Design (Sub-project 2)

- **Date**: 2026-07-08
- **Status**: Approved (pending spec review)

## Context

Sub-project 2 of 3 in the autonomous-mode goal. Sub-project 1 (the audit/HISTORY gate, `agentdeck history`) is done. The user-chosen shape for this sub-project:

- **Scope (C):** a policy layer plus a new explicit command `agentdeck approval auto --confirm` that auto-approves allowlisted pending approvals and dispatches them, stopping at dispatch (no capture/review/release — that is sub-project 3).
- **Allowlist model:** an agent allowlist plus a max-count budget (both grounded in real fields — `agent_id` and a count — because the plan-step `risk` field is free text, not an enum).
- **Storage:** the policy (allowlist + budget) is set once when turning on the mode and stored in `.agentdeck/config.toml`; `approval auto` reads the stored policy.

`autonomous` already exists in the codebase as a **deliberately-rejected placeholder** in the `ask`/`approve`/`autonomous` gradient. This sub-project turns it into a real, bounded, fully-audited delegation mode. Because "accept autonomous" contradicts the current "autonomous is rejected" card/test/docs, those coupled surfaces update together (see "Coupled changes").

## Goal

A human pre-authorizes autonomous mode once with a defined scope (which agents' work may be auto-approved, and a max count), then `agentdeck approval auto --confirm` auto-approves and dispatches the allowlisted pending approvals within that scope — every action recorded in the audit ledger (and therefore in `agentdeck history`). It stops at dispatch.

## Non-goals

- No plan → capture → review → release loop (sub-project 3).
- No risk-level gating (free-text `risk` makes it unreliable; agent allowlist + count is the backstop).
- No force-spawning: `approval auto` dispatches only to agents with a running pane (mirrors `dispatch-ready --confirm`); others stay approved-but-blocked.
- No exhaustive contract/validator gold-plating beyond what consistency requires (see "Coupled changes"); a fuller GUI-lighting pass can be a follow-up.

## Design

### 1. Config schema (`.agentdeck/config.toml`)

- `[leader] approval_mode = "autonomous"` — a third value alongside `confirm` (ask) and `approve`.
- New `[autonomous]` section:
  - `allowed_agents = ["planner", "coder"]` — agent ids whose pending approvals may be auto-approved.
  - `max_approvals = 5` — max auto-approvals per `approval auto` run.
- `load_config` parses these into the config object as `config.autonomous` (a small dataclass `AutonomousPolicy(allowed_agents: tuple[str, ...], max_approvals: int)`), defaulting to `allowed_agents=()`, `max_approvals=0` when the section is absent.
- Writer helper `update_autonomous_policy(root, allowed_agents, max_approvals)` writes the `[autonomous]` section (mirrors `update_leader_approval_mode`). `_control_mode_from_approval_mode` gains an `autonomous` branch.

### 2. `policy set-mode --mode autonomous --confirm --allow-agent <id> [--allow-agent <id> ...] --max-approvals <N>`

Replaces the current rejection of `--mode autonomous`.

- New args (only meaningful with `--mode autonomous`): `--allow-agent` (repeatable, `action="append"`), `--max-approvals` (int), `--confirm` (flag).
- Validation (reject, write nothing, on failure):
  - `--mode autonomous` without `--confirm` → reject, append `policy_mode_rejected` (reason "autonomous requires --confirm").
  - No `--allow-agent`, or `--max-approvals` missing / `< 1` → reject (reason "autonomous requires --allow-agent and --max-approvals >= 1").
  - Any `--allow-agent` that is not a configured worker `agent_id` → reject (reason "unknown agent: <id>").
- On success: write `[leader] approval_mode = "autonomous"` and the `[autonomous]` section (allowed_agents, max_approvals); append `policy_mode_updated` (payload: mode=autonomous, allowed_agents, max_approvals). Print the resulting policy JSON.
- `--mode ask|approve` unchanged (autonomous args ignored / not required).

### 3. Pure selection function (new module `src/agentdeck/autonomy.py`, testable, reused by sub-project 3)

`select_auto_approvals(pending, allowed_agents, max_approvals) -> tuple[list[dict], list[dict]]`

- `pending`: pending approval dicts (each has `approval_id`, `agent_id`, ...).
- Returns `(selected, skipped)`:
  - `selected`: pending approvals whose `agent_id` is in `allowed_agents`, in ledger order, capped at `max_approvals`.
  - `skipped`: the rest, each annotated with `reason` = `"agent not in allowlist"` (agent not allowed) or `"budget exhausted"` (allowed but past the cap).
- Pure and deterministic; no I/O.

### 4. `agentdeck approval auto --confirm`

- `--confirm` required (else reject, write nothing, print "approval auto requires --confirm").
- Requires the current mode to be autonomous (`config.leader.approval_mode == "autonomous"`); else reject: "autonomous mode is not enabled; run agentdeck policy set-mode --mode autonomous --confirm --allow-agent <id> --max-approvals <N>". No writes on reject.
- Operates on `pending` approvals only (status `pending`). It does not re-approve or touch already-`approved` approvals — those remain the domain of `approval dispatch-ready --confirm`. If there are no allowlisted pending approvals, it is not an error: it returns a summary with zero counts.
- Reads the stored `config.autonomous` policy. Loads pending approvals from state. Runs `select_auto_approvals(pending, policy.allowed_agents, policy.max_approvals)`.
- For each `selected` approval:
  1. Auto-approve it: mark `status="approved"`, append `approval_decided` (payload includes `status="approved"`, `approval_id`, and `source="autonomous"` so the ledger distinguishes auto from human approvals).
  2. Dispatch it **only if** the target agent has a running pane — reuse the exact dispatch path used by `approval dispatch-ready --confirm` (`approval_dispatch_ready_command` internals): create message/attempt/job, send to the tmux pane, mark dispatched, append `approval_dispatched`. Agents without a running pane stay `approved` with a `blocker` ("agent is not running: <id>"), reported but not force-spawned.
- Append one summary event `approval_auto_completed` (payload: auto_approved, dispatched, blocked, skipped counts).
- Return JSON: `mode="approval_auto"`, `requires_explicit_user=true`, `safety="delegated"`, `auto_approved`, `dispatched` (with `trace_command`s), `blocked` (with reasons), `skipped` (with reasons), and the policy used.
- Fully audited: every auto-approve and dispatch is its own event; the summary event feeds `agentdeck history`.

### 5. control_mode_card + minimal contract consistency

Because `policy set-mode --mode autonomous` now succeeds, the following must stay consistent:

- `_workbench_control_mode_card`: the `autonomous` option becomes `enabled=True` (when not the current mode) with `safety="delegated"`; its `set_mode` control is a **disabled template** with command `agentdeck policy set-mode --mode autonomous --confirm --allow-agent <id> --max-approvals <N>` and blocker "requires --allow-agent and --max-approvals" (consistent with the assign-role template pattern). When autonomous IS the current mode, the option is disabled with blocker "already current mode".
- Natural-language policy mode ("开启 autonomous" / "完全放权" / "自主模式"): now suggests the explicit templated `set-mode --mode autonomous --confirm --allow-agent <id> --max-approvals <N>` command as the next step (still `requires_explicit_user`, still not executed), instead of implying it is unimplemented.
- `agentdeck approval auto --confirm` is exposed as a control under the operator/approval registry scope so GUI/TUI can render it (disabled unless autonomous mode is on; `safety=delegated`, `requires_explicit_user=true`).

Explicitly out of scope for this spec (a later GUI-lighting pass): exhaustive validator rules for the new control kinds and every natural-language variant beyond the above.

### 6. `agentdeck history` tie-in (sub-project 1)

Add humanize entries in `src/agentdeck/history.py::_MILESTONES` for the new/auto events:

- `approval_auto_completed` → "Auto-approve run · N approved, M dispatched".
- `approval_decided` with `source=="autonomous"` renders "Approval auto-approved · <approval_id>" (distinguish from human "Approval approved · <id>").

## Coupled changes (because autonomous is no longer rejected)

- Update `test_policy_set_mode_rejects_autonomous_without_mutating_config` → now `test_policy_set_mode_enables_autonomous_with_confirm_and_allowlist` (accepts with valid args; still rejects without `--confirm` / without allowlist / unknown agent).
- Update the control-mode-card assertions that expect `blocker="autonomous execution policy is not implemented"` (test_agent_cli.py:~7395/7429) to the new enabled/template shape.
- Update `test_leader_chat_suggests_autonomous_policy_command_but_keeps_it_blocked` (test_leader_cli.py:4138) to reflect that autonomous is now suggestable (templated) rather than blocked-as-unimplemented.
- Update CLAUDE.md rules that state `policy set-mode --mode autonomous` must be rejected and that `control_mode_card` autonomous is a disabled/unimplemented placeholder — to describe the new bounded autonomous mode.
- Update README + `HISTORY.md` + `docs/handoff/current-development-state.md`.

## Safety boundary (preserved)

- Turning on autonomous is an explicit `--confirm`'d human action that must name a concrete scope (allowlist + positive budget); an empty/invalid scope is rejected.
- `approval auto` is an explicit `--confirm`'d human action; it only auto-approves within the pre-authorized allowlist, capped by the budget; it does not force-spawn (dispatch only to running panes); and it stops at dispatch.
- Every auto-approval and dispatch is an audit event → visible in `agentdeck history`. The program kernel (not any LLM) enforces the allowlist/budget/gates — consistent with the north-star "model does semantics, program does the loop."

## Testing

- Pure `select_auto_approvals`: allowlist filtering, budget cap (order preserved), skip reasons, empty allowlist → nothing selected.
- `policy set-mode --mode autonomous`: requires `--confirm`; requires ≥1 valid `--allow-agent` + `--max-approvals >= 1`; unknown agent rejected; on success config `[leader] approval_mode` + `[autonomous]` written and `policy_mode_updated` appended; `ask`/`approve` still work.
- `approval auto`: requires `--confirm`; rejects when mode != autonomous (no writes); auto-approves allowlisted pending up to budget (using a fake tmux backend for dispatch); dispatches to running agents, leaves non-running as approved-blocked; skips non-allowlisted and budget-exceeded with reasons; appends the expected audit events; correct summary JSON.
- `control_mode_card`: autonomous option enabled with the template set-mode control (contract self-check still passes).
- `agentdeck history`: humanizes `approval_auto_completed` and autonomous `approval_decided`.
- Full suite stays green.

## Resolved decisions

- Scope C: new `approval auto --confirm` command (not changing `dispatch-ready` semantics, not a full loop).
- Allowlist = agent allowlist + max-count budget (no risk-level gating).
- Policy stored in a dedicated `[autonomous]` section in `.agentdeck/config.toml`, set at mode-switch time.
- One spec for the whole sub-project (config + set-mode + select fn + approval auto + minimal contract consistency + history tie-in), because accepting autonomous is coupled to the card/test/docs that currently reject it.
