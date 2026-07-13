# AgentDeck Phase 3 M2 Project Daemon Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one authoritative on-demand Project Daemon per project so a confirmed frozen Mission continues after the interactive client disconnects, pauses on new authority or ambiguity, and resumes from compact audited state.

**Architecture:** A single Python daemon owns scheduling behind a versioned project-local Unix domain socket. StateStore, append-only events, and ProjectView remain durable truth; clients are observers unless one holds the controller lease. The scheduler advances one validated transition at a time and routes managed Workers through explicit ACP or tmux without silent fallback.

**Tech Stack:** Python 3.12 standard library (`asyncio`, Unix sockets, `fcntl`, `subprocess`, JSON), existing AgentDeck StateStore/contracts/Mission/ACP/tmux primitives, pytest, temporary real subprocesses, conda environment `agentdeck`.

---

## Scope and execution discipline

Execute tasks strictly in order. M2 is delivered as:

- **M2a:** Tasks 1–6 — daemon foundation, IPC, controller lease, CLI, contracts.
- **M2b:** Tasks 7–13 — frozen execution, scheduler, Worker supervision, recovery, background operation, policy, reconnection, migration.
- **M2c:** Task 14 — crash matrix, deterministic acceptance, real Codex/Claude rehearsal, documentation.

For every task:

1. write the listed failing test first;
2. run the exact focused command and capture the expected RED;
3. add only the minimal implementation for that task;
4. run focused and named regression suites;
5. update `HISTORY.md` in the same semantic commit;
6. run `git diff --check` and compile touched modules;
7. commit locally; do not merge, push, install adapters, or change authentication.

Do not add A2A, remote execution, global roaming, notifications, Desktop/IDE clients, Windows IPC, full transcript persistence, automatic install/login, AgentDeck-as-ACP-Agent, a terminal emulator, or native same-session TUI attach.

## File responsibility map

Create:

- `src/agentdeck/daemon/__init__.py` — public daemon types only.
- `src/agentdeck/daemon/protocol.py` — bounded RPC envelopes and handshake.
- `src/agentdeck/daemon/lifecycle.py` — identity, metadata, lock, socket ownership, idle/stop gates.
- `src/agentdeck/daemon/lease.py` — observer/controller lease state machine.
- `src/agentdeck/daemon/server.py` — Unix socket request server and subscriptions.
- `src/agentdeck/daemon/client.py` — connect, on-demand spawn, handshake, request API.
- `src/agentdeck/daemon/scheduler.py` — pure scheduler gate and one-transition runner.
- `src/agentdeck/daemon/recovery.py` — reconciliation classification.
- `src/agentdeck/daemon/supervisor.py` — exact ACP/tmux attempt routing.
- `src/agentdeck/daemon/service.py` — composition root and bounded service loop.
- `docs/contracts/daemon-runtime-schema.md`.
- `docs/contracts/mission-scheduler-schema.md`.
- `docs/contracts/client-session-schema.md`.

Modify without unrelated refactoring:

- `src/agentdeck/models.py` — compact ProjectView daemon/scheduler/recovery fields.
- `src/agentdeck/state.py` — daemon, lease, snapshot, attempt, and recovery atomic records.
- `src/agentdeck/config.py` — bounded `[daemon]` settings.
- `src/agentdeck/contracts.py` — discovery, examples, validators, Workbench metadata.
- `src/agentdeck/cli.py` — daemon commands and foreground client wiring only.
- `src/agentdeck/conversation/session.py` — daemon-backed confirmed Mission handoff and compact reconnection route.
- `src/agentdeck/mission_orchestration.py` — frozen execution snapshot creation and daemon submission boundary.
- `src/agentdeck/workflow.py` — reuse validated handoff/result functions; no second scheduler.
- `README.md`, `README.zh-CN.md`, roadmap, handoff, and `HISTORY.md`.

## Task 1: Define compact daemon lifecycle records and configuration

**Files:**
- Create: `src/agentdeck/daemon/__init__.py`
- Create: `src/agentdeck/daemon/lifecycle.py`
- Modify: `src/agentdeck/models.py`
- Modify: `src/agentdeck/config.py`
- Modify: `src/agentdeck/state.py`
- Test: `tests/test_daemon_lifecycle.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write failing lifecycle/config tests**

Add tests proving defaults, bounds, pure record validation, and zero-write rejection:

```python
def test_daemon_config_defaults_are_project_local() -> None:
    config = load_config(PROJECT_ROOT)
    assert config.daemon.idle_grace_seconds == 600
    assert config.daemon.start_timeout_seconds == 10
    assert config.daemon.max_frame_bytes == 1024 * 1024


def test_daemon_record_rejects_identity_drift_before_state_write(tmp_path: Path) -> None:
    store = initialized_store(tmp_path)
    before = tree_bytes(tmp_path)
    record = build_daemon_record(
        instance_id="dai_1",
        project_root_hash="wrong",
        start_nonce="nonce",
        state="starting",
        created_at=NOW,
    )
    with pytest.raises(ValueError, match="project identity mismatch"):
        store.record_daemon_state(record, expected_project_root_hash="expected")
    assert tree_bytes(tmp_path) == before
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
conda run -n agentdeck pytest tests/test_daemon_lifecycle.py -q
```

Expected: collection/import failure because daemon config and lifecycle builders do not exist.

- [ ] **Step 3: Add immutable configuration and lifecycle builders**

Add bounded types:

```python
@dataclass(frozen=True)
class DaemonConfig:
    idle_grace_seconds: int = 600
    start_timeout_seconds: int = 10
    controller_ttl_seconds: int = 30
    max_frame_bytes: int = 1024 * 1024


DAEMON_STATES = {
    "starting", "ready", "busy", "idle_grace", "stopping", "stopped", "blocked"
}


def build_daemon_record(*, instance_id: str, project_root_hash: str,
                        start_nonce: str, state: str,
                        created_at: str) -> dict[str, object]:
    if state not in DAEMON_STATES:
        raise ValueError("invalid daemon state")
    return {
        "instance_id": instance_id,
        "project_root_hash": project_root_hash,
        "start_nonce_hash": hashlib.sha256(start_nonce.encode()).hexdigest(),
        "state": state,
        "created_at": created_at,
        "updated_at": created_at,
    }
