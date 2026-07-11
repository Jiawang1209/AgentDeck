# Protocol-Native Phase 0 Baseline and Phase 1 Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze the current natural-language Mission as a real compatibility baseline, then add transport/session/turn/update/permission domain and discovery contracts without changing the default tmux execution path.

**Architecture:** Keep existing Mission and tmux behavior authoritative while introducing a separate pure protocol domain in `runtime/protocol.py`. Persist protocol records append-only through `StateStore`, expose compact summaries through ProjectView and a read-only `protocol status` command, and publish a versioned `protocol-runtime` contract. No ACP subprocess, daemon, default REPL, migration, or backend switch is implemented in this phase.

**Tech Stack:** Python 3.12 standard library, dataclasses, JSON/JSONL state, argparse CLI, pytest, existing ProjectView/contract helpers, conda environment `agentdeck`.

---

## Scope boundaries

This plan implements only Phase 0 and Phase 1 from the approved V2 design.

Included:

- real two-message Codex/Claude Mission acceptance and durable evidence;
- pure protocol identity and state validators;
- append-only Agent session, turn, update, and permission-request records;
- compact ProjectView summaries;
- read-only protocol runtime status;
- contract discovery, docs, HISTORY, handoff, and tests;
- tmux capability metadata with no runtime behavior change.

Excluded and reserved for separate specs/plans:

- ACP JSON-RPC client or adapter subprocess;
- project daemon and daemon locking;
- default interactive `agentdeck` REPL;
- global project roaming and notifications;
- V2 state migration commands;
- Desktop/Workspace Client;
- replacing tmux dispatch, capture, readiness, or reply extraction.

## File structure

- Create `src/agentdeck/runtime/protocol.py` — pure protocol enums, record builders, validation, compact summaries, and capability identity.
- Modify `src/agentdeck/models.py` — add ProjectView fields only; do not add protocol behavior here.
- Modify `src/agentdeck/state.py` — persist and project protocol records.
- Modify `src/agentdeck/runtime/base.py` — add read-only transport capability declaration to the backend protocol.
- Modify `src/agentdeck/runtime/tmux.py` — declare tmux fallback capabilities; no input/capture lifecycle changes.
- Modify `src/agentdeck/contracts.py` — protocol-runtime discovery/example/validator and contract index entry.
- Modify `src/agentdeck/cli.py` — read-only `protocol status` and `contract protocol-runtime` routes.
- Create `docs/contracts/protocol-runtime-schema.md` — public contract and safety boundary.
- Modify `docs/contracts/project-view-schema.md` — additive protocol summary fields.
- Create `tests/test_protocol_runtime.py` — pure domain, store, status, and safety tests.
- Modify `tests/test_contracts.py` — discovery/example/index/validator tests.
- Modify `tests/test_agent_cli.py` — tmux capability and no-regression CLI tests.
- Create `docs/validation/2026-07-11-natural-language-mission-acceptance.md` — sanitized real Phase 0 evidence.
- Modify `README.md`, `HISTORY.md`, `docs/handoff/current-development-state.md`, `CLAUDE.md`, and `AGENT.md` — user and handoff documentation.

### Task 1: Freeze the real natural-language Mission baseline

**Files:**
- Create: `docs/validation/2026-07-11-natural-language-mission-acceptance.md`
- Modify: `README.md`
- Modify: `HISTORY.md`
- Modify: `docs/handoff/current-development-state.md`
- Modify: `CLAUDE.md`
- Modify: `AGENT.md`
- Test: existing Mission, workflow, runtime, and CLI tests

- [ ] **Step 1: Verify the feature branch before real execution**

Run:

```bash
conda run --no-capture-output -n agentdeck pytest -q
conda run --no-capture-output -n agentdeck python -m compileall src tests -q
git diff --check
```

Expected: all tests pass, compileall emits no error, and diff check is empty.

- [ ] **Step 2: Create a fresh acceptance project**

Run:

```bash
ACCEPTANCE=/Users/liuyue/Desktop/agentdeck-protocol-v2-phase0-acceptance
rm -rf "$ACCEPTANCE"
mkdir -p "$ACCEPTANCE"
git -C "$ACCEPTANCE" init -q
cd "$ACCEPTANCE"
conda run --no-capture-output -n agentdeck agentdeck project init
conda run --no-capture-output -n agentdeck agentdeck leader set-provider --provider codex-cli --model gpt-5.5
conda run --no-capture-output -n agentdeck agentdeck doctor
```

