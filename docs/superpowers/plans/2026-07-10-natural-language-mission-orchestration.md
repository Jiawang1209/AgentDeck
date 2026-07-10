# Natural-Language Mission Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a human create a bounded multi-Agent Mission with one ordinary-language request and execute the complete Codex/Claude sequential workflow after one overall confirmation.

**Architecture:** Add a focused Mission domain module, a provider-aware runtime-readiness module, and a stateful Mission orchestration service that composes the existing Leader provider, StateStore, tmux backend, and sequential workflow engine. Mission state becomes authoritative and is projected through ProjectView, workbench, Leader Chat, contract discovery, and controls; existing skill, approval, workflow, and runtime safety boundaries remain intact.

**Tech Stack:** Python 3.12 standard library, AgentDeck CLI/StateStore/ProjectView contracts, tmux runtime backend, pytest, conda environment `agentdeck`.

---

## File map

New focused files:

- `src/agentdeck/mission.py` — pure Mission intent, Worker selection, launch-model derivation, plan validation, status constants, and payload helpers.
- `src/agentdeck/mission_orchestration.py` — stateful preview/run/resume orchestration that composes existing provider/runtime/workflow services.
- `src/agentdeck/runtime/readiness.py` — provider-aware pane readiness classification and bounded waiting.
- `tests/test_mission.py` — pure Mission domain tests.
- `tests/test_mission_orchestration.py` — stateful/fake-runtime Mission execution tests.
- `docs/contracts/mission-schema.md` — durable Mission response/card/state contract.
- `docs/validation/2026-07-10-natural-language-mission-acceptance.md` — real CLI acceptance evidence.

Modified integration files:

- `src/agentdeck/models.py` — add ProjectView `missions` projection field.
- `src/agentdeck/state.py` — persist and summarize `missions[]`.
- `src/agentdeck/contracts.py` — Mission contract constants/examples/validators/index; ProjectView/workbench/Leader Chat validation additions.
- `src/agentdeck/cli.py` — `mission status|run|resume`, natural-language Mission routes/cards, workbench and controls integration.
- `src/agentdeck/runtime/base.py` — readiness protocol only if needed by the new readiness module; do not broaden runtime authority.
- `tests/test_agent_cli.py` — Mission CLI, ProjectView, workbench, controls, contract-index coverage.
- `tests/test_leader_cli.py` — one-request preview and natural-language confirm/status/resume coverage.
- `tests/test_contracts.py` — Mission and cross-contract validator drift coverage.
- `docs/contracts/project-view-schema.md`, `docs/contracts/workbench-schema.md`, `docs/contracts/leader-chat-schema.md`, `docs/contracts/contract-index-schema.md` — additive Mission fields/cards/routes.
- `README.md`, `HISTORY.md`, `docs/handoff/current-development-state.md` — user workflow, slice history, final handoff.

## Task 1: Create an isolated implementation worktree and verify baseline

**Files:**
- No tracked file changes.

- [ ] **Step 1: Create the worktree from the approved design commit**

Run:

```bash
git worktree add /Users/liuyue/Desktop/Github_repos/multi-agent-explore-mission-worktree \
  -b codex/natural-language-mission f2cb81f6
```

Expected: a clean worktree on `codex/natural-language-mission`. Do not copy, stage, reset, or delete the user's `.omc/*` or untracked `AGENTS.md` from the main worktree.

- [ ] **Step 2: Verify environment and baseline**

Run:

```bash
cd /Users/liuyue/Desktop/Github_repos/multi-agent-explore-mission-worktree
conda run --no-capture-output -n agentdeck python -m pip install -e .
conda run --no-capture-output -n agentdeck pytest -q
conda run --no-capture-output -n agentdeck python -m compileall src tests -q
git status --short
```

Expected: current full suite passes, compileall is silent, and the feature worktree is clean.

## Task 2: Implement the pure Mission domain contract

**Files:**
- Create: `src/agentdeck/mission.py`
- Create: `tests/test_mission.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write failing intent and Worker-selection tests**

Add tests that express the public pure API:

```python
from agentdeck.mission import mission_intent, select_mission_agents


def test_mission_intent_accepts_plain_multi_agent_execution_request(config):
    intent = mission_intent("让 Codex 和 Claude 一人一句接龙百家姓，共8轮", config)
    assert intent == {
        "execution_requested": True,
        "requested_agent_ids": [],
        "requested_providers": ["codex", "claude"],
        "multi_agent": True,
    }


def test_selection_prefers_running_then_shared_then_config_order(config):
    selected = select_mission_agents(
        config,
        requested_agent_ids=[],
        requested_providers=["codex", "claude"],
        bindings={"planner": {"status": "running"}},
    )
    assert [agent.agent_id for agent in selected.agents] == ["planner", "reviewer"]
    assert selected.blockers == ()


def test_selection_blocks_when_two_provider_families_are_not_available(config):
    selected = select_mission_agents(
        config,
        requested_agent_ids=[],
        requested_providers=["codex", "claude"],
        bindings={},
    )
    assert selected.agents == ()
    assert selected.blockers == ("no configured claude worker",)