```

Store only compact identity and lifecycle facts. Do not persist PID, socket
absolute path, raw nonce, argv, environment, or home path in StateStore.

- [ ] **Step 4: Add atomic StateStore daemon mutation**

Use the existing state lock and atomic replacement:

```python
def record_daemon_state(self, record: Mapping[str, object], *,
                        expected_project_root_hash: str) -> dict[str, object]:
    validate_daemon_record(record)
    if record["project_root_hash"] != expected_project_root_hash:
        raise ValueError("project identity mismatch")
    with self._protocol_mutation_lock():
        state = self.load()
        state["daemon_runtime"] = dict(record)
        self._atomic_save(state)
    return dict(record)
```

Initialize additive state keys without changing existing project behavior.

- [ ] **Step 5: Run focused and config/state regressions**

```bash
conda run -n agentdeck pytest \
  tests/test_daemon_lifecycle.py \
  tests/test_agent_cli.py \
  tests/test_conversation_state.py \
  tests/test_protocol_runtime.py -q
conda run -n agentdeck python -m compileall -q src/agentdeck/daemon src/agentdeck/models.py src/agentdeck/config.py src/agentdeck/state.py
git diff --check
```

Expected: all pass; no daemon process or socket is created by these tests.

- [ ] **Step 6: Update history and commit**

```bash
git add HISTORY.md src/agentdeck/daemon/__init__.py src/agentdeck/daemon/lifecycle.py \
  src/agentdeck/models.py src/agentdeck/config.py src/agentdeck/state.py \
  tests/test_daemon_lifecycle.py
git commit -m "Add project daemon lifecycle records"
```

## Task 2: Define the bounded versioned local RPC protocol

**Files:**
- Create: `src/agentdeck/daemon/protocol.py`
- Test: `tests/test_daemon_protocol.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write failing protocol tests**

Cover valid handshake, unknown fields, size limit, malformed JSON, incompatible
version, and sanitized errors:

```python
def test_handshake_binds_project_and_protocol() -> None:
    request = decode_request(encode_request(RpcRequest.handshake(
        request_id="req_1",
        project_root_hash="abc",
        client_version="0.1.0",
        protocol_version="daemon-rpc/v1",
    )))
    assert request.method == "handshake"
    assert request.params["project_root_hash"] == "abc"


def test_protocol_rejects_oversized_frame_without_echoing_secret() -> None:
    secret = "TOKEN=do-not-echo"
    with pytest.raises(RpcProtocolError, match="frame exceeds limit") as error:
        decode_frame((secret * 1000).encode(), max_bytes=128)
    assert secret not in str(error.value)
```

- [ ] **Step 2: Run and verify RED**

```bash
conda run -n agentdeck pytest tests/test_daemon_protocol.py -q
```

Expected: import failure for `agentdeck.daemon.protocol`.

- [ ] **Step 3: Implement envelopes and validation**

Use newline-delimited canonical JSON with one object per frame:

```python
DAEMON_RPC_PROTOCOL_VERSION = "daemon-rpc/v1"

@dataclass(frozen=True)
class RpcRequest:
    request_id: str
    method: str
    params: dict[str, JsonValue]

@dataclass(frozen=True)
class RpcResponse:
    request_id: str
    ok: bool
    result: dict[str, JsonValue] | None
    error: dict[str, JsonValue] | None

@dataclass(frozen=True)
class RpcEvent:
    event_id: str
    revision: int
    kind: str
    summary: dict[str, JsonValue]
```

Allow only exact fields, strict JSON values, bounded UTF-8 bytes, unique request
ids per connection, method allowlisting, and generic sanitized diagnostics.

- [ ] **Step 4: Implement fail-closed handshake negotiation**

```python
def negotiate_handshake(request: RpcRequest, *, project_root_hash: str,
                        daemon_version: str,
                        project_view_version: str) -> dict[str, JsonValue]:
    compatible = (
        request.params.get("protocol_version") == DAEMON_RPC_PROTOCOL_VERSION
        and request.params.get("project_root_hash") == project_root_hash
    )
    return {
        "protocol_version": DAEMON_RPC_PROTOCOL_VERSION,
        "daemon_version": daemon_version,
        "project_view_schema_version": project_view_version,
        "compatible": compatible,
        "write_enabled": compatible,
        "capabilities": ["status"] if not compatible else ["status", "mutate", "subscribe"],
    }
```

An incompatible connection may call only handshake and minimal status.

- [ ] **Step 5: Run focused validation**

```bash
conda run -n agentdeck pytest tests/test_daemon_protocol.py -q
conda run -n agentdeck python -m compileall -q src/agentdeck/daemon/protocol.py
git diff --check
```

- [ ] **Step 6: Update history and commit**

```bash
git add HISTORY.md src/agentdeck/daemon/protocol.py tests/test_daemon_protocol.py
git commit -m "Define the project daemon RPC protocol"
```

## Task 3: Enforce one verified daemon instance per project

**Files:**
- Modify: `src/agentdeck/daemon/lifecycle.py`
- Test: `tests/test_daemon_process_lifecycle.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write real-process lifecycle RED tests**

Use temporary projects and helper child processes. Assert two concurrent starters
produce one owner, stale metadata does not kill unrelated processes, and cleanup
removes only matching files:

```python
def test_concurrent_startup_elects_one_owner(tmp_path: Path) -> None:
    project = initialized_project(tmp_path)
    results = run_two_start_contenders(project)
    assert sorted(item.role for item in results) == ["follower", "owner"]
    assert results[0].instance_id == results[1].instance_id


def test_stale_metadata_never_kills_unverified_process(tmp_path: Path) -> None:
    innocent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        write_metadata(tmp_path, pid=innocent.pid, start_nonce_hash="wrong")
        reconcile_endpoint(tmp_path, expected_project_hash="project")
        assert innocent.poll() is None
    finally:
        innocent.terminate()
```

- [ ] **Step 2: Run and verify RED**

```bash
conda run -n agentdeck pytest tests/test_daemon_process_lifecycle.py -q
```

Expected: missing `DaemonEndpoint`/`acquire_daemon_ownership` APIs.

- [ ] **Step 3: Implement project endpoint and ownership proof**

```python
@dataclass(frozen=True)
class DaemonEndpoint:
    metadata_path: Path
    socket_path: Path
    lock_path: Path

