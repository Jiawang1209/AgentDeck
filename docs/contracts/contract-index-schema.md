# Contract Index Discovery

`agentdeck contract list` is the read-only discovery index for GUI, TUI, natural-language, and automation clients that need to find AgentDeck's machine-readable contract commands without hard-coding every subcommand.

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

- `project-view`
- `continue`
- `doctor`
- `events`
- `workbench`
- `controls`
- `agent-runtime`
- `leader-chat`
- `leader-actions`
- `leader-review`
- `leader-action`
- `approvals`
- `inbox`
- `trace`
- `artifacts`

When a new GUI-consumable contract command is added, update `CONTRACT_INDEX_SPECS` in `src/agentdeck/contracts.py`, this document, and the contract index tests in the same commit.
