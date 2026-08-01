# AgentDeck

**A local-first, governable, protocol-native multi-agent workbench.**

AgentDeck turns a natural-language goal into a reviewable Mission, coordinates real Codex, Claude, and other Agents, and keeps execution visible, auditable, and recoverable.

> North star: Hermes-like conversation, ACP-native communication, CCB-style real multi-agent collaboration, and a stronger orchestration and governance kernel.

[中文](README.zh-CN.md)

## Start with a conversation

```bash
conda env create -f environment.yml
conda activate agentdeck
python -m pip install -e .
agentdeck
```

Running bare `agentdeck` in a terminal now opens the Phase 3 M1 foreground conversation. In an uninitialized directory it first shows an exact project-setup preview. In a project it can use the configured API-backed LLM or Agent CLI as Leader, turn an open request into a frozen Mission preview, and execute only after natural-language confirmation of that exact preview.

When a project has a background Mission recovery fact, bare `agentdeck` first
prints the validated ProjectView `mission_recovery` card and then enters the
normal conversation UI. A project with no Mission to recover remains quiet.
This reconnect rendering is deterministic and does not call an LLM, inspect
tmux, write state, or reconstruct a transcript. Semantic Missions expose only
their compact step hash, bound across the frozen step, attempt, and validated
result; legacy recovery cards keep their existing exact shape.

```text
You       › Let Codex implement this and Claude review it.
AgentDeck › Mission preview: 2 Workers, approval required.
You       › Confirm the current preview.
AgentDeck › Mission started. Use /status or open the workbench to inspect it.
```

`agentdeck leader chat --message "..."` remains available for scripts and debugging.

## What works today

- explicit API or Agent-CLI Leader identity and readiness;
- deterministic `/help`, `/status`, `/approvals`, `/trace`, setup, and exit intents without an LLM call;
- bounded foreground conversation context with compact, transcript-free conversation state;
- exact, expiring, consume-once preview confirmation;
- one authoritative on-demand project daemon that continues a confirmed Mission after the client disconnects;
- deterministic reconnect, crash reconciliation, and exact permission/ownership/safety pauses;
- Mission planning, approval, dispatch, inbox/reply/ack, trace, workflow, and recovery primitives;
- ACP Worker routing when configured and ready, with no silent transport fallback;
- visible read-only tmux mirrors, explicit reroute/takeover, and single-writer ownership;
- ProjectView and versioned GUI-ready contracts for conversation, Leader, and Worker transport facts;
- governed Skill and Memory provenance;
- optional G2 planner/orchestrator split: add `[leader.planner]` / `[leader.orchestrator]`
  sub-sections (each with optional `provider` / `model`, falling back to `[leader]`;
  a sub-section naming a different provider must give an explicit `model` —
  config loading fails closed instead of feeding another provider's model name
  to the target backend)
  and `leader plan` / `run --task` / natural-language plan requests run two reasoning
  stages — a planner macro brief with acceptance criteria, then an orchestrator step
  expansion — landing one plan whose record and ProjectView item carry
  `planner_backend`, `orchestrator_backend`, and the frozen `planner_brief` snapshot;
  explicit `--provider/--model` overrides and unconfigured projects keep the
  single-stage path byte-identical, and stage failures are audited as
  `leader_provider_failed` with `stage=planner|orchestrator`;
- G5 quantified review: a review worker may add one `verdict: <single-line JSON>`
  (`review-verdict/v1`: per-criterion `pass|fail|unknown`, `overall`, optional
  `score`) to its structured reply — valid verdicts land on the reply record and
  in ProjectView/trace, and `leader review` / `leader summary` / `run --plan-id`
  derive a read-only `verdict_summary` aligned with the plan's acceptance
  criteria (`unverified`/`extra` gaps included); invalid verdicts never block
  reply ingestion, replies without a verdict are byte-identical to before, and
  the only verdict-driven behavior is the auto-merge gate: `run-loop --follow
  --merge-on-complete` withholds the automatic merge when `overall` is not
  `pass` (reporting `plan_merge.mode=verdict_blocked` plus the explicit
  human `worktree merge-plan --confirm` override, which is never gated);
  review-step
  approval dispatches (a later step whose plan already has an earlier task
  branch) additionally embed the plan's acceptance criteria and the verdict
  output format in the worker prompt — prompt context only, never authority;
