# Conversation Runtime Contract

`agentdeck contract conversation-runtime` discovers the additive M1 foreground conversation contract. `--example` includes one deterministic payload validated before output. The contract version is `conversation-runtime/v1`; `schema_version` continues to use the single-source ProjectView version.

The response fields are `schema_version`, `contract_version`, `mode`, `conversation_id`, `state`, `active_turn`, `pending_preview`, `leader_backend`, `ownership`, `cancellation`, `semantic_clarification_card`, `controls`, and `blockers`. Conversation states follow the approved append-only lifecycle. `active_turn` and `pending_preview` are compact derived facts, never transcript or full preview content.

`semantic_clarification_card` is the bounded discovery fixture for unresolved semantic authority. Its exact fields are `schema_version`, `authority_hash`, `unresolved_count`, `question`, and `controls`. Its controls are limited to `clarify` and `inspect`; it cannot confirm a preview or dispatch work. Ordinary runtime snapshots project `null`; an unresolved Conversation response carries the real card without calling the Leader or creating plan, Mission, approval, message, job, inbox, or preview-binding state.

Controls use `kind`, `label`, `command`, `safety`, `enabled`, and `blocker`. Inspect controls cannot execute or cancel work. Cancellation and execution require explicit-user safety and remain subject to current lifecycle/binding validation; a control is not an authorization token.

The payload is read-only discovery. It does not create a conversation, call a Leader, consume a preview, flush an outbox, invoke ACP, inspect tmux, or send terminal input.