Expected: doctor reports the configured Codex CLI Leader ready. First-run Codex/Claude workspace trust may be completed manually as setup; no Worker task text may be manually entered.

- [ ] **Step 3: Send exactly the two business messages**

Run the first message:

```bash
conda run --no-capture-output -n agentdeck agentdeck leader chat \
  --message '让 Codex 和 Claude 一人一句接龙百家姓，共8轮' \
  > /tmp/agentdeck-phase0-preview.json
jq -e '.mode == "mission_preview" and .mission_preview_card.step_count == 8 and (.mission_preview_card.selected_agents | map(.agent_id)) == ["planner", "reviewer"]' \
  /tmp/agentdeck-phase0-preview.json
MISSION_ID=$(jq -r '.mission_preview_card.mission_id' /tmp/agentdeck-phase0-preview.json)
```

Run the second and final business message:

```bash
conda run --no-capture-output -n agentdeck agentdeck leader chat \
  --message "批准执行 $MISSION_ID" \
  > /tmp/agentdeck-phase0-run.json
```

Expected: no third natural-language message and no manual task input are required.

- [ ] **Step 4: Verify the completed Mission from public surfaces**

Run:

```bash
conda run --no-capture-output -n agentdeck agentdeck mission status --mission-id "$MISSION_ID" > /tmp/agentdeck-phase0-status.json
conda run --no-capture-output -n agentdeck agentdeck status > /tmp/agentdeck-phase0-project-view.json
conda run --no-capture-output -n agentdeck agentdeck workbench > /tmp/agentdeck-phase0-workbench.json
conda run --no-capture-output -n agentdeck agentdeck events --limit 100 > /tmp/agentdeck-phase0-events.json
jq -e '.status == "completed" and .current_step == 8' /tmp/agentdeck-phase0-status.json
jq -e '[.events[] | select(.event_type == "workflow_step_completed")] | length == 8' /tmp/agentdeck-phase0-events.json
jq -e '[.events[] | select(.event_type == "mission_confirmed")] | length == 1' /tmp/agentdeck-phase0-events.json
```

Expected summaries, in order:

```text
赵钱孙李
周吴郑王
冯陈褚卫
蒋沈韩杨
朱秦尤许
何吕施张
孔曹严华
金魏陶姜
```

If a real compatibility defect appears, stop this task, capture evidence, use systematic debugging and TDD, commit the minimal fix with HISTORY, independently review it, then repeat from a new acceptance directory.

- [ ] **Step 5: Write sanitized acceptance evidence**

Create `docs/validation/2026-07-11-natural-language-mission-acceptance.md` with these exact sections:

```markdown
# Natural-Language Mission Acceptance

## Environment
## User interaction: exactly two messages
## Frozen Mission and selected Workers
## Eight-turn transcript
## Public status and ProjectView agreement
## Audit counts and lineage
## First-run trust boundary
## Failed attempts and defects converted to tests
## Cleanup
## Verdict
```

Record CLI versions, sanitized IDs, commands, eight summaries, event counts, and the fact that trust was the only manual setup interaction. Do not include email addresses, credentials, tokens, or full private terminal transcripts.

- [ ] **Step 6: Update product and handoff docs**

Add a short Phase 0 acceptance link to README. Mark Phase 0 complete in the handoff only after the real run passes. Update HISTORY, CLAUDE, and AGENT with the durable path, two-message constraint, and no-manual-Worker-input rule.

- [ ] **Step 7: Run Phase 0 regression and commit**

Run:

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_mission.py tests/test_mission_orchestration.py tests/test_workflow.py tests/test_runtime_readiness.py -q
conda run --no-capture-output -n agentdeck pytest -q
git diff --check
```

Expected: focused and full suites pass.

Commit:

```bash
git add README.md HISTORY.md CLAUDE.md AGENT.md docs/handoff/current-development-state.md docs/validation/2026-07-11-natural-language-mission-acceptance.md
git commit -m "Validate the natural-language Mission baseline"
```

### Task 2: Add pure protocol runtime domain types

**Files:**
- Create: `src/agentdeck/runtime/protocol.py`
- Create: `tests/test_protocol_runtime.py`

- [ ] **Step 1: Write failing domain tests**

Create tests that import the not-yet-existing module:

```python
from agentdeck.runtime.protocol import (
    AGENT_SESSION_STATES,
    PERMISSION_STATES,
    TURN_STATES,
    UPDATE_KINDS,
    TransportCapabilities,
    build_agent_session,
    build_permission_request,
    build_transport_update,
    build_turn,
)


