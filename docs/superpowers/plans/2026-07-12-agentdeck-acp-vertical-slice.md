# AgentDeck Phase 2 ACP Vertical Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect AgentDeck to one real ACP v1 Agent through stdio and prove initialize, new/load/resume, prompt streaming, fail-closed permission handling, completion, disconnect, and append-only recovery without changing tmux defaults.

**Architecture:** Use the official `agent-client-protocol` Python SDK as a typed client and launch an explicitly configured adapter argv without a shell. Keep wire handling, pure ACP-to-ledger mapping, Client callbacks, StateStore lifecycle transitions, CLI orchestration, and contracts in separate units; existing tmux flows remain untouched.

**Tech Stack:** Python 3.12, `agent-client-protocol==0.11.0`, asyncio stdio JSON-RPC, Pydantic ACP schema models, standard-library CLI/TOML/JSON, pytest, tmux fallback unchanged.

**Approved design required before execution:** `docs/superpowers/specs/2026-07-12-agentdeck-acp-vertical-slice-design.md`

---

## File map

- Create `src/agentdeck/runtime/acp_mapping.py`: pure capability/update/stop-reason/permission normalization.
- Create `src/agentdeck/runtime/acp_client.py`: ACP Client callbacks with injected update and permission sinks.
- Create `src/agentdeck/runtime/acp.py`: official-SDK subprocess lifecycle and bounded operations.
- Create `tests/fixtures/fake_acp_agent.py`: deterministic stdio ACP Agent for process-level tests.
- Create `tests/test_acp_mapping.py`: pure mapping and bounds tests.
- Create `tests/test_acp_runtime.py`: state machine, callback, subprocess, and failure tests.
- Create `docs/contracts/acp-runtime-schema.md`: public discovery/command contract.
- Create `docs/validation/phase2-claude-agent-acp-vertical-slice.md`: sanitized live evidence, only after the real smoke passes.
- Modify `pyproject.toml`, `environment.yml`: pin the official Python SDK.
- Modify `src/agentdeck/models.py`, `src/agentdeck/config.py`: additive per-Agent transport configuration.
- Modify `src/agentdeck/runtime/protocol.py`, `src/agentdeck/state.py`: append-only transitions and turn kinds.
- Modify `src/agentdeck/contracts.py`, `src/agentdeck/cli.py`: discovery, validators, and explicit foreground commands.
- Modify `docs/contracts/project-view-schema.md`, `docs/contracts/protocol-runtime-schema.md`, `docs/contracts/contract-index-schema.md`: transition projection and ACP contract discovery.
- Modify `tests/test_protocol_runtime.py`, `tests/test_contracts.py`, `tests/test_agent_cli.py`, `tests/test_dispatch_cli.py`, `tests/test_mission_orchestration.py`, `tests/test_tmux_runtime.py`: compatibility and new behavior.
- Modify `README.md`, `HISTORY.md`, `CLAUDE.md`, `AGENT.md`, `docs/handoff/current-development-state.md`: truthful phase status and safety boundaries.

### Task 1: Pin the official SDK and add backward-compatible Agent transport config

**Files:**
- Modify: `pyproject.toml`
- Modify: `environment.yml`
- Modify: `src/agentdeck/models.py`
- Modify: `src/agentdeck/config.py`
- Test: `tests/test_agent_cli.py`
- Test: `tests/test_dispatch_cli.py`
- Test: `tests/test_mission_orchestration.py`

- [ ] **Step 1: Write failing config tests**

Add tests that load an old config and an explicit ACP config:

```python
def test_existing_agent_defaults_to_tmux_transport(tmp_path: Path) -> None:
    write_default_config(tmp_path)
    agent = load_config(tmp_path).agents[0]
    assert agent.transport == "tmux"
    assert agent.transport_command == ()


def test_agent_can_configure_explicit_acp_argv(tmp_path: Path) -> None:
    write_project_config(tmp_path, agent_extra='''
transport = "acp"
transport_command = ["claude-agent-acp", "--hide-claude-auth"]
''')
    agent = load_config(tmp_path).agents[0]
    assert agent.transport == "acp"
    assert agent.transport_command == ("claude-agent-acp", "--hide-claude-auth")
```

