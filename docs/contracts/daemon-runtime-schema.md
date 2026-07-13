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
