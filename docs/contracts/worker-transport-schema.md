# Worker Transport Contract

`agentdeck contract worker-transport` discovers the M1 Worker transport and ownership contract; `--example` emits a validated deterministic example. The contract version is `worker-transport/v1`.

The response fields are `schema_version`, `contract_version`, `mode`, `agent_id`, `configured_transport`, `effective_transport`, `readiness`, `capabilities`, `fallback`, `live_mirror`, `ownership`, `controls`, and `blockers`. The effective transport cannot silently differ from configured transport. A fallback is only an explicit reroute affordance with confirmation and blocker facts.

The tmux live mirror must remain read-only. Takeover and return-control require explicit-user safety, current readiness, no active turn or permission, no executing workflow step, and exact ownership/binding validation. Pending or human ownership blocks AgentDeck prompts. Controls communicate affordances; they do not authorize transport changes or bypass runtime/approval gates.