Add invalid matrices for unknown transport, string instead of argv list, empty argv element, boolean elements, and `transport="acp"` with an empty command.

- [ ] **Step 2: Run RED**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_agent_cli.py -k 'transport_command or defaults_to_tmux_transport' -q
```

Expected: collection/assertion failures because `AgentSpec` has no transport fields.

- [ ] **Step 3: Add dependencies and additive fields**

Use exact dependency declarations:

```toml
dependencies = ["agent-client-protocol==0.11.0"]
```

```yaml
  - pip:
      - -e .
      - agent-client-protocol==0.11.0
```

Extend the frozen model without changing existing constructor behavior:

```python
@dataclass(frozen=True)
class AgentSpec:
    agent_id: str
    role: str
    provider: str
    command: str
    workspace_mode: str = "shared"
    role_prompt: str = ""
    transport: str = "tmux"
    transport_command: tuple[str, ...] = ()
```

Parse `transport` and `transport_command` with exact-type validation. Do not infer ACP from provider or command name.

- [ ] **Step 4: Prove legacy behavior does not drift**

Add assertions that old config serialization, `dispatch`, Mission effective agents, and tmux spawn still use the old `command` field and never inspect `transport_command` when `transport` is absent.

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_agent_cli.py tests/test_dispatch_cli.py tests/test_mission_orchestration.py tests/test_tmux_runtime.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml environment.yml src/agentdeck/models.py src/agentdeck/config.py tests/test_agent_cli.py tests/test_dispatch_cli.py tests/test_mission_orchestration.py HISTORY.md
git commit -m "Add explicit ACP agent transport configuration"
```

### Task 2: Add pure append-only lifecycle transitions

**Files:**
- Modify: `src/agentdeck/runtime/protocol.py`
- Modify: `src/agentdeck/state.py`
- Test: `tests/test_protocol_runtime.py`

- [ ] **Step 1: Write state-machine RED tests**

Add exact constants and tests:

```python
PROTOCOL_ENTITY_TYPES = ("session", "turn", "permission")
TURN_KINDS = ("prompt", "load_replay")


def test_session_transition_is_append_only(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    session = record_acp_session(store, native_session_id="native-1")
    before = store.state_path.read_bytes()
    transition = store.record_protocol_transition(
        entity_type="session", entity_id=session["session_id"],
        from_state="created", to_state="ready", reason="session_new_completed",
        details={},
    )
    assert transition["transition_id"].startswith("pst_")
    assert store.load()["agent_sessions"] == [session]
    assert store.state_path.read_bytes() != before
```

Cover allowed edges, forbidden edges, stale `from_state`, unknown entity, duplicate transition ID, invalid details, `disconnected -> reconnecting -> ready`, and zero-write rejection including pending outbox bytes.

- [ ] **Step 2: Run RED**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_protocol_runtime.py -k 'transition or turn_kind' -q
```

Expected: failures for missing transition builder/store method and missing `kind`.

- [ ] **Step 3: Implement pure transition validation**

Add `disconnected` to `AGENT_SESSION_STATES`, `kind="prompt"` to `build_turn`, and a builder:

```python
def build_protocol_transition(
    entity_type: str, entity_id: str, from_state: str, to_state: str,
    reason: str | None, details: dict[str, Any],
) -> dict[str, Any]:
    validate_transition_edge(entity_type, from_state, to_state)
    return {
        "transition_id": new_id("pst"), "entity_type": entity_type,
        "entity_id": entity_id, "from_state": from_state, "to_state": to_state,
        "reason": reason, "details": clone_json_value(details), "created_at": utc_now(),
    }
