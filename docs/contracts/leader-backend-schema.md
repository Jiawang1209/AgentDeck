# Leader Backend Contract

`agentdeck contract leader-backend` discovers the M1 Leader backend contract; `--example` emits a validated deterministic example. The contract version is `leader-backend/v1`.

The response fields are `schema_version`, `contract_version`, `mode`, `backend_kind`, `identity`, `readiness`, `transport`, `capabilities`, `fallback`, `controls`, and `blockers`. `backend_kind` distinguishes API and Agent-CLI Leaders. Transport identity is explicit (`http`, `acp`, or `cli_subprocess`) and must remain stable for a turn.

`readiness=ready` cannot carry blockers. `fallback.automatic` must always be false: an ACP failure never silently retries a CLI or tmux path. Setup/use/assign controls remain previews or explicit-user actions and never install software, authenticate, read credentials, or grant execution permission.

For `transport=cli_subprocess`, readiness first requires the configured executable and then a bounded, read-only native-schema help probe. `codex-cli` must advertise both `--output-schema` and `--output-last-message`; `claude-cli` must advertise both `--json-schema` and `--output-format`. A ready CLI subprocess exposes capabilities `plan` and `native_json_schema`. A blocked CLI subprocess exposes only `plan` and one fixed blocker: native schema unsupported, executable unavailable, or native schema capability unavailable. The probe does not construct a provider, change project or global state, or retain help output.

`native_json_schema` is only a Leader generation capability. It does not establish Worker readiness, identify or inspect a tmux pane, grant tool or runtime permissions, approve execution, authorize dispatch, or enable any provider/transport fallback. Claude CLI generation uses one `--permission-mode plan` subprocess and accepts only the successful native JSON result envelope; AgentDeck still applies its own exact plan schema, frozen authority, approval, and runtime gates.