```

Also cover explicit agent ids, generic “多个智能体”, existing non-Mission help/status phrases, unknown ids, and deterministic ties.

- [ ] **Step 2: Run RED**

Run:

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_mission.py -q
```

Expected: collection fails because `agentdeck.mission` does not exist.

- [ ] **Step 3: Implement intent and selection minimally**

Create the module with immutable values and no state/runtime access:

```python
from __future__ import annotations

from dataclasses import dataclass, replace
import re
import shlex
from typing import Any

from .models import AgentSpec, LeaderConfig, ProjectConfig

MISSION_STATUSES = (
    "pending_confirmation", "preparing", "running",
    "completed", "stopped", "interrupted",
)
MISSION_SCHEMA_VERSION = "mission/v1"


@dataclass(frozen=True)
class MissionSelection:
    agents: tuple[AgentSpec, ...]
    blockers: tuple[str, ...]


def provider_family(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"codex", "codex-cli"}:
        return "codex"
    if normalized in {"claude", "claude-cli"}:
        return "claude"
    return normalized


def mission_intent(message: str, config: ProjectConfig) -> dict[str, object] | None:
    # Preserve explicit chat routes by requiring both execution and multi-Agent evidence.
    normalized = message.strip().lower()
    execution = any(token in normalized for token in ("让", "执行", "开始", "协作", "完成", "run"))
    requested_ids = [a.agent_id for a in config.agents if re.search(rf"(?<![\w-]){re.escape(a.agent_id.lower())}(?![\w-])", normalized)]
    requested_providers = [name for name in ("codex", "claude") if name in normalized]
    generic_multi = any(token in normalized for token in ("多个智能体", "两个 agent", "协作", "交替", "接龙", "依次"))
    if not execution or not (len(requested_ids) >= 2 or len(requested_providers) >= 2 or generic_multi):
        return None
    return {
        "execution_requested": True,
        "requested_agent_ids": requested_ids,
        "requested_providers": requested_providers,
        "multi_agent": True,
    }
```

Implement `select_mission_agents()` with the exact ranking from the spec and return blockers instead of silently substituting requested providers.

Also implement `selected_agent_summaries()` and `startup_action_summaries()` in this pure module. These helpers expose only compact provider/role/workspace/runtime/effective-model provenance and `reuse|spawn` intent; they must never expose environment variables, credentials, or the full shell command.

- [ ] **Step 4: Add failing launch-model and plan-validation tests**

```python
from agentdeck.mission import effective_mission_agent, validate_mission_plan


def test_codex_worker_inherits_matching_cli_leader_model(config):
    effective = effective_mission_agent(config.agents[0], config.leader)
    assert effective.agent.command == "codex --model gpt-5.5"
    assert effective.model == "gpt-5.5"
    assert effective.model_source == "leader_inherited"


def test_explicit_worker_model_is_never_overridden(config):
    agent = replace(config.agents[0], command="codex --model gpt-5.4")
    effective = effective_mission_agent(agent, config.leader)
    assert effective.agent.command == "codex --model gpt-5.4"
    assert effective.model_source == "configured_command"


def test_mission_plan_rejects_unselected_worker(valid_plan):
    with pytest.raises(ValueError, match="not selected"):
        validate_mission_plan(valid_plan, selected_agent_ids=("planner", "reviewer"), timeout_seconds=180)
```

Cover fewer than two agents/steps, non-consecutive steps, invalid timeout, and selected-set expansion.

- [ ] **Step 5: Run RED, implement, and run GREEN**

Implement:

```python
@dataclass(frozen=True)
class EffectiveMissionAgent:
    agent: AgentSpec
    model: str | None
    model_source: str


def effective_mission_agent(agent: AgentSpec, leader: LeaderConfig) -> EffectiveMissionAgent:
    tokens = shlex.split(agent.command)
    for index, token in enumerate(tokens):
        if token in {"--model", "-m"} and index + 1 < len(tokens):
            return EffectiveMissionAgent(agent, tokens[index + 1], "configured_command")
    leader_family = provider_family(leader.provider)
    if provider_family(agent.provider) == leader_family and leader_family in {"codex", "claude"}:
        derived = replace(agent, command=shlex.join([*tokens, "--model", leader.model]))
        return EffectiveMissionAgent(derived, leader.model, "leader_inherited")
    return EffectiveMissionAgent(agent, None, "provider_default")
```

