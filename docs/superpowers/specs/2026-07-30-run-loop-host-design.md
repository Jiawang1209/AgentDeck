# Run-Loop Background Host Design (2026-07-30)

Status: approved by user (2026-07-30). Three forks were decided explicitly:
**(1)** the background runner hosts the *existing* wave engine rather than
teaching the M2 Mission scheduler about plans; **(2)** safety is one gate at
start plus a mandatory bounded budget; **(3)** process management reuses only
pidfile + log + single-instance mutual exclusion — no socket, no lease.

## Problem

Round 12's walk-away loop needed eight manual relaunches of
`run-loop --follow`: each segment is bounded on purpose, and the foreground
process dies with the client. Box releases are now largely automated (MCP
delegation + match normalization), so "relaunch the segment" is the remaining
human point in an otherwise unattended loop.

## Why not the M2 daemon

`src/agentdeck/daemon/` (11 modules, ~9.9k lines) schedules **Missions**
(`mission_id`/`mission_state` state machine, ACP-first transports with tmux
as an alternative, its own permission state machine and controller lease).
The live-proven lane is the other one: `run-loop` drives `plans[]` +
`approvals[]` over tmux panes with file-channel reply ingest. The two are
fully decoupled today (nothing under `daemon/` references `run_loop` or
`autonomy`; `autonomy.py` never reads daemon state), and the daemon lane has
been dormant in live use since Phase 3 M2 (2026-07-13) with its own live gate
historically BLOCKED.

This mirrors the SQLite lesson recorded in the migration route spec: two
donor kernels died from building a parallel kernel; the winning move was
swapping the backend *inside* the proven module. By analogy the host runs the
already-proven wave engine unchanged and does not become a second scheduler.
The Mission lane stays untouched — not one line — and remains dormant.

## Naming boundary

New command surface: `agentdeck run-loop-host start|status|stop`, plus an
internal `agentdeck run-loop-host serve` that the start command spawns.
`agentdeck daemon …` (Mission lane) and `agentdeck loop once` keep byte-identical
semantics. The name deliberately avoids "daemon" so operators never confuse
the plan-lane host with the Mission daemon.

State lives under `.agentdeck/run-loop-host/`:
`host.json` (single-instance record) and `host.log` (JSONL, one line per wave).

## 1. Start (the only write path)

```
agentdeck run-loop-host start --plan-id <id> --confirm --max-waves <n> \
  [--interval <seconds>] [--release-boxes] [--merge-on-complete]
```

Gates, all required, each refusing with zero writes and no spawn:

- `--confirm`
- `config.leader.approval_mode == "autonomous"` (same gate class as
  `run-loop`/`approval auto`/`boxes watch`)
- `--max-waves >= 1` — **mandatory** with the host (the bounded budget
  decision); a host with no ceiling is not offered
- a known `--plan-id`
- single instance: if `host.json` records a live pid for this project, refuse
  and point at `run-loop-host status`

On success: write `host.json`
`{pid, plan_id, started_at, max_waves, interval, release_boxes,
merge_on_complete, log_path, wave_count: 0, last_gate: null,
last_wave_at: null, stopped_reason: null}`, append a
`run_loop_host_started` audit event, and print `mode=run_loop_host_started`
with pid, log_path, `status_command`, `stop_command`.

Spawn shape (reusing the pattern already proven by the daemon client):
`subprocess.Popen(argv, start_new_session=True,
stdin/stdout/stderr=DEVNULL, cwd=<project root>)` where argv is
`agentdeck run-loop-host serve --project <root> --plan-id <id> …`. The child
never inherits a terminal, so it survives client disconnect.

## 2. Host loop (engine unchanged)

The child repeats the **existing** single-wave `_run_loop` engine — the same
function `--follow` drives — on the given interval. Per wave it appends one
JSONL line to `host.log` (the wave payload plus a wave index) and updates
`host.json`'s `wave_count` / `last_gate` / `last_wave_at`.

Termination, same shape as `--follow`: stop when the gate is no longer
`waiting_for_reply`, or when `max_waves` is reached. On termination write
`stopped_reason`, append `run_loop_host_stopped`
(with `wave_count` and `stopped_reason`), and clear the live pid.

`stopped_reason` is a closed enum (`RUN_LOOP_HOST_STOPPED_REASONS`), each
value carrying an explicit follow-up command in the status payload:

- `gate_reached` — the wave gate is no longer `waiting_for_reply`; the gate's
  own `next_command` (from the last wave) is the follow-up.
- `budget_exhausted` — `max_waves` reached while still
  `waiting_for_reply`; the follow-up is starting a new host.
- `policy_revoked` — `approval_mode` is no longer `autonomous`.
- `signalled` — a SIGTERM from `run-loop-host stop` was honored after the
  current wave finished.
