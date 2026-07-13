# AgentDeck Foreground Conversation M1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make bare `agentdeck` open a safe persistent foreground natural-language conversation that can select an API or Agent-CLI Leader, generate an exact-bound Mission preview, and enter the existing governed multi-Agent execution path through ACP or explicit tmux fallback.

**Architecture:** Add a thin `TerminalConversationUI → ConversationSession → ConversationRouter → LeaderGateway` composition layer above the existing Leader-chat, Mission, approval, workflow, ACP, tmux, ledger, ProjectView, and workbench primitives. Conversation lifecycle and confirmation bindings are append-only compact records in the existing state store; full conversational text remains bounded in memory. ACP is the structured control plane, tmux is the visible/debug/takeover and legacy plane, and ProjectView remains authoritative.

**Tech Stack:** Python 3.12 standard library, argparse, dataclasses, JSON/JSONL state, TOML configuration, existing ACP Python SDK integration, tmux backend, pytest, conda environment `agentdeck`.

---

## Scope and file map

Create focused modules instead of extending the already large `cli.py` and `contracts.py` with domain logic:

- `src/agentdeck/conversation/models.py`: immutable conversation records, state names, limits, and pure builders.
- `src/agentdeck/conversation/lifecycle.py`: complete-history validation and append-only transition projection.
- `src/agentdeck/conversation/bindings.py`: canonical preview execution facts, digest, expiry, drift, consume-once checks.
- `src/agentdeck/conversation/router.py`: slash commands, deterministic intents, setup flow, open-ended routing, and exact confirmation classification.
- `src/agentdeck/conversation/leader_gateway.py`: API, ACP-Agent, and explicit CLI Leader capability/readiness/prompt routing with no silent fallback.
- `src/agentdeck/conversation/transports.py`: Worker ACP/tmux route decision and governed ownership/takeover state machine.
- `src/agentdeck/conversation/session.py`: one foreground session, bounded in-memory context, turn cancellation, and orchestration composition.
- `src/agentdeck/conversation/terminal_ui.py`: terminal read/render loop and Ctrl-C/EOF/exit semantics only.
- `src/agentdeck/conversation/__init__.py`: narrow public exports.
- `src/agentdeck/state.py`: locked conversation transaction, event outbox, compact ProjectView projection.
- `src/agentdeck/mission_orchestration.py`: shared validated Mission-candidate-to-preview primitive.
- `src/agentdeck/contracts.py`: three versioned contracts, validators, examples, and contract-index registration.
- `src/agentdeck/config.py`: explicit Leader backend/transport configuration serialization.
- `src/agentdeck/cli.py`: bare-command entry wiring and deterministic contract commands.
- `src/agentdeck/models.py`: ProjectView conversation summary field only.
- `tests/test_conversation_*.py`: focused unit, state, routing, gateway, transport, UI, and end-to-end tests.
- `tests/fixtures/fake_acp_leader.py`: deterministic ACP Leader fixture using the existing ACP test style.
- `docs/contracts/*.md`, `README.md`, `README.zh-CN.md`, `HISTORY.md`, and `docs/handoff/current-development-state.md`: durable public contract and delivery status.

M1 does not add a daemon, global roaming, durable full transcripts, automatic installation/login, native Agent-to-Agent ACP, or a new execution engine.

### Task 1: Freeze conversation domain records and lifecycle laws

**Files:**
- Create: `src/agentdeck/conversation/__init__.py`
- Create: `src/agentdeck/conversation/models.py`
- Create: `src/agentdeck/conversation/lifecycle.py`
- Test: `tests/test_conversation_lifecycle.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write failing lifecycle tests**

Add table-driven tests that construct `conversation`, `turn`, `preview`, and `ownership` transitions and assert legal projection, terminal immutability, unique IDs, known references, one active turn, and one pending preview:

```python
def test_conversation_history_rejects_two_active_turns() -> None:
    records = [conversation_record("c1")]
    transitions = [
        transition("conversation", "c1", None, "created"),
        transition("conversation", "c1", "created", "ready"),
        transition("turn", "t1", None, "created", conversation_id="c1"),
        transition("turn", "t1", "created", "routing", conversation_id="c1"),
        transition("turn", "t2", None, "created", conversation_id="c1"),
    ]
    with pytest.raises(ValueError, match="one active turn"):
        validate_conversation_history(records, transitions)