def daemon_endpoint(root: Path) -> DaemonEndpoint:
    runtime = root / ".agentdeck" / "runtime"
    return DaemonEndpoint(
        metadata_path=runtime / "daemon.json",
        socket_path=runtime / "daemon.sock",
        lock_path=runtime / "daemon.lock",
    )
```

Acquire `fcntl.LOCK_EX | LOCK_NB`, write metadata by fsync + atomic replace,
verify root hash and nonce through handshake, and unlink only endpoints proven
stale while holding the startup lock.

- [ ] **Step 4: Add idle and stop pure gates**

```python
def daemon_keepalive_reasons(view: Mapping[str, object]) -> tuple[str, ...]:
    reasons = []
    if view["client_count"]:
        reasons.append("clients_connected")
    if view["active_mission_count"]:
        reasons.append("active_mission")
    if view["pending_decision_count"]:
        reasons.append("pending_decision")
    if view["outbox_count"]:
        reasons.append("outbox_pending")
    return tuple(reasons)
```

Normal stop is allowed only when reasons are empty.

- [ ] **Step 5: Run focused process tests repeatedly**

```bash
for run in 1 2 3 4 5; do
  conda run -n agentdeck pytest tests/test_daemon_process_lifecycle.py -q || exit 1
done
```

Do not install a repeat plugin. Then run compileall and diff check.

- [ ] **Step 6: Update history and commit**

```bash
git add HISTORY.md src/agentdeck/daemon/lifecycle.py tests/test_daemon_process_lifecycle.py
git commit -m "Enforce one daemon per project"
```

## Task 4: Add observer/controller lease semantics

**Files:**
- Create: `src/agentdeck/daemon/lease.py`
- Modify: `src/agentdeck/state.py`
- Test: `tests/test_daemon_lease.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write lease state-machine tests**

```python
def test_only_current_generation_can_mutate() -> None:
    lease = grant_controller(client_id="client-a", now=NOW, ttl_seconds=30)
    assert validate_controller(lease, lease_id=lease.lease_id,
                               generation=lease.generation, now=NOW)
    with pytest.raises(LeaseError, match="stale controller lease"):
        validate_controller(lease, lease_id=lease.lease_id,
                            generation=lease.generation - 1, now=NOW)


def test_takeover_requires_exact_preview_and_increments_generation() -> None:
    current = grant_controller(client_id="client-a", now=NOW, ttl_seconds=30)
    preview = preview_takeover(current, requester="client-b", now=NOW)
    taken = confirm_takeover(current, preview, requester="client-b", now=NOW)
    assert taken.client_id == "client-b"
    assert taken.generation == current.generation + 1
```

- [ ] **Step 2: Run and verify RED**

```bash
conda run -n agentdeck pytest tests/test_daemon_lease.py -q
```

- [ ] **Step 3: Implement immutable lease transitions**

Define observer registration, first-controller grant, renew, expiry, release,
takeover preview digest, and confirmed generation change. Use timezone-aware UTC
and reject backward time or non-positive TTL.

- [ ] **Step 4: Persist compact lease audit facts atomically**

```python
def commit_controller_lease(self, transition: LeaseTransition) -> None:
    with self._protocol_mutation_lock():
        state = self.load()
        validate_lease_transition(state.get("controller_lease"), transition)
        state["controller_lease"] = transition.current.summary()
        state["daemon_event_outbox"].append(asdict(transition.audit_event))
        self._atomic_save(state)
```

Do not persist raw client terminal data or connection objects.

- [ ] **Step 5: Run lease and state regressions**

```bash
conda run -n agentdeck pytest \
  tests/test_daemon_lease.py tests/test_conversation_bindings.py \
  tests/test_conversation_state.py tests/test_protocol_runtime.py -q
git diff --check
```

- [ ] **Step 6: Update history and commit**

```bash
git add HISTORY.md src/agentdeck/daemon/lease.py src/agentdeck/state.py tests/test_daemon_lease.py
git commit -m "Add single-controller daemon leases"
```

## Task 5: Implement the Unix socket server and on-demand client

**Files:**
- Create: `src/agentdeck/daemon/server.py`
- Create: `src/agentdeck/daemon/client.py`
- Create: `tests/fixtures/fake_daemon_server.py`
- Test: `tests/test_daemon_ipc.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write failing end-to-end IPC tests**

Cover handshake, minimal incompatible status, request correlation, concurrent
observers, mutation rejection without lease, bounded frame, slow subscriber
backpressure, EOF, and socket cleanup:

```python
@pytest.mark.asyncio
async def test_observer_can_status_but_cannot_mutate(running_daemon) -> None:
    client = await DaemonClient.connect(running_daemon.endpoint)
    assert (await client.request("status", {}))["mode"] == "daemon_status"
    with pytest.raises(DaemonClientError, match="controller lease required"):
        await client.request("mission.pause", {"mission_id": "mis_1"})


@pytest.mark.asyncio
async def test_slow_subscriber_is_disconnected_without_blocking_state() -> None:
    slow = await raw_client_without_reads(running_daemon.endpoint)
    await publish_more_than_queue_bound(running_daemon.server)
    assert running_daemon.server.scheduler_progressed is True
    assert slow.was_closed is True
```

- [ ] **Step 2: Run and verify RED**

```bash
conda run -n agentdeck pytest tests/test_daemon_ipc.py -q
```

- [ ] **Step 3: Implement bounded server connections**

Use `asyncio.start_unix_server`, require handshake as the first frame, cap each
connection's request and event queues, and serialize writes per connection:

```python
class DaemonServer:
    async def _handle(self, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter) -> None:
        connection = ConnectionState(writer=writer, event_queue=asyncio.Queue(128))
        try:
            request = await read_request(reader, self.max_frame_bytes)
            await self._handshake(connection, request)
            await self._serve(connection, reader)
        finally:
            await self._close_connection(connection)
