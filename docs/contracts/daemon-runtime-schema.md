# Daemon Runtime Contract

`daemon-runtime/v1` is the compact, sanitized observation surface for the
one-per-project AgentDeck daemon. Discover it with
`agentdeck contract daemon-runtime` and inspect live state with
`agentdeck daemon status`.

The exact response fields are `schema_version`, `mode`, `state`, `health`,
`client_count`, `controller_present`, `idle_exit_pending`, `protocol_version`,
`compatibility`, `blockers`, and `controls`.

Status and logs controls are inspect-only. Start and stop remain explicit
runtime operations. `daemon status` never connects to the endpoint or writes
state; an active durable record is therefore rendered as last-known with
`health=unknown` and `compatibility=unverified`. The rendered stop command is
`agentdeck daemon stop --confirm`; its verified client obtains a temporary
controller through the sole lease-exempt `controller.acquire` bootstrap RPC
when no explicit `--lease-id` / `--lease-generation` pair is supplied. A
rendered control is never authority: `controller_present` is freshly derived
from the current unexpired lease, and the daemon revalidates the lease, endpoint
and durable identity, other clients, keepalive facts, and requested action. It
then commits and durably flushes the lease release before returning an accepted
response. Its server-owned stop event is set only after that response has been
drained to the requester. Raw PID, socket path, nonce, lease credentials,
credentials, and home-directory paths are excluded.

The daemon reloads its persisted keepalive view on every idle poll. Client-only
activity is `ready`; any non-client Mission, Worker, pending approval,
permission, reply, recovery/decision/ambiguity, outbox, recovery, safe-shutdown,
or atomic-write fact is `busy`. Idle grace begins only when the reason set is
empty. The server maintains a monotonic, process-local activity generation:
accepting a connection and decoding each protocol-valid request increments it
once; close does not increment it. Any observed generation change resets a full
grace window, including a short connection that starts and ends between idle
polls. Expired active leases are
committed once as terminal expiry transitions and synchronously flushed, so
ProjectView does not retain a stale controller indefinitely. When an automatic
temporary-controller stop is rejected, lease-gated `controller.release` must be
confirmed before the original blocker is returned; cleanup failure is itself a
blocker. Explicit caller credentials are not released by this cleanup path.
Offline ProjectView uses the same pure current-lease predicate without writing:
only a strictly parsed active-namespace lease with an aware future expiry sets
`controller_present=true`; expired, terminal, naive, and malformed facts are
false.

## Governed Worker authority

Daemon Worker mutations remain two-call preview/confirm operations and are
serialized by the project service owner. A confirmed ACP permission is only a
human policy decision: immediately before returning an ACP `allow_once`, the
daemon revalidates the exact Mission attempt, ACP session/turn, permission
binding, frozen project/action scope, permission policy, and runtime ownership.
An out-of-scope target, unsupported tool kind, human-owned Worker, stale turn,
or mismatched `acp` attempt / `acp-adapter` session is denied without granting
the effect. An allowed decision first atomically consumes the exact
permission/tool-call/effect tuple; only then may the daemon return ACP
`allow_once`. Exact replay returns `permission_consumed` without another write
or authorization, conflicting lineage fails closed, and failure before the
atomic commit grants nothing. The resulting gate decision is durably auditable.

`worker.takeover` records a bounded baseline for the exact controller
generation: ACP session/turn lineage, artifact lineage, and a content-hashed
project worktree manifest (excluding daemon-owned `.agentdeck/` and Git
metadata). `worker.return-control` additionally requires bounded
`reported_changes` (`summary` plus exact changed relative paths), unchanged
session/artifact lineage, a safe boundary, and an execution-time rescan matching
the preview. Missing reports, drift, escaping symlinks, changed authority, or a
replayed preview fail closed. Successful return consumes the active baseline;
the report and reconciliation remain durable audit facts.

The baseline cannot be created from empty or unverifiable runtime facts. An ACP
Worker must be present in project configuration and have one exact ready
`acp-adapter` AgentSession matching provider, project workspace, native session
identity and required capabilities, with no active turn. A tmux Worker must
have the exact running project binding and pass a read-only `pane_exists` probe
through the same project's configured socket and session. These facts are
revalidated for both preview and confirmation. Any projection, gate, or
confirmation failure keeps the baseline active and atomically records an exact
ambiguous reconciliation decision plus conversation/recovery audit facts.
ProjectView conversation blockers and scheduler facts surface that blocker;
only a later fully verified return resolves it.

Startup recovery is transport-derived. A tmux attempt with a durable submitted
receipt may remain observable and wait for its Worker. An ACP attempt in
`submitted` or `running` cannot preserve its live process connection across a
daemon restart, so it is classified `ambiguous` before permission handling.
That persisted recovery blocker is also consumed by the live scheduler; later
permission approval cannot reinterpret the disconnected attempt as resumable.