```

- [ ] **Step 2: Run the lifecycle tests and verify RED**

Run: `conda run -n agentdeck pytest tests/test_conversation_lifecycle.py -q`

Expected: collection fails because `agentdeck.conversation.lifecycle` does not exist.

- [ ] **Step 3: Implement the minimal pure domain API**

Define bounded constants and explicit edges in `models.py` and validate the full history in `lifecycle.py`:

```python
MAX_CONTEXT_TURNS = 24
MAX_CONTEXT_BYTES = 131_072
CONVERSATION_EDGES = {
    "created": {"ready"},
    "ready": {"busy", "waiting_confirmation", "closing"},
    "busy": {"ready", "waiting_confirmation", "closing"},
    "waiting_confirmation": {"ready", "busy", "closing"},
    "closing": {"closed"},
}

def append_validated_transition(
    base_records: dict[str, list[dict[str, object]]],
    transitions: list[dict[str, object]],
    candidate: dict[str, object],
) -> list[dict[str, object]]:
    proposed = [*transitions, candidate]
    validate_conversation_history(base_records, proposed)
    return proposed
```

Use the exact edge sets from the approved spec. Treat `completed`, `blocked`, `failed`, `cancelled`, `ambiguous`, `consumed`, `expired`, `invalidated`, and `closed` as terminal. Validation must be pure and must not touch files, providers, ACP, or tmux.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `conda run -n agentdeck pytest tests/test_conversation_lifecycle.py -q`

Expected: all lifecycle tests pass.

- [ ] **Step 5: Update history and commit**

Record the append-only lifecycle slice in `HISTORY.md`, then run `git diff --check` and commit:

```bash
git add src/agentdeck/conversation tests/test_conversation_lifecycle.py HISTORY.md
git commit -m "Add foreground conversation lifecycle model"
```

### Task 2: Add exact preview bindings and zero-write validation

**Files:**
- Create: `src/agentdeck/conversation/bindings.py`
- Test: `tests/test_conversation_bindings.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write failing digest and consumption tests**

Cover canonical key ordering, current Leader/model, project identity, action identity, plan/Mission hash, expiry, state drift, already-consumed binding, wrong confirmation text, and byte-for-byte unchanged state on rejection:

```python
def test_binding_rejects_project_drift_without_consuming() -> None:
    binding = preview_binding(execution_digest=digest_for(project_hash="before"))
    before = deepcopy(binding)
    with pytest.raises(PreviewBindingError, match="state drift"):
        validate_preview_execution(binding, current_facts(project_hash="after"), now=NOW)
    assert binding == before
```

- [ ] **Step 2: Run binding tests and verify RED**

Run: `conda run -n agentdeck pytest tests/test_conversation_bindings.py -q`

Expected: import fails for the new binding module.

- [ ] **Step 3: Implement canonical execution facts and digest**

Use JSON canonicalization over control-specific facts only:

```python
def execution_digest(facts: Mapping[str, object]) -> str:
    payload = json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def validate_preview_execution(binding, current_facts, *, now):
    if binding["state"] != "pending":
        raise PreviewBindingError("preview is not pending")
    if parse_time(binding["expires_at"]) <= now:
        raise PreviewBindingError("preview expired")
    if not hmac.compare_digest(binding["execution_digest"], execution_digest(current_facts)):
        raise PreviewBindingError("preview state drift")
```

Do not hash the whole state file. Keep confirmation classification separate from authorization; only the common execution handler consumes a validated binding.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `conda run -n agentdeck pytest tests/test_conversation_bindings.py -q`

Expected: all binding tests pass.

- [ ] **Step 5: Update history and commit**

```bash
git add src/agentdeck/conversation/bindings.py tests/test_conversation_bindings.py HISTORY.md
git commit -m "Bind conversation confirmation to exact previews"
```

### Task 3: Persist compact conversation truth through one locked transaction and outbox

