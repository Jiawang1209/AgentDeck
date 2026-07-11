# Protocol Runtime Contract

`agentdeck contract protocol-runtime [--example]` publishes the read-only discovery contract for the future `agentdeck protocol status` surface. The contract version is `protocol-runtime/v1`; its ProjectView compatibility version remains the current `schema_version`.

## Status response

The status response has exactly these fields, in order: `mode`, `contract_version`, `project`, `runtime_backend`, `agent_sessions`, `protocol_turns`, `transport_updates`, `permission_requests`, and `controls`. `mode` is `protocol_runtime_status`. `project` and `runtime_backend` are non-empty strings.

The four summaries reuse the strict ProjectView shapes and semantics:

- `agent_sessions`: `count`, `by_state`, `items`; each item exposes `session_id`, `agent_id`, `provider`, `transport`, `state`, `capabilities`, `native_session_present`, `workspace`, `created_at`, `updated_at`.
- `protocol_turns`: `count`, `by_state`, `items`; each item exposes `turn_id`, `session_id`, `message_id`, `state`, `created_at`, `updated_at`.
- `transport_updates`: `count`, `by_kind`, `items`; each item exposes only `update_id`, `session_id`, `turn_id`, `sequence`, `kind`, `created_at`. Update payload content is deliberately absent.
- `permission_requests`: `count`, `pending_count`, `by_status`, `items`; each item exposes only `permission_id`, `session_id`, `turn_id`, `tool_name`, `risk`, `status`, `decision`, `created_at`. Provider-native permission identifiers and execution targets are deliberately absent. Pending requests have `decision: null`.

Counts are non-negative integers (booleans are rejected), equal their grouped totals, and item distributions match when the total is at most 20. Items are the sorted latest 20 and have unique identities. Sessions and turns use the published state enums, updates use the published kind enum, and permissions use the published status enum. `transport` is an extensible non-empty string; structured capability flags describe supported behavior.

The summaries form a linked chain: every turn references a listed session; every update and permission references a listed turn and the same session.

## Controls

Controls have exactly `kind`, `label`, `command`, `safety`, `enabled`, and `blocker`. The response exposes exactly three enabled, unblocked inspect controls:

- `agentdeck protocol status`
- `agentdeck status`
- `agentdeck contract protocol-runtime`

These commands contain no placeholders and grant no execution authority.

## Discovery payload

Discovery exposes `schema_version`, `contract_version`, `status_command`, `contract_path`, `contract_exists`, response/summary/item/capability/control field lists, the session/turn/update/permission enums, `project_view_contract`, and `workbench_contract`. With `--example`, it additionally exposes `example_protocol_runtime` and same-source example field lists.

## Safety and current phase

Contract discovery and the modeled status surface are fully read-only: they do not call a provider, inspect or send input to tmux, write state, or decide a permission. In Phase 1, protocol records are produced primarily by contract tests and manual `StateStore` usage; tmux dispatch does not yet emit them automatically. This contract does not implement `agentdeck protocol status`.