```

- [ ] **Step 4: Implement connect-or-start client**

The client first connects; only a missing/unverified endpoint enters bounded
startup election. Spawn with the current environment's Python executable and a
dedicated internal command, with stdout/stderr redirected to project-local
bounded daemon logs. Never shell-parse user text.

```python
async def connect_or_start(root: Path, config: ProjectConfig) -> DaemonClient:
    try:
        return await DaemonClient.connect_verified(root, config)
    except DaemonUnavailable:
        await start_daemon_if_owner(root, config)
        return await wait_for_verified_daemon(root, config.daemon.start_timeout_seconds)
```

- [ ] **Step 5: Run IPC and lifecycle regressions**

```bash
conda run -n agentdeck pytest \
  tests/test_daemon_protocol.py tests/test_daemon_process_lifecycle.py \
  tests/test_daemon_lease.py tests/test_daemon_ipc.py -q
conda run -n agentdeck python -m compileall -q src/agentdeck/daemon tests/fixtures/fake_daemon_server.py
git diff --check
```

- [ ] **Step 6: Update history and commit**

```bash
git add HISTORY.md src/agentdeck/daemon/server.py src/agentdeck/daemon/client.py \
  tests/fixtures/fake_daemon_server.py tests/test_daemon_ipc.py
git commit -m "Connect clients to the project daemon"
```

## Task 6: Publish M2a CLI, ProjectView, Workbench, and contracts

**Files:**
- Create: `docs/contracts/daemon-runtime-schema.md`
- Create: `docs/contracts/mission-scheduler-schema.md`
- Create: `docs/contracts/client-session-schema.md`
- Modify: `src/agentdeck/contracts.py`
- Modify: `src/agentdeck/models.py`
- Modify: `src/agentdeck/state.py`
- Modify: `src/agentdeck/cli.py`
- Test: `tests/test_daemon_contracts.py`
- Test: `tests/test_daemon_cli.py`
- Modify: `tests/test_contracts.py`
- Modify: `tests/test_agent_cli.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write failing CLI and contract tests**

Assert exact contract fields and read-only behavior:

```python
@pytest.mark.parametrize("name", [
    "daemon-runtime", "mission-scheduler", "client-session"
])
def test_daemon_contract_examples_are_valid(name: str, capsys) -> None:
    assert cli.main(["contract", name, "--example"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["example"]["schema_version"].endswith("/v1")


def test_daemon_status_is_read_only(tmp_project, monkeypatch, capsys) -> None:
    before = tree_bytes(tmp_project)
    assert cli.main(["daemon", "status"]) == 0
    assert tree_bytes(tmp_project) == before
```

- [ ] **Step 2: Run and verify RED**

```bash
conda run -n agentdeck pytest tests/test_daemon_contracts.py tests/test_daemon_cli.py -q
```

- [ ] **Step 3: Add strict contract helpers and discovery**

Define exact response fields and validators for:

```python
DAEMON_RUNTIME_RESPONSE_FIELDS = (
    "schema_version", "mode", "state", "health", "client_count",
    "controller_present", "idle_exit_pending", "protocol_version",
    "compatibility", "blockers", "controls",
)
MISSION_SCHEDULER_RESPONSE_FIELDS = (
    "schema_version", "mode", "state", "active_mission_id",
    "active_step", "next_transition", "blockers", "controls",
)
CLIENT_SESSION_RESPONSE_FIELDS = (
    "schema_version", "mode", "client_id", "role", "lease_generation",
    "compatible", "write_enabled", "blockers", "controls",
)
```

Register all three in `CONTRACT_INDEX_SPECS` and document safety invariants.

- [ ] **Step 4: Add ProjectView/workbench cards and daemon CLI**

Add compact `daemon` and `scheduler` top-level projections and workbench cards.
Add `daemon status/start/stop/logs` plus hidden `_daemon serve`. `status` may
inspect endpoint/metadata but must not start a daemon; bare `agentdeck` may call
`connect_or_start` only in a real TTY. Non-TTY no-subcommand behavior remains
the M1 fail-fast path.

- [ ] **Step 5: Run M2a and broad contract/CLI regression**

```bash
conda run -n agentdeck pytest \
  tests/test_daemon_*.py tests/test_contracts.py tests/test_agent_cli.py \
  tests/test_dashboard.py tests/test_conversation_terminal_ui.py -q
conda run -n agentdeck python -m compileall -q src tests
git diff --check
```

- [ ] **Step 6: Update docs/history and commit M2a**

Update contract index documentation and handoff with the exact M2a boundary:
no background Mission scheduling yet.

```bash
git add HISTORY.md docs/handoff/current-development-state.md docs/contracts \
  src/agentdeck/contracts.py src/agentdeck/models.py src/agentdeck/state.py \
  src/agentdeck/cli.py tests/test_daemon_contracts.py tests/test_daemon_cli.py \
  tests/test_contracts.py tests/test_agent_cli.py
git commit -m "Expose the project daemon control surface"
```

## Task 7: Freeze Mission execution snapshots and attempt identities

**Files:**
- Modify: `src/agentdeck/mission_orchestration.py`
- Modify: `src/agentdeck/state.py`
- Create: `tests/test_daemon_mission_snapshot.py`
- Modify: `tests/test_mission_orchestration.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write snapshot and drift RED tests**

```python
def test_confirmed_mission_freezes_execution_authority(tmp_project) -> None:
    result = confirm_mission_for_daemon(tmp_project, mission_id="mis_1")
    snapshot = result["execution_snapshot"]
    assert snapshot["mission_hash"] == canonical_hash(snapshot["mission"])
    assert snapshot["policy_hash"] == canonical_hash(snapshot["policy"])
    assert [item["transport"] for item in snapshot["workers"]] == ["acp", "tmux"]


def test_snapshot_drift_rejects_before_attempt_write(tmp_project) -> None:
    before = tree_bytes(tmp_project)
    with pytest.raises(MissionRunError, match="frozen execution drift"):
        prepare_attempt(tmp_project, changed_worker="reviewer")
    assert tree_bytes(tmp_project) == before
