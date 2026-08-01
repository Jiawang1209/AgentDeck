# Run-loop background host contract

Discovery entrypoint:

```bash
agentdeck contract run-loop-host
agentdeck contract run-loop-host --example
```

`agentdeck run-loop-host start|status|stop` hosts the **unchanged** single-wave
run-loop engine (`agentdeck contract run-loop`) in a detached background
process that survives client disconnect. Hosting is not authorization: every
wave-engine invariant is inherited unchanged (approval-gated, allowlist +
budget, running panes only, never force-spawn, never read a pane to infer
completion, file-channel replies only, step-ordering guard, delegated box
release with per-release audit).

Design spec: `docs/superpowers/specs/2026-07-30-run-loop-host-design.md`.

## Start response (`mode=run_loop_host_started`)

| Field | Meaning |
| --- | --- |
| `ok` | `true` on success |
| `mode` | fixed `run_loop_host_started` |
| `plan_id` | the single plan this host drives |
| `pid` | detached child pid |
| `max_waves` | mandatory bounded wave budget (`>= 1`) |
| `interval` | seconds between waves |
| `release_boxes` | whether delegation-covered authorization boxes are released between waves |
| `merge_on_complete` | whether task branches merge when the final gate is `complete` |
| `log_path` | project-relative append-only JSONL log |
| `status_command` | `agentdeck run-loop-host status` |
| `stop_command` | `agentdeck run-loop-host stop --confirm` |
| `requires_explicit_user` | always `true` |
| `safety` | always `delegated` |

### Start gates (all five required, refusal is zero-write / zero-spawn, exit non-0)

1. `--confirm` present.
2. `config.leader.approval_mode == "autonomous"` (same gate as `run-loop`).
3. Explicit `--max-waves >= 1` (no unbounded host).
4. `--interval >= 0` (2026-08-01). Zero is legal and means "no sleep between
   waves"; **negative is refused**. Every consumer guards the sleep with
   `if interval > 0`, so a negative value silently means "never sleep" — it
   would turn `--max-waves 300 --interval -5`, which a human reads as ~8 hours
   of walk-away, into 300 waves burned as fast as the machine allows. The wave
   budget is a safety bound, so the interval that paces it is one too. Shared
   with the other seven `--interval` entry points via one helper
   (`_reject_negative_interval`); `serve` carries the same gate.
5. `--plan-id` names a known plan.

A second `start` while a live host is running is refused (single instance per
project); a stale record (dead pid) does not block a new start.

## Status response (`mode=run_loop_host_status`, read-only)

| Field | Meaning |
| --- | --- |
| `ok` | `true` |
| `mode` | fixed `run_loop_host_status` |
| `running` | pid recorded and alive |
| `stale` | pid recorded but dead (needs `stop --confirm` cleanup) |
| `pid` | recorded pid or `null` |
| `plan_id` | hosted plan or `null` when no record |
| `wave_count` | completed waves |
| `max_waves` | wave budget |
| `interval` | seconds between waves |
| `last_gate` | last wave's run-loop gate (`stopped_reason` enum of the wave engine) |
| `last_wave_at` | timestamp of the last completed wave |
| `stopped_reason` | closed enum below, `null` while running |
| `log_path` | append-only JSONL log path |
| `start_command_template` | explicit start template |
| `stop_command` | explicit stop command |
| `human_gate` | box evidence object, or `null` — non-null only when `stopped_reason=human_gate` |

Record states: no record (never hosted / cleaned), running (`running=true`),
stale (`stale=true`), clean stop (`pid=null`, neither running nor stale).
`running` and `stale` are mutually exclusive. `status` never writes state,
never probes tmux, never touches the plan.

### `human_gate` evidence object

Field list single source: `run_loop_host.HUMAN_GATE_FIELDS` (imported by
`contracts.py`; discoverable as `human_gate_fields` in
`agentdeck contract run-loop-host`).