def test_build_agent_session_uses_session_identity_not_pane_identity() -> None:
    record = build_agent_session(
        agent_id="planner",
        provider="codex",
        transport="tmux",
        native_session_id=None,
        workspace="/tmp/project",
        capabilities=TransportCapabilities.tmux_fallback(),
    )
    assert record["session_id"].startswith("ags_")
    assert record["state"] == "created"
    assert record["observation_bindings"] == []
    assert "pane_id" not in record


def test_build_turn_rejects_unknown_session() -> None:
    with pytest.raises(ValueError, match="session_id must start with ags_"):
        build_turn(session_id="pane-%1", message_id="msg_1")


def test_permission_request_starts_pending_and_cannot_self_authorize() -> None:
    request = build_permission_request(
        session_id="ags_123",
        turn_id="trn_123",
        tool_name="write_file",
        target="/tmp/project/file.py",
        risk="write",
    )
    assert request["status"] == "pending"
    assert request["decision"] is None
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_protocol_runtime.py -q
```

Expected: collection fails with `ModuleNotFoundError: agentdeck.runtime.protocol`.

- [ ] **Step 3: Implement the pure protocol module**

Create constants and frozen capabilities:

```python
AGENT_SESSION_STATES = ("created", "connecting", "ready", "busy", "reconnecting", "stopped", "failed")
TURN_STATES = ("created", "submitted", "streaming", "waiting_permission", "completed", "blocked", "failed", "ambiguous")
UPDATE_KINDS = ("progress", "text", "tool_call", "tool_result", "permission_request", "artifact", "completion", "error")
PERMISSION_STATES = ("pending", "approved", "denied", "expired")


@dataclass(frozen=True)
class TransportCapabilities:
    structured_sessions: bool
    streaming_updates: bool
    structured_tools: bool
    permission_requests: bool
    resume_session: bool
    observable_terminal: bool

    @classmethod
    def tmux_fallback(cls) -> "TransportCapabilities":
        return cls(False, False, False, False, False, True)

    def summary(self) -> dict[str, bool]:
        return asdict(self)
```

Implement builders with strict non-empty string validation, `new_id("ags")`, `new_id("trn")`, `new_id("upd")`, `new_id("prm")`, and `utc_now()`. Builders return JSON-serializable dictionaries and never read state, call providers, inspect tmux, or grant permission.

- [ ] **Step 4: Add invalid-input matrices**

Parametrize empty identities, boolean-as-string impostors, unknown states/kinds, missing risk, and permission decisions supplied at creation. Assert `ValueError` and no mutable default sharing between records.

- [ ] **Step 5: Run tests and commit**

Run:

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_protocol_runtime.py -q
```

Expected: all protocol domain tests pass.

Commit:

```bash
git add src/agentdeck/runtime/protocol.py tests/test_protocol_runtime.py HISTORY.md
git commit -m "Add protocol runtime domain records"
```

### Task 3: Persist append-only protocol records

**Files:**
- Modify: `src/agentdeck/state.py`
- Modify: `tests/test_protocol_runtime.py`

- [ ] **Step 1: Write failing StateStore tests**

Add tests asserting a fresh state has the four collections and that record methods append exact domain records:

```python
def test_state_store_records_protocol_lineage_append_only(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    session = store.record_agent_session(
        agent_id="planner",
        provider="codex",
        transport="tmux",
        native_session_id=None,
        workspace=str(tmp_path),
        capabilities=TransportCapabilities.tmux_fallback(),
    )
    turn = store.record_protocol_turn(session_id=session["session_id"], message_id="msg_123")
    update = store.record_transport_update(
        session_id=session["session_id"], turn_id=turn["turn_id"], sequence=1,
        kind="progress", payload={"message": "working"},
    )
    permission = store.record_permission_request(
        session_id=session["session_id"], turn_id=turn["turn_id"],
        tool_name="write_file", target="src/app.py", risk="write",
    )
    state = store.load()
    assert state["agent_sessions"] == [session]
    assert state["protocol_turns"] == [turn]
    assert state["transport_updates"] == [update]
    assert state["permission_requests"] == [permission]
```

