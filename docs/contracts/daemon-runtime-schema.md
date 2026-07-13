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
`health=unknown` and `compatibility=unverified`. Stop uses the exact explicit
form `agentdeck daemon stop --confirm --lease-id <lease_id> --lease-generation <generation>`.
The stop control remains disabled until a
client supplies those current controller-lease facts. A rendered control is
never authority: the daemon revalidates the lease, endpoint and durable
identity, other clients, keepalive facts, and requested action before returning
an accepted response. Its server-owned stop event is set only after that
response has been drained to the requester. Raw PID, socket path, nonce,
credentials, and home-directory paths are excluded.