**Files:**
- Modify: `src/agentdeck/state.py`
- Modify: `src/agentdeck/models.py`
- Test: `tests/test_conversation_state.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write failing atomicity and recovery tests**

Test one lock-protected commit containing base records, transitions, domain mutation, and pending `EventRecord` outbox entries. Inject validation, state-write, and event-flush failures. Assert validation failures are full-tree zero-write; flush failures leave a recoverable outbox and do not repeat domain mutation.

```python
def test_flush_failure_preserves_committed_outbox_without_repeating_domain_write(tmp_path, monkeypatch):
    store = initialized_store(tmp_path)
    monkeypatch.setattr(store, "append_event", raising_append)
    result = store.commit_conversation_mutation(mutation())
    assert result["outbox_blocked"] is True
    state = store.load()
    assert len(state["conversation_turns"]) == 1
    assert len(state["conversation_event_outbox"]) == 1
```

- [ ] **Step 2: Run state tests and verify RED**

Run: `conda run -n agentdeck pytest tests/test_conversation_state.py -q`

Expected: `commit_conversation_mutation` is missing.

- [ ] **Step 3: Implement state defaults, locked commit, and idempotent flush**

Add defaults for the four compact record arrays and the outbox. Reuse the repository's `fcntl` locking style and atomic temporary-file replacement. Persist outbox `event_id`; flush only IDs absent from `events.jsonl`, then remove delivered entries in a second locked state commit:

```python
def commit_conversation_mutation(self, mutation: ConversationMutation) -> dict[str, object]:
    with self._state_lock():
        current = self.load()
        proposed = mutation.apply_and_validate(copy.deepcopy(current))
        self._atomic_save(proposed)
    return self.flush_conversation_event_outbox()
```

Do not claim cross-file atomicity. Before project initialization, instantiate no `StateStore` that creates layout.

- [ ] **Step 4: Project compact conversation truth into ProjectView**

Add `conversation: dict[str, Any]` to `ProjectView`, containing counts, latest IDs/states, pending binding summary, Leader backend summary, ownership, and blockers—never transcript text, prompt text, credentials, raw stderr, or raw ACP input.

- [ ] **Step 5: Run focused and existing state regressions**

Run:

```bash
conda run -n agentdeck pytest tests/test_conversation_state.py tests/test_protocol_runtime.py tests/test_mission_orchestration.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Update history and commit**

```bash
git add src/agentdeck/state.py src/agentdeck/models.py tests/test_conversation_state.py HISTORY.md
git commit -m "Persist compact conversation lifecycle truth"
```

### Task 4: Publish the three versioned M1 contracts

**Files:**
- Modify: `src/agentdeck/contracts.py`
- Create: `docs/contracts/conversation-runtime-schema.md`
- Create: `docs/contracts/leader-backend-schema.md`
- Create: `docs/contracts/worker-transport-schema.md`
- Modify: `docs/contracts/contract-index-schema.md`
- Test: `tests/test_conversation_contracts.py`
- Modify: `tests/test_contracts.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write failing contract discovery and mutation tests**

Assert the contract index exposes all three names and commands. For each example, mutate every required field, illegal state, unsafe control, transport label, and blocker relationship and assert validator failure.

```python
@pytest.mark.parametrize("name", ["conversation-runtime", "leader-backend", "worker-transport"])
def test_m1_contract_is_discoverable(name: str) -> None:
    item = contract_item(name)
    assert item["command"] == f"agentdeck contract {name}"
```

- [ ] **Step 2: Run contract tests and verify RED**

Run: `conda run -n agentdeck pytest tests/test_conversation_contracts.py tests/test_contracts.py -q`

Expected: the new contracts are absent.

- [ ] **Step 3: Add single-source constants, examples, responses, and validators**

Define `CONVERSATION_RUNTIME_CONTRACT_VERSION = "conversation-runtime/v1"`, `LEADER_BACKEND_CONTRACT_VERSION = "leader-backend/v1"`, and `WORKER_TRANSPORT_CONTRACT_VERSION = "worker-transport/v1"`. Register them in `CONTRACT_INDEX_SPECS`. Validators must reject unknown/missing response fields, unsafe enabled controls, contradictory readiness, silent fallback claims, and ownership/takeover inconsistencies.

- [ ] **Step 4: Document exact schemas and safety semantics**

Each document must state response fields, state enumerations, controls, blockers, redaction, single-writer behavior, and that controls are not authorization tokens.

- [ ] **Step 5: Run contract tests and command smoke**

Run:

```bash
conda run -n agentdeck pytest tests/test_conversation_contracts.py tests/test_contracts.py -q
conda run -n agentdeck agentdeck contract conversation-runtime --example
conda run -n agentdeck agentdeck contract leader-backend --example
conda run -n agentdeck agentdeck contract worker-transport --example
```

Expected: validators report `ok=true`; every command emits one valid JSON document.

- [ ] **Step 6: Update history and commit**

```bash
git add src/agentdeck/contracts.py docs/contracts tests/test_conversation_contracts.py tests/test_contracts.py HISTORY.md
git commit -m "Publish foreground conversation contracts"
```

### Task 5: Extract one shared validated Mission preview primitive

**Files:**
- Modify: `src/agentdeck/mission_orchestration.py`
- Test: `tests/test_mission_orchestration.py`
- Test: `tests/test_conversation_mission.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write failing shared-path tests**