Add rejection tests for unknown session, unknown turn, duplicate `(turn_id, sequence)`, and a permission request whose turn belongs to another session. Capture `state.json` bytes before each rejected call and assert byte-for-byte equality afterward.

- [ ] **Step 2: Run the focused RED**

Run:

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_protocol_runtime.py -q
```

Expected: failures report missing state keys and `StateStore` methods.

- [ ] **Step 3: Add empty collections and record methods**

Extend fresh state with:

```python
"agent_sessions": [],
"protocol_turns": [],
"transport_updates": [],
"permission_requests": [],
```

Each record method must load, validate referenced identities before mutation, build through `runtime.protocol`, append once, save once, and append one audit event only after save succeeds:

```python
self.append_event(EventRecord.create("agent_session_recorded", {
    "session_id": record["session_id"],
    "agent_id": record["agent_id"],
    "transport": record["transport"],
}))
```

Use corresponding event names `protocol_turn_recorded`, `transport_update_recorded`, and `permission_request_recorded`. Event payloads remain compact and never include streamed text or permission secrets.

- [ ] **Step 4: Add deterministic lookup helpers**

Implement `agent_session_by_id`, `protocol_turn_by_id`, and list helpers. Unknown identity raises `KeyError`; duplicate sessions raise `ValueError("duplicate agent session identity")` and duplicate turns raise `ValueError("duplicate protocol turn identity")` rather than silently selecting one.

- [ ] **Step 5: Run tests and commit**

Run:

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_protocol_runtime.py tests/test_agent_cli.py -q
```

Expected: all focused tests pass.

Commit:

```bash
git add src/agentdeck/state.py tests/test_protocol_runtime.py HISTORY.md
git commit -m "Persist protocol runtime lineage"
```

### Task 4: Expose compact protocol facts through ProjectView

**Files:**
- Modify: `src/agentdeck/models.py`
- Modify: `src/agentdeck/state.py`
- Modify: `docs/contracts/project-view-schema.md`
- Modify: `tests/test_protocol_runtime.py`
- Modify: `tests/test_agent_cli.py`

- [ ] **Step 1: Write failing ProjectView tests**

Record one session, turn, update, and permission request, then assert:

```python
view = asdict(store.project_view(config))
assert view["agent_sessions"]["count"] == 1
assert view["agent_sessions"]["items"][0]["session_id"] == session["session_id"]
assert view["protocol_turns"]["by_state"] == {"created": 1}
assert view["transport_updates"]["items"][0] == {
    "update_id": update["update_id"],
    "session_id": session["session_id"],
    "turn_id": turn["turn_id"],
    "sequence": 1,
    "kind": "progress",
    "created_at": update["created_at"],
}
assert "payload" not in view["transport_updates"]["items"][0]
assert view["permission_requests"]["pending_count"] == 1
```

Fresh projects must expose four empty summaries, not omit fields or return `null`.

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_protocol_runtime.py tests/test_agent_cli.py -q
```

Expected: ProjectView fields are missing.

- [ ] **Step 3: Add additive ProjectView fields**

Add default dict fields after `agents`-related core fields:

```python
agent_sessions: dict[str, Any] = field(default_factory=dict)
protocol_turns: dict[str, Any] = field(default_factory=dict)
transport_updates: dict[str, Any] = field(default_factory=dict)
permission_requests: dict[str, Any] = field(default_factory=dict)
```

Do not change `PROJECT_VIEW_SCHEMA_VERSION` in this slice; the change is additive and must be documented as such.

- [ ] **Step 4: Implement bounded compact summary helpers**

StateStore summaries expose `count`, stable `by_state` or `by_kind`, and at most the latest 20 items. Session summaries include identity, agent, provider, transport, state, capability summary, native-session presence as a boolean, workspace, and timestamps. They exclude secrets and adapter-private payload. Update summaries exclude `payload` completely.

- [ ] **Step 5: Update ProjectView schema documentation and tests**

Document field shapes, the 20-item bound, additive-v1 decision, and read-only semantics. Add contract rejection tests for missing top-level protocol fields only if the existing ProjectView validator treats all documented fields as mandatory; otherwise document them as additive fields with empty defaults.

- [ ] **Step 6: Run tests and commit**

Run:

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_protocol_runtime.py tests/test_agent_cli.py tests/test_contracts.py -q
```