```

Allowed tables must include only edges used by the design. Do not allow terminal turn states to return to streaming or denied permissions to become approved.

- [ ] **Step 4: Persist under the protocol mutation lock**

Add fresh state key `protocol_state_transitions`, exact lineage lookup by entity type, derived-current-state validation, `protocol_state_transition_recorded` compact event, and existing outbox semantics. Never rewrite the base entity.

- [ ] **Step 5: Run protocol regression and commit**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_protocol_runtime.py -q
git diff --check
git add src/agentdeck/runtime/protocol.py src/agentdeck/state.py tests/test_protocol_runtime.py HISTORY.md
git commit -m "Record append-only protocol lifecycle transitions"
```

### Task 3: Project compact lifecycle state through ProjectView

**Files:**
- Modify: `src/agentdeck/models.py`
- Modify: `src/agentdeck/state.py`
- Modify: `src/agentdeck/contracts.py`
- Modify: `docs/contracts/project-view-schema.md`
- Modify: `docs/contracts/protocol-runtime-schema.md`
- Test: `tests/test_protocol_runtime.py`
- Test: `tests/test_contracts.py`

- [ ] **Step 1: Write ProjectView RED tests**

```python
def test_project_view_derives_current_protocol_states(tmp_path: Path) -> None:
    store, session, turn, permission = seeded_transition_lineage(tmp_path)
    view = store.project_view(load_config(tmp_path))
    assert view.agent_sessions["items"][0]["state"] == "disconnected"
    assert view.protocol_turns["items"][0]["state"] == "completed"
    assert view.permission_requests["items"][0]["status"] == "denied"
    assert view.protocol_state_transitions["count"] == 6
    assert "details" not in view.protocol_state_transitions["items"][0]
```

Add full-history validation tests where invalid lineage lies outside the latest-20 window.

- [ ] **Step 2: Run RED**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_protocol_runtime.py tests/test_contracts.py -k 'transition' -q
```

- [ ] **Step 3: Implement derivation and contract validation**

Add `protocol_state_transitions` to ProjectView. Validate the complete transition history in O(n), then project count plus latest 20 compact items. Base summaries expose derived state but never transition details, update payload, permission options, or native credentials.

- [ ] **Step 4: Update protocol-runtime discovery**

Expose transition fields, entity types, allowed state vocabularies, and bounded latest-window rules. Increment neither ProjectView nor protocol-runtime schema version unless the existing compatibility policy requires it; if a required field is added, update the documented v1 contract atomically and prove old state still renders.

- [ ] **Step 5: Verify and commit**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_protocol_runtime.py tests/test_contracts.py -q
git diff --check
git add src/agentdeck/models.py src/agentdeck/state.py src/agentdeck/contracts.py docs/contracts/project-view-schema.md docs/contracts/protocol-runtime-schema.md tests/test_protocol_runtime.py tests/test_contracts.py HISTORY.md
git commit -m "Project protocol lifecycle transitions"
```

### Task 4: Implement pure ACP mapping with strict bounds

**Files:**
- Create: `src/agentdeck/runtime/acp_mapping.py`
- Create: `tests/test_acp_mapping.py`

- [ ] **Step 1: Write mapping RED tests**

```python
def test_agent_message_chunk_maps_to_text() -> None:
    mapped = map_session_update(agent_message("hello", message_id="m1"))
    assert mapped == ("text", {"role": "agent", "message_id": "m1", "content": {"type": "text", "text": "hello"}})


def test_capabilities_are_negotiated_not_assumed() -> None:
    caps = map_agent_capabilities(initialize_response(load=False, resume=False))
    assert caps.structured_sessions is True
    assert caps.streaming_updates is True
    assert caps.permission_requests is True
    assert caps.resume_session is False
    assert caps.observable_terminal is False
```

Add matrices for tool starts/updates, plans, artifacts, unknown discriminators, malformed IDs, unsupported content, stop reasons, 64 KiB message limit, 2 MiB turn budget, and 256-update limit.