```

- [ ] **Step 2: Run and verify RED**

```bash
conda run -n agentdeck pytest tests/test_daemon_mission_snapshot.py -q
```

- [ ] **Step 3: Build canonical frozen snapshot**

```python
def build_execution_snapshot(config: ProjectConfig, mission: Mapping[str, object],
                             plan: Mapping[str, object],
                             policy: Mapping[str, object]) -> dict[str, object]:
    body = {
        "mission": compact_frozen_mission(mission, plan),
        "workers": frozen_worker_bindings(config, mission),
        "policy": compact_policy_snapshot(policy),
        "limits": frozen_limits(mission),
    }
    return {**body, "execution_hash": canonical_hash(body)}
```

Exclude raw prompts, commands containing secrets, full Skill/Memory content,
native session ids, and pane history.

- [ ] **Step 4: Add attempt records and atomic preparation**

Attempt fields include `attempt_id`, `mission_id`, `step_id`, `agent_id`,
`configured_transport`, `dispatch_key`, snapshot hash, state, timestamps,
receipt summary, blocker, and terminal reason. `prepared` is committed before an
external dispatch.

- [ ] **Step 5: Run Mission/conversation/workflow regression**

```bash
conda run -n agentdeck pytest \
  tests/test_daemon_mission_snapshot.py tests/test_mission_orchestration.py \
  tests/test_conversation_mission.py tests/test_conversation_session.py \
  tests/test_workflow.py -q
git diff --check
```

- [ ] **Step 6: Update history and commit**

```bash
git add HISTORY.md src/agentdeck/mission_orchestration.py src/agentdeck/state.py \
  tests/test_daemon_mission_snapshot.py tests/test_mission_orchestration.py
git commit -m "Freeze daemon Mission execution authority"
```

## Task 8: Implement the pure one-transition Mission scheduler

**Files:**
- Create: `src/agentdeck/daemon/scheduler.py`
- Test: `tests/test_daemon_scheduler.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write table-driven scheduler RED tests**

Test every state class without state writes or runtime calls:

```python
@pytest.mark.parametrize(("facts", "decision"), [
    (facts(step="pending", worker_ready=True), "prepare_dispatch"),
    (facts(attempt="prepared"), "dispatch_prepared"),
    (facts(attempt="submitted"), "await_worker"),
    (facts(reply="validated", handoff=False), "record_handoff"),
    (facts(permission="pending"), "wait_human"),
    (facts(attempt="ambiguous"), "wait_ambiguity"),
    (facts(all_steps="completed"), "complete_mission"),
])
def test_scheduler_selects_exactly_one_transition(facts, decision) -> None:
    assert schedule_gate(facts).kind == decision
```

Also assert the input mapping remains byte-for-byte unchanged.

- [ ] **Step 2: Run and verify RED**

```bash
conda run -n agentdeck pytest tests/test_daemon_scheduler.py -q
```

- [ ] **Step 3: Implement exhaustive decision types**

```python
@dataclass(frozen=True)
class SchedulerDecision:
    kind: Literal[
        "prepare_dispatch", "dispatch_prepared", "await_worker",
        "validate_reply", "record_handoff", "activate_next",
        "wait_human", "wait_ambiguity", "blocked", "complete_mission", "idle"
    ]
    mission_id: str | None
    step_id: str | None
    attempt_id: str | None
    blocker: str | None
```

Validate complete input facts and fail closed on conflicting active attempts,
snapshot drift, missing lineage, unknown states, or ownership conflict.

- [ ] **Step 4: Add a one-decision runner interface**

```python
class SchedulerEffects(Protocol):
    def apply(self, decision: SchedulerDecision) -> EffectResult: ...

def run_scheduler_once(facts: SchedulerFacts, effects: SchedulerEffects) -> EffectResult:
    decision = schedule_gate(facts)
    return effects.apply(decision)
```

No loops, sleeps, providers, ACP, tmux, or filesystem access belong in the pure
gate.

- [ ] **Step 5: Run scheduler and autonomy regressions**

```bash
conda run -n agentdeck pytest \
  tests/test_daemon_scheduler.py tests/test_autonomy.py \
  tests/test_agent_cli.py -q
git diff --check
```

- [ ] **Step 6: Update history and commit**

```bash
git add HISTORY.md src/agentdeck/daemon/scheduler.py tests/test_daemon_scheduler.py
git commit -m "Select one background Mission transition"
```

## Task 9: Route exact Worker attempts through ACP or tmux

**Files:**
- Create: `src/agentdeck/daemon/supervisor.py`
- Modify: `src/agentdeck/conversation/transports.py`
- Modify: `src/agentdeck/runtime/acp.py`
- Modify: `src/agentdeck/workflow.py`
- Test: `tests/test_daemon_supervisor.py`
- Modify: `tests/fixtures/fake_acp_agent.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write transport/supervision RED tests**

```python
def test_acp_failure_never_calls_tmux_fallback() -> None:
    calls = []
    with pytest.raises(WorkerAttemptError, match="ACP Worker failed"):
        supervise_attempt(acp_attempt(), acp=lambda: fail(calls, "acp"),
                          tmux=lambda: calls.append("tmux"))
    assert calls == ["acp"]


def test_worker_b_starts_only_after_agentdeck_validates_worker_a() -> None:
    ledger = fake_ledger(reply_state="received")
    assert supervisor_gate(ledger).next_worker is None
    ledger = fake_ledger(reply_state="validated", handoff_state="recorded")
    assert supervisor_gate(ledger).next_worker == "reviewer"
```

- [ ] **Step 2: Run and verify RED**

```bash
conda run -n agentdeck pytest tests/test_daemon_supervisor.py -q
```

- [ ] **Step 3: Implement transport-exact attempt supervision**

```python
class WorkerAttemptSupervisor:
    async def execute(self, attempt: AttemptRecord) -> AttemptOutcome:
        route = self.router.describe(self.agent(attempt.agent_id), self.facts(attempt))
        if route.effective_transport != attempt.configured_transport:
            raise WorkerAttemptError("Worker transport drift")
        if route.effective_transport == "acp":
            return await self._execute_acp(attempt, route)
        return await self._execute_tmux(attempt, route)