| Field | Meaning |
| --- | --- |
| `agent_id` | the awaited worker sitting behind the box |
| `box_kind` | `command` or `mcp_tool` (same taxonomy as `agentdeck agent boxes`) |
| `command` | the `$ `-line command the box is asking about, or `null` for an MCP box |
| `mcp_server` | MCP server name for an MCP tool box, else `null` |
| `mcp_tool` | MCP tool name for an MCP tool box, else `null` |
| `waiting_hint` | the on-screen prompt **marker line** as captured (e.g. `Press enter to confirm or esc to cancel`) — this is what `_detect_waiting_for_input` returns, not the option line |

### A gate is only decided on positive proof

Detection requires **four** conditions, and any failure of any one of them
fails open to ordinary polling — burning budget is always preferable to
truncating a healthy walk-away segment:

1. the scan classified the box as `no active delegation` (a `pane capture
   failed` skip is runtime flakiness, not a human gate);
2. the agent is in **this plan's** awaiting set (a box on an idle agent, or on
   an agent awaited by a different plan, never stops this host);
3. `box_pending` — the pane genuinely shows a **pending** box, proven by the
   active selector glyph `›1.` (whitespace-folded) inside the pending-box
   region, the same hard constraint `_MCP_TOOL_BOX_PATTERN` already enforces.
   This matters because `_detect_waiting_for_input`'s `Would you like to run`
   marker also fires on an **already-answered** collapsed box (`… ? -> Yes`),
   where nothing is waiting for anyone;
4. a parsed box identity (`box_kind` non-null). An unparsed box yields an
   all-null identity, and an all-null identity always equals itself — so the
   two-sighting debounce would confirm on the first pair every time.

Conditions 3 and 4 were added after a final adversarial review (2026-08-01)
demonstrated both false-stop paths end to end. They are regression-nailed by
tests that drive real pane text through the **unmocked** scanner.

`human_gate` is **provenance, not authorization**. It only tells a human where
to look; AgentDeck never presses the box — a human does, in that pane. The
validator refuses a payload whose `stopped_reason` is `human_gate` while
`human_gate` is `null`, and refuses an object missing any of the six fields, so
a half-broken status payload never prints.

## Stop response

`stop --confirm` sends SIGTERM once; the child finishes the **current wave**
before exiting (a worker is never cut mid-wave). The bounded wait never
escalates to SIGKILL.

Stop `mode` enum:

| Mode | Meaning | Exit | Follow-up |
| --- | --- | --- | --- |
| `run_loop_host_stopped` | child exited after SIGTERM | 0 | `agentdeck run-loop-host status` |
| `run_loop_host_stop_timed_out` | child still alive after bounded wait; record kept for manual handling | 1 | `agentdeck run-loop-host status` |
| `run_loop_host_stale_cleared` | record pointed at a dead pid; pid cleared, no signal sent | 0 | `agentdeck run-loop-host status` |

| Field | Meaning |
| --- | --- |
| `ok` | `false` only for the timeout mode |
| `mode` | one of the enum above |
| `plan_id` | hosted plan |
| `pid` | signalled pid (or the stale pid that was cleared) |
| `wave_count` | waves completed when stopped |
| `stopped_reason` | closed enum below or `null` on timeout |
| `next_command` | explicit follow-up |

## Closed `stopped_reason` enum

| Reason | Meaning | Follow-up |
| --- | --- | --- |
| `gate_reached` | wave gate is no longer `waiting_for_reply` | the gate's own `next_command` (see log) |
| `budget_exhausted` | `--max-waves` reached while still waiting | new explicit `start` with a fresh budget |
| `policy_revoked` | `approval_mode` left `autonomous` (remote brake — the child re-reads config each wave) | `agentdeck policy set-mode …` then explicit restart |
| `signalled` | `stop --confirm` SIGTERM accepted after the current wave | `agentdeck run-loop-host status` |
| `engine_error` | wave engine raised; only the exception type is logged, never provider output | inspect `host.log`, `agentdeck events` |
| `human_gate` | the awaited worker is sitting behind an **undelegated** authorization box; the reply will never arrive on its own | go to that pane, read the box, press it yourself, then explicit restart |