- [ ] **Step 2: Run RED**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_acp_mapping.py -q
```

Expected: import failure.

- [ ] **Step 3: Implement only pure functions**

Define:

```python
MAX_ACP_MESSAGE_BYTES = 64 * 1024
MAX_ACP_TURN_PAYLOAD_BYTES = 2 * 1024 * 1024
MAX_ACP_UPDATES_PER_TURN = 256

def map_agent_capabilities(response: InitializeResponse) -> TransportCapabilities:
    resume = response.agent_capabilities.session_capabilities
    return TransportCapabilities(
        structured_sessions=True,
        streaming_updates=True,
        structured_tools=True,
        permission_requests=True,
        resume_session=bool(resume and resume.resume is not None),
        observable_terminal=False,
    )


def map_stop_reason(stop_reason: str) -> tuple[str, str]:
    return STOP_REASON_TO_TURN_STATE.get(stop_reason, ("failed", "unknown_stop_reason"))
```

Implement `map_session_update()` with `isinstance` branches over the SDK's generated update models and implement `summarize_permission()` by copying only tool-call ID, title/kind, bounded target, and conservative risk. Use `model_dump(by_alias=True, exclude_none=True)` only as an input to explicit allowlisted extraction. Never persist `_meta`, raw environment, or arbitrary unknown fields.

- [ ] **Step 4: Verify and commit**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_acp_mapping.py -q
git diff --check
git add src/agentdeck/runtime/acp_mapping.py tests/test_acp_mapping.py HISTORY.md
git commit -m "Map ACP wire facts into the protocol ledger"
```

### Task 5: Implement fail-closed ACP Client callbacks

**Files:**
- Create: `src/agentdeck/runtime/acp_client.py`
- Create: `tests/test_acp_runtime.py`

- [ ] **Step 1: Write callback RED tests**

```python
@pytest.mark.asyncio
async def test_non_tty_permission_is_cancelled() -> None:
    sink = FakeLedgerSink()
    client = AgentDeckAcpClient(sink=sink, decide=non_tty_decider)
    result = await client.request_permission("native-1", tool_call(), permission_options())
    assert result.outcome.outcome == "cancelled"
    assert sink.permissions[-1]["decision"] == "denied"


@pytest.mark.asyncio
async def test_session_update_is_sequenced_once() -> None:
    sink = FakeLedgerSink()
    client = AgentDeckAcpClient(sink=sink, decide=reject_once)
    await client.session_update("native-1", agent_message("a"))
    await client.session_update("native-1", agent_message("b"))
    assert [item.sequence for item in sink.updates] == [0, 1]
```

Cover allow-once, reject-once, disabled always options, unknown option ID, Ctrl-C, timeout, update after completion, wrong session, and concurrent permission requests.

- [ ] **Step 2: Run RED**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_acp_runtime.py -k 'permission or session_update' -q
```

- [ ] **Step 3: Implement injected callbacks**

```python
class AgentDeckAcpClient:
    def __init__(self, sink: AcpLedgerSink, decide: PermissionDecider) -> None:
        self._sink = sink
        self._decide = decide

    async def session_update(self, session_id: str, update: SessionUpdate, **_: Any) -> None:
        kind, payload = map_session_update(update)
        await self._sink.append_update(session_id, kind, payload)

    async def request_permission(
        self, session_id: str, tool_call: ToolCallUpdate,
        options: list[PermissionOption], **_: Any,
    ) -> RequestPermissionResponse:
        pending = await self._sink.append_permission(session_id, tool_call, options)
        decision = await self._decide(pending, options)
        await self._sink.append_permission_decision(pending, decision)
        return decision.to_acp_response()
```

Implement `read_text_file`, `write_text_file`, and all terminal callbacks as explicit unsupported errors because `ClientCapabilities(fs=None, terminal=False)` is advertised. They must never access disk or spawn a terminal.

- [ ] **Step 4: Verify and commit**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_acp_runtime.py -k 'permission or session_update or unsupported_client' -q
git add src/agentdeck/runtime/acp_client.py tests/test_acp_runtime.py HISTORY.md
git commit -m "Bridge ACP updates and permissions fail closed"
```