```

Persist a submitted receipt immediately after the transport confirms admission.
Map formal ACP stop reason or validated tmux reply into the same compact outcome.

- [ ] **Step 4: Reuse compact workflow handoff validation**

Extract or call the existing workflow handoff validator; do not create a second
format. Persist only validated summary, verification, risks, next steps,
artifact path/hash, and trace ids.

- [ ] **Step 5: Run ACP/tmux/workflow regressions**

```bash
conda run -n agentdeck pytest \
  tests/test_daemon_supervisor.py tests/test_conversation_transports.py \
  tests/test_acp_runtime.py tests/test_tmux_runtime.py \
  tests/test_workflow.py -q
git diff --check
```

- [ ] **Step 6: Update history and commit**

```bash
git add HISTORY.md src/agentdeck/daemon/supervisor.py \
  src/agentdeck/conversation/transports.py src/agentdeck/runtime/acp.py \
  src/agentdeck/workflow.py tests/test_daemon_supervisor.py \
  tests/fixtures/fake_acp_agent.py
git commit -m "Supervise background Worker attempts"
```

## Task 10: Reconcile crash state and fail closed on ambiguity

**Files:**
- Create: `src/agentdeck/daemon/recovery.py`
- Modify: `src/agentdeck/state.py`
- Test: `tests/test_daemon_recovery.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write recovery classification RED tests**

```python
@pytest.mark.parametrize(("facts", "classification"), [
    (facts(prepared=True, dispatched=False), "resumable"),
    (facts(dispatched=True, receipt=False), "ambiguous"),
    (facts(receipt=True, reply=False), "resumable"),
    (facts(reply=True, handoff=False), "resumable"),
    (facts(permission="pending"), "waiting_human"),
    (facts(transport="missing"), "blocked"),
    (facts(mission="completed"), "terminal"),
])
def test_recovery_classifies_from_persisted_evidence(facts, classification) -> None:
    assert reconcile_gate(facts).classification == classification
```

Prove reconciliation calls no provider, ACP process, tmux input, or dispatch.

- [ ] **Step 2: Run and verify RED**

```bash
conda run -n agentdeck pytest tests/test_daemon_recovery.py -q
```

- [ ] **Step 3: Implement complete-evidence reconciliation**

```python
@dataclass(frozen=True)
class RecoveryDecision:
    classification: Literal[
        "resumable", "waiting_human", "ambiguous", "blocked", "terminal"
    ]
    reason: str
    mission_id: str
    attempt_id: str | None
    next_transition: str | None
```

Unknown external dispatch outcome is always ambiguous. A reply with complete
validated lineage may resume at handoff creation without re-running the Worker.

- [ ] **Step 4: Persist recovery decision before scheduling**

Commit the classification and audited event atomically. A daemon startup must
reconcile every non-terminal Mission and finish pending outboxes before enabling
the scheduler.

- [ ] **Step 5: Run recovery/state/protocol regression**

```bash
conda run -n agentdeck pytest \
  tests/test_daemon_recovery.py tests/test_daemon_scheduler.py \
  tests/test_conversation_state.py tests/test_protocol_runtime.py -q
git diff --check
```

- [ ] **Step 6: Update history and commit**

```bash
git add HISTORY.md src/agentdeck/daemon/recovery.py src/agentdeck/state.py \
  tests/test_daemon_recovery.py
git commit -m "Reconcile daemon Mission recovery"
```

## Task 11: Compose the authoritative daemon service loop

**Files:**
- Create: `src/agentdeck/daemon/service.py`
- Modify: `src/agentdeck/daemon/server.py`
- Modify: `src/agentdeck/daemon/client.py`
- Modify: `src/agentdeck/cli.py`
- Test: `tests/test_daemon_service.py`
- Test: `tests/test_daemon_background_mission.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write service and disconnect RED tests**

Use real daemon subprocess plus fake Workers:

```python
def test_confirmed_mission_continues_after_client_disconnect(tmp_project) -> None:
    client = start_and_connect(tmp_project)
    mission = client.confirm(fake_two_worker_mission())
    client.close()
    wait_for(lambda: project_view(tmp_project).missions["latest_id"] == mission.id)
    fake_worker_complete(tmp_project, "planner")
    wait_for(lambda: active_worker(tmp_project) == "reviewer")


def test_service_applies_only_one_external_effect_per_iteration(tmp_project) -> None:
    service = service_with_two_ready_steps(tmp_project)
    service.tick()
    assert service.effect_log == ["prepare:step-1"]
```

- [ ] **Step 2: Run and verify RED**

```bash
conda run -n agentdeck pytest \
  tests/test_daemon_service.py tests/test_daemon_background_mission.py -q
```

- [ ] **Step 3: Implement bounded service phases**

```python
class ProjectDaemonService:
    async def run(self) -> None:
        await self.reconcile_all()
        await self.server.start()
        while not self.shutdown_complete:
            await self.flush_safe_outboxes()
            await self.run_one_scheduler_transition()
            await self.evaluate_idle_shutdown()
            await self.wakeup.wait(timeout=self.next_deadline())
```

Use one service-owned mutation/effect queue. Long Worker I/O produces completion
messages back to the queue; it does not mutate state concurrently.

- [ ] **Step 4: Route confirmed Mission submission to daemon**

M1's exact preview consumption remains the authority boundary. After consuming,
the client sends the frozen execution snapshot to the daemon, which validates
the same digest and records scheduling admission. If the daemon is unavailable,
the Mission remains confirmed-but-not-admitted with a visible recovery control;
the client never silently runs the foreground legacy path.

- [ ] **Step 5: Run service, conversation, Mission, and full focused regression**

```bash
conda run -n agentdeck pytest \
  tests/test_daemon_*.py tests/test_conversation_*.py \
  tests/test_mission_orchestration.py tests/test_workflow.py -q
conda run -n agentdeck python -m compileall -q src tests
git diff --check
```

- [ ] **Step 6: Update history and commit**

```bash
git add HISTORY.md src/agentdeck/daemon/service.py src/agentdeck/daemon/server.py \
  src/agentdeck/daemon/client.py src/agentdeck/cli.py \
  tests/test_daemon_service.py tests/test_daemon_background_mission.py
