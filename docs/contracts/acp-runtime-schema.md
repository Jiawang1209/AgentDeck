# ACP Runtime Contract

`agentdeck contract acp-runtime [--example]` publishes the discovery metadata for the Phase 2 ACP diagnostic surface. Its contract version is `acp-runtime/v1`.

## Read-only preflight

```text
agentdeck protocol acp preflight --agent <agent_id>
```

Preflight opens an existing project without creating layout, resolves one configured Agent, requires `transport = "acp"`, preserves the exact `transport_command` argv, checks the `acp` Python module and `agent-client-protocol` package version through import metadata, and resolves executables through `shutil.which`.

For the known first target `claude-agent-acp`, preflight also requires Node 22 or newer. Node version evidence is read from the installed Node headers adjacent to the resolved binary; AgentDeck never invokes `node --version` or any adapter command.

The response fields are `mode`, `contract_version`, `project`, `ready`, `agent`, `adapter`, `sdk`, `node`, `blockers`, and `controls`. `ready` is true exactly when `blockers` is empty. Controls are inspect-only and are never authorization tokens.

The command does not spawn a process, authenticate, create or load a session, call a provider, inspect tmux, create directories, acquire runtime locks, write state/outbox/events, or change permissions. It validates the complete response before its single JSON stdout write. Command/config errors use stderr and produce no partial stdout.

## Future foreground responses

Discovery also publishes the planned run/load/resume response fields, transition fields, entity kinds, safety values, and confirmation requirements. This Task 7 slice does not add `run`, `load`, or `resume` routes. Those operations remain unavailable until their later approved tasks.

The `--example` fixture is deterministic and uses only a sanitized fake Agent, fake adapter argv, fake SDK version, and `/example` path. It contains no local path, credential, transcript, provider output, or real installed version.