Expected: all focused tests pass.

Commit:

```bash
git add src/agentdeck/models.py src/agentdeck/state.py tests/test_protocol_runtime.py tests/test_agent_cli.py tests/test_contracts.py docs/contracts/project-view-schema.md HISTORY.md
git commit -m "Expose protocol lineage in ProjectView"
```

### Task 5: Publish the protocol-runtime contract

**Files:**
- Create: `docs/contracts/protocol-runtime-schema.md`
- Modify: `src/agentdeck/contracts.py`
- Modify: `tests/test_contracts.py`

- [ ] **Step 1: Write failing discovery and validator tests**

Add tests for:

```python
payload = protocol_runtime_contract_response(contract_path, include_example=True)
assert payload["contract_version"] == "protocol-runtime/v1"
assert payload["status_command"] == "agentdeck protocol status"
assert payload["example_protocol_runtime"]["mode"] == "protocol_runtime_status"
assert validate_protocol_runtime_contract(payload["example_protocol_runtime"])["valid"] is True
```

Mutation tests delete every required response field, replace counts with booleans, introduce an unsupported transport, include an update `payload`, set a permission decision while status remains pending, and mismatch turn/session lineage. Each mutation must return `valid=False` with a stable error.

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_contracts.py -k protocol_runtime -q
```

Expected: imports or assertions fail because the contract does not exist.

- [ ] **Step 3: Add contract constants and example**

Define:

```python
PROTOCOL_RUNTIME_CONTRACT_VERSION = "protocol-runtime/v1"
PROTOCOL_RUNTIME_RESPONSE_FIELDS = (
    "mode", "contract_version", "project", "runtime_backend",
    "agent_sessions", "protocol_turns", "transport_updates",
    "permission_requests", "controls",
)
```

The example contains one tmux fallback session and empty turns/updates/permissions. Controls are inspect-only: `agentdeck protocol status`, `agentdeck status`, and `agentdeck contract protocol-runtime`.

- [ ] **Step 4: Implement strict validation and discovery**

The validator checks exact top-level fields, count/list agreement, compact item fields, supported states/kinds, non-negative integer sequences excluding booleans, lineage, no raw update payload, pending permission semantics, and inspect-only controls. Validate the example before returning it.

Add `protocol-runtime` to `CONTRACT_INDEX_SPECS` with `docs/contracts/protocol-runtime-schema.md`.

- [ ] **Step 5: Write the contract document**

Document:

- response and item fields;
- state/kind enums;
- capability fields;
- compact/redacted boundaries;
- read-only controls;
- no provider, tmux, permission, or mutation side effects;
- Phase 1 limitation: records exist for contract and test use but current tmux dispatch does not yet emit them automatically.

- [ ] **Step 6: Run tests and commit**

Run:

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_contracts.py -k 'protocol_runtime or contract_index' -q
```

Expected: focused contract tests pass.

Commit:

```bash
git add src/agentdeck/contracts.py tests/test_contracts.py docs/contracts/protocol-runtime-schema.md HISTORY.md
git commit -m "Publish the protocol runtime contract"
```

### Task 6: Add read-only protocol status CLI

**Files:**
- Modify: `src/agentdeck/cli.py`
- Modify: `tests/test_protocol_runtime.py`
- Modify: `tests/test_agent_cli.py`

- [ ] **Step 1: Write failing CLI tests**

Add a fresh-project test and a populated-project test:

```python
exit_code = cli.main(["protocol", "status"])
payload = json.loads(capsys.readouterr().out)
assert exit_code == 0
assert payload["mode"] == "protocol_runtime_status"
assert payload["agent_sessions"]["count"] == 0
assert payload["controls"][0]["command"] == "agentdeck protocol status"
```

Snapshot `state.json`, `events.jsonl`, and the project tree before/after. Assert byte-for-byte and path-set equality. Monkeypatch provider and tmux entrypoints to fail if called.

