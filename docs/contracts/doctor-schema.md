# Doctor Contract

`agentdeck doctor` is the local setup diagnostics surface for AgentDeck. It checks tmux, project configuration, and the configured Leader provider readiness without calling the provider or exposing secret values.

## Discovery

```bash
agentdeck contract doctor
agentdeck contract doctor --example
```

The contract command returns:

```json
{
  "schema_version": "project-view/v1",
  "doctor_command": "agentdeck doctor",
  "response_fields": [],
  "configured_leader_fields": [],
  "provider_check_fields": [],
  "workbench_contract": "agentdeck contract workbench",
  "leader_chat_contract": "agentdeck contract leader-chat",
  "leader_review_contract": "agentdeck contract leader-review"
}
```

Use `agentdeck contract doctor --example` to include a stable GUI-ready diagnostics fixture. GUI clients can follow the related contract commands to render setup guidance next to workbench, natural-language Leader chat, and deterministic Leader review surfaces without hard-coding those schema entrypoints.

## Response

`agentdeck doctor` returns:

```json
{
  "ok": false,
  "doctor_command": "agentdeck doctor",
  "root": "/workspace/agentdeck-example",
  "config_exists": true,
  "config_path": "/workspace/agentdeck-example/.agentdeck/config.toml",
  "tmux": {},
  "configured_leader": {},
  "deepseek": {},
  "openai_compatible": {},
  "codex_cli": {},
  "claude_cli": {}
}
```

`configured_leader` describes the configured Leader from `.agentdeck/config.toml`:

```json
{
  "agent_id": "leader",
  "provider": "deepseek",
  "model": "deepseek-chat",
  "approval_mode": "confirm",
  "provider_backend": "api",
  "provider_transport": "http",
  "ready": false,
  "supported": true,
  "missing_env": ["DEEPSEEK_API_KEY"],
  "detail": "DEEPSEEK_API_KEY is not set; provider calls are disabled",
  "command_path": null,
  "setup_commands": [
    "export DEEPSEEK_API_KEY=\"<your-deepseek-api-key>\""
  ]
}
```

Provider checks such as `deepseek` and `openai_compatible` contain:

```json
{
  "ok": false,
  "detail": "DEEPSEEK_API_KEY is not set; provider calls are disabled",
  "provider_backend": "api",
  "provider_transport": "http",
  "command_path": null,
  "setup_commands": [
    "export DEEPSEEK_API_KEY=\"<your-deepseek-api-key>\""
  ]
}
```

`codex_cli` and `claude_cli` use the same provider check shape, with `provider_backend=cli`, `provider_transport=subprocess`, `command_path` set to the resolved local executable path when the command is available, and `setup_commands` set to the relevant login/doctor commands.

## Boundaries

- The command is diagnostic only.
- The command must not call the configured Leader provider.
- `setup_commands` must only contain placeholder commands that a human can copy and edit outside AgentDeck.
- Output must never include real API key values.
- API-backed providers such as `deepseek` and `openai-compatible` report `provider_backend=api`, `provider_transport=http`, missing environment variable names, and `command_path=null`; CLI-backed providers such as `codex-cli` and `claude-cli` report `provider_backend=cli`, `provider_transport=subprocess`, whether the local command is available on PATH, and the resolved command path when found. The fake provider uses `local/local`; unsupported legacy provider names use `unknown/unknown`.
- Top-level provider checks are diagnostics for provider selection. The top-level `ok` remains tied to tmux, config existence, and the currently configured Leader provider readiness.
- GUI clients can use `agentdeck contract doctor` to discover fields before rendering setup guidance.
- The discovery payload should expose adjacent workbench, Leader chat, and Leader review contracts so setup screens can link directly to the main control surfaces.