Drive both the legacy provider path and a pre-generated `LeaderMissionCandidate` through the same function. Assert exact ordering: provider schema validation, raw Mission validation, normalization, normalized Mission validation, in-memory card validation, then one state commit. Assert invalid candidates produce no Mission, plan, approval, event, or outbox write.

```python
def test_candidate_path_never_calls_provider(tmp_path):
    provider = ExplodingProvider()
    payload = create_mission_preview_from_candidate(
        config=config(tmp_path), store=store(tmp_path), candidate=valid_candidate()
    )
    assert payload["mode"] == "mission_preview"
    assert provider.calls == 0
```

- [ ] **Step 2: Run tests and verify RED**

Run: `conda run -n agentdeck pytest tests/test_conversation_mission.py tests/test_mission_orchestration.py -q`

Expected: the shared primitive is missing.

- [ ] **Step 3: Implement the candidate type and shared primitive**

Use an immutable candidate matching the existing provider-plan schema:

```python
@dataclass(frozen=True)
class LeaderMissionCandidate:
    provider: str
    model: str
    user_message: str
    plan: dict[str, Any]
    timeout_seconds: int

def create_mission_preview_from_candidate(*, config, store, candidate):
    validate_provider_plan_schema(candidate.plan)
    validate_mission_plan(candidate.plan, selected_agent_ids, candidate.timeout_seconds)
    normalized = normalize_mission_plan_metadata(candidate.plan, len(candidate.plan["steps"]))
    validate_mission_plan(normalized, selected_agent_ids, candidate.timeout_seconds)
    return _commit_validated_mission_preview(...)
```

Refactor existing `create_mission_preview()` so it calls the provider exactly once and delegates to this primitive. Preserve legacy output byte shape where frozen by tests.

- [ ] **Step 4: Run focused regressions and verify GREEN**

Run: `conda run -n agentdeck pytest tests/test_conversation_mission.py tests/test_mission_orchestration.py tests/test_leader_cli.py -q`

Expected: all tests pass; legacy provider call count remains one.

- [ ] **Step 5: Update history and commit**

```bash
git add src/agentdeck/mission_orchestration.py tests/test_conversation_mission.py tests/test_mission_orchestration.py HISTORY.md
git commit -m "Share validated Mission preview creation"
```

### Task 6: Implement explicit LeaderGateway backends with no silent fallback

**Files:**
- Create: `src/agentdeck/conversation/leader_gateway.py`
- Modify: `src/agentdeck/config.py`
- Modify: `src/agentdeck/providers/__init__.py`
- Create: `tests/fixtures/fake_acp_leader.py`
- Create: `tests/test_conversation_leader_gateway.py`
- Modify: `tests/test_provider_openai_compatible.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write failing backend matrix tests**

Cover API Leader, Claude ACP Leader, Hermes foreground ACP new/prompt only, explicit Codex/Claude CLI subprocess fallback, missing configuration, executable absence, SDK/node blockers, timeout, cancellation, multiple JSON documents, 64 KiB ACP frame, 2 MiB/256-fragment total bounds, and adapter disconnect. Assert backend identity and transport never change during a call.

- [ ] **Step 2: Run gateway tests and verify RED**

Run: `conda run -n agentdeck pytest tests/test_conversation_leader_gateway.py -q`

Expected: `LeaderGateway` is missing.

- [ ] **Step 3: Extend explicit Leader configuration without changing defaults**

Parse and serialize optional fields under `[leader]`:

```toml
backend_kind = "api"
transport = "http"
transport_command = []
```

Accepted combinations are `api/http`, `agent_cli/acp`, and `agent_cli/cli_subprocess`. Existing configs derive the current API/CLI behavior without rewriting files. Reject invalid combinations during config load.

- [ ] **Step 4: Implement readiness, capabilities, and bounded prompt routing**

Expose one narrow interface:

```python
class LeaderGateway:
    def describe(self, config: LeaderConfig) -> LeaderBackendStatus: ...
    def generate_mission(self, request: LeaderRequest, cancel: CancellationToken) -> LeaderMissionCandidate: ...
