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

### Start gates (all four required, refusal is zero-write, exit non-0)

1. `--confirm` present.
2. `config.leader.approval_mode == "autonomous"` (same gate as `run-loop`).
3. Explicit `--max-waves >= 1` (no unbounded host).
4. `--plan-id` names a known plan.

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

Record states: no record (never hosted / cleaned), running (`running=true`),
stale (`stale=true`), clean stop (`pid=null`, neither running nor stale).
`running` and `stale` are mutually exclusive. `status` never writes state,
never probes tmux, never touches the plan.

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

**Walk-away-chain exception (2026-07-31).** `gate_reached` is only recorded when the just-finished wave's gate is not `waiting_for_reply` **and** that wave did not itself append a review-iteration round. If the wave's `review_iterations[]` carries an item with `round` (see `docs/contracts/run-loop-schema.md`), the serve loop keeps going for one more wave — bounded by `--max-waves` as always, same as `budget_exhausted` — instead of stopping at that wave's honestly-reported non-waiting gate (typically `needs_human_approval`, since the newly appended rework/re-review approvals start `pending`). This lets the next wave's normal auto-approve + dispatch pick up the appended rework itself, matching the frozen spec chain (fail → append → next wave approves+dispatches rework → … → `complete` → merge). Gate honesty is unchanged: each wave's own logged `stopped_reason` in `host.log` is exactly what that wave produced; only the serve loop's continue-vs-stop decision differs, and a round-appending wave still counts against the `--max-waves` budget like any other.

## Host record and log

- Single-instance record `.agentdeck/run-loop-host/host.json` (atomic replace;
  corrupt/missing reads as "no host").
- Append-only JSONL log `.agentdeck/run-loop-host/host.log` shared across
  hosts; history is never truncated or rewritten.
- Audit events `run_loop_host_started` / `run_loop_host_stopped` land in the
  project event journal.

## Boundaries

- Hosting is not authorization. The host only sequences waves of the already
  sanctioned engine; it never widens permissions, never force-spawns, never
  captures panes, and never infers worker completion.
- This host is deliberately separate from the M2 Mission daemon
  (`agentdeck daemon …`, `src/agentdeck/daemon/`), which is untouched.
- Non-goals: multi-plan host, unbounded host, restart-on-crash supervision,
  remote host.