git commit -m "Run confirmed Missions in the project daemon"
```

## Task 12: Enforce permission, takeover, reroute, and shutdown gates

**Files:**
- Modify: `src/agentdeck/daemon/service.py`
- Modify: `src/agentdeck/daemon/supervisor.py`
- Modify: `src/agentdeck/conversation/transports.py`
- Modify: `src/agentdeck/state.py`
- Modify: `src/agentdeck/cli.py`
- Test: `tests/test_daemon_governance.py`
- Modify: `tests/test_protocol_runtime.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write governance RED tests**

```python
def test_new_permission_pauses_without_next_worker_dispatch(tmp_project) -> None:
    run_until_permission(tmp_project, risk="external_network")
    view = project_view(tmp_project)
    assert view.scheduler["state"] == "waiting_human"
    assert dispatch_count(tmp_project, "reviewer") == 0


def test_stop_refuses_active_work_and_force_records_ambiguity(tmp_project) -> None:
    active = running_unknown_outcome(tmp_project)
    assert daemon_stop(tmp_project).blocker == "active Mission"
    preview = daemon_force_stop_preview(tmp_project)
    confirm_force_stop(tmp_project, preview)
    assert attempt(active.attempt_id).state == "ambiguous"
```

Also test explicit reroute preview, takeover safe boundary, human-owned prompt
block, return-control reconciliation, stale preview rejection, and observer
mutation rejection.

- [ ] **Step 2: Run and verify RED**

```bash
conda run -n agentdeck pytest tests/test_daemon_governance.py -q
```

- [ ] **Step 3: Implement three independent gates**

```python
def authorize_effect(effect, *, snapshot, policy, runtime) -> GateResult:
    for gate in (
        frozen_scope_gate(effect, snapshot),
        permission_policy_gate(effect, policy),
        runtime_ownership_gate(effect, runtime),
    ):
        if not gate.allowed:
            return gate
    return GateResult.allowed_result()
```

Never treat client control, ACP recommendation, Worker text, or role context as
permission.

- [ ] **Step 4: Implement exact-bound governance previews**

Takeover, return control, transport reroute, permission decision, Mission pause/
resume/cancel, and force daemon stop all use canonical execution digests,
expiry, consume-once semantics, current generation, and state revalidation.

- [ ] **Step 5: Run governance/ACP/tmux/approval regression**

```bash
conda run -n agentdeck pytest \
  tests/test_daemon_governance.py tests/test_protocol_runtime.py \
  tests/test_conversation_bindings.py tests/test_conversation_transports.py \
  tests/test_agent_cli.py tests/test_tmux_runtime.py tests/test_acp_runtime.py -q
git diff --check
```

- [ ] **Step 6: Update history and commit**

```bash
git add HISTORY.md src/agentdeck/daemon/service.py \
  src/agentdeck/daemon/supervisor.py src/agentdeck/conversation/transports.py \
  src/agentdeck/state.py src/agentdeck/cli.py tests/test_daemon_governance.py \
  tests/test_protocol_runtime.py
git commit -m "Govern background Mission authority"
```

## Task 13: Add deterministic reconnection, migration, and final M2b surfaces

**Files:**
- Modify: `src/agentdeck/conversation/session.py`
- Modify: `src/agentdeck/state.py`
- Modify: `src/agentdeck/contracts.py`
- Modify: `src/agentdeck/cli.py`
- Modify: `docs/contracts/project-view-schema.md`
- Modify: `docs/contracts/workbench-schema.md`
- Test: `tests/test_daemon_reconnection.py`
- Test: `tests/test_daemon_migration.py`
- Modify: `tests/test_conversation_surfaces.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write reconnection/migration RED tests**

```python
def test_reconnection_summary_requires_no_llm(tmp_project, monkeypatch) -> None:
    seed_waiting_permission_mission(tmp_project, completed=4, total=6)
    monkeypatch.setattr(LeaderGateway, "generate_mission",
                        lambda *_: pytest.fail("LLM must not be called"))
    response = reconnect_conversation(tmp_project)
    assert response.kind == "mission_recovery"
    assert response.payload["progress"] == {"completed": 4, "total": 6}
    assert response.payload["decision"]["kind"] == "permission"


def test_old_mission_migration_preview_is_zero_write(tmp_project) -> None:
    seed_m1_state_without_execution_snapshot(tmp_project)
    before = tree_bytes(tmp_project)
    preview = migration_preview(tmp_project)
    assert preview["legacy_missions"][0]["mode"] == "inspect_only"
    assert tree_bytes(tmp_project) == before
```

- [ ] **Step 2: Run and verify RED**

```bash
conda run -n agentdeck pytest \
  tests/test_daemon_reconnection.py tests/test_daemon_migration.py -q
```

- [ ] **Step 3: Implement deterministic recovery card**

Derive completed steps, recent validated results, active/wait reason, exact
decision controls, trace commands, and workspace control solely from ProjectView
and compact ledger facts. Do not persist or reconstruct full transcript.

- [ ] **Step 4: Implement migration preview/confirm**

Read old state, compute exact source hash and additive target changes, make a
project-local backup on confirmed migration, and mark legacy Missions without a
complete frozen snapshot inspect-only. Reconfirmation creates a new snapshot;
it never mutates old history into apparent prior authority.

- [ ] **Step 5: Run M2b and broad surface regression**

```bash
conda run -n agentdeck pytest \
  tests/test_daemon_*.py tests/test_conversation_*.py \
  tests/test_contracts.py tests/test_agent_cli.py tests/test_dashboard.py -q
conda run -n agentdeck python -m compileall -q src tests
git diff --check
```

- [ ] **Step 6: Update docs/history and commit M2b**

```bash
git add HISTORY.md docs/contracts/project-view-schema.md \
  docs/contracts/workbench-schema.md docs/handoff/current-development-state.md \
  src/agentdeck/conversation/session.py src/agentdeck/state.py \
  src/agentdeck/contracts.py src/agentdeck/cli.py \
  tests/test_daemon_reconnection.py tests/test_daemon_migration.py \
  tests/test_conversation_surfaces.py
git commit -m "Recover background Missions on reconnect"
```

## Task 14: Complete crash matrix, documentation, and real M2 acceptance

**Files:**
- Create: `tests/test_daemon_crash_matrix.py`
- Create: `tests/test_daemon_acceptance.py`
- Create after PASS: `docs/validation/2026-07-13-phase3-m2-project-daemon.md`
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/roadmap/product-north-star.md`
- Modify: `docs/roadmap/ultimate-goal-roadmap.md`
- Modify: `docs/handoff/current-development-state.md`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write the deterministic crash matrix**