### Task 6: Build the bounded official-SDK subprocess transport

**Files:**
- Create: `src/agentdeck/runtime/acp.py`
- Create: `tests/fixtures/fake_acp_agent.py`
- Modify: `tests/test_acp_runtime.py`

- [ ] **Step 1: Create a deterministic fake Agent and RED tests**

The fixture must use the official SDK's Agent side and switch scenarios by argv. Add process-level tests:

```python
@pytest.mark.asyncio
async def test_transport_initializes_and_completes_prompt(tmp_path: Path) -> None:
    result = await run_fake_scenario(tmp_path, "stream_end_turn")
    assert result.protocol_version == 1
    assert result.native_session_id == "fake-session-1"
    assert result.stop_reason == "end_turn"
    assert result.disconnect_reason == "clean_exit"


@pytest.mark.asyncio
async def test_transport_never_uses_shell(monkeypatch) -> None:
    seen = capture_create_subprocess_exec(monkeypatch)
    await start_transport((sys.executable, FAKE_AGENT, "stream_end_turn"))
    assert seen.shell is False
```

Add version mismatch, timeout, malformed frame, bounded stderr, EOF-before-response, and cancellation scenarios.

- [ ] **Step 2: Run RED**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_acp_runtime.py -k 'transport_' -q
```

- [ ] **Step 3: Implement transport with the official APIs**

Use the verified SDK surface:

```python
async with spawn_agent_process(
    client_factory, argv[0], *argv[1:], cwd=workspace,
    transport_kwargs={"limit": MAX_ACP_MESSAGE_BYTES},
) as (conn, process):
    initialized = await asyncio.wait_for(
        conn.initialize(
            protocol_version=1,
            client_capabilities=ClientCapabilities(fs=None, terminal=False),
            client_info=Implementation(name="agentdeck", title="AgentDeck", version=VERSION),
        ),
        timeout=request_timeout,
    )
```

Do not pass the whole parent environment explicitly or persist it. Rely on the SDK's bounded default inherited environment, and test that secrets never appear in ledger/output.

- [ ] **Step 4: Implement deterministic shutdown**

Close the connection, wait five seconds, terminate, wait two seconds, then kill only the child process if still alive. EOF during an active prompt returns an ambiguous outcome; it never fabricates `end_turn`.

- [ ] **Step 5: Verify and commit**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_acp_runtime.py -q
git diff --check
git add src/agentdeck/runtime/acp.py tests/fixtures/fake_acp_agent.py tests/test_acp_runtime.py HISTORY.md
git commit -m "Add a bounded ACP stdio client transport"
```

### Task 7: Publish preflight and acp-runtime contract discovery

**Files:**
- Modify: `src/agentdeck/contracts.py`
- Modify: `src/agentdeck/cli.py`
- Create: `docs/contracts/acp-runtime-schema.md`
- Modify: `docs/contracts/contract-index-schema.md`
- Modify: `tests/test_contracts.py`
- Modify: `tests/test_agent_cli.py`

- [ ] **Step 1: Write discovery/preflight RED tests**

```python
def test_acp_preflight_is_read_only(tmp_path: Path, monkeypatch) -> None:
    init_project(tmp_path, acp_agent=True)
    before = snapshot_tree(tmp_path / ".agentdeck")
    payload = run_cli(tmp_path, "protocol", "acp", "preflight", "--agent", "planner")
    assert payload["mode"] == "acp_preflight"
    assert payload["ready"] is True
    assert snapshot_tree(tmp_path / ".agentdeck") == before
    assert no_provider_or_tmux_calls(monkeypatch)
```

Cover missing SDK, missing executable, wrong transport, empty argv, Node <22 for `claude-agent-acp`, unknown agent, and no project creation.