- run-loop background host: `agentdeck run-loop-host start --plan-id <id>
  --confirm --max-waves <n> [--interval <s>] [--release-boxes]
  [--merge-on-complete]` runs the unchanged single-wave run-loop engine in a
  detached process that survives client disconnect (autonomous-mode gated,
  mandatory wave budget, single instance per project); `agentdeck
  run-loop-host status` is read-only; `agentdeck run-loop-host stop --confirm`
  sends SIGTERM, the child finishes its current wave, and there is no SIGKILL
  escalation — flipping `approval_mode` away from `autonomous` also stops the
  host at the next wave (`stopped_reason=policy_revoked`); with
  `--release-boxes`, if the worker the host is waiting on is sitting behind an
  **undelegated** authorization box — a reply that will never arrive on its own
  — the host stops with `stopped_reason=human_gate` and carries the on-screen
  box evidence (agent, box kind, command / MCP server+tool, waiting hint) into
  `host.json`, `host.log`, the `run_loop_host_stopped` audit event and
  `run-loop-host status`, instead of burning the whole wave budget polling (a
  live run spent 834 of 846 waves, 3h37m, doing exactly that); the evidence is
  **provenance, not authorization** — AgentDeck never presses the box, a human
  does — and detection reuses only the scan `--release-boxes` already performs,
  so a host started without that flag still reads no pane at all; discovery:
  `agentdeck contract run-loop-host`;
- review iteration loop: a review reply whose verdict `overall` is `fail` or
  `needs_changes` appends a deterministic rework + re-review step pair to the
  same plan (rework task = failed criteria + the reviewer's reply verbatim,
  truncated with a trace pointer — zero LLM calls), as ordinary PENDING
  approvals handled by the existing autonomous allowlist/budget and
  step-order guard; bounded by `[autonomous] max_review_rounds` (default 2,
  `--max-review-rounds` overrides, `0` disables byte-identically); idempotent
  per triggering reply and fail-closed on ambiguity; `run-loop --follow` /
  `run-loop-host` continue past the append wave so the whole
  fail → rework → re-review → pass → merge chain runs inside one bounded
  invocation, while exhausted budgets fall back to the human gate with the
  merge withheld (`verdict_blocked`); explicit manual trigger:
  `agentdeck plan rework --plan-id <id> --confirm`, whose opt-in `--refine`
  flag has the configured Leader provider distill the review feedback into the
  rework task once (explicit-only — run-loop never refines, so it still never
  calls a provider — and any provider failure falls back to the deterministic
  template with `refined: false` + a closed `refine_skipped_reason`, exit 0);
  discovery: `agentdeck contract plan-rework`;
- review groups + round reviewer: the optional `[review]` config section turns
  one review stage into an ordered group of reviewers — `reviewers = ["reviewer",
  "planner"]` deterministically expands every review step into N consecutive
  serial steps at plan generation (no parallel dispatch, execution engine
  unchanged), aggregated **any-fail-blocks** and only judged once the whole
  group has replied, so a partial group never burns an extra iteration round;
  `round_reviewer = "planner"` swaps who performs the re-review an iteration
  round appends (appended re-review groups are themselves group-aware). The
  resulting `verdict_summary.group` (`size`/`complete`/`rule`/`members[]`) and
  the `review_group`/`review_group_member` markers on `plan status` steps are
  read-only provenance — never authorization. When a group member's reply
  carries no parseable verdict the group reports `complete: false`, keeping a
  sibling's `fail` visible and the automatic merge withheld while never
  triggering a partial-group rework round (the explicit human
  `worktree merge-plan --confirm` is never gated). With no `[review]` section
  configured every path is byte-identical to before, except that
  `verdict_summary` always carries the additive `group` projection
  (`size: 1` for a lone reviewer).
