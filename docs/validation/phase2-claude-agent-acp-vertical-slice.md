# Phase 2 Claude Agent ACP Vertical Slice Validation

## Verdict

**PASS** on 2026-07-12. AgentDeck completed one foreground ACP v1 vertical
slice against `@agentclientprotocol/claude-agent-acp@0.58.1` in a disposable
project. The implementation commit under test was `240d7915`.

This evidence covers only the explicitly confirmed foreground ACP commands.
tmux remains the default runtime for existing projects, dispatch, Mission, and
workflow. This validation does not claim a daemon, default REPL, global roaming,
Workspace Client, or multi-agent ACP Mission.

## Versions

- ACP protocol version: `1`
- AgentDeck Python ACP SDK: `agent-client-protocol==0.11.0`
- Claude adapter package: `@agentclientprotocol/claude-agent-acp@0.58.1`
- Node.js: `v22.23.0`
- Python: `3.12.13` (`agentdeck` conda environment)

The adapter was installed and authenticated by the human operator outside
AgentDeck. AgentDeck did not install software, invoke `npx`, change
authentication, or read credentials.

## Sanitized durable identities and results

- AgentDeck session: `ags_aa22f5cb1ef0`
- New/prompt turn: `trn_21eaa996731b` — `completed` / `end_turn`
- Load replay turn: `trn_04a9d253f58a` — `completed` / `loaded`
- Resume prompt turn: `trn_615d84938acb` — `completed` / `end_turn`
- Permission request: `prm_66c9a75df4e6` — `denied` through the exact current
  `reject_once` option
- Final session state: `disconnected` with `clean_exit`
- Native session identity remained present and stable across new, load, and
  resume.
- Load persisted contiguous non-completion replay before `loaded`, including a
  kind/payload hash match with the prior conversation.
- Resume added one normal prompt turn without pre-prompt history replay.
- Global command counts matched the durable ledger after every operation.
- Requested file `agentdeck-acp-must-not-exist.txt`: **absent** after the
  rejected write.

The operator's user-level Claude permission mode was `auto`. The disposable
acceptance project therefore wrote only a project-local
`.claude/settings.local.json` with `permissions.defaultMode=default` so the
ACP permission bridge was exercised deterministically. The user-level setting
was not changed.

## Commands and results

Read-only rehearsal:

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_acp_runtime.py \
  -k 'live_acp_gate or real_preflight_rehearsal or live_claude' -q
```

Result: `4 passed, 1 skipped, 53 deselected`.

Real opt-in acceptance:

```bash
AGENTDECK_ACP_LIVE=1 \
AGENTDECK_ACP_COMMAND="$(command -v claude-agent-acp)" \
conda run --no-capture-output -n agentdeck \
pytest tests/test_acp_runtime.py::test_live_claude_agent_vertical_slice -q -s
```

Final result: `1 passed in 23.86s`.

No transcript, raw tool input, token count, email, authentication material,
API key, environment dump, native session identifier, or absolute home path is
included in this report.