- [ ] **Step 2: Run RED**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_contracts.py tests/test_agent_cli.py -k 'acp_preflight or acp_runtime_contract' -q
```

- [ ] **Step 3: Add `acp-runtime/v1`**

Define exact field tuples and `validate_acp_runtime_contract()`. Register `agentdeck contract acp-runtime [--example]` in `CONTRACT_INDEX_SPECS`. The example uses the fake Agent and contains no real paths, versions, transcript, or credentials.

- [ ] **Step 4: Implement read-only preflight**

Use `StateStore.open_existing`, `importlib.util.find_spec("acp")`, `shutil.which(argv[0])`, and explicit config validation. Do not run `--version`, install packages, write events, or create `.agentdeck`.

- [ ] **Step 5: Verify and commit**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_contracts.py tests/test_agent_cli.py -k 'acp_' -q
git diff --check
git add src/agentdeck/contracts.py src/agentdeck/cli.py docs/contracts/acp-runtime-schema.md docs/contracts/contract-index-schema.md tests/test_contracts.py tests/test_agent_cli.py HISTORY.md
git commit -m "Publish ACP runtime preflight and contract"
```

### Task 8: Wire new-session run and foreground permission UI

**Files:**
- Modify: `src/agentdeck/cli.py`
- Modify: `src/agentdeck/runtime/acp.py`
- Modify: `tests/test_agent_cli.py`
- Modify: `tests/test_acp_runtime.py`

- [ ] **Step 1: Write CLI RED tests**

```python
def test_acp_run_requires_confirm_and_writes_nothing(tmp_path: Path) -> None:
    init_project(tmp_path, acp_agent=True)
    before = snapshot_tree(tmp_path / ".agentdeck")
    result = run_cli_error(tmp_path, "protocol", "acp", "run", "--agent", "planner", "--prompt", "hello")
    assert "--confirm" in result.stderr
    assert snapshot_tree(tmp_path / ".agentdeck") == before


def test_acp_run_prints_one_validated_json_document(tmp_path: Path) -> None:
    result = run_fake_cli(tmp_path, "run", stdin="2\n")
    assert result.json["mode"] == "acp_run"
    assert result.json["turn"]["state"] == "completed"
    assert result.stdout.count("\n{") == 0
```

Add tests for non-TTY cancel, allow-once, reject-once, disabled always options, prompt timeout, Ctrl-C, and validator failure with empty stdout.

- [ ] **Step 2: Run RED**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_agent_cli.py -k 'acp_run' -q
```

- [ ] **Step 3: Implement the exact ledger order**

Initialize first, call `session/new`, then record the AgentSession with its real native ID. Record/transition the prompt turn, persist sequenced updates, persist each permission before asking, persist the decision before returning it, persist stop reason completion, and finally persist disconnection.

- [ ] **Step 4: Implement stderr-only selection**

```python
def foreground_permission_decider(request: PermissionCard, stdin: TextIO, stderr: TextIO) -> Decision:
    if not stdin.isatty():
        return Decision.cancelled("non_interactive")
    # render only bounded labels; accept only enabled allow_once/reject_once option IDs
```

Allow at most three invalid entries and 60 seconds. EOF/Ctrl-C/timeout returns cancelled.

- [ ] **Step 5: Verify and commit**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_agent_cli.py tests/test_acp_runtime.py tests/test_protocol_runtime.py -q
git diff --check
git add src/agentdeck/cli.py src/agentdeck/runtime/acp.py tests/test_agent_cli.py tests/test_acp_runtime.py HISTORY.md
git commit -m "Run one governed ACP prompt turn"
```

### Task 9: Add exact load and resume flows

**Files:**
- Modify: `src/agentdeck/runtime/acp.py`
- Modify: `src/agentdeck/cli.py`
- Modify: `tests/fixtures/fake_acp_agent.py`
- Modify: `tests/test_acp_runtime.py`
- Modify: `tests/test_agent_cli.py`

- [ ] **Step 1: Write load/resume RED tests**

