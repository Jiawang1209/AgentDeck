# Phase 2 Claude ACP Live Acceptance SOP

This is the operator procedure for the opt-in real acceptance gate. It is not a
PASS report. Do not create
`docs/validation/phase2-claude-agent-acp-vertical-slice.md` until the live test
passes.

## Safety prerequisites

- A human installs and authenticates `claude-agent-acp` outside AgentDeck.
- `AGENTDECK_ACP_COMMAND` is the exact absolute path of an existing executable.
- Do not use `npx`, `npm`, `pip`, an installer command, or an auto-download shim.
- Do not change authentication as part of this procedure.
- Run in the `agentdeck` conda environment.

## Read-only rehearsal

The default suite includes a disposable-project rehearsal that configures an
exact nonexistent `claude-agent-acp` path and calls the real preflight CLI. It
asserts that SDK 0.11.0 and Node >=22 are ready, the missing executable is the
only blocker, and the project tree is byte-for-byte unchanged.

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_acp_runtime.py \
  -k 'live_acp_gate or real_preflight_rehearsal or live_claude' -q
```

Expected without opt-in: gate/rehearsal tests pass and the live test is skipped.
A missing or non-executable `AGENTDECK_ACP_COMMAND` is an explicit setup
blocker/skip, never a pass.

## Real acceptance

After a human confirms the adapter installation and authentication:

```bash
AGENTDECK_ACP_LIVE=1 \
AGENTDECK_ACP_COMMAND="$(command -v claude-agent-acp)" \
conda run --no-capture-output -n agentdeck \
pytest tests/test_acp_runtime.py::test_live_claude_agent_vertical_slice -q -s
```

The test creates a disposable project and invokes the real AgentDeck CLI for
project initialization, preflight, run, load, resume, a second prompt, and
protocol status. The permission interaction uses a pseudo-terminal and selects
the exact current `reject_once` option. Acceptance requires the requested file
to remain absent and the compact status plus durable ledger to agree on one
native session, prompt/load-replay/prompt turns, denied permission, and final
disconnect.

Failure output is bounded and replaces the home directory with `<HOME>`. Never
copy transcript text, raw tool input, environment dumps, credentials, email,
tokens, or auth-file contents into durable evidence.

## PASS-only evidence

Only after the command above passes, create the reserved PASS report with the
date, AgentDeck commit, ACP protocol version, adapter/package version,
Node/Python versions, internal IDs, exact result states, file non-creation
check, and commands. If adapter authentication or capability negotiation fails,
record the exact compact setup blocker in `HISTORY.md` and the handoff instead;
do not write the PASS report and do not begin Task 12.