- G6 role bindings: `agentdeck roles` (and the identical workbench `roles_card`,
  `mode = role_bindings`)
  is a read-only map of the six north-star role layers — frontdesk, planner,
  orchestrator, coder, code_reviewer, round_reviewer — showing what each layer
  is bound to, with which provider/model, its lifecycle, its live runtime status
  and what is missing. The six roles are deliberately **not** flattened into one
  agent table: a closed `binding_kind` (`command` / `logical_leader` /
  `worker_agent`) says what kind of thing each layer is and thereby why some
  fields are necessarily null (a logical Leader sub-role never has a pane; the
  intake command never has a provider). Bindings are derived from existing
  authority only — `resolved_planner_backend` / `resolved_orchestrator_backend`
  (with the `[leader]` fallback), `leader_backend_identity`, `[review]`, and the
  ProjectView `agents[]` runtime projection — so no tmux is read and no second
  state source appears. `binding_status` is the closed triple
  `bound` / `unbound` / `ambiguous`: when two agents could both be the coder the
  card reports `ambiguous`, lists **every** candidate and never silently picks
  one. A configured `[review] reviewers` group is a different thing from
  ambiguity: the layer binds its head and reports the **whole ordered group**
  in the additive `group_members` (`["reviewer", "planner"]` in configured —
  i.e. serial dispatch — order) while `candidates` stays empty, so a GUI never
  draws a lone reviewer for a project that really runs a two-person review
  group. The topology is an observation surface, not an authorization — it
  changes no gate and authorizes no dispatch; discovery:
  `agentdeck contract role-bindings`. It is a different card from the older
  workbench `role_topology_card` (`mode = role_topology`), which reports what
  each coordination role / worker is doing **right now**; both survive and the
  two `mode` values keep them apart.