Add `contract protocol-runtime --example` CLI coverage and verify the output passes `validate_protocol_runtime_contract` for the embedded example.

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_protocol_runtime.py tests/test_agent_cli.py -k protocol -q
```

Expected: argparse rejects `protocol` and `protocol-runtime`.

- [ ] **Step 3: Implement payload composition**

Add a helper that obtains one validated ProjectView and returns:

```python
{
    "mode": "protocol_runtime_status",
    "contract_version": PROTOCOL_RUNTIME_CONTRACT_VERSION,
    "project": view.project,
    "runtime_backend": view.runtime_backend,
    "agent_sessions": view.agent_sessions,
    "protocol_turns": view.protocol_turns,
    "transport_updates": view.transport_updates,
    "permission_requests": view.permission_requests,
    "controls": protocol_runtime_status_controls(),
}
```

Validate before printing. On validation failure, print no partial JSON and return non-zero with a concise stderr message.

- [ ] **Step 4: Wire argparse routes**

Add:

```text
agentdeck protocol status
agentdeck contract protocol-runtime
agentdeck contract protocol-runtime --example
```

Unknown protocol subcommands remain argparse errors. The status route is read-only and must not create a project.

- [ ] **Step 5: Run tests and commit**

Run:

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_protocol_runtime.py tests/test_agent_cli.py tests/test_contracts.py -q
```

Expected: all focused CLI and contract tests pass.

Commit:

```bash
git add src/agentdeck/cli.py src/agentdeck/contracts.py tests/test_protocol_runtime.py tests/test_agent_cli.py tests/test_contracts.py HISTORY.md
git commit -m "Expose read-only protocol runtime status"
```

### Task 7: Declare tmux fallback transport capabilities

**Files:**
- Modify: `src/agentdeck/runtime/base.py`
- Modify: `src/agentdeck/runtime/tmux.py`
- Modify: `src/agentdeck/contracts.py`
- Modify: `tests/test_tmux_runtime.py`
- Modify: `tests/test_agent_cli.py`

- [ ] **Step 1: Write failing capability tests**

Add:

```python
def test_tmux_backend_declares_fallback_capabilities() -> None:
    capabilities = TmuxBackend().capabilities()
    assert capabilities == TransportCapabilities.tmux_fallback()
    assert capabilities.observable_terminal is True
    assert capabilities.structured_sessions is False
    assert capabilities.permission_requests is False
```

Add a doctor/agent-runtime contract test proving these fields are informational and do not change readiness, pane creation, send, capture, or approval output.

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_tmux_runtime.py tests/test_agent_cli.py -k capabilities -q
```

Expected: `TmuxBackend` has no `capabilities` method.

- [ ] **Step 3: Extend the backend protocol and tmux implementation**

In `RuntimeBackend` add:

```python
def capabilities(self) -> TransportCapabilities:
    raise NotImplementedError
```

In `TmuxBackend` return `TransportCapabilities.tmux_fallback()`. Do not alter any existing tmux command, delay, buffer, readiness, pane, or dispatch method.

- [ ] **Step 4: Expose capability metadata in agent-runtime discovery**

Add `transport_capability_fields` and `tmux_fallback_capabilities` to `agentdeck contract agent-runtime`. Do not add them to authorization controls and do not mark tmux as ACP compatible.

- [ ] **Step 5: Run regression and commit**

Run:

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_tmux_runtime.py tests/test_runtime_readiness.py tests/test_agent_cli.py tests/test_contracts.py -q
```

Expected: focused runtime suite passes with no change to existing tmux command assertions.

Commit:

```bash
git add src/agentdeck/runtime/base.py src/agentdeck/runtime/tmux.py src/agentdeck/contracts.py tests/test_tmux_runtime.py tests/test_agent_cli.py tests/test_contracts.py HISTORY.md
git commit -m "Declare tmux fallback transport capabilities"
```

### Task 8: Complete Phase 1 documentation and release verification

**Files:**
- Modify: `README.md`
- Modify: `HISTORY.md`
- Modify: `docs/handoff/current-development-state.md`
- Modify: `docs/contracts/contract-index-schema.md`
- Modify: `CLAUDE.md`
- Modify: `AGENT.md`

- [ ] **Step 1: Update documentation without overloading README**

