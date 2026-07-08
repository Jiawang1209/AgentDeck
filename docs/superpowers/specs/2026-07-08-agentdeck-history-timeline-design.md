# AgentDeck History Timeline — Design (Sub-project 1)

- **Date**: 2026-07-08
- **Status**: Approved (pending spec review)
- **Approach**: A — derive-on-command (chosen over per-action append)

## Context

This is **sub-project 1 of 3** in a larger goal: give AgentDeck an autonomous ("full access") control mode where a human pre-authorizes once and the workflow runs a whole round automatically, bounded by an allowlist/budget, with everything recorded (the project's `autonomous` authorization tier, currently a deliberate placeholder).

The three sub-projects, in the user-chosen order:

1. **Audit / HISTORY gate (this spec)** — a deterministic, human-readable Markdown timeline derived from the existing `events.jsonl` ledger. It is the "audit gate" precondition that makes delegation acceptable, and is independently useful today.
2. **Autonomous policy switch + allowlist/budget** — implement `agentdeck policy set-mode --mode autonomous --confirm`, the allowlist/budget config, and light up `control_mode_card`.
3. **Executing round loop engine** — a command that actually chains `plan → approval → dispatch → capture → review → release`, honoring the policy (pause at gates in ask/approve; auto-advance within the allowlist in autonomous).

**This spec covers sub-project 1 only.** Sub-projects 2 and 3 are out of scope here; the executing loop (sub-project 3) will later call `agentdeck history --write` after each round so the file "grows" naturally.

## Goal

`agentdeck history` renders a human-readable, **regenerable** Markdown timeline of what AgentDeck did — one entry per meaningful milestone — from the `events.jsonl` audit ledger. No LLM. It gives the user the same "development history" record habit they use in this project, produced deterministically.

## Non-goals

- No LLM-curated prose changelog (that is a possible later layer on `leader summary` / `learn review`, explicitly deferred).
- No new state or second source of truth: `events.jsonl` remains authoritative; HISTORY.md is a pure projection.
- No new JSON discovery contract: `history` renders the existing events ledger (discoverable via `agentdeck contract events`), the same way `agentdeck dashboard` renders the workbench contract.
- No autonomous policy or executing loop (sub-projects 2 and 3).

## Design

### Module: `src/agentdeck/history.py`

Pure functions, no I/O (mirrors `dashboard.py`):

- `render_history_markdown(events: list[dict], project: str) -> str`
  - Input: events as stored (oldest → newest), plus the project name.
  - Output: Markdown, **newest-first**, grouped by date.
  - Title: `# AgentDeck History — <project>`.
  - Under each `## YYYY-MM-DD` (newest date first), one line per milestone event, newest-first within the day:
    `- HH:MM:SS · <humanized action> · <detail>` where `HH:MM:SS` comes from `created_at`.
  - Events not in the milestone set are skipped.
  - Deterministic: same events in → same Markdown out (safe to regenerate/overwrite).
- `_humanize_event(event: dict) -> str | None`
  - Maps `event_type` + `payload` to a `"<action> · <detail>"` string.
  - Returns `None` to skip (noise events).

### State store change

One small read-only addition to `StateStore`:

- `all_events() -> list[dict]` — reads and returns the full `events.jsonl` (oldest → newest), no cap. Needed because `list_events(limit<=0)` returns empty. Read-only; writes nothing.

### Milestone events (humanize allowlist)

Grounded in the event types the codebase currently emits:

| event_type | Humanized |
| --- | --- |
| `project_initialized` | Project initialized |
| `leader_plan_created` | Plan created · `<plan_id>` / task |
| `run_started` | Run started · `<task>` |
| `approvals_created_from_plan` | Approvals created from plan · `<plan_id>` |
| `approval_created_from_chat` | Approval created (from chat) |
| `approval_decided` | Approval `<status>` · `<approval_id>` |
| `approval_dispatched` | Approval dispatched · `<approval_id>` |
| `approval_dispatch_ready_completed` | Batch dispatch completed · `<count>` |
| `task_dispatched` | Task dispatched → `<agent_id>` |
| `task_replied` / `reply_captured` | Reply captured · `<agent_id>` |
| `round_released` | Round released · round `<round>` |
| `round_release_rejected` | Release rejected · `<reason>` |
| `policy_mode_updated` | Control mode → `<mode>` |
| `policy_mode_rejected` | Control mode change rejected · `<mode>` |
| `leader_provider_updated` | Leader provider → `<provider>/<model>` |
| `leader_provider_update_rejected` | Provider switch rejected |
| `leader_provider_failed` | Provider failed · `<detail>` |
| `agent_spawned` | Agent spawned · `<agent_id>` |
| `agent_spawn_ready_completed` | Agents spawned · `<spawned_count>` |
| `agent_stopped` | Agent stopped · `<agent_id>` |
| `agent_input_sent` | Input sent → `<agent_id>` |
| `agent_role_assigned` | Role assigned · `<agent_id>` → `<role>` |
| `agent_runtime_stale` | Runtime marked stale · `<agent_id>` |
| `inbox_item_acked` | Inbox acked · `<inbox_id>` |
| `leader_action_suggested` | Leader action suggested · `<kind>` |
| `leader_action_applied` | Leader action applied · `<kind>` |
| `skill_imported` / `skill_loaded` / `skill_suggested` / `skill_created` | Skill `<verb>` · `<name>` |
| `memory_suggested` / `memory_applied` | Memory `<verb>` |

**Skipped (noise):** `leader_chat_turn` (fires on every natural-language message), and any internal validation-failure events such as `leader_chat_contract_failed`. Unknown/unmapped event types are skipped (so new events never break rendering; they simply don't appear until mapped).

### Command: `agentdeck history [--limit N] [--write [PATH]]`

- Loads **all** events by default via a new read-only helper `StateStore.all_events()` (reads the full `events.jsonl`, no cap). `--limit N` restricts to the most recent N via the existing `store.list_events(N)`. (Note: `store.list_events(limit<=0)` returns empty, so "all" cannot go through it — hence the dedicated `all_events()` helper.)
- Renders via `render_history_markdown(events, project_name)`.
- **Without `--write`**: prints the Markdown to stdout. Fully read-only.
- **With `--write` (optional PATH)**: writes the rendered Markdown to the file. Default PATH = `.agentdeck/HISTORY.md`. Idempotent full overwrite (it is a complete projection). Prints the resolved path.
  - `.agentdeck/` is the gitignored state dir, so the file never collides with a human-maintained root `HISTORY.md` and is not accidentally committed. A custom `--write <path>` can target a committed location if the user wants.
- Registered as a top-level subparser next to `dashboard`.

### Read-only / safety boundary

- Never mutates `events.jsonl` or any other state.
- `--write` only writes the derived, regenerable HISTORY.md projection — consistent with the project's "read-only projection of the ledger" philosophy (like `agentdeck workbench` / `dashboard`). It does not call a provider, spawn/dispatch, read tmux panes, or approve anything.
- Because it is a pure projection, the future autonomous loop (sub-project 3) can call `history --write` after each round with no risk of drift.

## Testing

- Unit-test `render_history_markdown(events, project)` with a crafted events list:
  - newest-first ordering (across days and within a day);
  - date grouping (`## YYYY-MM-DD` headers);
  - a few humanized lines assert the mapped phrasing + a payload detail;
  - noise events (`leader_chat_turn`, unknown type) are absent;
  - deterministic: rendering the same input twice is identical.
- Command tests:
  - `agentdeck history` prints Markdown (contains the title and at least one milestone), state unchanged;
  - `agentdeck history --write` materializes `.agentdeck/HISTORY.md` with the same content and leaves `events.jsonl`/state unchanged.

## Docs

- README: a short paragraph on `agentdeck history` (+ `--write`) as a read-only ledger projection.
- `HISTORY.md` (this project's own dev changelog): one entry per the dev convention.
- `docs/handoff/current-development-state.md`: mark sub-project 1 done, point to sub-project 2 (autonomous policy switch + allowlist).

## Resolved decisions

- Approach A (derive-on-command), not per-action append.
- Default write path `.agentdeck/HISTORY.md` (gitignored; override via `--write <path>`).
- Deterministic timeline, no LLM.
- No new JSON contract (renders the existing events ledger).
