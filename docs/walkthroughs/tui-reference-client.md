# TUI Reference Client (`agentdeck dashboard`)

`agentdeck dashboard` is a **read-only reference client** for the AgentDeck workbench contract. Its only job is to prove a point: the read-only `agentdeck workbench` contract is sufficient to drive a GUI/TUI. Everything it shows is derived from the workbench snapshot payload alone — it reads no private state, calls no provider, and never writes anything.

## What it is

- A CLI command `agentdeck dashboard` that builds the exact same payload as `agentdeck workbench` (same `_workbench_snapshot_payload` + `validate_workbench_contract()` gate) and renders it as human-readable text instead of JSON.
- A pure rendering function `src/agentdeck/dashboard.py::render_workbench_dashboard(payload: dict) -> str`. It takes only the contract payload — as an external GUI client would fetch it — and returns text. It has no side effects.

## What it renders

Each section maps to a workbench card and echoes the explicit commands a human would run (it never invents commands):

| Section | Source card | Shows |
| --- | --- | --- |
| Header | `project_view`, `next_command` | project, mode, schema, next explicit command |
| Recovery | `recovery` | recovery status/reason + recommended command |
| Run progress | `run_progress_card` | latest plan's steps/approval statuses + single explicit next command (omitted when no plan) |
| Role topology | `role_topology_card` | logical + worker roles, status, blocker, per-role inspect command, `N roles, M blocked` |
| Worker activity | `worker_lifecycle_card` | per-worker lifecycle stage + active message/job/reply ids + inbox/artifact counts |
| Review gate | `review_gate_card` | gate status/reason + code/round review stages |
| Release | `release_preview_card` | release status; `agentdeck release --confirm` when ready; released rounds |
| Ledger | `ledger_card` | message/job/reply/artifact/inbox counts |
| Queue | `queue_card` | active queue source + next command |
| Learning layer | `skill_suggestions_card`, `memory_suggestions_card` | pending skill/memory suggestion queues + explicit memory apply command |
| Command palette | `control_registry[]` | per-scope total / enabled / blocked counts, with `agentdeck controls --scope <scope>` drill-down |

`agentdeck dashboard --watch [--interval <seconds>] [--iterations <n>]` re-renders the same text on an interval (mirrors `agentdeck workbench --watch`), still read-only.

## Sample

Running it on a freshly initialized project (default `planner` / `coder` / `reviewer` agents, DeepSeek Leader not yet configured):

```text
AgentDeck — my-project  [mode: workbench]
schema: project-view/v1
Next: agentdeck doctor

── Recovery ────────────────────────────────────────
status: provider_setup_required — configured Leader provider is not ready: deepseek
  → agentdeck doctor

── Role topology ───────────────────────────────────
6 roles, 1 blocked
  logical frontdesk        ready                local-rule   → agentdeck leader chat-history
  logical planner          idle                 deepseek     → agentdeck plan list
  logical orchestrator     idle                 deepseek     → agentdeck leader actions
  worker  planning         idle                 codex        → agentdeck inbox --agent planner
  worker  implementation   idle                 codex        → agentdeck inbox --agent coder
  worker  review           blocked              claude       → agentdeck inbox --agent reviewer
      ⨯ blocked: waiting for artifacts

── Worker activity ─────────────────────────────────
  planner        idle
  coder          idle
  reviewer       idle

── Review gate ─────────────────────────────────────
status: blocked — waiting for artifacts
  code_review   reviewer     waiting_for_artifacts  (waiting for artifacts)
  round_review  —            missing_reviewer  (round_reviewer is not configured)

── Release ─────────────────────────────────────────
status: blocked — waiting for artifacts

── Ledger ──────────────────────────────────────────
  messages 0  jobs 0  replies 0  artifacts 0  inbox 0

── Queue ───────────────────────────────────────────
active source: provider_health
  next: agentdeck doctor

── Learning layer ──────────────────────────────────
skill suggestions: 0 pending  (agentdeck skills suggestions)
memory suggestions: 0 pending  (agentdeck memory suggestions)

── Command palette ─────────────────────────────────
105 controls  (drill down: agentdeck controls --scope <scope>)
  leader               7 controls   5 enabled   2 blocked
  ...
  role_topology        7 controls   7 enabled   0 blocked
```

## Why it matters

The whole layered-role design (Phases G1–G6) exposes state only through read-only contracts, with every mutation behind an explicit human command. This reference client is the proof that discipline paid off: a GUI/TUI can render the full operator picture — roles, blockers, review gate, release readiness, and a command palette that preserves each control's `enabled` / `blocker` — **without** touching tmux, state files, or providers. A richer GUI would render the same contract; this text client just makes the sufficiency check runnable and testable today.

For the full command palette (every control with its `safety` / `enabled` / `blocker`), use `agentdeck controls` (optionally `--scope <scope>`). For the raw JSON snapshot, use `agentdeck workbench`. See `docs/walkthroughs/layered-role-round.md` for the end-to-end round these surfaces describe.
