# Contract Index Discovery

`agentdeck contract list` is the read-only discovery index for GUI, TUI, natural-language, and automation clients that need to find AgentDeck's machine-readable contract commands without hard-coding every subcommand.

The M1 foreground-conversation slice adds `conversation-runtime`, `leader-backend`, and `worker-transport` to this index.

It does not read `.agentdeck/` state, does not inspect tmux panes, does not call any Leader provider, and does not mutate the project.

## Command

```bash
agentdeck contract list
```

## Response Fields

- `schema_version`: current ProjectView schema version.
- `contracts_command`: the command that produced this index.
- `contract_docs_dir`: absolute path to the local contract documentation directory.
- `response_fields`: ordered top-level field list for this index payload.
- `contract_item_fields`: ordered field list for each item in `contracts`.
- `count`: number of indexed contract entries.
- `contracts`: ordered contract discovery entries.

## Contract Item Fields

- `name`: stable contract name.
- `command`: read-only discovery command for the contract.
- `example_command`: read-only discovery command with a stable example payload.
- `contract_path`: absolute path to the Markdown schema document.
- `contract_exists`: whether `contract_path` exists in the local checkout.

## Indexed Contracts

The index currently lists these contract names in order:

- `daemon-runtime`
- `mission-scheduler`
- `client-session`
- `project-view`
- `continue`
- `loop`
- `doctor`
- `events`
- `run`
- `run-loop`
- `run-loop-all`
- `run-loop-host`
- `plan-rework`
- `workflow`
- `mission`
- `migration`
- `demo`
- `plans`
- `release`
- `workbench`
- `controls`
- `skills`
- `memory`
- `learning-review`
- `agent-runtime`
- `protocol-runtime`
- `acp-runtime`
- `conversation-runtime`
- `leader-backend`
- `worker-transport`
- `leader-chat`
- `leader-status`
- `leader-actions`
- `leader-review`
- `leader-summary`
- `leader-action`
- `approvals`
- `inbox`
- `trace`
- `artifacts`
- `worktree`
- `delegation`
- `frontdesk`
- `role-bindings`
- `goal`

`protocol-runtime` is discoverable through `agentdeck contract protocol-runtime [--example]`; its live read-only projection is `agentdeck protocol status`, and its durable schema is `docs/contracts/protocol-runtime-schema.md`.

`acp-runtime` is discoverable through `agentdeck contract acp-runtime [--example]`; its live read-only preflight is `agentdeck protocol acp preflight --agent <agent_id>`, and its durable schema is `docs/contracts/acp-runtime-schema.md`.

The three M1 contracts are discovery/validation surfaces. They describe current conversation lifecycle, explicit Leader backend identity, and Worker transport/ownership facts without executing controls or authorizing fallback.

`migration` is discoverable through `agentdeck contract migration [--example]`.
It describes the read-only existing-project preview and exact confirmed migration
responses documented in `docs/contracts/migration-schema.md`.

The contract/example and preflight are not proof of real-adapter acceptance. The explicit human-run gate and current blocker procedure are in `docs/validation/phase2-acp-live-acceptance-sop.md`.

When a new GUI-consumable contract command is added, update `CONTRACT_INDEX_SPECS` in `src/agentdeck/contracts.py`, this document, and the contract index tests in the same commit.