- one-shot walk-away goal: `agentdeck goal preview --task <text>` →
  `agentdeck goal start --plan-id <id> --confirm` compresses the four commands
  and nine flags it took to climb to the top of the autonomy ladder into two
  steps and **one information-complete confirmation**. `preview` reuses the
  `leader plan` path to write a plan, then lays the whole pending authorization
  out at once — steps, budget (`300` waves by default, always printed with
  `↑ 缺省值,可用 --max-waves 改`, plus interval / review rounds / approval
  budget), active delegations, the merge policy, and the closed list of
  conditions that will stop and come back to you; `start` then **calls** the
  existing `approval approve-plan --confirm` and `run-loop-host start
  --confirm` implementations (never copies them), so `goal` adds no new kind of
  action and everything after it is the unchanged host wave engine. The
  principle is **compress the confirmations, not remove them**: `--confirm`,
  the mandatory bounded `--max-waves`, the autonomous allowlist, the approval
  and review-round budgets, the step-ordering guard, file-channel replies and
  `human_gate` stops are all inherited unchanged, and `goal start` has six
  gates of its own (`--confirm`, autonomous mode, a known `--plan-id`,
  `--max-waves >= 1`, **no live run-loop host** — a stale record does not
  block — and **the plan must have come from a `goal preview`**, enforced by
  provenance persisted on the plan record, so a `plan_id` from `leader plan`
  whose authorization screen was never shown is refused) whose refusal is
  zero-write and zero-spawn. The live-host gate is
  evaluated with the other four *before any mutation*, reusing the same
  liveness probe as `run-loop-host start`, so a refusal is never preceded by
  approving the plan. The single
  most important boundary: **`goal` never flips `approval_mode`** — a standing
  policy decision is not a per-goal one, so a non-autonomous project gets a
  blocker carrying the explicit `policy set-mode` command a human must run and
  a `null` confirm command. Defaults: `--release-boxes` on (an explicitly
  granted delegation would otherwise be pointless; unmatched boxes still stop
  at `human_gate`), `--merge-on-complete` off (merging into main deserves its
  own separate nod — the normal terminal state is "review passed, waiting for
  you to merge"). The preview says plainly that **this one confirmation
  approves all N steps up front**, prints the autonomous allowlist
  (`budget.allowed_agents`) next to the approval budget with the note that
  both bound only the *later* autonomous auto-approvals (rework rounds), and
  marks every step whose agent falls outside that allowlist
  (`steps[].in_allowlist`, rendered `← 白名单外`) — human approval has always
  been allowlist-blind, so the screen must show whose work is being authorized
  beyond the autonomous set rather than imply a bound that does not hold. Both
  commands render a human-readable summary by default and the full payload only
  with `--json`; discovery: `agentdeck contract goal`.

Useful observation commands:

```bash
agentdeck status
agentdeck workbench
agentdeck controls
agentdeck roles
agentdeck frontdesk --message "开始运行 冒烟测试"
agentdeck events --limit 20
agentdeck contract frontdesk --example
agentdeck contract conversation-runtime --example
agentdeck contract leader-backend --example
agentdeck contract worker-transport --example
agentdeck contract migration --example
agentdeck project migration-preview
```

## Safety boundary

Natural language is never execution authority. AgentDeck binds confirmation to exact execution facts, does not silently change ACP to tmux, and keeps permission, approval, runtime-safety, and ownership gates independent. Common inline credential assignments are redacted from durable Mission provenance.

For semantic Missions, AgentDeck is the control plane around LLM reasoning, not
a replacement for it. The user supplies required authority; the Leader may add
separately visible proposals; ambiguous facts remain unresolved; and only the
exact confirmed preview becomes frozen authority. AgentDeck then compiles the
Worker tasks deterministically and binds confirmation to the authority,
compiled-task, policy, and preview-generation facts. That single Mission
confirmation does not grant later ACP tool permissions or bypass runtime
safety, ownership, or approval gates.

ProjectView exposes only compact semantic provenance: schema/state, hashes,
counts, compiled-step count, and blockers. It does not expose full effects,
before/after literals, prompts, or secrets. This slice does not add A2A, remote
execution, a GUI redesign, or a terminal emulator.

Phase 3 M2 now runs daemon-admitted frozen Missions in one verified, on-demand
project daemon. Closing the interactive client does not revoke the frozen
authority or stop the scheduler. AgentDeck mediates every Worker transition,
records compact handoffs before starting the next Worker, and uses the exact
configured ACP or tmux transport without fallback. New permission, ambiguity,
ownership conflict, drift, or safety escalation pauses for an exact human
decision. Bare `agentdeck` reconnects from compact ProjectView facts without an
LLM or transcript reconstruction. Existing projects use read-only migration
preview followed by an expiring explicit confirmation; incomplete historical
Missions remain inspect-only.

M2 is project-local. A2A, remote daemons, global roaming, notifications,
Desktop/IDE Workspace Clients, full transcript recovery, automatic adapter
installation/login, Windows IPC, and a terminal emulator remain future work.

## Architecture

```text
Human / CLI / future TUI or Desktop
              |
      ConversationSession
              |
 Mission / Approval / Ledger / Recovery
              |
   Protocol-native Runtime Kernel
       /                 \
     ACP            tmux visible plane
       \                 /
   Codex / Claude / other Agents
```

ACP standardizes Agent communication; it does not replace AgentDeck's Mission, policy, scheduler, audit, or recovery layers.

## Documentation

- [Product north star](docs/roadmap/product-north-star.md)
- [Phase 3 M1 design](docs/superpowers/specs/2026-07-13-agentdeck-foreground-conversation-design.md)
- [Current development state](docs/handoff/current-development-state.md)
- [Contract index](docs/contracts/contract-index-schema.md)
- [Architecture](docs/architecture/)

Run verification in the project environment:

```bash
conda run --no-capture-output -n agentdeck pytest -q
conda run --no-capture-output -n agentdeck python -m compileall src tests -q
```