README receives only one short current-status sentence and a link to the protocol-runtime contract. Put field-level behavior in `docs/contracts/protocol-runtime-schema.md`; put implementation constraints in CLAUDE/AGENT; put verification evidence in HISTORY and handoff.

The handoff must state:

- Phase 0 baseline ID/path and verdict;
- Phase 1 records and commands implemented;
- tmux remains the active default backend;
- records are not yet automatically emitted by tmux dispatch;
- next product fork is Phase 2 ACP vertical slice and requires its own approved spec/plan.

- [ ] **Step 2: Run contract examples**

Run:

```bash
conda run --no-capture-output -n agentdeck agentdeck contract protocol-runtime --example > /tmp/agentdeck-protocol-runtime-contract.json
conda run --no-capture-output -n agentdeck agentdeck protocol status > /tmp/agentdeck-protocol-runtime-status.json
jq -e '.example_protocol_runtime.mode == "protocol_runtime_status"' /tmp/agentdeck-protocol-runtime-contract.json
jq -e '.mode == "protocol_runtime_status"' /tmp/agentdeck-protocol-runtime-status.json
```

Expected: all jq checks return zero.

- [ ] **Step 3: Run full verification**

Run:

```bash
conda run --no-capture-output -n agentdeck pytest -q
conda run --no-capture-output -n agentdeck python -m compileall src tests -q
git diff --check
git status --short
```

Expected: the full suite passes, compileall and diff check are clean, and status shows only intended Phase 1 changes.

- [ ] **Step 4: Request independent review**

Review against:

- `docs/roadmap/product-north-star.md`;
- `docs/superpowers/specs/2026-07-11-agentdeck-protocol-native-v2-design.md`;
- this implementation plan;
- protocol permission fail-closed rules;
- no behavior change to current tmux dispatch.

Resolve every Critical/Important finding with RED-first tests and a separate commit. Re-run the full verification after the final semantic change.

- [ ] **Step 5: Commit Phase 1 completion**

```bash
git add README.md HISTORY.md CLAUDE.md AGENT.md docs/handoff/current-development-state.md docs/contracts/contract-index-schema.md docs/contracts/protocol-runtime-schema.md
git commit -m "Complete the protocol runtime model phase"
```

### Task 9: Integrate the completed branch into main and push

**Files:**
- No source edits expected; integration only after all prior tasks and review pass.

- [ ] **Step 1: Verify both worktrees before integration**

Run in the feature worktree:

```bash
git status --short
git log --oneline --decorate -8
```

Expected: clean feature worktree and all planned commits present.

Run in the main worktree:

```bash
git status --short
git branch --show-current
```

Expected: branch is `main`. Existing user-owned `.omc/` and `AGENTS.md` changes must not be staged, deleted, or overwritten. If tracked main files overlap the feature merge, stop and ask the user.

- [ ] **Step 2: Merge non-destructively into main**

From the main worktree, after preserving all user-owned changes:

```bash
git merge --no-ff codex/natural-language-mission -m "Merge protocol-native AgentDeck foundation"
```

Expected: merge succeeds without touching unrelated `.omc/` or untracked `AGENTS.md`. On conflict, stop; do not reset, checkout, clean, or discard user files.

- [ ] **Step 3: Verify the merged main branch**

Run:

```bash
conda run --no-capture-output -n agentdeck pytest -q
conda run --no-capture-output -n agentdeck python -m compileall src tests -q
git diff --check
git status --short
```

Expected: tests pass. Status may still show only the user's pre-existing `.omc/` and `AGENTS.md` changes; no AgentDeck implementation file may be modified after the merge commit.

- [ ] **Step 4: Push main only after merged verification**

Run:

```bash
git push origin main
```

Expected: push succeeds. Record the pushed commit ID in the final handoff. Do not force-push.

## Final phase acceptance

Phase 0 and Phase 1 are complete only when:

- the real two-message Codex/Claude Mission passes all eight turns;
- durable sanitized validation evidence exists;
- the protocol domain rejects invalid identity, lineage, state, and permission inputs;
- StateStore rejection paths are zero-write;
- ProjectView exposes bounded compact protocol summaries and no streamed payload content;
- `agentdeck protocol status` is read-only and contract-validated;
- tmux declares fallback capabilities without behavior drift;
- the full suite passes after the final change;
- independent review has no unresolved Critical or Important findings;
- main is merged and verified before the non-force push.