### `human_gate` detection

A live run burned 834 of 846 waves (98%, 3h37m) polling a plan whose awaited
worker sat behind a Playwright authorization box that no delegation covered.
`waiting_for_reply` was honest but conflated two states: *the worker is
thinking* (polling is right) and *the worker is behind a box nobody will press*
(polling is forever). `human_gate` splits the second one out.

- **Detection only runs with `--release-boxes`.** It reuses the `skipped[]`
  that `_scan_release_delegated_boxes` already returns from the scan the host
  performs at segment start (wave 0) and in each wave gap. A host started
  **without** `--release-boxes` reads no pane at all — that invariant is
  unchanged byte for byte, and no new pane-reading surface is added.
- A skip counts as a candidate only when its `reason` is
  `no active delegation` (a `pane capture failed` skip is runtime jitter, not a
  human gate) **and** its `agent_id` is in this plan's awaiting set
  (dispatched-but-unreplied approvals — the same single-source set the
  file-channel ingestion uses). Boxes on idle agents or other plans never stop
  this host.
- **Debounce: the same box must be seen on two consecutive scans.** Identity is
  `(agent_id, box_kind, command, mcp_server, mcp_tool)`; `waiting_hint` is
  display text and does not participate. A box that appears and gets released
  in between therefore costs at most one extra wave. The candidate lives only
  in the serve process's memory, so a restarted host recounts from scratch.
- **Fail-open.** Any detection failure (nothing parsed, scan raised) simply
  does not decide — the host falls back to existing polling. Better one extra
  wave than falsely stopping a healthy walk-away segment.
- Stopping here **never widens authorization**: the release path (exact
  delegation prefix / MCP two-sided equality) is untouched, no tmux input is
  ever sent, and no plan / approval / runtime state is written — only the host
  record, the host log and one `run_loop_host_stopped` audit event. A
  `human_gate` stop is not a `complete` gate, so `--merge-on-complete` never
  fires from it.

**Walk-away-chain exception (2026-07-31).** `gate_reached` is only recorded when the just-finished wave's gate is not `waiting_for_reply` **and** that wave did not itself append a review-iteration round. If the wave's `review_iterations[]` carries an item with `round` (see `docs/contracts/run-loop-schema.md`), the serve loop keeps going for one more wave — bounded by `--max-waves` as always, same as `budget_exhausted` — instead of stopping at that wave's honestly-reported non-waiting gate (typically `needs_human_approval`, since the newly appended rework/re-review approvals start `pending`). This lets the next wave's normal auto-approve + dispatch pick up the appended rework itself, matching the frozen spec chain (fail → append → next wave approves+dispatches rework → … → `complete` → merge). Gate honesty is unchanged: each wave's own logged `stopped_reason` in `host.log` is exactly what that wave produced; only the serve loop's continue-vs-stop decision differs, and a round-appending wave still counts against the `--max-waves` budget like any other.

## Host record and log

- Single-instance record `.agentdeck/run-loop-host/host.json` (atomic replace;
  corrupt/missing reads as "no host").
- Append-only JSONL log `.agentdeck/run-loop-host/host.log` shared across
  hosts; history is never truncated or rewritten.
- Audit events `run_loop_host_started` / `run_loop_host_stopped` land in the
  project event journal.
- A confirmed human gate appears in three consistent places: the record's
  `human_gate` object, a `{"event": "human_gate", …}` log line carrying the same
  fields plus the wave number, and the `run_loop_host_stopped` audit event's
  `human_gate`. `status` renders the record's copy.

## Boundaries

- Hosting is not authorization. The host only sequences waves of the already
  sanctioned engine; it never widens permissions, never force-spawns, never
  captures panes, and never infers worker completion.
- This host is deliberately separate from the M2 Mission daemon
  (`agentdeck daemon …`, `src/agentdeck/daemon/`), which is untouched.
- Non-goals: multi-plan host, unbounded host, restart-on-crash supervision,
  remote host.