```

API delegates to existing providers. ACP uses the existing `AgentDeckAcpClient` lifecycle and only capabilities proven for the configured adapter. CLI subprocess delegates to the existing bounded provider implementation and labels transport `cli_subprocess`. Never catch one backend error and retry another transport.

- [ ] **Step 5: Run focused tests and regressions**

Run:

```bash
conda run -n agentdeck pytest tests/test_conversation_leader_gateway.py tests/test_provider_openai_compatible.py tests/test_acp_runtime.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Update history and commit**

```bash
git add src/agentdeck/conversation/leader_gateway.py src/agentdeck/config.py src/agentdeck/providers tests/fixtures/fake_acp_leader.py tests/test_conversation_leader_gateway.py tests/test_provider_openai_compatible.py HISTORY.md
git commit -m "Route foreground requests through explicit Leaders"
```

### Task 7: Build deterministic routing, setup preview, slash commands, and exact confirmation

**Files:**
- Create: `src/agentdeck/conversation/router.py`
- Modify: `src/agentdeck/cli.py`
- Create: `tests/test_conversation_router.py`
- Modify: `tests/test_leader_cli.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write failing routing table tests**

Test `/help`, `/leader`, `/model`, `/team`, `/role`, `/status`, `/approvals`, `/trace`, `/takeover`, `/return-control`, and `/quit`; localized deterministic intents; open-ended requests with configured/unconfigured Leader; exact current-preview confirmation; stale/ambiguous confirmation; setup preview outside a project; and zero filesystem writes before confirmed init.

```python
def test_open_request_without_leader_returns_setup_preview_but_status_still_works():
    assert route("/status", context_without_leader()).kind == "deterministic"
    blocked = route("请设计一个功能", context_without_leader())
    assert blocked.kind == "leader_setup_preview"
    assert blocked.may_call_leader is False
```

- [ ] **Step 2: Run router tests and verify RED**

Run: `conda run -n agentdeck pytest tests/test_conversation_router.py -q`

Expected: router module is missing.

- [ ] **Step 3: Implement ordered, side-effect-free classification**

Use an explicit first-match table: exit → help/slash → setup confirmation → pending-preview confirmation/rejection → deterministic governance intent → open-ended Leader request. Return typed route decisions; do not execute providers, state writes, tmux, or init from the classifier.

- [ ] **Step 4: Implement common preview execution handler**

Both natural confirmation and deterministic CLI confirmation call one handler that re-derives facts, validates digest/expiry/state, marks the binding consumed in the same locked mutation as the authorized action, and returns the persisted result. The setup handler rechecks canonical cwd and marker absence, invokes existing project-init semantics as the first write, then records compact lineage. Audit failure returns `initialized_with_audit_blocker` and never rolls init back.

- [ ] **Step 5: Run router and legacy chat regressions**

Run: `conda run -n agentdeck pytest tests/test_conversation_router.py tests/test_leader_cli.py tests/test_agent_cli.py -q`

Expected: all tests pass; `leader chat --message` behavior remains available.

- [ ] **Step 6: Update history and commit**

```bash
git add src/agentdeck/conversation/router.py src/agentdeck/cli.py tests/test_conversation_router.py tests/test_leader_cli.py HISTORY.md
git commit -m "Route foreground natural language safely"
```

### Task 8: Route Workers through ACP or explicit tmux and govern takeover

**Files:**
- Create: `src/agentdeck/conversation/transports.py`
- Modify: `src/agentdeck/runtime/protocol.py`
- Modify: `src/agentdeck/runtime/tmux.py`
- Create: `tests/test_conversation_transports.py`
- Modify: `tests/test_tmux_runtime.py`
- Modify: `tests/test_acp_runtime.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write failing routing and ownership tests**