- `engine_error` — the wave engine raised; the exception type is logged (no
  provider output, no secrets) and the follow-up is `agentdeck run-loop
  --plan-id <id> --confirm` in the foreground for diagnosis.

`host.log` is a single append-only JSONL file per project, shared across
successive hosts; every line carries `plan_id`, `wave`, and a timestamp, and
each run is delimited by its own start/stop lines, so history from earlier
hosts is never truncated or rewritten.

**Config is re-read every wave**: if `approval_mode` is no longer
`autonomous`, the host stops with `stopped_reason=policy_revoked`. This is
the remote brake — a human can halt an unattended host by flipping the mode,
without finding the pid.

`--merge-on-complete` and `--release-boxes` behave exactly as in `--follow`,
including the segment-start box scan.

## 3. Read-only status

`agentdeck run-loop-host status` reads `host.json` plus a pid liveness probe
and prints `mode=run_loop_host_status` with `running`, `stale`, `pid`,
`plan_id`, `wave_count`, `max_waves`, `interval`, `last_gate`,
`last_wave_at`, `stopped_reason`, `log_path`, `stop_command`. It validates
before printing; it never writes state, never reads a tmux pane, never calls
a provider.

Three record states, all exit 0:

- **no record ever** (`host.json` absent): `running=false`, `stale=false`,
  every host field null, and `start_command_template` as the follow-up.
- **live pid**: `running=true`, `stale=false`, live counters, `stop_command`
  as the follow-up.
- **dead pid with a leftover record**: `running=false`, `stale=true`,
  the last known counters retained, and `stop` as the cleanup follow-up.
  A cleanly terminated host leaves `running=false`, `stale=false` and its
  final `stopped_reason` — a finished run is not "stale".

## 4. Stop

`agentdeck run-loop-host stop --confirm` sends SIGTERM. The child installs a
handler that finishes the **current** wave and then exits cleanly — a worker
is never cut off mid-dispatch — recording `stopped_reason=signalled`. The
command waits a bounded timeout, then clears the pid and appends
`run_loop_host_stopped` with `source=explicit`.

Stop's own response `mode` is distinct from the host's `stopped_reason`:
`run_loop_host_stopped` (the child exited within the timeout),
`run_loop_host_stop_timed_out` (SIGTERM sent, child still alive at the
deadline — the record is **left in place** for the human and the command
returns non-zero; it never escalates to SIGKILL), or
`run_loop_host_stale_cleared` (no live pid; the leftover record is cleared).
Missing `--confirm`, and a project with no record at all, refuse with zero
writes.

## 5. Safety invariants (all inherited, none relaxed)

The wave engine's semantics are byte-identical: approval-gated; allowlist and
shared budget; dispatch only to agents with an existing running pane (never
force-spawn); never read a pane to infer completion; the only accepted
completion signal remains the worker's structured file-channel reply; the
step-ordering guard holds; delegated box release still goes through
`_scan_release_delegated_boxes` (including the match normalization landed
earlier today). The host changes **which process executes the engine**, not
what the engine is allowed to do. Hosting is not authorization.

## 6. Contract and docs

New GUI-discoverable contract `run-loop-host`: response field tuples for the
three shapes, `validate_run_loop_host_*_contract()` validators, example
fixtures, registration in `CONTRACT_INDEX_SPECS` / `agentdeck contract list`,
and `docs/contracts/run-loop-host-schema.md`. CLAUDE.md gains a rule
paragraph stating the gates, the bounded budget, the policy-revoked brake,
the inherited invariants, and the explicit boundary against the Mission
daemon. README and HISTORY sync in the same commits.

## 7. Test surface (TDD)

1. Gate matrix — missing `--confirm`, non-autonomous mode, missing or `<1`
   `--max-waves`, unknown plan, second start while a live host exists: each
   refuses with zero writes, no `host.json` mutation, and no spawn (spawn is
   injected as a fake in tests; no real process is created).
2. Host loop with a fake engine: wave count advances, JSONL log lines are
   one-per-wave, `host.json` updates, termination on non-`waiting_for_reply`
   gate and on `max_waves`, `run_loop_host_stopped` payload correctness.
3. Policy brake: flipping `approval_mode` to `ask` between waves stops the
   host with `stopped_reason=policy_revoked`.
4. Status: read-only (state byte-identical before/after), correct fields,
   stale detection for a dead pid.
5. Stop: SIGTERM path finishes the current wave then exits; missing
   `--confirm` refuses; timeout path reports `stop_timed_out` and keeps the
   record; stale record cleared.
6. Contract: discovery payload exposes all three field sets; examples pass
   their validators; `contract list` includes the new entry.

## Non-goals (future forks needing a new decision)

Multi-plan hosts (`--all` in the background), unbounded hosts, restart-on-crash
supervision, remote/global hosts, and any convergence of the Mission lane with
the plan lane.