Run:

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_mission.py -q
```

Expected: all Mission domain tests pass.

- [ ] **Step 6: Update history and commit**

Record the pure-domain slice and safety boundaries in `HISTORY.md`, then:

```bash
git add src/agentdeck/mission.py tests/test_mission.py HISTORY.md
git commit -m "Add natural-language mission domain"
```

## Task 3: Persist Mission state and project it through ProjectView

**Files:**
- Modify: `src/agentdeck/models.py`
- Modify: `src/agentdeck/state.py`
- Modify: `tests/test_mission.py`
- Modify: `tests/test_agent_cli.py`
- Modify: `docs/contracts/project-view-schema.md`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write failing StateStore tests**

```python
def test_state_store_creates_updates_and_summarizes_mission(tmp_path):
    store = StateStore(tmp_path)
    created = store.create_mission(
        user_message="让 Codex 和 Claude 接龙",
        provider="fake",
        model="fake-plan",
        leader_backend=leader_backend_identity("fake", "fake-plan"),
        plan_id="pln_demo",
        plan_hash="sha256:plan",
        selected_agents=[SELECTED_CODEX, SELECTED_CLAUDE],
        startup_actions=[SPAWN_CODEX, SPAWN_CLAUDE],
        timeout_seconds=180,
        blockers=[],
    )
    assert created["mission_id"].startswith("mis_")
    assert created["status"] == "pending_confirmation"
    updated = store.update_mission(created["mission_id"], status="running", workflow_run_id="wfr_demo")
    assert store.mission_by_id(created["mission_id"]) == updated
```

Add a ProjectView test asserting `payload["missions"]` contains `count`, `by_status`, `latest_id`, and compact `items[]`, while excluding raw launch commands and full prompts.

- [ ] **Step 2: Run RED**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_mission.py tests/test_agent_cli.py -k mission -q
```

Expected: failures for missing StateStore methods and ProjectView field.

- [ ] **Step 3: Implement state methods and projection**

Add `missions: dict[str, Any]` to `ProjectView`. Import `MISSION_SCHEMA_VERSION` from `agentdeck.mission` into `state.py`, and ensure new state initializes `"missions": []`.

Implement exact store methods:

```python
def create_mission(self, **values: Any) -> dict[str, Any]:
    state = self.load()
    now = utc_now()
    record = {
        "mission_id": new_id("mis"),
        "schema_version": MISSION_SCHEMA_VERSION,
        "status": "pending_confirmation",
        "stop_reason": None,
        "workflow_run_id": None,
        "confirmed_at": None,
        "completed_at": None,
        "created_at": now,
        "updated_at": now,
        **values,
    }
    state.setdefault("missions", []).append(record)
    self.save(state)
    return record


def mission_by_id(self, mission_id: str) -> dict[str, Any]:
    for item in self.load().get("missions", []):
        if item.get("mission_id") == mission_id:
            return item
    raise KeyError(mission_id)


def update_mission(self, mission_id: str, **changes: Any) -> dict[str, Any]:
    state = self.load()
    record = next(
        (item for item in state.setdefault("missions", []) if item.get("mission_id") == mission_id),
        None,
    )
    if record is None:
        raise KeyError(mission_id)
    record.update(changes)
    record["updated_at"] = utc_now()
    if changes.get("status") == "completed" and not record.get("completed_at"):
        record["completed_at"] = record["updated_at"]
    self.save(state)
    return record


def list_missions(self) -> list[dict[str, Any]]:
    return list(self.load().get("missions", []))
```

Add `_mission_summaries()` and pass it to `ProjectView(...)` from the same loaded state.