Assert configured ACP uses ACP, configured tmux uses tmux, ACP failure never silently reroutes, tmux pane loss never implies completion, live mirror is read-only, and takeover is allowed only when ready with no active turn, permission, or workflow step. Test `agentdeck→takeover_pending→human` and `human→return_pending→agentdeck`, including rollback and disconnected blockers.

- [ ] **Step 2: Run transport tests and verify RED**

Run: `conda run -n agentdeck pytest tests/test_conversation_transports.py -q`

Expected: transport router is missing.

- [ ] **Step 3: Implement explicit transport decisions**

```python
@dataclass(frozen=True)
class WorkerRoute:
    agent_id: str
    transport: Literal["acp", "tmux"]
    ready: bool
    blocker: str | None
    fallback_available: bool
    fallback_requires_confirmation: bool
```

The router reads config plus ProjectView/runtime facts and never executes. A separate confirmed dispatcher calls existing ACP or tmux primitives. Do not reinterpret tmux capabilities as ACP capabilities.

- [ ] **Step 4: Implement live mirror and ownership transitions**

Mirror controls may inspect/attach/select a pane but may not send input. Takeover/return controls revalidate all gates before changing ownership. While pending or human-owned, AgentDeck cannot prompt that Worker.

- [ ] **Step 5: Run focused regressions**

Run:

```bash
conda run -n agentdeck pytest tests/test_conversation_transports.py tests/test_tmux_runtime.py tests/test_acp_runtime.py tests/test_dispatch_cli.py tests/test_workflow.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Update history and commit**

```bash
git add src/agentdeck/conversation/transports.py src/agentdeck/runtime tests/test_conversation_transports.py tests/test_tmux_runtime.py tests/test_acp_runtime.py HISTORY.md
git commit -m "Combine ACP control with governed tmux visibility"
```

### Task 9: Compose the bounded ConversationSession

**Files:**
- Create: `src/agentdeck/conversation/session.py`
- Modify: `src/agentdeck/conversation/__init__.py`
- Create: `tests/test_conversation_session.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write failing session orchestration tests**

Cover three natural turns, deterministic routes without Leader calls, Mission generation, exact preview presentation, confirmation, existing governance handoff, cancellation, timeout, disconnect, ambiguity, context eviction at 24 turns/128 KiB, and proof that prompt/response text is absent from state/events.

- [ ] **Step 2: Run session tests and verify RED**

Run: `conda run -n agentdeck pytest tests/test_conversation_session.py -q`

Expected: `ConversationSession` is missing.

- [ ] **Step 3: Implement the composition loop one turn at a time**

```python
class ConversationSession:
    def handle(self, text: str) -> ConversationResponse:
        decision = self.router.classify(text, self.snapshot())
        if decision.kind == "deterministic":
            return self._handle_deterministic(decision)
        if decision.kind == "confirm_preview":
            return self._execute_current_binding(decision)
        if decision.kind == "leader_request":
            candidate = self.leader_gateway.generate_mission(decision.request, self.cancel_token)
            return self._present_mission(candidate)
        return self._render_blocker(decision)
```

The session derives UI state from durable lifecycle projection, retains only bounded in-memory display context, and stores only hashes/counts/IDs/stop reasons. It does not duplicate Mission, approval, workflow, dispatch, or ACP engines.

- [ ] **Step 4: Run session and integration regressions**

Run:

```bash
conda run -n agentdeck pytest tests/test_conversation_session.py tests/test_conversation_mission.py tests/test_mission_orchestration.py tests/test_workflow.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Update history and commit**

```bash
git add src/agentdeck/conversation/session.py src/agentdeck/conversation/__init__.py tests/test_conversation_session.py HISTORY.md
git commit -m "Compose the foreground conversation session"
```

### Task 10: Make bare `agentdeck` the terminal conversation entrypoint

**Files:**
- Create: `src/agentdeck/conversation/terminal_ui.py`
- Modify: `src/agentdeck/cli.py`
- Modify: `src/agentdeck/__main__.py`
- Create: `tests/test_conversation_terminal_ui.py`
- Modify: `tests/test_agent_cli.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write failing TTY/non-TTY and signal tests**

