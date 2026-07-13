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
rendered control is never authority: the daemon revalidates the lease, endpoint
and durable identity, other clients, keepalive facts, and requested action. It
then commits and durably flushes the lease release before returning an accepted
response. Its server-owned stop event is set only after that response has been
drained to the requester. Raw PID, socket path, nonce, lease credentials,
credentials, and home-directory paths are excluded.
