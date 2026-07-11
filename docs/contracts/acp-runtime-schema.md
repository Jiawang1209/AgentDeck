# ACP Runtime Contract

`agentdeck contract acp-runtime [--example]` publishes the discovery metadata for the Phase 2 ACP diagnostic surface. Its contract version is `acp-runtime/v1`.

## Read-only preflight

```text
agentdeck protocol acp preflight --agent <agent_id>
```

Preflight opens an existing project without creating layout, resolves one configured Agent, requires `transport = "acp"`, preserves the exact `transport_command` argv, checks the `acp` Python module and pinned `agent-client-protocol==0.11.0` package version through import metadata, and resolves executables through `shutil.which`. Successful executable results are normalized with `expanduser().resolve(strict=False)` before entering the contract. SDK discovery never imports the module; missing specs, broken finders, missing/invalid distribution metadata, empty versions, and discovery exceptions all become the same deterministic `present=false`, `version=null`, `ACP Python SDK is unavailable or unusable` diagnostic.

For the known first target `claude-agent-acp`, preflight also requires Node 22 or newer. Target matching is case-insensitive and strips `.exe`, `.cmd`, or `.bat` from the executable basename in both producer and validator. Node version evidence is read from the installed Node headers adjacent to the resolved binary; AgentDeck never invokes `node --version` or any adapter command.

The response fields are `mode`, `contract_version`, `project`, `ready`, `agent`, `adapter`, `sdk`, `node`, `blockers`, and `controls`. The validator enforces exact nested fields/types and cross-field facts: presence matches absolute executable paths, absent SDK has no version, a present SDK has a non-empty observed version, the known Claude adapter requires Node 22 with a parseable version, Node readiness is derived rather than trusted, and blockers are the exact stable ordered projection of failed facts. An observed SDK version other than the pin is a valid diagnostic response with the stable `ACP Python SDK version must be 0.11.0` blocker, not contract corruption. Top-level `ready` is true exactly when every required fact is ready and `blockers` is empty. Controls are enabled inspect-only controls with null blockers and are never authorization tokens.

The command does not spawn a process, authenticate, create or load a session, call a provider, inspect tmux, create directories, acquire runtime locks, write state/outbox/events, or change permissions. It validates the complete response before its single JSON stdout write. Command/config errors use stderr and produce no partial stdout.

## Foreground run response

`agentdeck protocol acp run --agent <agent_id> --prompt <text> --confirm` starts one explicitly configured adapter, negotiates ACP v1, creates one native session, and runs one prompt turn. Missing confirmation, invalid configuration, or a not-ready preflight spawns no process and writes no state. The final response is constructed from persisted facts and validated before one JSON document is written to stdout. Diagnostics and permission questions use stderr only.

Run responses contain the exact `run_response_fields` published by discovery. Completion is derived only from the ACP prompt `stopReason`; streamed text is never completion proof. The session is always transitioned to `disconnected` during bounded cleanup.

Every response field has one durable source. Agent/session/native identity and negotiated capabilities come from the immutable AgentSession; protocol version and bounded negotiated Agent identity come from the `session_new_completed` transition details; stop reason comes only from the completion TransportUpdate; disconnect reason comes from the terminal session transition; turn state and counts are derived from the complete persisted lineage. The CLI reloads these records after disconnect and does not use transport result locals to construct stdout.

Permission admission is atomic under the protocol mutation lock. Before any durable mutation, AgentDeck computes the prospective redacted permission update against the complete persisted turn update count and payload-byte total. Only an in-budget request appends the pending PermissionRequest, redacted TransportUpdate, waiting-permission transition, and their outbox events in one state save. Boundary failure leaves the entire tree, including a pre-existing pending outbox, byte-for-byte unchanged; it cannot create an orphan permission.

Streaming admission reserves one update slot and a 512-byte payload allowance for a terminal completion or compact error. A post-creation bound, transport, callback, or persistence error is cancelled where possible and terminalized as failed or ambiguous whenever the ledger remains writable. Response reconstruction first validates the complete protocol identity and transition history, exact session/turn ownership, contiguous update sequences, completion uniqueness and stop-reason/state consistency, and one final disconnect; corrupt or conflicting state produces no stdout.

Adapter stderr is never printed or returned as text. The only CLI diagnostic is a compact summary containing presence, bounded byte count, truncation, line count, and SHA-256. Cleanup failure is reported with a stable message and persisted as `cleanup_failed`; raw exceptions, prompts, tool data, native IDs, and stderr content are excluded.

## Future load/resume responses

Discovery also publishes the planned load/resume response fields. Those operations remain unavailable until their later approved tasks.

The `--example` fixture is deterministic and uses only a sanitized fake Agent, fake adapter argv, fake SDK version, and `/example` path. It contains no local path, credential, transcript, provider output, or real installed version.
