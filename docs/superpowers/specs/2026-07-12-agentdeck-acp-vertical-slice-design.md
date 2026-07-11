# AgentDeck Phase 2 ACP Vertical Slice Design

Status: **Awaiting human review and approval**

Date: 2026-07-12

Scope: one real ACP Agent, one foreground client connection at a time, no daemon

## 1. Decision summary

Phase 2 will make AgentDeck a real ACP v1 client for one explicitly configured Agent. The first acceptance target is `@agentclientprotocol/claude-agent-acp` 0.58.1, launched as an external stdio subprocess. AgentDeck will use the official ACP Python SDK, negotiate capabilities at runtime, create and reload a native session, send a prompt, persist streamed updates, bridge one permission decision fail-closed, finish the turn, disconnect cleanly, and explicitly resume the same native session in a new process.

The slice is intentionally a diagnostic and integration surface, not the final product entrypoint. Commands remain under `agentdeck protocol acp preflight|run|load|resume`; tmux remains the default runtime for all existing flows. No existing dispatch, Mission, workflow, approval, readiness, or pane behavior is routed through ACP in this phase.

## 2. Evidence baseline

Research was refreshed on 2026-07-12 rather than inferred from the earlier Phase 1 model.

- ACP's current stable wire protocol is v1. Wire compatibility is negotiated through `initialize.protocolVersion`; library and schema artifact versions are not wire versions.
- A client must initialize before creating a session. Capabilities omitted by either peer are unsupported.
- ACP v1 requires the baseline Agent methods `session/new`, `session/prompt`, `session/cancel`, and `session/update`. `session/load` is guarded by `loadSession`; `session/resume` is separately guarded by `sessionCapabilities.resume`.
- `session/load` replays conversation history through `session/update` before returning. `session/resume` reconnects without replay.
- A prompt completes when the original `session/prompt` request returns a `stopReason` after its updates and permission requests have settled.
- Permission is a reverse request from Agent to Client through `session/request_permission`; the Client returns either an offered `optionId` or `cancelled`.
- The official ACP Registry currently lists Claude Agent 0.58.1, distributed as `@agentclientprotocol/claude-agent-acp@0.58.1`.
- Inspection of that exact npm tarball confirmed protocol v1, `loadSession`, `sessionCapabilities.resume`, `session/new`, `session/load`, `session/resume`, streamed updates, and reverse permission requests. Its declared runtime is Node >=22.
- The official ACP Python SDK is published as `agent-client-protocol`; the version observed during design was 0.11.0. It provides typed schemas, stdio subprocess transport, client callbacks, load, resume, prompt, update, and permission plumbing.
- This machine currently has Claude Code 2.1.207, Codex CLI 0.131.0, and Node 22.23.0. It does not currently have the ACP adapter or Python ACP SDK installed. Existing Claude Code login is useful evidence but is not accepted as proof that the adapter can authenticate; the live acceptance must prove that separately.
- Anthropic's current Claude Code CLI reference documents interactive, print, continue, and resume modes but no native ACP stdio-server mode. Therefore the first target is explicitly the registered Claude Agent ACP adapter, not the `claude` executable pretending to speak ACP. This is an inference from the documented CLI surface and must be rechecked during implementation.

Authoritative sources:

- [ACP repository and versioning](https://github.com/agentclientprotocol/agent-client-protocol)
- [ACP v1 initialization](https://agentclientprotocol.com/protocol/v1/initialization)
- [ACP v1 session setup, load, and resume](https://agentclientprotocol.com/protocol/v1/session-setup)
- [ACP v1 prompt turn](https://agentclientprotocol.com/protocol/v1/prompt-turn)
- [ACP v1 tool calls and permission requests](https://agentclientprotocol.com/protocol/v1/tool-calls)
- [Official ACP Python SDK](https://github.com/agentclientprotocol/python-sdk)
- [Official ACP Registry](https://github.com/agentclientprotocol/registry)
- [Claude Agent ACP adapter](https://github.com/agentclientprotocol/claude-agent-acp)
- [Claude Code CLI reference](https://docs.anthropic.com/en/docs/claude-code/cli-usage)

## 3. Alternatives considered

### A. Official Python ACP client plus an external registry adapter — recommended

AgentDeck uses the official Python SDK for JSON-RPC, schemas, cancellation, and stdio lifecycle, while a separately installed adapter owns provider-specific behavior.

Advantages:

- smallest standards-aligned path from the Phase 1 model to real wire traffic;
- keeps AgentDeck provider-neutral and makes the adapter replaceable;
- receives upstream schema and transport fixes rather than maintaining a private protocol fork;
- supports strict fake-process tests and an opt-in real Agent smoke test;
- matches the north-star split between ACP-native Agents, adapters, and tmux fallback.

Costs:

- adds one Python runtime dependency;
- requires an explicitly installed external Agent executable;
- adapter releases and auth behavior must be treated as external compatibility facts.

### B. Hand-written JSON-RPC stdio client

AgentDeck could implement framing, request correlation, reverse requests, cancellation, capability parsing, and schema validation using only the standard library.

This avoids a dependency, but duplicates the highest-risk protocol machinery, increases malformed-message and concurrency surface, and is more likely to drift as ACP v1 gains optional capabilities. It is rejected for the first real slice.

### C. Embed Claude Agent SDK directly

AgentDeck could call Anthropic's SDK and translate its events into Phase 1 records without a separate ACP process.

This may simplify one provider, but it proves a Claude integration rather than an ACP client, binds Python AgentDeck to provider-specific semantics, and weakens future Codex/Gemini/OpenCode portability. It is rejected as the runtime kernel boundary.

## 4. Product boundary

### In scope

- one configured ACP adapter command represented as an argv list and executed without a shell;
- read-only adapter preflight;
- ACP v1 initialize and exact capability negotiation;
- `session/new`, `session/load`, `session/prompt`, streamed `session/update`, `session/request_permission`, prompt `stopReason`, clean EOF/disconnect, and `session/resume`;
- append-only, lineage-validated persistence through the Phase 1 protocol model;
- a foreground permission question with a non-interactive fail-closed path;
- deterministic CLI and contract responses suitable for later Frontdesk/GUI use;
- fake-Agent conformance tests plus one opt-in real Claude Agent acceptance in a disposable project;
- explicit setup blockers when the SDK, adapter, Node version, auth, or required capability is unavailable.

### Out of scope

- changing the default `runtime.backend = "tmux"`;
- routing `dispatch`, approvals, Mission, workflow, run-loop, or natural-language chat through ACP;
- project daemon, background continuation, default `agentdeck` REPL, global roaming, notifications, or Workspace Client;
- Codex-and-Claude multi-agent ACP Mission;
- AgentDeck acting as an ACP Agent;
- client filesystem or terminal capabilities, MCP server injection, images, audio, session modes, model pickers, slash commands, usage billing, authentication UI, or adapter installation;
- automatic permission approval, durable `allow_always` policy, or translating ACP metadata into execution authority;
- schema migration or rewriting existing tmux sessions.

## 5. Configuration and command surface

### Agent configuration

`AgentSpec` gains additive fields with backward-compatible defaults:

```toml
[[agents]]
id = "planner"
role = "implementation"
provider = "claude"
command = "claude"
transport = "acp"
transport_command = ["claude-agent-acp"]
```

- `command` remains the existing tmux fallback command.
- `transport` defaults to `tmux` for every existing project.
- `transport_command` defaults to an empty tuple and is required only for an explicit ACP command.
- The argv list is passed directly to `asyncio.create_subprocess_exec` through the SDK. It is never joined into a shell command.
- AgentDeck never installs, updates, or discovers a remote adapter package as a side effect.

### Commands

```text
agentdeck protocol acp preflight --agent <id>
agentdeck protocol acp run --agent <id> --prompt <text> --confirm
agentdeck protocol acp load --session-id <ags_id> --confirm
agentdeck protocol acp resume --session-id <ags_id> --prompt <text> --confirm
agentdeck contract acp-runtime [--example]
```

`preflight` is read-only. It validates project config, Agent identity, explicit ACP transport selection, argv shape, executable presence, Python SDK availability, and the known Node >=22 requirement for the first acceptance adapter. It does not start the adapter, authenticate, create a session, or write state.

The other three commands are foreground and require `--confirm` because they start an external Agent process and may cause model/tool activity. They produce one final JSON object on stdout. Human permission questions and adapter diagnostics use stderr so the stdout contract is never interleaved with prompts or streaming text.

`load` proves history replay and exits after the load response. `resume` proves reconnect-without-replay and then executes one new prompt. Neither command silently falls back to the other method.

## 6. Runtime architecture

```text
agentdeck protocol acp preflight|run|load|resume
          |
          v
  ACP command controller
          |
          +--> StateStore protocol ledger
          |
          v
  AcpClientTransport (official Python SDK)
          |
       stdio JSON-RPC v1
          |
          v
  explicitly configured ACP Agent subprocess
  (first acceptance: claude-agent-acp 0.58.1)
```

New implementation units are intentionally small:

1. `runtime/acp.py` owns subprocess lifetime, SDK connection, initialize/new/load/resume/prompt calls, timeouts, cancellation, EOF classification, and negotiated capability conversion. It does not know CLI formatting.
2. `runtime/acp_client.py` implements ACP Client callbacks. It normalizes updates and permission requests, then calls injected ledger/decision functions. It does not grant permission itself.
3. `runtime/acp_mapping.py` contains pure mappings from typed ACP payloads to AgentDeck update kinds, compact permission facts, stop reasons, and capability summaries.
4. CLI orchestration resolves config, creates StateStore records, supplies the foreground decision callback, validates the final contract, and prints only after validation.
5. The existing tmux backend remains unchanged. No shared `RuntimeBackend` method is repurposed to hide the different lifecycle semantics in this slice.

## 7. Append-only lifecycle model

Phase 1 records are immutable append-only facts, but their initial `state` fields alone cannot describe a real connection changing over time. Phase 2 therefore adds one generic append-only collection:

```text
protocol_state_transitions[]
```

Each transition contains:

```text
transition_id
entity_type       # session | turn | permission
entity_id
from_state
to_state
reason            # compact stable code or null
details           # bounded metadata, never streamed content or credentials
created_at
```

Rules:

- the referenced entity must exist exactly once;
- `from_state` must equal the state derived from the base record plus prior transitions;
- allowed state edges are explicit pure tables;
- duplicate transition IDs and invalid edges reject before any write;
- one state mutation and its compact audit event use the existing protocol outbox discipline;
- ProjectView derives current state without rewriting base records;
- rejected transitions are byte-for-byte zero-write.

`AGENT_SESSION_STATES` gains `disconnected`. A successful new session is recorded only after the native `sessionId` is returned, so the immutable AgentSession contains the real opaque native identity. Clean subprocess exit transitions the session to `disconnected`; a later load/resume transitions `disconnected -> reconnecting -> ready`. Unexpected EOF during an active turn also marks that turn `ambiguous`.

`ProtocolTurn` gains a backward-compatible `kind` with default `prompt`. Phase 2 uses `load_replay` for the synthetic turn that owns replayed `session/update` records. `session/resume` creates no replay turn; the subsequent prompt creates a normal prompt turn.

Permission requests remain immutable records. Their approved/denied/expired current state is derived from transition records, so an approval never overwrites the original request.

## 8. Wire flow and ledger mapping

### New session and prompt

1. Validate config and `--confirm`; no subprocess exists before both pass.
2. Spawn the configured argv without a shell and establish one SDK connection.
3. Send `initialize(protocolVersion=1)` with minimal client capabilities. Phase 2 advertises no client filesystem, terminal, or MCP facilities.
4. Reject incompatible protocol versions. Treat omitted optional Agent capabilities as unsupported.
5. Call `session/new` with the canonical absolute project root and an empty MCP server list.
6. After receiving the native session ID, append the AgentSession and `created -> ready` transition.
7. Append a prompt turn, transition it to `submitted`, and call `session/prompt` with one text content block.
8. For each validated `session/update`, append exactly one monotonically sequenced TransportUpdate and transition the turn to `streaming` when appropriate.
9. Handle any reverse permission request through the permission bridge below.
10. When `session/prompt` returns, append a completion update containing only the normalized stop reason and transition the turn to `completed`, `blocked`, or `failed` according to an explicit table.
11. Close stdin, wait a bounded grace period, terminate then kill only if required, and append `disconnected` with a stable reason.

### Load

1. Resolve the internal `ags_` identity and its non-empty native session ID.
2. Spawn and initialize a fresh adapter process.
3. Require `loadSession=true`; otherwise fail before sending `session/load`.
4. Transition the existing session through `reconnecting` to `ready`.
5. Create one `load_replay` turn. Persist replay notifications in received order.
6. Complete the replay turn only after the `session/load` response arrives, then disconnect cleanly.

### Resume

1. Resolve the same internal/native session identity and start a fresh initialized process.
2. Require `sessionCapabilities.resume`; never substitute `session/load`.
3. Call `session/resume` and assert that no history replay updates arrive before its response. Any replay is a protocol error.
4. Transition to `ready`, then run a new prompt turn through the normal flow.
5. Disconnect cleanly after completion.

## 9. Update mapping and bounds

All incoming payloads are validated by the SDK schema before mapping. The mapper produces only AgentDeck's existing update kinds:

- agent/user/thought message chunks -> `text` with role, message ID when present, and content block;
- plan/status/config/session information -> `progress` with the ACP update discriminator and bounded normalized fields;
- tool call start/update -> `tool_call` or `tool_result` according to status;
- permission reverse request -> `permission_request` plus a PermissionRequest entity;
- diffs/resources -> `artifact` summaries without reading referenced files;
- prompt response -> `completion` with normalized `stop_reason`;
- malformed, unknown, or inconsistent traffic -> `error`, then fail or mark ambiguous according to whether the active request outcome is known.

Limits are enforced before persistence: 64 KiB per decoded JSON-RPC message, 2 MiB cumulative persisted update payload per turn, 256 updates per turn, and 120 seconds per request in the first implementation. Reaching a bound cancels the prompt, records a compact error, and fails closed. Unknown future update discriminators are never silently treated as successful completion.

ProjectView continues to expose only compact summaries and never streamed text, raw tool input, permission options, credentials, environment variables, or adapter stderr.

## 10. Permission bridge

The permission callback performs this order exactly:

1. Validate session/turn correlation, tool-call identity, and non-empty offered options.
2. Derive conservative compact facts: tool name/title, target summary, and risk. Unknown tools use `risk=unknown`.
3. Append the pending PermissionRequest and a redacted permission-request TransportUpdate.
4. Transition the turn to `waiting_permission`.
5. If stdin is not an interactive TTY, the request is cancelled immediately and recorded as denied; AgentDeck never blocks unattended execution waiting for input.
6. On a TTY, render numbered options to stderr. Only an exact option from the current ACP request can be returned.
7. Phase 2 permits explicit `allow_once` and `reject_once`. `allow_always` and `reject_always` remain visible but disabled because durable permission policy is not part of this slice.
8. EOF, timeout, Ctrl-C, invalid input exhaustion, or process disconnect returns `cancelled` and records denied/expired state.
9. Append the permission transition before returning the decision to the Agent, then return the turn to `streaming` if it remains active.

ACP option names, metadata, Agent prompts, skills, memory, and context are never authorization. Only the human's exact foreground selection controls the reverse response.

## 11. Failure and recovery semantics

- Missing SDK or adapter: preflight/setup blocker; no process and no state write.
- Spawn failure: compact failed event; no AgentSession because no native session exists.
- Initialize version mismatch: close process and report unsupported protocol; no false ACP-ready capability.
- Missing load/resume capability: reject before the unsupported request; do not fallback.
- Malformed JSON/schema violation: cancel active request, record compact error, mark active turn ambiguous if completion is unknown, disconnect.
- Duplicate update: reject a duplicate `(turn_id, sequence)` through existing StateStore guarantees.
- Permission disconnect: persist denied/expired and return no approval.
- EOF before prompt response: session `disconnected`, turn `ambiguous`; never synthesize completion from the last text chunk.
- Ctrl-C: send `session/cancel`, settle any pending permission as cancelled, wait bounded grace, then terminate.
- Resume after ambiguous disconnect: requires an explicit human command with `--confirm`; it never happens automatically.
- Adapter stderr is bounded and surfaced only as a diagnostic summary. It is not parsed as protocol truth or stored wholesale.

## 12. Contracts and observation

`acp-runtime/v1` describes:

- preflight response and blockers;
- run/load/resume response fields;
- negotiated protocol, Agent identity, capabilities, internal/native session linkage, turn outcome, update count, permission count, disconnect reason, and inspect controls;
- protocol state-transition fields and allowed entity kinds;
- safety values and confirmation requirements;
- a deterministic fake-Agent example with no real credentials or output.

Every command constructs its response from persisted facts, validates through `validate_acp_runtime_contract()`, and prints only after validation. Contract discovery is added to `agentdeck contract list`, the contract index, workbench contract discovery, and `docs/contracts/acp-runtime-schema.md`.

`agentdeck protocol status` remains read-only and gains the compact transition summary only when the ProjectView/schema change is implemented. Existing fields keep their meanings.

## 13. Testing strategy

### Pure and StateStore tests

- capability conversion matrices;
- state transition allowed/forbidden edges and stale `from_state`;
- prompt/load-replay turn kinds;
- permission option filtering and fail-closed outcomes;
- update mapping, redaction, size/count bounds, duplicate sequence, and unknown discriminators;
- crash/outbox replay and byte-for-byte zero-write rejection.

### Fake ACP Agent integration tests

A stdio fixture process emits exact JSON-RPC scenarios:

- initialize + new + streamed text + end_turn;
- load with ordered replay;
- resume without replay followed by a prompt;
- permission allow-once, reject-once, non-TTY cancel, Ctrl-C cancel;
- version mismatch, omitted capability, malformed message, duplicate update, stderr noise, timeout, EOF before response, and update after completion.

Tests assert subprocess argv is never executed through a shell and environment/credentials are not persisted.

### Compatibility regression

The full existing suite must remain green. Dedicated tests prove ordinary `dispatch`, Mission, workflow, approval, readiness, tmux commands, and default project config produce byte-for-byte equivalent behavior when ACP fields are absent.

### Real acceptance

The live test is opt-in and never installs the adapter. In a disposable project with an explicitly installed and authenticated `claude-agent-acp`:

1. preflight reports ready and the exact executable/version facts;
2. run creates a native session and completes one harmless text prompt;
3. a prompt requesting a disposable file change triggers permission; the operator chooses reject-once and verifies no file is created;
4. the process disconnects and ProjectView/ledger agree on session, turn, update, permission, and transition facts;
5. load replays the same session history;
6. resume reconnects without replay and completes a second harmless prompt;
7. no third-party credential, full transcript, raw tool input, email, token, or environment dump enters the durable evidence report.

If the current Claude login cannot authenticate the adapter, the result is a documented setup blocker, not a workaround, auto-login, or false pass.

## 14. Acceptance criteria

Phase 2 is complete only when all are true:

1. One real registry Agent completes initialize, new, prompt, streaming, permission rejection, completion, disconnect, load, and resume through AgentDeck.
2. Every required optional method is capability-gated and version negotiation is exact.
3. Pending permission cannot self-authorize; non-interactive execution fails closed.
4. Completion is based only on the prompt response stop reason, never terminal text or last chunk heuristics.
5. The same internal `ags_` identity resolves to one opaque native session across disconnect/load/resume.
6. All lifecycle facts are append-only, lineage-valid, observable through ProjectView/CLI contracts, and recoverable after an outbox interruption.
7. tmux remains the default and all existing behavior/tests remain unchanged.
8. A durable sanitized real-acceptance report records exact versions, IDs, commands, outcomes, and blockers.
9. README and handoff describe ACP as implemented only after the real acceptance passes.
10. No daemon, default REPL, multi-agent ACP Mission, automatic adapter install, or unrelated refactor is included.

## 15. Implementation approval gate

This document and its companion implementation plan authorize no production-code changes by themselves. Implementation may begin only after a human explicitly reviews and approves both documents. Any proposed change to use a different first Agent, auto-install an adapter, support durable allow-always policy, advertise client filesystem/terminal capabilities, add a daemon, or route existing dispatch through ACP is a new product fork and must stop for human direction.