Use injected stdin/stdout/stderr and fake session objects. Assert no-subcommand TTY starts the UI; non-TTY returns 2, empty stdout, one bounded stderr hint, and zero project/provider/runtime writes; `--help` returns 0. Assert active-turn Ctrl-C cancels only the turn, idle first Ctrl-C clears, idle second Ctrl-C exits, `/quit`, `exit`, `退出`, and EOF exit safely.

```python
def test_bare_non_tty_fails_fast_without_constructing_session(monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(cli, "ConversationSession", forbidden_constructor)
    assert cli.main([]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "leader chat --message" in captured.err
```

- [ ] **Step 2: Run terminal tests and verify RED**

Run: `conda run -n agentdeck pytest tests/test_conversation_terminal_ui.py tests/test_agent_cli.py -q`

Expected: bare command still prints argparse help and the new expectations fail.

- [ ] **Step 3: Implement the terminal-only UI loop**

Keep rendering and input handling separate from session logic. Bound every rendered response and diagnostic. Never print a partial JSON contract. Use monotonic time to recognize the idle double-Ctrl-C window; clear the window after normal input.

- [ ] **Step 4: Wire no-subcommand entry safely**

In `main()`, preserve subcommand parsing. When no `func` exists: return deterministic help for explicit `--help`; reject non-TTY before config/project/store construction; otherwise create the foreground UI. Preserve all existing subcommands exactly.

- [ ] **Step 5: Run focused tests and CLI smoke**

Run:

```bash
conda run -n agentdeck pytest tests/test_conversation_terminal_ui.py tests/test_agent_cli.py -q
printf '' | conda run -n agentdeck agentdeck >/tmp/agentdeck-out 2>/tmp/agentdeck-err; test $? -eq 2
test ! -s /tmp/agentdeck-out
conda run -n agentdeck agentdeck --help >/dev/null
```

Expected: tests and help pass; piped bare invocation returns 2 without stdout.

- [ ] **Step 6: Update history and commit**

```bash
git add src/agentdeck/conversation/terminal_ui.py src/agentdeck/cli.py src/agentdeck/__main__.py tests/test_conversation_terminal_ui.py tests/test_agent_cli.py HISTORY.md
git commit -m "Open foreground conversation from bare agentdeck"
```

### Task 11: Integrate ProjectView, workbench, status, and natural-language observation

**Files:**
- Modify: `src/agentdeck/state.py`
- Modify: `src/agentdeck/contracts.py`
- Modify: `src/agentdeck/cli.py`
- Modify: `src/agentdeck/dashboard.py`
- Modify: `docs/contracts/project-view-schema.md`
- Modify: `docs/contracts/workbench-schema.md`
- Modify: `docs/contracts/leader-chat-schema.md`
- Create: `tests/test_conversation_surfaces.py`
- Modify: `tests/test_dashboard.py`
- Modify: `tests/test_leader_cli.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write failing cross-surface truth tests**

Create one persisted conversation with a pending preview and ACP/tmux Worker facts. Compare ProjectView, workbench card, status response, contract response, and deterministic conversation `/status`. Assert IDs, states, counts, backend/transport, ownership, blockers, and controls agree while transcript text is absent.

- [ ] **Step 2: Run surface tests and verify RED**

Run: `conda run -n agentdeck pytest tests/test_conversation_surfaces.py -q`

Expected: conversation cards/fields are absent.

- [ ] **Step 3: Add read-only cards and controls**

Derive `conversation_runtime_card`, `leader_backend_card`, and `worker_transport_card` from ProjectView and the contract helpers. Controls remain inspect-only or explicit-user and carry blockers. Rendering must not call providers, inspect/capture tmux, send input, flush runtime work, or mutate state.

- [ ] **Step 4: Update contract documentation**

Document the added ProjectView/workbench/leader-chat fields and keep `PROJECT_VIEW_SCHEMA_VERSION` as the only ProjectView schema-version source.

- [ ] **Step 5: Run surface regressions**

Run:

```bash
conda run -n agentdeck pytest tests/test_conversation_surfaces.py tests/test_dashboard.py tests/test_leader_cli.py tests/test_contracts.py -q
```

Expected: all tests pass and cross-surface values agree.

- [ ] **Step 6: Update history and commit**

```bash
git add src/agentdeck/state.py src/agentdeck/contracts.py src/agentdeck/cli.py src/agentdeck/dashboard.py docs/contracts tests/test_conversation_surfaces.py tests/test_dashboard.py tests/test_leader_cli.py HISTORY.md
git commit -m "Expose foreground conversation control surfaces"
```

### Task 12: Complete M1 documentation, deterministic acceptance, and real disposable rehearsal

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/roadmap/ultimate-goal-roadmap.md`
- Modify: `docs/handoff/current-development-state.md`
- Modify: `HISTORY.md`
- Create: `tests/test_conversation_acceptance.py`
- Create after PASS only: `docs/validation/2026-07-13-phase3-m1-foreground-conversation.md`

