# Daemon Runtime Contract

`daemon-runtime/v1` is the compact, sanitized observation surface for the
one-per-project AgentDeck daemon. Discover it with
`agentdeck contract daemon-runtime` and inspect live state with
`agentdeck daemon status`.

The exact response fields are `schema_version`, `mode`, `state`, `health`,
`client_count`, `controller_present`, `idle_exit_pending`, `protocol_version`,
`compatibility`, `blockers`, and `controls`.

Status and logs controls are inspect-only. Start and stop remain explicit
runtime operations. A rendered control is never authority: endpoint identity,
protocol compatibility, controller/keepalive facts, and the requested action
are revalidated at execution time. Raw PID, socket path, nonce, credentials,
and home-directory paths are excluded.