- [ ] **Step 4: Document, run GREEN, and commit**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_mission.py tests/test_agent_cli.py -k 'mission or project_view' -q
conda run --no-capture-output -n agentdeck python -m compileall src tests -q
git diff --check
git add src/agentdeck/models.py src/agentdeck/state.py tests/test_mission.py tests/test_agent_cli.py docs/contracts/project-view-schema.md HISTORY.md
git commit -m "Persist mission state in ProjectView"
```

## Task 4: Add the Mission contract and CLI discovery

**Files:**
- Modify: `src/agentdeck/contracts.py`
- Modify: `src/agentdeck/cli.py`
- Modify: `tests/test_contracts.py`
- Modify: `tests/test_agent_cli.py`
- Create: `docs/contracts/mission-schema.md`
- Modify: `docs/contracts/contract-index-schema.md`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write failing contract tests**

```python
def test_mission_contract_discovery_and_examples(capsys):
    assert cli.main(["contract", "mission", "--example"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "mission"
    assert payload["example_preview"]["mode"] == "mission_preview"
    assert payload["example_status"]["mode"] == "mission_status"
    assert payload["example_run"]["mode"] == "mission_run"
    assert set(payload["example_preview"]) == set(payload["preview_response_fields"])
```

Add validator-negative tests for status drift, selected count mismatch, enabled confirmation with blockers, command/id mismatch, and run payload without `confirmed=true`.

- [ ] **Step 2: Run RED**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_contracts.py tests/test_agent_cli.py -k mission -q
```

Expected: missing contract functions/subcommand/index item.

- [ ] **Step 3: Implement constants, examples, validators, and CLI parser**

Add `mission` to `CONTRACT_INDEX_SPECS`, and define stable field tuples:

```python
MISSION_PREVIEW_RESPONSE_FIELDS = (
    "schema_version", "ok", "mode", "mission_id", "status", "user_message",
    "provider", "model", "leader_backend", "plan_id", "plan_hash", "plan",
    "selected_agents", "startup_actions", "step_count", "timeout_seconds",
    "can_start", "blockers", "confirmation_command", "status_command",
    "workbench_command", "controls", "safety", "requires_explicit_user",
)
MISSION_STATUS_RESPONSE_FIELDS = (
    "schema_version", "ok", "mode", "mission_id", "status", "user_message",
    "plan_id", "plan_hash", "workflow_run_id", "current_step", "step_count",
    "timeout_seconds", "selected_agents", "blockers", "stop_reason",
    "created_at", "updated_at", "confirmed_at", "completed_at", "can_resume",
    "status_command", "resume_command", "attach_command", "workbench_command",
    "controls", "safety", "requires_explicit_user",
)
MISSION_RUN_RESPONSE_FIELDS = (*MISSION_STATUS_RESPONSE_FIELDS, "confirmed")
MISSION_SELECTED_AGENT_FIELDS = (
    "agent_id", "provider", "role", "workspace_mode", "runtime_status",
    "effective_model", "model_source",
)
```

Implement `mission_contract_payload`, `mission_contract_response`, `mission_example`, `validate_mission_preview_contract`, `validate_mission_status_contract`, and `validate_mission_run_contract`. Register `contract mission` in `cli.py` using the same parser/handler pattern as `contract workflow`.

- [ ] **Step 4: Write durable docs, run GREEN, and commit**

Document every response/item/control field, status transition, safety rule, example command, and read/write boundary in `docs/contracts/mission-schema.md`; update contract index docs.

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_contracts.py tests/test_agent_cli.py -k 'mission or contract_index' -q
git diff --check
git add src/agentdeck/contracts.py src/agentdeck/cli.py tests/test_contracts.py tests/test_agent_cli.py docs/contracts/mission-schema.md docs/contracts/contract-index-schema.md HISTORY.md
git commit -m "Add mission contract discovery"
```

## Task 5: Create a natural-language Mission preview

**Files:**
- Create: `src/agentdeck/mission_orchestration.py`
- Modify: `src/agentdeck/cli.py`
- Modify: `tests/test_mission_orchestration.py`
- Modify: `tests/test_leader_cli.py`
- Modify: `docs/contracts/leader-chat-schema.md`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write failing preview-service tests**

Use a recording fake provider and a backend class that raises if touched:

```python
def test_create_preview_selects_workers_and_never_touches_runtime(project):
    result = create_mission_preview(
        config=project.config,
        store=project.store,
        provider=RecordingProvider(EIGHT_STEP_PLAN),
        user_message="让 Codex 和 Claude 一人一句接龙百家姓，共8轮",
        timeout_seconds=180,
    )
    assert result["mode"] == "mission_preview"
    assert [item["provider"] for item in result["selected_agents"]] == ["codex", "claude"]
    assert result["can_start"] is True
    assert result["confirmation_command"].endswith(f"批准执行 {result['mission_id']}\"")
    assert project.store.load()["workflow_runs"] == []
    assert project.store.load()["skill_loads"] == []
```

Assert the provider request contains only selected Workers and Mission fixed-sequence instructions; the persisted project config remains byte-identical.

Add a preview blocker test where `shutil.which()` cannot resolve one selected Worker command. It must return `can_start=false`, preserve the plan/preview for inspection, disable confirmation, and perform zero runtime calls.

- [ ] **Step 2: Write failing Leader Chat route tests**

```python
def test_leader_chat_plain_language_creates_mission_preview(tmp_path, monkeypatch, capsys):
    root = prepare_project(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "leader_provider", lambda _name: FakeMissionProvider())
    assert cli.main(["leader", "chat", "--message", "让 Codex 和 Claude 一人一句接龙百家姓，共8轮"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "mission_preview"
    assert payload["mission_preview_card"]["mission_id"].startswith("mis_")
    assert payload["intent_card"]["embedded_card"] == "mission_preview_card"
    assert payload["next_command"] == payload["mission_preview_card"]["confirmation_command"]
```

Assert existing help/status/skill/memory/trace routes remain byte-for-byte shaped and are not hijacked.

- [ ] **Step 3: Run RED**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_mission_orchestration.py tests/test_leader_cli.py -k mission -q
```

Expected: missing orchestration service and route.

- [ ] **Step 4: Implement preview service**

Implement a focused entry point:

```python
def create_mission_preview(
    *,
    config: ProjectConfig,
    store: StateStore,
    provider: LeaderProvider,
    user_message: str,
    timeout_seconds: int,
) -> dict[str, object]:
    intent = mission_intent(user_message, config)
    if intent is None:
        raise ValueError("message is not a multi-agent mission request")
    selection = select_mission_agents(
        config,
        requested_agent_ids=tuple(str(item) for item in intent["requested_agent_ids"]),
        requested_providers=tuple(str(item) for item in intent["requested_providers"]),
        bindings=store.load().get("agents", {}),
    )
    effective = tuple(effective_mission_agent(agent, config.leader) for agent in selection.agents)
    command_blockers = tuple(
        f"worker command not found: {item.agent.agent_id}"
        for item in effective
        if shutil.which(shlex.split(item.agent.command)[0]) is None
    )
    blockers = (*selection.blockers, *command_blockers)
    selected_config = replace(config, agents=tuple(item.agent for item in effective))
    plan = LeaderOrchestrator(selected_config, provider).plan(
        mission_planning_task(user_message), config.leader.model,
        skill_context=_explicit_leader_skill_context(store, selected_config),
    )
    validate_mission_plan(plan, tuple(a.agent.agent_id for a in effective), timeout_seconds)
    explicit_skill_context = store.project_view(config).skills
    plan_record = store.record_plan(
        user_message,
        provider.name,
        config.leader.model,
        plan,
        skill_context=explicit_skill_context,
    )
    mission = store.create_mission(
        user_message=user_message,
        provider=provider.name,
        model=config.leader.model,
        leader_backend=plan_record["leader_backend"],
        plan_id=plan_record["plan_id"],
        plan_hash=workflow_plan_hash(plan_record),
        selected_agents=selected_agent_summaries(effective, store),
        startup_actions=startup_action_summaries(effective, store),
        timeout_seconds=timeout_seconds,
        step_count=len(plan["steps"]),
        can_start=not blockers,
        blockers=list(blockers),
    )
    store.append_event(
        EventRecord.create(
            "mission_preview_created",
            {
                "mission_id": mission["mission_id"],
                "plan_id": plan_record["plan_id"],
                "selected_agent_ids": [item.agent.agent_id for item in effective],
                "step_count": len(plan["steps"]),
            },
        )
    )
    return mission_preview_payload(mission, plan_record)
```

Do not import CLI-private helpers into this service. Move or expose only the smallest shared helper needed for explicit loaded-skill ProjectView context.

- [ ] **Step 5: Route through Leader Chat and validate response**

Add Mission intent before generic role/task/plan fallback. Populate `mission_preview_card`, explanation, intent card, filtered control registry, and chat/audit records. Call `validate_leader_chat_contract()` before printing.

- [ ] **Step 6: Run GREEN, document, and commit**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_mission.py tests/test_mission_orchestration.py tests/test_leader_cli.py -k mission -q
conda run --no-capture-output -n agentdeck pytest tests/test_leader_cli.py -k 'help or skill or memory or trace' -q
git diff --check
git add src/agentdeck/mission_orchestration.py src/agentdeck/cli.py tests/test_mission_orchestration.py tests/test_leader_cli.py docs/contracts/leader-chat-schema.md HISTORY.md
git commit -m "Create missions from natural language"
```

## Task 6: Add provider-aware Worker readiness

**Files:**
- Create: `src/agentdeck/runtime/readiness.py`
- Create: `tests/test_runtime_readiness.py`
- Modify: `src/agentdeck/runtime/base.py` only if a protocol type is required
- Modify: `HISTORY.md`

- [ ] **Step 1: Write failing classification tests from real TUI evidence**

```python
@pytest.mark.parametrize(
    ("provider", "output", "expected"),
    [
        ("codex", CODEX_READY_SCREEN, "ready"),
        ("codex", CODEX_STARTING_MCP_SCREEN, "starting"),
        ("codex", CODEX_MODEL_INCOMPATIBLE_SCREEN, "failed"),
        ("claude", CLAUDE_READY_SCREEN, "ready"),
        ("claude", CLAUDE_TRUST_SCREEN, "setup_required"),
        ("claude", CLAUDE_LOGIN_SCREEN, "setup_required"),
    ],
)
def test_classify_worker_readiness(provider, output, expected):
    assert classify_worker_readiness(provider, output).status == expected
```

Fixtures must be short sanitized excerpts from the earlier real acceptance, not full copyrighted or secret-bearing histories.

- [ ] **Step 2: Write failing bounded wait tests**

Cover starting-to-ready, pane loss, setup blocker, error, and timeout with injected monotonic/sleeper:

```python
def test_wait_for_selected_workers_stops_before_dispatch_on_trust_prompt(
    runtime_config, fake_backend, claude_agent
):
    fake_backend.outputs["%2"] = CLAUDE_TRUST_SCREEN
    result = wait_for_worker_readiness(
        runtime_config=runtime_config,
        backend=fake_backend,
        selected=((claude_agent, "%2"),),
        timeout_seconds=30,
        poll_interval=0,
        monotonic=lambda: 0.0,
        sleeper=lambda _seconds: None,
    )
    assert result.all_ready is False
    assert result.results[0].status == "setup_required"
    assert fake_backend.sent == []
```

- [ ] **Step 3: Run RED, implement minimal adapters, run GREEN**

Implement immutable result types and pure classification before the bounded polling wrapper:

```python
@dataclass(frozen=True)
class WorkerReadiness:
    agent_id: str
    provider: str
    status: str
    reason: str | None


def classify_worker_readiness(provider: str, output: str) -> WorkerReadinessEvidence:
    normalized = output.lower()
    family = provider_family(provider)
    if family == "claude" and "yes, i trust this folder" in normalized:
        return WorkerReadinessEvidence("setup_required", "directory trust required")
    if family == "codex" and "requires a newer version of codex" in normalized:
        return WorkerReadinessEvidence("failed", "configured model is incompatible with Codex CLI")
    if family == "claude" and "❯" in output and "context" in normalized:
        return WorkerReadinessEvidence("ready", None)
    if family == "codex" and "›" in output and "starting mcp servers" not in normalized:
        return WorkerReadinessEvidence("ready", None)
    if "starting mcp servers" in normalized:
        return WorkerReadinessEvidence("starting", "CLI startup is still in progress")
    return WorkerReadinessEvidence("starting", "CLI prompt not ready")
```

Readiness must never return ready from pane existence alone.

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_runtime_readiness.py -q
git add src/agentdeck/runtime/readiness.py src/agentdeck/runtime/base.py tests/test_runtime_readiness.py HISTORY.md
git commit -m "Probe Codex and Claude worker readiness"
```

## Task 7: Run and resume one confirmed Mission

**Files:**
- Modify: `src/agentdeck/mission_orchestration.py`
- Modify: `src/agentdeck/cli.py`
- Modify: `tests/test_mission_orchestration.py`
- Modify: `tests/test_agent_cli.py`
- Modify: `docs/contracts/mission-schema.md`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write failing selected-startup and complete-run tests**

```python
def test_run_mission_spawns_only_selected_workers_then_completes(project):
    preview = seed_pending_mission(project, agents=("planner", "reviewer"))
    result = run_mission(
        config=project.config,
        store=project.store,
        backend=CorrelatedMissionBackend(),
        mission_id=preview["mission_id"],
        readiness_timeout_seconds=30,
    )
    assert result["status"] == "completed"
    assert project.backend.spawned == ["planner", "reviewer"]
    assert "coder" not in project.backend.spawned
    assert len(result["workflow"]["turns"]) == 8
    assert project.store.mission_by_id(preview["mission_id"])["workflow_run_id"].startswith("wfr_")
```

The fake backend must emulate pane creation, provider readiness, prompt send, and token-correlated replies using real workflow code.

- [ ] **Step 2: Write failing safety/idempotency tests**

Cover:

```python
def test_run_mission_with_setup_blocker_dispatches_zero_steps(project):
    mission_id = seed_pending_mission(project)
    project.backend.readiness["reviewer"] = "setup_required"
    result = run_mission(config=project.config, store=project.store, backend=project.backend, mission_id=mission_id)
    assert result["status"] == "stopped"
    assert result["stop_reason"] == "worker_setup_required"
    assert project.backend.sent == []


def test_duplicate_confirmation_does_not_create_second_workflow(project):
    mission_id = seed_completed_mission(project)
    before = len(project.store.load().get("workflow_runs", []))
    result = run_mission(config=project.config, store=project.store, backend=project.backend, mission_id=mission_id)
    assert result["status"] == "completed"
    assert len(project.store.load().get("workflow_runs", [])) == before


def test_plan_hash_drift_stops_before_runtime(project):
    mission_id = seed_pending_mission(project)
    mutate_saved_plan_task(project.store)
    result = run_mission(config=project.config, store=project.store, backend=project.backend, mission_id=mission_id)
    assert result["stop_reason"] == "plan_drift"
    assert project.backend.spawned == []


def test_partial_spawn_failure_keeps_bindings_and_dispatches_zero_steps(project):
    mission_id = seed_pending_mission(project)
    project.backend.fail_spawn_for = "reviewer"
    result = run_mission(config=project.config, store=project.store, backend=project.backend, mission_id=mission_id)
    assert result["stop_reason"] == "worker_start_failed"
    assert project.store.agent_binding("planner") is not None
    assert project.backend.sent == []


def test_resume_reuses_existing_workflow_without_duplicate_dispatch(project):
    mission_id, run_id, sent_before = seed_interrupted_mission(project)
    result = resume_mission(config=project.config, store=project.store, backend=project.backend, mission_id=mission_id)
    assert result["status"] == "completed"
    assert result["workflow_run_id"] == run_id
    assert project.backend.sent.count(sent_before[0]) == 1
```

Assert exact Mission statuses, stop reasons, event types, workflow count, sent prompt count, and selected-agent bounds.

- [ ] **Step 3: Run RED**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_mission_orchestration.py -q
```

- [ ] **Step 4: Implement the orchestration pipeline**

Implement shared internal phases rather than duplicating run/resume:

```python
def run_mission(
    *,
    config: ProjectConfig,
    store: StateStore,
    backend: RuntimeBackend,
    mission_id: str,
    readiness_timeout_seconds: int = 60,
) -> dict[str, object]:
    mission = store.mission_by_id(mission_id)
    if mission["status"] in {"preparing", "running", "completed"}:
        return mission_status_payload(store, mission)
    _validate_frozen_mission(store, mission)
    mission = store.update_mission(mission_id, status="preparing", confirmed_at=utc_now(), blockers=[])
    store.append_event(
        EventRecord.create("mission_confirmed", {"mission_id": mission_id, "plan_id": mission["plan_id"]})
    )
    prepared = _prepare_selected_workers(config=config, store=store, backend=backend, mission=mission)
    readiness = wait_for_worker_readiness(
        runtime_config=config.runtime,
        backend=backend,
        selected=prepared,
        timeout_seconds=readiness_timeout_seconds,
    )
    if not readiness.all_ready:
        return _stop_mission(store, mission_id, reason=readiness.stop_reason, blockers=readiness.blockers)
    workflow_record = _create_or_resume_workflow(config=config, store=store, mission=mission)
    store.update_mission(mission_id, status="running", workflow_run_id=workflow_record["run_id"])
    result = run_sequential_workflow(
        config=config,
        store=store,
        backend=backend,
        run_id=workflow_record["run_id"],
    )
    return _project_workflow_result_to_mission(store, mission_id, result)
```

Catch `KeyboardInterrupt` at the CLI boundary and persist `interrupted`; convert runtime exceptions into audited bounded stop reasons instead of traceback state leaks.

- [ ] **Step 5: Add `mission status|run|resume` CLI commands**

Register:

```text
agentdeck mission status --mission-id <id>
agentdeck mission run --mission-id <id> --confirm
agentdeck mission resume --mission-id <id> --confirm
```

`run` and `resume` without `--confirm` return non-zero and perform zero writes/runtime calls. Validate every JSON payload before printing.

- [ ] **Step 6: Run GREEN, update contract docs, and commit**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_mission_orchestration.py tests/test_agent_cli.py -k mission -q
conda run --no-capture-output -n agentdeck python -m compileall src tests -q
git diff --check
git add src/agentdeck/mission_orchestration.py src/agentdeck/cli.py tests/test_mission_orchestration.py tests/test_agent_cli.py docs/contracts/mission-schema.md HISTORY.md
git commit -m "Run confirmed natural-language missions"
```

## Task 8: Complete natural-language confirmation, status, recovery, workbench, and controls

**Files:**
- Modify: `src/agentdeck/cli.py`
- Modify: `src/agentdeck/contracts.py`
- Modify: `tests/test_leader_cli.py`
- Modify: `tests/test_agent_cli.py`
- Modify: `tests/test_contracts.py`
- Modify: `docs/contracts/leader-chat-schema.md`
- Modify: `docs/contracts/workbench-schema.md`
- Modify: `docs/contracts/controls-schema.md`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write failing natural-language confirmation tests**

```python
def test_leader_chat_confirms_the_named_mission_once(tmp_path, monkeypatch, capsys):
    mission_id = seed_pending_mission(root=tmp_path, store=StateStore(tmp_path))
    monkeypatch.setattr(cli, "TmuxBackend", CorrelatedMissionBackend)
    assert cli.main(["leader", "chat", "--message", f"批准执行 {mission_id}"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "mission_run"
    assert payload["mission_run_card"]["status"] == "completed"
    assert payload["intent_card"]["embedded_card"] == "mission_run_card"
```

Also test id-less unique confirmation, ambiguous pending Missions, `查看 mission`, `查看当前 mission`, `继续 mission`, completed re-confirmation, and no collision with ordinary approval intent.

- [ ] **Step 2: Write failing workbench/control tests**

Assert `mission_card` is derived from ProjectView and control registry contains exact Mission inspect/confirm/resume/attach controls with stable ids and aligned blockers. Rendering workbench/controls must not call provider/tmux or mutate state.

- [ ] **Step 3: Run RED, implement routes/cards, run GREEN**

Add Mission routes before generic approval/continue routes. Reuse `run_mission`, `resume_mission`, and status payload helpers directly; do not execute command strings. Add Mission explanation/intent-card labels and validate the complete Leader Chat response.

Add workbench card derivation from ProjectView `missions` and register its controls in the existing control registry builder.

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_leader_cli.py tests/test_agent_cli.py tests/test_contracts.py -k mission -q
```

- [ ] **Step 4: Update durable docs and commit**

```bash
git diff --check
git add src/agentdeck/cli.py src/agentdeck/contracts.py tests/test_leader_cli.py tests/test_agent_cli.py tests/test_contracts.py docs/contracts/leader-chat-schema.md docs/contracts/workbench-schema.md docs/contracts/controls-schema.md HISTORY.md
git commit -m "Expose mission orchestration across Leader chat"
```

## Task 9: Add one contiguous deterministic one-request/one-confirmation rehearsal

**Files:**
- Modify: `tests/test_mission_orchestration.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write the end-to-end test before any further production change**

The test must use public CLI entry points for both human messages:

```python
def test_plain_language_then_one_confirmation_completes_eight_turn_mission(
    tmp_path, monkeypatch, capsys
):
    assert cli.main(["leader", "chat", "--message", "让 Codex 和 Claude 一人一句接龙百家姓，共8轮"]) == 0
    preview = json.loads(capsys.readouterr().out)
    mission_id = preview["mission_preview_card"]["mission_id"]

    assert cli.main(["leader", "chat", "--message", f"批准执行 {mission_id}"]) == 0
    completed = json.loads(capsys.readouterr().out)

    assert completed["mission_run_card"]["status"] == "completed"
    assert [turn["agent_id"] for turn in completed["mission_run_card"]["turns"]] == [
        "planner", "reviewer", "planner", "reviewer",
        "planner", "reviewer", "planner", "reviewer",
    ]
    assert [turn["handoff"]["summary"] for turn in completed["mission_run_card"]["turns"]] == EXPECTED_BAIJIAXING
```

Assert the human never invoked role assignment, TOML edit, skill load, agent spawn, plan id, workflow preview, or workflow run; state has one Mission, one plan, one workflow, eight messages/replies, and complete Mission/workflow audit events.

- [ ] **Step 2: Run RED or confirm existing implementation already satisfies the new public test**

If it fails, the failure must expose a missing goal requirement. Add the smallest production fix only after observing the failure, then rerun. Every bug found here gets its own regression assertion.

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_mission_orchestration.py -q
```

- [ ] **Step 3: Run focused cross-surface regression and commit**

```bash
conda run --no-capture-output -n agentdeck pytest \
  tests/test_mission.py tests/test_mission_orchestration.py tests/test_runtime_readiness.py \
  tests/test_leader_cli.py tests/test_agent_cli.py tests/test_contracts.py -k mission -q
git add tests/test_mission_orchestration.py HISTORY.md src/agentdeck
git commit -m "Rehearse one-confirmation mission flow"
```

Only include `src/agentdeck` files in this commit if the RED rehearsal required a tested production fix.

## Task 10: Real Codex/Claude acceptance and final product documentation

**Files:**
- Create: `docs/validation/2026-07-10-natural-language-mission-acceptance.md`
- Modify: `README.md`
- Modify: `HISTORY.md`
- Modify: `docs/handoff/current-development-state.md`
- Modify: `CLAUDE.md`
- Modify: `AGENT.md`

- [ ] **Step 1: Prepare an isolated acceptance project without manual Mission assembly**

Create a temporary git project, run `agentdeck project init`, and configure only the already authenticated Leader provider through the supported CLI. Do not edit TOML, assign Worker roles, load skills, spawn agents, or create a plan/workflow manually.

Record:

```bash
codex --version
claude --version
agentdeck doctor
```

If first-run directory trust appears, complete that human setup explicitly and record it as a prerequisite, not Mission automation.

- [ ] **Step 2: Create and confirm through natural language only**

Run:

```bash
agentdeck leader chat --message "让 Codex 和 Claude 一人一句接龙百家姓，共8轮"
agentdeck leader chat --message "批准执行 mis_xxx"
```

The second command may run in the foreground while another terminal attaches to the returned tmux session. Do not invoke `skills load`, `agent spawn`, `leader plan`, `workflow preview`, or `workflow run`.

- [ ] **Step 3: Audit every completion requirement**

Use only public read surfaces:

```bash
agentdeck mission status --mission-id mis_xxx
agentdeck status
agentdeck workbench
agentdeck events --limit 100
```

Verify:

- Mission and workflow are completed;
- eight turns alternate selected Codex/Claude Workers;
- summaries are `赵钱孙李 / 周吴郑王 / 冯陈褚卫 / 蒋沈韩杨 / 朱秦尤许 / 何吕施张 / 孔曹严华 / 金魏陶姜`;
- every later prompt contains only the previous compact handoff;
- ProjectView/workbench/contracts expose the same ids/status;
- no unrelated Worker was spawned;
- exactly one Mission confirmation event exists;
- trace and audit lineage are complete.

- [ ] **Step 4: Convert every real-run defect into a RED regression before fixing**

Do not patch from observation alone. Add a focused failing test, observe the expected failure, implement the minimal fix, rerun focused tests, and commit the fix with `HISTORY.md`.

- [ ] **Step 5: Write acceptance and user documentation**

The acceptance report must include sanitized versions/models, Mission/plan/workflow ids, selected Workers, exact transcript, command boundary, blockers encountered, fixes, machine assertions, and cleanup status.

README and agent/handoff docs must show the two-message user workflow first, then setup prerequisites and explicit CLI equivalents. State that Mission is fixed sequential v1 and does not auto-login, auto-trust, load skills, or broaden permissions.

- [ ] **Step 6: Run final verification**

```bash
conda run --no-capture-output -n agentdeck pytest -q
conda run --no-capture-output -n agentdeck python -m compileall src tests -q
conda run --no-capture-output -n agentdeck agentdeck contract mission --example >/tmp/agentdeck-mission-contract.json
jq -e '.example_preview.mode == "mission_preview" and .example_run.mode == "mission_run"' /tmp/agentdeck-mission-contract.json >/dev/null
git diff --check
git status --short
```

Expected: full suite passes; compileall, contract smoke, and diff checks are clean; only intended feature files are changed.

- [ ] **Step 7: Commit final acceptance slice**

```bash
git add README.md HISTORY.md CLAUDE.md AGENT.md docs/handoff/current-development-state.md docs/validation/2026-07-10-natural-language-mission-acceptance.md
git commit -m "Validate natural-language Codex Claude missions"
```

Do not push. Keep the original main-worktree `.omc/*` and untracked `AGENTS.md` untouched.