- [ ] **Step 1: Write the failing deterministic M1 acceptance test**

In one temporary directory: enter pre-init setup, confirm exact init, select a fake Leader, complete three turns, create a two-Worker Mission, confirm it, run through existing governance, exercise fake ACP plus explicit tmux facts, inspect status/approval/trace, cancel a turn, and exit. Assert compact ledger/ProjectView/contracts/counts/hashes/file effects agree and no transcript or secret-like data persists.

- [ ] **Step 2: Run acceptance test and verify RED or expose integration gaps**

Run: `conda run -n agentdeck pytest tests/test_conversation_acceptance.py -q`

Expected before final integration corrections: at least one acceptance assertion fails for an unconnected M1 surface; record the exact failing assertion, then make only the minimal integration correction in its owning module.

- [ ] **Step 3: Make acceptance GREEN and update concise product docs**

Document `agentdeck` as the primary interactive entry, keep `agentdeck leader chat --message` as scripting/debugging, explain Leader selection, exact previews, ACP/tmux dual plane, deterministic no-LLM intents, and M1 limitations. Keep README narrative concise and link contracts/specs for details. Keep English and Chinese README aligned.

- [ ] **Step 4: Run fresh focused and full verification**

Run:

```bash
conda run -n agentdeck pytest tests/test_conversation_acceptance.py tests/test_conversation_*.py -q
conda run -n agentdeck pytest -q
conda run -n agentdeck python -m compileall src tests -q
git diff --check
```

Expected: focused tests pass; the full suite passes with only explicitly documented opt-in live skips; compileall and diff check pass.

- [ ] **Step 5: Perform the disposable live rehearsal**

Use a fresh temporary project and only already installed/authenticated adapters. Do not install packages, change global authentication, expose credentials, or write outside the disposable project. Follow the 11 acceptance actions in the approved spec. If a real adapter is unavailable or unready, record the exact sanitized blocker and do not claim PASS.

- [ ] **Step 6: Write sanitized PASS evidence only after all gates pass**

The validation report records commit, versions, internal IDs, backend/transport identity, states, stop reasons, counts, hashes, commands, and outcomes. It excludes transcript text, raw prompts/tool input, credentials, email/token/auth data, environment dumps, native opaque session IDs, and absolute home paths.

- [ ] **Step 7: Final self-review and commit**

Check every M1 requirement against a passing test or evidence item. Confirm no M2/M3 daemon/global-roaming code entered the diff. Then commit:

```bash
git add README.md README.zh-CN.md docs/roadmap/ultimate-goal-roadmap.md docs/handoff/current-development-state.md HISTORY.md tests/test_conversation_acceptance.py docs/validation/2026-07-13-phase3-m1-foreground-conversation.md
git commit -m "Complete Phase 3 M1 foreground conversation"
```

## Execution discipline

- Execute tasks strictly in order; do not begin M2 or M3 during this plan.
- Use `superpowers:test-driven-development` for every behavior change.
- After each task, run its focused tests, update `HISTORY.md`, review against the approved spec, review code quality, and create one local commit.
- Never mutate the user's main-worktree `.omc/` or untracked `AGENTS.md`.
- Never merge, push, open a PR, install adapters, or change authentication without explicit human authorization.
- If implementation reveals a product fork—especially daemon semantics, global roaming, automatic auth/install, AgentDeck acting as an ACP Agent, or native same-session TUI attach—stop and ask the human instead of expanding M1.

## Final M1 completion gate

M1 is complete only when all 12 tasks are committed, focused and full tests pass, deterministic acceptance passes, live evidence is honestly PASS or explicitly blocked, ProjectView/ledger/contracts agree, documentation is synchronized, and the worktree is clean. M2 design begins only after the human reviews this evidence and explicitly approves the next milestone.