```python
def test_load_requires_load_session_capability(tmp_path: Path) -> None:
    result = run_fake_cli(tmp_path, "load", scenario="no_load_capability")
    assert result.returncode != 0
    assert fake_requests(result) == ["initialize"]


def test_resume_rejects_history_replay_before_response(tmp_path: Path) -> None:
    result = run_fake_cli(tmp_path, "resume", scenario="resume_illegal_replay")
    assert result.json["ok"] is False
    assert result.json["error"] == "unexpected_resume_replay"
```

Cover unknown `ags_`, missing native ID, load ordered replay, load response before completion, resume capability omission, no load fallback, reconnect transitions, new prompt after resume, and clean disconnect.

- [ ] **Step 2: Run RED**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_acp_runtime.py tests/test_agent_cli.py -k 'acp_load or acp_resume or load_session or resume_session' -q
```

- [ ] **Step 3: Implement capability-exact flows**

Use `conn.load_session(cwd, native_session_id, mcp_servers=[])` only after `loadSession is True`. Use `conn.resume_session(native_session_id, cwd, mcp_servers=[])` only after `sessionCapabilities.resume` is present. Never catch unsupported-method errors and try the other method.

- [ ] **Step 4: Verify lineage and idempotency**

Assert load replay uses one `kind=load_replay` turn and monotonic sequences; resume uses no replay turn and retains the original internal/native session mapping.

- [ ] **Step 5: Verify and commit**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_acp_runtime.py tests/test_agent_cli.py tests/test_protocol_runtime.py -q
git diff --check
git add src/agentdeck/runtime/acp.py src/agentdeck/cli.py tests/fixtures/fake_acp_agent.py tests/test_acp_runtime.py tests/test_agent_cli.py HISTORY.md
git commit -m "Load and resume ACP sessions explicitly"
```

### Task 10: Integrate observation surfaces and freeze tmux compatibility

**Files:**
- Modify: `src/agentdeck/contracts.py`
- Modify: `src/agentdeck/cli.py`
- Modify: `docs/contracts/acp-runtime-schema.md`
- Modify: `docs/contracts/workbench-schema.md`
- Modify: `tests/test_contracts.py`
- Modify: `tests/test_agent_cli.py`
- Modify: `tests/test_dispatch_cli.py`
- Modify: `tests/test_mission_orchestration.py`
- Modify: `tests/test_tmux_runtime.py`

- [ ] **Step 1: Write cross-surface RED tests**

Assert `protocol status`, ProjectView, workbench contract discovery, and each ACP command agree on session/turn/update/permission/transition counts and latest IDs. Assert existing tmux command argv, pane readiness, dispatch messages, Mission plans, approvals, and workflow state are byte-for-byte unchanged for old config.

- [ ] **Step 2: Run RED**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_contracts.py tests/test_agent_cli.py tests/test_dispatch_cli.py tests/test_mission_orchestration.py tests/test_tmux_runtime.py -k 'acp or tmux or legacy' -q
```

- [ ] **Step 3: Add only inspect controls**

Expose `agentdeck protocol acp preflight`, `agentdeck protocol status`, and `agentdeck contract acp-runtime` as inspect controls. Run/load/resume controls must remain `explicit_user` and disabled unless their concrete identity/confirmation requirements are present. A control is never an authorization token.

- [ ] **Step 4: Verify complete regression and commit**

```bash
conda run --no-capture-output -n agentdeck pytest -q
conda run --no-capture-output -n agentdeck python -m compileall src tests -q
git diff --check
git add src/agentdeck/contracts.py src/agentdeck/cli.py docs/contracts/acp-runtime-schema.md docs/contracts/workbench-schema.md tests/test_contracts.py tests/test_agent_cli.py tests/test_dispatch_cli.py tests/test_mission_orchestration.py tests/test_tmux_runtime.py HISTORY.md
git commit -m "Integrate ACP runtime observation surfaces"
```

### Task 11: Run the opt-in real Claude Agent acceptance

**Files:**
- Create after success: `docs/validation/phase2-claude-agent-acp-vertical-slice.md`
- Modify: `tests/test_acp_runtime.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Add an opt-in smoke gate**

