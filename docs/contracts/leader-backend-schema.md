# Leader Backend Contract

`agentdeck contract leader-backend` discovers the M1 Leader backend contract; `--example` emits a validated deterministic example. The contract version is `leader-backend/v1`.

The response fields are `schema_version`, `contract_version`, `mode`, `backend_kind`, `identity`, `readiness`, `transport`, `capabilities`, `fallback`, `controls`, and `blockers`. `backend_kind` distinguishes API and Agent-CLI Leaders. Transport identity is explicit (`http`, `acp`, or `cli_subprocess`) and must remain stable for a turn.

`readiness=ready` cannot carry blockers. `fallback.automatic` must always be false: an ACP failure never silently retries a CLI or tmux path. Setup/use/assign controls remain previews or explicit-user actions and never install software, authenticate, read credentials, or grant execution permission.
