# ACP Runtime Contract

`agentdeck contract acp-runtime [--example]` publishes the discovery metadata for the Phase 2 ACP diagnostic surface. Its contract version is `acp-runtime/v1`.

The foreground implementation and fake-Agent conformance are available, but the real
`claude-agent-acp` acceptance is blocked on human-managed adapter installation and
authentication. This contract is not live-acceptance evidence and does not make Phase 2
complete. The operator-only gate is documented in
`docs/validation/phase2-acp-live-acceptance-sop.md`; no PASS report exists until that gate passes.

Confirmed reconnect commands are `agentdeck protocol acp load --session-id <ags_id> --confirm`
and `agentdeck protocol acp resume --session-id <ags_id> --prompt <text> --confirm`.
Both resolve exactly one persisted internal session, its opaque native session id, configured
agent, and canonical workspace before starting a fresh stdio process. Load requires negotiated
`loadSession` and owns replay in one `load_replay` turn. Resume requires
`sessionCapabilities.resume`, rejects replay before its response, creates no replay turn, and
then runs one normal prompt turn on the same identity. Neither path falls back to the other.
The creating transition stores compact adapter provenance (`argv_hash` and hashed executable
identity, never raw arguments). Reconnect requires the persisted `acp-adapter` transport,
provider, agent, workspace, and adapter provenance to match current configuration before spawn;
drift is a zero-write blocker.

Update ownership uses a callback-captured lifecycle generation and an async phase lock. A
callback dispatched while load/resume is sealed cannot be admitted later merely because a
prompt generation has opened. ACP updates carry no originating request id, however: a wire
message that truly arrives only after `session/prompt` is in flight must be treated as prompt
traffic by protocol ordering. An adapter that asynchronously emits old replay at that point is
an uncorrelatable protocol violation and is covered by adapter conformance/order tests; the
runtime does not claim to infer provenance absent from the wire message.

The official SDK 0.11 dispatcher schedules `session/update` notification handlers as tasks while
responses are resolved directly by the receive loop. AgentDeck therefore installs a synchronous
incoming stream observer that counts each update at the SDK scheduling boundary; the callback
settles that exact count in `finally`, and load waits for the known pre-response set before sealing
its lifecycle generation. This is a deterministic dispatcher barrier, not an event-loop sleep or
quiet-time heuristic. A notification first observed after the load response is not valid load
replay and is rejected by the sealed phase without a ledger write.
The observer first validates exact `SessionNotification` params with the official SDK model;
malformed params set only the compact `invalid_session_update` diagnostic and never increment the
barrier or persist raw input. Each load resets the barrier generation, and settlement waiting is
hard-bounded to a small timeout derived from the request timeout. A valid observed notification
whose router callback never settles fails with a compact callback-settlement timeout, allowing the
existing load controller to terminalize the replay and disconnect rather than hang.

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

Run/load/resume responses contain the exact response fields published by discovery. `session_count`, `turn_count`, `update_count`, `permission_count`, and `transition_count` are all complete-ledger global summary counts, not current-turn metrics. Every `latest_*_id` is selected with the same stable `(created_at, identity)` ordering used by ProjectView summaries. These facts therefore match `agentdeck protocol status`, ProjectView, and the workbench's embedded ProjectView after multi-run, load, resume, and permission histories. A missing global permission is represented by `latest_permission_id=null`; identities are provenance, never authority. The response's `turn_id` / `turn_state` and `session_id` / `session_state` continue to identify the completed command target. Completion is derived only from that turn's ACP prompt `stopReason`; streamed text is never completion proof. The session is always transitioned to `disconnected` during bounded cleanup.

Every response field has one durable source. Agent/session/native identity and negotiated capabilities come from the immutable AgentSession; protocol version and bounded negotiated Agent identity come from the `session_new_completed` transition details; stop reason comes only from the completion TransportUpdate; disconnect reason comes from the terminal session transition; turn state and counts are derived from the complete persisted lineage. The CLI reloads these records after disconnect and does not use transport result locals to construct stdout.

Permission admission is atomic under the protocol mutation lock. Before any durable mutation, AgentDeck computes the prospective redacted permission update against the complete persisted turn update count and payload-byte total. Only an in-budget request appends the pending PermissionRequest, redacted TransportUpdate, waiting-permission transition, and their outbox events in one state save. Boundary failure leaves the entire tree, including a pre-existing pending outbox, byte-for-byte unchanged; it cannot create an orphan permission.

The foreground permission menu renders on stderr only. Every numbered option includes a bounded, single-line human label followed by its machine-stable ACP kind marker (`[allow_once]`, `[reject_once]`, `[allow_always]`, or `[reject_always]`); always options also carry `[disabled]`. Selection remains the displayed number and maps only to the exact current option ID. Names, localization, ordering, prompts, and metadata never determine authorization.

Streaming admission reserves one update slot and a 512-byte payload allowance for a terminal completion or compact error. A post-creation bound, transport, callback, or persistence error is cancelled where possible and terminalized as failed or ambiguous whenever the ledger remains writable. Response reconstruction first validates the complete protocol identity and transition history, exact session/turn ownership, contiguous update sequences, completion uniqueness and stop-reason/state consistency, and one final disconnect; corrupt or conflicting state produces no stdout.

Adapter stderr is never printed or returned as text. The only CLI diagnostic is a compact summary containing presence, bounded byte count, truncation, line count, and SHA-256. Cleanup failure is reported with a stable message and persisted as `cleanup_failed`; raw exceptions, prompts, tool data, native IDs, and stderr content are excluded.

Strict response reconstruction validates cross-record lineage after global identity validation and before transition derivation. Updates and permissions whose session differs from their referenced turn, including otherwise valid references to a second session, are corruption and produce no stdout or new writes. Foreground cancellation cannot bypass cleanup: repeated cancellation waits on the single shielded bounded cleanup task, persists exactly one disconnect and a terminal turn, then re-raises cancellation.

## Observation and control surfaces

Discovery publishes the observation vocabulary and six stable control templates. Preflight, protocol status, and contract discovery are inspect surfaces. Run, load, and resume are `explicit_user` controls and remain disabled in the generic workbench registry until a client supplies concrete identities, prompt/capability requirements, readiness, and confirmation. Rendering or selecting a control never authorizes execution or bypasses command validation, `--confirm`, negotiated capabilities, or permission handling.

All observation commands remain read-only: protocol status, ProjectView status, workbench, and contract discovery do not write state/events, call a provider, inspect tmux, or start `transport_command`.

The `--example` fixture is deterministic and uses only a sanitized fake Agent, fake adapter argv, fake SDK version, and `/example` path. It contains no local path, credential, transcript, provider output, or real installed version.