```python
@pytest.mark.skipif(os.environ.get("AGENTDECK_ACP_LIVE") != "1", reason="explicit opt-in required")
def test_live_claude_agent_vertical_slice(tmp_path: Path) -> None:
    adapter = os.environ["AGENTDECK_ACP_COMMAND"]
    # exercise preflight, run, rejected write permission, load, resume
```

The test must refuse to use `npx --yes`, npm install, pip install, or any auto-download command.

- [ ] **Step 2: Human explicitly installs/authenticates the adapter outside AgentDeck**

Do not automate this step. Record the exact executable path and version after the human confirms setup. If auth fails, stop with a setup blocker and do not mark Phase 2 complete.

- [ ] **Step 3: Run in a disposable project**

```bash
AGENTDECK_ACP_LIVE=1 \
AGENTDECK_ACP_COMMAND="$(command -v claude-agent-acp)" \
conda run --no-capture-output -n agentdeck \
pytest tests/test_acp_runtime.py::test_live_claude_agent_vertical_slice -q -s
```

Expected: initialize/new/prompt/end_turn, reject-once without file creation, clean disconnect, load replay, resume without replay, and second prompt all pass.

- [ ] **Step 4: Write sanitized evidence only after PASS**

The report contains date, AgentDeck commit, ACP protocol version, adapter/package version, Node/Python versions, internal session/turn/permission IDs, exact result states, file non-creation check, and commands. It excludes transcript text, token counts, email, auth files, API keys, environment dumps, raw tool input, and absolute home paths.

- [ ] **Step 5: Commit evidence**

```bash
git add tests/test_acp_runtime.py docs/validation/phase2-claude-agent-acp-vertical-slice.md HISTORY.md
git commit -m "Validate the real Claude ACP vertical slice"
```

### Task 12: Complete Phase 2 documentation and release verification

**Files:**
- Modify: `README.md`
- Modify: `HISTORY.md`
- Modify: `CLAUDE.md`
- Modify: `AGENT.md`
- Modify: `docs/handoff/current-development-state.md`
- Modify: `docs/roadmap/product-north-star.md` only if status wording requires it

- [ ] **Step 1: Update status without overstating scope**

Document that one foreground real ACP vertical slice passed, while tmux remains default and dispatch/Mission/workflow are not yet routed through ACP. Keep daemon, default REPL, multi-agent ACP Mission, global roaming, and Workspace Client explicitly unimplemented.

- [ ] **Step 2: Run contract smoke**

```bash
conda run --no-capture-output -n agentdeck agentdeck contract acp-runtime --example > /tmp/agentdeck-acp-runtime-contract.json
jq -e '.schema_version == "acp-runtime/v1"' /tmp/agentdeck-acp-runtime-contract.json
```

- [ ] **Step 3: Run fresh full verification**

```bash
conda run --no-capture-output -n agentdeck pytest -q
conda run --no-capture-output -n agentdeck python -m compileall src tests -q
git diff --check
git status --short
```

Expected: all tests pass and only intended Phase 2 files are changed.

- [ ] **Step 4: Request independent final review**

Review the entire Phase 2 range against the design, with special focus on version/capability negotiation, no-shell argv, permission fail-closed, append-only transitions, replay/resume distinction, no false completion, no credentials in state, and zero tmux behavior drift. Fix all Critical/Important findings and repeat verification.

- [ ] **Step 5: Commit the release documentation**

```bash
git add README.md HISTORY.md CLAUDE.md AGENT.md docs/handoff/current-development-state.md docs/roadmap/product-north-star.md
git commit -m "Complete the ACP vertical slice phase"
```

## Execution stop condition

Do not start Task 1 until a human explicitly approves both the design and this plan. During implementation, stop rather than expanding scope if the real adapter requires auto-installation, a daemon, client filesystem/terminal capabilities, durable allow-always policy, or routing existing Mission/dispatch through ACP. After Task 12, do not merge or push without a separate human instruction.