Parameterize process termination at every required boundary:

```python
@pytest.mark.parametrize(("crash_point", "expected"), [
    ("before_prepare", "resumable"),
    ("after_prepare_before_dispatch", "resumable"),
    ("after_dispatch_before_receipt", "ambiguous"),
    ("after_receipt_before_reply", "resumable"),
    ("after_reply_before_handoff", "resumable"),
    ("after_handoff_before_next_dispatch", "resumable"),
    ("permission_pending", "waiting_human"),
    ("outbox_flush", "resumable"),
    ("shutdown", "interrupted"),
])
def test_crash_recovery_never_repeats_unknown_effect(crash_point, expected, tmp_project):
    result = run_crash_scenario(tmp_project, crash_point)
    assert result.recovery_classification == expected
    assert result.duplicate_dispatches == 0
```

- [ ] **Step 2: Write the deterministic M2 product acceptance**

In one temporary project: start bare client through PTY helper, confirm one
two-Worker Mission once, disconnect the client, complete fake ACP Worker A,
prove AgentDeck records completion/handoff before starting fake tmux Worker B,
pause on permission, reconnect a new controller, decide, complete, and assert
ProjectView/ledger/contracts/events/hashes/file effects agree with no transcript
or secret persistence.

- [ ] **Step 3: Run deterministic acceptance and fix only owning modules**

```bash
conda run -n agentdeck pytest \
  tests/test_daemon_crash_matrix.py tests/test_daemon_acceptance.py -q
```

Record each genuine RED in HISTORY before the minimal fix. Do not weaken an
assertion to accept duplicate or ambiguous external effects.

- [ ] **Step 4: Run focused and full verification**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_daemon_*.py -q
conda run --no-capture-output -n agentdeck pytest -q
conda run -n agentdeck python -m compileall -q src tests
git diff --check
```

Expected: all tests pass with only documented opt-in live skips.

- [ ] **Step 5: Perform the disposable real Codex/Claude rehearsal**

Use only already installed/authenticated adapters. Do not install packages,
change auth, expose credentials, or touch the main user project. The rehearsal
must:

1. initialize a fresh disposable project through bare `agentdeck`;
2. select an explicit ready Leader;
3. create implementation and review steps;
4. confirm the exact Mission once;
5. use at least one real ACP Worker and expose the other exact transport;
6. disconnect the interactive client while work is active;
7. prove the daemon and Worker progress continue;
8. reconnect and render deterministic recovery;
9. exercise one permission/safety pause and exact decision;
10. complete or honestly stop with a sanitized blocker;
11. prove Worker B started only after AgentDeck validated Worker A;
12. compare ProjectView, ledger, artifacts, trace, hashes, contracts, and files.

- [ ] **Step 6: Write PASS evidence only if every gate passes**

The validation report contains commit, component versions, internal AgentDeck
ids, transport identities, lifecycle states, stop reasons, counts, hashes,
commands, crash classifications, and results. It excludes transcript, raw
prompts/tool I/O, credentials, email/token/auth data, environment dumps, native
opaque session ids, and absolute home paths. If a real adapter is unavailable,
record the exact sanitized blocker and do not claim live PASS.

- [ ] **Step 7: Update concise product docs and final handoff**

Document bare `agentdeck` background continuation, reconnect behavior, ACP/tmux
roles, exact safety pauses, and M2 limitations. Keep English/Chinese README
aligned and concise. Mark A2A, remote daemon, global roaming, Workspace Client,
notifications, and full transcript as future work.

- [ ] **Step 8: Final self-review and commit M2c**

Confirm every spec requirement maps to a passing test or evidence line; no M2
out-of-scope code entered the diff; worktree contains no runtime `.agentdeck/`
state; and all prior task commits exist.

```bash
git add HISTORY.md README.md README.zh-CN.md docs/roadmap \
  docs/handoff/current-development-state.md tests/test_daemon_crash_matrix.py \
  tests/test_daemon_acceptance.py \
  docs/validation/2026-07-13-phase3-m2-project-daemon.md
git commit -m "Complete Phase 3 M2 background Missions"
```

## Spec coverage map

| Approved spec requirement | Implementation tasks |
| --- | --- |
| One project daemon, identity, idle lifetime, safe stop | 1, 3, 5, 6, 12 |
| Versioned Unix RPC and incompatible-client fail-closed behavior | 2, 5, 6 |
| Multiple observers and one controller lease | 4, 5, 6, 12 |
| Frozen Mission scope and one-confirmation authority | 7, 8, 11, 12 |
| AgentDeck-mediated Worker completion and compact handoff | 8, 9, 11 |
| Explicit ACP/tmux routing with no silent fallback | 9, 12 |
| Approval, permission, ownership, takeover, and reroute gates | 12 |
| Attempt receipts, reconciliation, ambiguity, and no duplicate unknown effects | 7, 10, 11, 14 |
| Compact ProjectView, contracts, workbench, and deterministic reconnect | 6, 13 |
| Existing-project migration preview and inspect-only legacy Missions | 13 |
| M2a/M2b/M2c acceptance and real Codex/Claude evidence | 6, 13, 14 |
| Out-of-scope enforcement, docs, and handoff | every task review, finalized in 14 |

## Final completion gate

Do not mark the M2 `/goal` complete until:

- all 14 tasks are committed in order;
- M2a, M2b, and M2c focused suites pass;
- the full suite, compileall, and diff check pass after the final semantic fix;
- crash recovery proves zero duplicate unknown external effects;
- client disconnect does not stop a confirmed Mission;
- permission, drift, ownership, and ambiguity pause safely;
- Worker B starts only after AgentDeck validates Worker A and records compact handoff;
- ProjectView, ledger, events, contracts, artifacts, and file effects agree;
- real disposable evidence is honestly PASS or explicitly blocked;
- no A2A, remote daemon, roaming, Workspace Client, notification, full transcript,
  automatic auth/install, Windows IPC, or terminal-emulator scope entered the diff;
- the worktree is clean and no merge/push/PR occurred without new human authority.
