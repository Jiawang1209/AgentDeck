# AgentDeck Product Kernel Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the foreground-first AgentDeck product that turns one natural-language goal into one confirmed Mission and coordinates Codex implementation, Claude review, Codex revision, and Claude acceptance through ACP with SQLite authority and real tmux observation.

**Architecture:** A clean `Product -> Application -> Kernel` dependency chain talks to external systems only through Ports and Adapters. The rewrite lives beside the legacy implementation until a real four-Worker Golden Product Gate passes; legacy code is unavailable by default and can enter only through an explicitly recorded Adapter admission.

**Tech Stack:** Python 3.12, stdlib dataclasses/enums/asyncio/sqlite3/tomllib/urllib, `agent-client-protocol==0.11.0`, Codex app-server JSON-RPC v2, Claude Agent ACP, tmux, pytest, optional Playwright only for the R7 browser evidence adapter.

---

## 0. What this program builds

The user will be able to run `agentdeck`, select Codex CLI, Claude CLI, or an
OpenAI-compatible API as Leader, select a model and one of three permission
profiles, describe a development goal in natural language, inspect one exact
Mission Preview, confirm it once, and let AgentDeck drive this fixed default
coding graph:

```text
Codex implementation
  -> Claude review
  -> Codex revision
  -> Claude acceptance
```

AgentDeck—not the Leader, Worker, ACP adapter, or tmux—owns Mission authority,
stage scheduling, permission decisions, handoffs, evidence, recovery, and final
acceptance state. Codex/Claude automatic communication is ACP-only. tmux shows
redacted decoded ACP events in real time and supports explicit human takeover;
it is never a task transport or completion oracle.

The MVP is foreground-first. `/exit` persists and safely interrupts active
work; re-entry restores the ProductSession. Continued execution after the
terminal closes, Memory, Skills, self-improvement, GUI, A2A, and remote clients
remain post-MVP.

## 1. Authority and execution rules

Every task in this plan is governed by:

1. `docs/superpowers/specs/2026-07-18-agentdeck-product-kernel-rewrite-design.md`;
2. `docs/roadmap/product-north-star.md`;
3. this plan;
4. the exact Task section being executed.

For every task:

- run commands with `conda run -n agentdeck` or after
  `conda activate agentdeck`;
- touch only the listed files;
- write the RED test first and confirm its named failure;
- implement the smallest GREEN behavior shown;
- update `HISTORY.md` in the same commit;
- run the focused tests and the phase regression gate;
- create the named local commit;
- do not push or merge;
- do not call a real provider, start a real ACP/tmux session, install software,
  change authentication, or alter global configuration unless the task is an
  explicitly labeled real gate and the human has authorized that gate.

Legacy source is `not admitted` unless a task names it under **Approved legacy
evidence**. Kernel and Application may never import admitted legacy code;
Adapters are the only legal import boundary.

**Implementation-inventory corrections (2026-07-19):** Task 8's evolved
execution coverage is split across two cohesive test modules so neither new
file exceeds the 500-line ceiling. Task 12's
declared durable `conversation_turns` transaction required the existing Store
Adapter to save and reload that aggregate, while the 500-line limit and
fail-closed validation required cohesive helper and quality-test splits. Task
16 likewise required one cohesive schema helper split to remain within the
same limit. The exact files are now listed in those tasks. This correction
does not add product behavior, change task order, admit legacy code, or widen
either task's design authority.

## 2. Permanent file map

```text
src/agentdeck/
  kernel/
    __init__.py
    session.py          # ProductSession state and transitions
    mission.py          # draft, version, preview, task graph and hashing
    permissions.py      # permission profiles and monotonic narrowing
    agents.py           # backend, instance and role identity
    execution.py        # attempts, handoffs, evidence and transitions
    diagnostics.py      # stable diagnostic facts
    events.py           # immutable domain events
  application/
    __init__.py
    session_service.py
    leader_service.py
    mission_service.py
    execution_service.py
    approval_service.py
    recovery_service.py
    support_service.py
    preflight_service.py
    migration_service.py
  ports/
    __init__.py
    leader.py
    worker.py
    transport.py
    store.py
    approval.py
    runtime.py
    clock.py
  adapters/
    __init__.py
    config.py
    discovery.py
    system_clock.py
    sqlite.py
    acp.py
    codex_app_server.py
    codex_acp_server.py
    tmux_observer.py
    providers.py
    browser.py
    legacy_state.py
  product/
    __init__.py
    shell.py
    renderer.py
    slash_commands.py
    presenter.py
    bootstrap.py
    observer.py

tests/product_kernel/
  __init__.py
  fakes.py
  test_architecture.py
  test_context_firewall.py
  test_kernel_*.py
  fixtures/
  worker_contract.py
  test_sqlite_*.py
  test_application_*.py
  test_product_*.py
  test_*adapter*.py
  test_*acp*.py
  test_*execution*.py
  test_*observer*.py
  test_four_stage_e2e.py
  test_golden_acceptance_contract.py
```

No new file may exceed 500 lines without an explicit split in the same task.
The legacy `src/agentdeck/cli.py`, `state.py`, `conversation/`, and
`daemon/` remain compatibility code, not places to add new domain behavior.

## 3. Phase gates

- **R0 exit:** package boundary, import guard, context firewall test, hidden
  `agentdeck _product` entry; bare `agentdeck` unchanged.
- **R1 exit:** pure Kernel and SQLite pass entirely with Fake Ports; one writer,
  idempotency, transactions, recovery.
- **R2 exit:** deterministic first-run Product Shell, setup, slash commands,
  goal retention, exit/re-entry with no LLM.
- **R3 exit:** API and ACP Leader proposals become validated exact Mission
  Previews; drifted confirmation is impossible.
- **R4 exit:** Fake and real-adapter contract suites prove ACP Workers,
  permissions, four-stage scheduling, handoffs and recovery.
- **R5 exit:** tmux faithfully renders per-Agent event streams and explicit
  takeover without influencing execution truth.
- **R6 exit:** every non-success state has a redacted human Error Card, trace,
  recovery action and outcome-known classification.
- **R7 exit:** deterministic E2E, read-only real preflight, real four-Worker
  website Mission and human product acceptance pass in a disposable project.
- **R8 exit:** bare `agentdeck` switches to Product Shell, explicit migration
  and rollback exist, legacy structured commands remain script/debug surfaces.

---

## Phase R0 — Rewrite boundary

### Task 1: Create the new packages and executable architecture guard

**Authority:** Design sections 6, 7.3, 7.4, 19 R0.

**Files:**
- Create: `src/agentdeck/kernel/__init__.py`
- Create: `src/agentdeck/application/__init__.py`
- Create: `src/agentdeck/ports/__init__.py`
- Create: `src/agentdeck/adapters/__init__.py`
- Create: `src/agentdeck/product/__init__.py`
- Create: `tests/product_kernel/__init__.py`
- Create: `tests/product_kernel/test_architecture.py`
- Modify: `HISTORY.md`

**Forbidden legacy imports:** every new package initially forbids
`agentdeck.cli`, `agentdeck.state`, `agentdeck.models`,
`agentdeck.conversation`, `agentdeck.daemon`, `agentdeck.mission`, and
`agentdeck.mission_orchestration`.

**Approved legacy evidence:** none.

- [ ] **Step 1: Write the package-existence and import-boundary RED test**

```python
# tests/product_kernel/test_architecture.py
from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[2] / "src" / "agentdeck"
LAYERS = ("kernel", "application", "ports", "adapters", "product")
FORBIDDEN = {
    "agentdeck.cli",
    "agentdeck.state",
    "agentdeck.models",
    "agentdeck.conversation",
    "agentdeck.daemon",
    "agentdeck.mission",
    "agentdeck.mission_orchestration",
}
ALLOWED_LAYER_IMPORTS = {
    "kernel": ("agentdeck.kernel",),
    "ports": ("agentdeck.kernel", "agentdeck.ports"),
    "application": ("agentdeck.kernel", "agentdeck.ports", "agentdeck.application"),
    "adapters": ("agentdeck.kernel", "agentdeck.ports", "agentdeck.adapters"),
    "product": ("agentdeck.kernel", "agentdeck.application", "agentdeck.product"),
}


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def test_rewrite_packages_exist() -> None:
    for layer in LAYERS:
        assert (PACKAGE_ROOT / layer / "__init__.py").is_file(), layer


def test_kernel_and_application_do_not_import_legacy() -> None:
    for layer in ("kernel", "application"):
        for path in (PACKAGE_ROOT / layer).glob("*.py"):
            assert not (imported_modules(path) & FORBIDDEN), path


def test_only_adapters_may_import_admitted_legacy() -> None:
    for layer in ("kernel", "application", "ports", "product"):
        for path in (PACKAGE_ROOT / layer).glob("*.py"):
            assert not (imported_modules(path) & FORBIDDEN), path


def test_layer_dependency_direction() -> None:
    for layer, allowed in ALLOWED_LAYER_IMPORTS.items():
        for path in (PACKAGE_ROOT / layer).glob("*.py"):
            if path == PACKAGE_ROOT / "product" / "bootstrap.py":
                continue  # explicit composition root
            internal = {name for name in imported_modules(path) if name.startswith("agentdeck.")}
            assert all(name.startswith(allowed) for name in internal), (path, internal)
```

- [ ] **Step 2: Run the RED test**

Run:

```bash
conda run -n agentdeck pytest tests/product_kernel/test_architecture.py -q
```

Expected: FAIL at `test_rewrite_packages_exist` because the new package
directories do not exist.

- [ ] **Step 3: Add empty package markers only**

Each new `__init__.py` contains:

```python
"""AgentDeck Product Kernel Rewrite package."""
```

- [ ] **Step 4: Run the focused and legacy entrypoint regressions**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_architecture.py -q
conda run -n agentdeck pytest tests/test_cli_structured_output.py -q
```

Expected: both commands PASS.

- [ ] **Step 5: Update HISTORY and commit**

```bash
git add src/agentdeck/{kernel,application,ports,adapters,product} tests/product_kernel HISTORY.md
git commit -m "test: enforce product kernel package boundary"
```

### Task 2: Add the hidden Product development entry without changing bare AgentDeck

**Authority:** Design sections 5, 6, 19 R0, 19 R8.

**Files:**
- Create: `src/agentdeck/product/bootstrap.py`
- Create: `tests/product_kernel/test_dev_entry.py`
- Modify: `src/agentdeck/cli.py:18390-19255`
- Modify: `HISTORY.md`

**Forbidden legacy imports:** `product/bootstrap.py` cannot import legacy
Conversation, daemon, state, runtime, or CLI modules.

**Approved legacy evidence:** only `build_parser()` and `main()` as the
temporary composition root; no legacy behavior is reused inside Product.

- [ ] **Step 1: Write the RED tests**

```python
# tests/product_kernel/test_dev_entry.py
from agentdeck.cli import build_parser


def test_hidden_product_entry_is_parseable() -> None:
    args = build_parser().parse_args(["_product", "--diagnostic"])
    assert args.command == "_product"
    assert args.diagnostic is True


def test_product_bootstrap_diagnostic_is_human_text(capsys) -> None:
    from agentdeck.product.bootstrap import run_product_dev

    assert run_product_dev(diagnostic=True) == 0
    assert capsys.readouterr().out == (
        "AgentDeck Product Kernel development entry: ready\n"
    )
```

- [ ] **Step 2: Confirm RED**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_dev_entry.py -q
```

Expected: FAIL because `agentdeck.product.bootstrap` and the `_product`
subcommand do not exist.

- [ ] **Step 3: Implement the minimal bootstrap and lazy CLI bridge**

```python
# src/agentdeck/product/bootstrap.py
from __future__ import annotations


def run_product_dev(*, diagnostic: bool = False) -> int:
    if diagnostic:
        print("AgentDeck Product Kernel development entry: ready")
        return 0
    print("AgentDeck Product Kernel is under development.")
    return 0
```

Add one legacy CLI handler near the other top-level command handlers:

```python
def product_dev_command(args: argparse.Namespace) -> int:
    from agentdeck.product.bootstrap import run_product_dev

    return run_product_dev(diagnostic=bool(args.diagnostic))
```

Add the hidden parser before `return parser`:

```python
product_dev = subparsers.add_parser("_product", help=argparse.SUPPRESS)
product_dev.add_argument("--diagnostic", action="store_true")
product_dev.set_defaults(func=product_dev_command)
```

- [ ] **Step 4: Verify hidden and bare entry behavior**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_dev_entry.py -q
conda run -n agentdeck pytest tests/test_conversation_acceptance.py -q
conda run -n agentdeck agentdeck _product --diagnostic
```

Expected: tests PASS and the command prints exactly the one diagnostic line.
Bare-entry acceptance remains unchanged.

- [ ] **Step 5: Update HISTORY and commit**

```bash
git add src/agentdeck/product/bootstrap.py src/agentdeck/cli.py tests/product_kernel/test_dev_entry.py HISTORY.md
git commit -m "feat: add hidden product kernel entry"
```

### Task 3: Make legacy admission and active-document consistency executable

**Authority:** Design sections 7.1-7.4, 20.1, 21.

**Files:**
- Create: `docs/migrations/product-kernel-legacy-reuse-register.md`
- Create: `tests/product_kernel/test_context_firewall.py`
- Modify: `HISTORY.md`

**Forbidden legacy imports:** all; this task reads paths and Markdown only.

**Approved legacy evidence:** none; the initial register is empty.

- [ ] **Step 1: Write the RED tests**

```python
# tests/product_kernel/test_context_firewall.py
from pathlib import Path

ROOT = Path(__file__).parents[2]
ACTIVE = (
    "README.md",
    "README.zh-CN.md",
    "AGENTS.md",
    "AGENT.md",
    "CLAUDE.md",
    "docs/handoff/current-development-state.md",
    "docs/roadmap/product-north-star.md",
    "docs/roadmap/ultimate-goal-roadmap.md",
    "docs/superpowers/specs/2026-07-18-agentdeck-product-kernel-rewrite-design.md",
)
OLD_MARKERS = (
    "2026-07-17-m2c-",
    "2026-07-13-agentdeck-project-daemon",
    "agentdeck-v1-kernel-reset",
)


def test_only_one_rewrite_spec_and_no_old_plan() -> None:
    specs = tuple((ROOT / "docs/superpowers/specs").glob("*.md"))
    plans = tuple((ROOT / "docs/superpowers/plans").glob("*.md"))
    assert [path.name for path in specs] == [
        "2026-07-18-agentdeck-product-kernel-rewrite-design.md"
    ]
    assert [path.name for path in plans] == [
        "2026-07-18-agentdeck-product-kernel-rewrite.md"
    ]


def test_active_documents_do_not_restore_old_authority() -> None:
    for relative in ACTIVE:
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert not any(marker in text for marker in OLD_MARKERS), relative


def test_legacy_reuse_register_exists_and_starts_empty() -> None:
    text = (
        ROOT / "docs/migrations/product-kernel-legacy-reuse-register.md"
    ).read_text(encoding="utf-8")
    assert "Status: no legacy code admitted" in text
    assert "| none |" in text
```

- [ ] **Step 2: Confirm RED**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_context_firewall.py -q
```

Expected: FAIL because the reuse register does not exist.

- [ ] **Step 3: Create the exact initial register**

```markdown
# Product Kernel Legacy Reuse Register

Status: no legacy code admitted

| Legacy module | New Adapter | Port | Characterization test | Decision |
|---|---|---|---|---|
| none | none | none | none | not admitted |

Every future row must be introduced by the same commit as its characterization
test and Adapter boundary. A row never grants Kernel or Application imports.
```

- [ ] **Step 4: Verify**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_context_firewall.py tests/product_kernel/test_architecture.py -q
```

Expected: PASS.

- [ ] **Step 5: Update HISTORY and commit**

```bash
git add docs/migrations/product-kernel-legacy-reuse-register.md tests/product_kernel/test_context_firewall.py HISTORY.md
git commit -m "test: make rewrite context firewall executable"
```

## Phase R1 — Pure Kernel and SQLite authority

### Task 4: Add injected time facts, immutable events, and stable diagnostics

**Authority:** Design sections 8.6, 15, 17.1, 19 R1.

**Files:**
- Create: `src/agentdeck/ports/clock.py`
- Create: `src/agentdeck/kernel/events.py`
- Create: `src/agentdeck/kernel/diagnostics.py`
- Create: `src/agentdeck/adapters/system_clock.py`
- Create: `tests/product_kernel/fakes.py`
- Create: `tests/product_kernel/test_kernel_diagnostics.py`
- Modify: `HISTORY.md`

**Forbidden legacy imports:** all legacy AgentDeck modules.

**Approved legacy evidence:** none.

- [ ] **Step 1: Write the RED test**

```python
# tests/product_kernel/test_kernel_diagnostics.py
from datetime import datetime, timezone

from agentdeck.kernel.diagnostics import Diagnostic, Severity
from agentdeck.kernel.events import DomainEvent
from tests.product_kernel.fakes import FrozenClock


def test_diagnostic_and_event_are_clocked_immutable_facts() -> None:
    clock = FrozenClock(datetime(2026, 7, 18, tzinfo=timezone.utc))
    diagnostic = Diagnostic.create(
        code="leader_unavailable",
        stage="discovery",
        severity=Severity.ERROR,
        actor="codex",
        summary="Codex is not ready.",
        cause="The configured executable was not found.",
        impact="The Mission did not start.",
        protection="No fallback transport was selected.",
        recovery_actions=("Run /setup.",),
        retryable=True,
        outcome_known=True,
        occurred_at=clock.now().isoformat(),
    )
    event = DomainEvent.create(
        kind="diagnostic_recorded",
        aggregate_type="product_session",
        aggregate_id="psn_test",
        payload={"code": diagnostic.code},
        occurred_at=clock.now().isoformat(),
    )
    assert diagnostic.occurred_at == "2026-07-18T00:00:00+00:00"
    assert event.payload == (("code", "leader_unavailable"),)
```

- [ ] **Step 2: Confirm RED**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_kernel_diagnostics.py -q
```

Expected: FAIL because the new clock, event, and diagnostic modules do not
exist.

- [ ] **Step 3: Implement the exact minimal types**

```python
# src/agentdeck/ports/clock.py
from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...
```

```python
# src/agentdeck/adapters/system_clock.py
from datetime import datetime, timezone


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)
```

```python
# tests/product_kernel/fakes.py
from dataclasses import dataclass
from datetime import datetime


@dataclass
class FrozenClock:
    value: datetime

    def now(self) -> datetime:
        return self.value
```

```python
# src/agentdeck/kernel/events.py
from dataclasses import dataclass
from typing import Mapping
from uuid import uuid4

@dataclass(frozen=True)
class DomainEvent:
    event_id: str
    kind: str
    aggregate_type: str
    aggregate_id: str
    payload: tuple[tuple[str, object], ...]
    occurred_at: str

    @classmethod
    def create(
        cls, *, kind: str, aggregate_type: str, aggregate_id: str,
        payload: Mapping[str, object], occurred_at: str,
    ) -> "DomainEvent":
        return cls(
            event_id=f"evt_{uuid4().hex}",
            kind=kind,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=tuple(sorted(payload.items())),
            occurred_at=occurred_at,
        )
```

```python
# src/agentdeck/kernel/diagnostics.py
from dataclasses import dataclass
from enum import StrEnum

class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class Diagnostic:
    code: str
    stage: str
    severity: Severity
    actor: str
    summary: str
    cause: str
    impact: str
    protection: str
    recovery_actions: tuple[str, ...]
    retryable: bool
    outcome_known: bool
    occurred_at: str
    mission_id: str | None = None
    task_id: str | None = None
    attempt_id: str | None = None
    trace_id: str | None = None

    @classmethod
    def create(cls, *, occurred_at: str, **facts: object) -> "Diagnostic":
        return cls(**facts, occurred_at=occurred_at)
```

- [ ] **Step 4: Run focused tests and architecture guard**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_kernel_diagnostics.py tests/product_kernel/test_architecture.py -q
```

Expected: PASS.

- [ ] **Step 5: Update HISTORY and commit**

```bash
git add src/agentdeck/{kernel,ports,adapters} tests/product_kernel HISTORY.md
git commit -m "feat: add kernel event and diagnostic facts"
```

### Task 5: Model ProductSession, Agent Backend, Instance, and Role identity

**Authority:** Design sections 5.2, 8.1–8.3, 19 R1.

**Files:**
- Create: `src/agentdeck/kernel/session.py`
- Create: `src/agentdeck/kernel/agents.py`
- Create: `tests/product_kernel/test_kernel_session.py`
- Create: `tests/product_kernel/test_kernel_agents.py`
- Modify: `HISTORY.md`

**Forbidden legacy imports:** all legacy modules listed in section 1.

**Approved legacy evidence:** none; legacy AgentRecord and conversation models
are specifically not reused.

- [ ] **Step 1: Write RED tests for legal session transitions and distinct instances**

```python
# tests/product_kernel/test_kernel_session.py
import pytest

from agentdeck.kernel.session import ProductSession, SessionState, TransitionError


def test_session_accepts_only_declared_transition() -> None:
    session = ProductSession.new("ses_1", "/tmp/project")
    assert session.transition(SessionState.READY).state is SessionState.READY
    with pytest.raises(TransitionError):
        session.transition(SessionState.COMPLETED)


def test_open_goal_is_retained_during_setup() -> None:
    session = ProductSession.new("ses_1", "/tmp/project")
    updated = session.retain_goal("build the page")
    assert updated.pending_goal == "build the page"
    assert session.pending_goal is None
```

```python
# tests/product_kernel/test_kernel_agents.py
from agentdeck.kernel.agents import AgentBackend, AgentInstance, AgentRole


def test_same_backend_roles_require_distinct_instances() -> None:
    backend = AgentBackend("codex-cli", "ACP", "0.131.0")
    implementer = AgentInstance("agt_1", backend, AgentRole.IMPLEMENTER, "acp_1")
    reviser = AgentInstance("agt_2", backend, AgentRole.REVISER, "acp_2")
    assert implementer.instance_id != reviser.instance_id
    assert implementer.session_id != reviser.session_id
```

- [ ] **Step 2: Confirm RED**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_kernel_session.py tests/product_kernel/test_kernel_agents.py -q
```

Expected: collection fails because the two Kernel modules do not exist.

- [ ] **Step 3: Implement immutable identities and explicit transitions**

```python
# src/agentdeck/kernel/session.py
from dataclasses import dataclass, replace
from enum import StrEnum


class SessionState(StrEnum):
    SETUP = "setup"
    READY = "ready"
    DRAFTING = "drafting"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    PAUSED = "paused"
    NEEDS_ATTENTION = "needs_attention"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TransitionError(ValueError):
    pass


_TRANSITIONS = {
    SessionState.SETUP: {SessionState.READY, SessionState.CANCELLED},
    SessionState.READY: {SessionState.DRAFTING, SessionState.CANCELLED},
    SessionState.DRAFTING: {SessionState.AWAITING_CONFIRMATION, SessionState.FAILED},
    SessionState.AWAITING_CONFIRMATION: {SessionState.DRAFTING, SessionState.RUNNING},
    SessionState.RUNNING: {SessionState.AWAITING_APPROVAL, SessionState.PAUSED,
                           SessionState.NEEDS_ATTENTION, SessionState.COMPLETED,
                           SessionState.FAILED, SessionState.CANCELLED},
    SessionState.AWAITING_APPROVAL: {SessionState.RUNNING, SessionState.FAILED,
                                     SessionState.CANCELLED},
    SessionState.PAUSED: {SessionState.RUNNING, SessionState.CANCELLED},
    SessionState.NEEDS_ATTENTION: {SessionState.RUNNING, SessionState.FAILED,
                                   SessionState.CANCELLED},
    SessionState.COMPLETED: set(), SessionState.FAILED: set(),
    SessionState.CANCELLED: set(),
}


@dataclass(frozen=True)
class ProductSession:
    session_id: str
    project_root: str
    state: SessionState
    pending_goal: str | None = None

    @classmethod
    def new(cls, session_id: str, project_root: str) -> "ProductSession":
        return cls(session_id, project_root, SessionState.SETUP)

    def retain_goal(self, goal: str) -> "ProductSession":
        return replace(self, pending_goal=goal)

    def transition(self, target: SessionState) -> "ProductSession":
        if target not in _TRANSITIONS[self.state]:
            raise TransitionError(f"illegal session transition: {self.state}->{target}")
        return replace(self, state=target)
```

`agents.py` defines frozen AgentBackend, AgentInstance, and exact
Leader/implementer/reviewer/reviser/acceptance-reviewer roles; it rejects empty
instance and ACP session IDs.

- [ ] **Step 4: Verify and commit**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_kernel_session.py tests/product_kernel/test_kernel_agents.py tests/product_kernel/test_architecture.py -q
git add src/agentdeck/kernel tests/product_kernel HISTORY.md
git commit -m "feat: model product session and agent identity"
```

### Task 6: Enforce the three permission profiles and monotonic narrowing

**Authority:** Design sections 8.7, 9, 13.

**Files:**
- Create: `src/agentdeck/kernel/permissions.py`
- Create: `tests/product_kernel/test_kernel_permissions.py`
- Modify: `HISTORY.md`

**Forbidden legacy imports:** all legacy modules; no native backend flags in
the Kernel.

**Approved legacy evidence:** none.

- [ ] **Step 1: Write the RED invariant test**

```python
from agentdeck.kernel.permissions import Effect, PermissionError, PermissionProfile, PermissionScope
import pytest


def test_permission_can_narrow_but_never_expand() -> None:
    mission = PermissionScope.for_profile(PermissionProfile.APPROVE_FOR_ME)
    task = mission.narrow({Effect.READ, Effect.WRITE_PROJECT})
    assert task.allows(Effect.WRITE_PROJECT)
    with pytest.raises(PermissionError, match="cannot expand"):
        task.narrow({*task.effects, Effect.PUBLISH})


def test_full_access_still_records_a_decision() -> None:
    scope = PermissionScope.for_profile(PermissionProfile.FULL_ACCESS)
    decision = scope.decide(Effect.NETWORK, actor="agt_1")
    assert decision.allowed is True
    assert decision.auditable is True
```

- [ ] **Step 2: Confirm RED**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_kernel_permissions.py -q
```

Expected: missing module.

- [ ] **Step 3: Implement the pure profile/effect lattice**

Define ASK_FOR_APPROVAL, APPROVE_FOR_ME, FULL_ACCESS; effects read,
write_project, command_project, network, write_external, credential,
destructive, and publish; frozen PermissionScope and PermissionDecision.
narrow() performs a subset check. decide() returns requires-human,
requires-independent-reviewer, or allowed facts but performs no approval.
Credential, destructive, external-write, and publish effects never become
silently self-reviewed by an executing Agent.

- [ ] **Step 4: Verify and commit**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_kernel_permissions.py tests/product_kernel/test_architecture.py -q
git add src/agentdeck/kernel/permissions.py tests/product_kernel/test_kernel_permissions.py HISTORY.md
git commit -m "feat: enforce product permission hierarchy"
```

### Task 7: Build canonical Mission Preview and exact confirmation

**Authority:** Design sections 5.3, 8.4, 11.3, 13.

**Files:**
- Create: `src/agentdeck/kernel/mission.py`
- Create: `tests/product_kernel/test_kernel_mission.py`
- Modify: `HISTORY.md`

**Forbidden legacy imports:** legacy mission/orchestration/models/state.

**Approved legacy evidence:** none; old Preview hashes are not authoritative.

- [ ] **Step 1: Write RED tests for determinism and drift rejection**

```python
import pytest

from agentdeck.kernel.mission import MissionDraft, PreviewDriftError


def draft() -> MissionDraft:
    return MissionDraft.coding_default(
        draft_id="drf_1", objective="build page", project_root="/tmp/p",
        leader_backend="codex-cli", leader_model="native-default",
        permission_profile="approve_for_me",
    )


def test_preview_hash_is_canonical() -> None:
    assert draft().preview(version=1).content_hash == draft().preview(version=1).content_hash


def test_confirmation_consumes_only_current_exact_preview() -> None:
    old = draft().preview(version=1)
    current = draft().revise(objective="build accessible page").preview(version=2)
    with pytest.raises(PreviewDriftError):
        current.confirm(preview_id=old.preview_id, content_hash=old.content_hash)
```

- [ ] **Step 2: Confirm RED**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_kernel_mission.py -q
```

Expected: missing module.

- [ ] **Step 3: Implement canonical projection and immutable version**

Use canonical JSON with sorted keys, compact separators, UTF-8, and SHA-256.
coding_default() emits exactly four ordered Task definitions with separate
Agent Instances, ACP routes, dependencies, criteria, non-goals, risks,
max_attempts=2, max_revision_cycles=1, and max_acp_reconnects=1. preview()
includes its version in the canonical payload. confirm() returns immutable
ConfirmedMissionVersion and compares both ID and hash with compare_digest.

- [ ] **Step 4: Verify and commit**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_kernel_mission.py tests/product_kernel/test_kernel_permissions.py -q
git add src/agentdeck/kernel/mission.py tests/product_kernel/test_kernel_mission.py HISTORY.md
git commit -m "feat: add exact mission preview confirmation"
```

### Task 8: Model Attempts, Handoffs, Evidence, review findings, and acceptance

**Authority:** Design sections 8.5–8.6, 13, 17, 23.

**Files:**
- Create: `src/agentdeck/kernel/execution.py`
- Create: `tests/product_kernel/test_kernel_execution.py`
- Create: `tests/product_kernel/test_kernel_execution_results.py`
- Modify: `HISTORY.md`

**Forbidden legacy imports:** legacy job/message/reply/workflow models.

**Approved legacy evidence:** none.

- [ ] **Step 1: Write RED tests for immutable Attempts and typed evidence**

```python
import pytest

from agentdeck.kernel.execution import Attempt, AttemptState, Evidence, Handoff, ResultError


def test_retry_creates_new_attempt_and_preserves_failure() -> None:
    first = Attempt.pending("att_1", "tsk_1", 1).fail("worker_failed")
    second = first.retry("att_2")
    assert first.state is AttemptState.FAILED
    assert second.attempt_id == "att_2" and second.ordinal == 2


def test_worker_prose_cannot_satisfy_acceptance() -> None:
    with pytest.raises(ResultError, match="typed evidence"):
        Evidence.acceptance("looks good", source_kind="worker_message")


def test_handoff_hash_covers_lineage_and_evidence() -> None:
    handoff = Handoff.create("hnd_1", "att_1", "tsk_2", "done", ("ev_1",))
    assert len(handoff.content_hash) == 64
```

- [ ] **Step 2: Confirm RED, then implement**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_kernel_execution.py -q
```

Expected: missing module. Implement the exact Attempt states in section 8.5,
legal transitions, retry constructor, typed Evidence kinds, ReviewFinding with
scope/evidence/severity, Handoff canonical hash, and AcceptanceResult requiring
evidence IDs for every criterion.

- [ ] **Step 3: Verify and commit**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_kernel_execution.py tests/product_kernel/test_kernel_execution_results.py tests/product_kernel/test_kernel_mission.py -q
git add src/agentdeck/kernel/execution.py tests/product_kernel/test_kernel_execution.py tests/product_kernel/test_kernel_execution_results.py HISTORY.md
git commit -m "feat: add execution lineage domain model"
```

### Task 9: Define Store Port and create the project-local SQLite schema

**Authority:** Design sections 10.1–10.3, 10.5, 19 R1.

**Files:**
- Create: `src/agentdeck/ports/store.py`
- Create: `src/agentdeck/adapters/sqlite.py`
- Create: `tests/product_kernel/test_sqlite_schema.py`
- Modify: `HISTORY.md`

**Forbidden legacy imports:** state, models, JSON/JSONL stores.

**Approved legacy evidence:** legacy state filenames may be used only as
migration-test input in R8, not here.

- [ ] **Step 1: Write the RED schema and pragma tests**

```python
from agentdeck.adapters.sqlite import SQLiteStore


TABLES = {"schema_metadata", "projects", "product_sessions", "conversation_turns",
          "agent_instances", "missions", "mission_versions", "tasks", "attempts",
          "handoffs", "approvals", "evidence", "commands", "events"}


def test_store_creates_only_project_local_database(tmp_path) -> None:
    store = SQLiteStore.open(tmp_path)
    assert store.path == tmp_path / ".agentdeck" / "agentdeck.db"
    found = {row[0] for row in store.connection.execute(
        "select name from sqlite_master where type='table'")}
    assert TABLES <= found


def test_sqlite_uses_safe_mvp_pragmas(tmp_path) -> None:
    store = SQLiteStore.open(tmp_path)
    assert store.connection.execute("pragma journal_mode").fetchone()[0] == "delete"
    assert store.connection.execute("pragma foreign_keys").fetchone()[0] == 1
    assert store.connection.execute("pragma synchronous").fetchone()[0] == 2
```

- [ ] **Step 2: Confirm RED, implement, and verify**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_sqlite_schema.py -q
```

Expected: missing adapter. Store protocol exposes transaction, command
lookup/record, aggregate load/save, event append, and close. SQLiteStore.open()
resolves the supplied root exactly, creates only root/.agentdeck, uses a
five-second busy timeout, foreign keys, DELETE journal, FULL synchronous, and
an idempotent version-1 migration inside BEGIN IMMEDIATE. No column stores
credentials or raw protocol/terminal frames.

```bash
conda run -n agentdeck pytest tests/product_kernel/test_sqlite_schema.py tests/product_kernel/test_architecture.py -q
git add src/agentdeck/ports/store.py src/agentdeck/adapters/sqlite.py tests/product_kernel/test_sqlite_schema.py HISTORY.md
git commit -m "feat: add project local sqlite authority"
```

### Task 10: Make writes atomic, idempotent, single-writer, and recoverable

**Authority:** Design sections 10.2–10.4, 13.

**Files:**
- Modify: `src/agentdeck/ports/store.py`
- Modify: `src/agentdeck/adapters/sqlite.py`
- Create: `src/agentdeck/application/recovery_service.py`
- Create: `tests/product_kernel/test_sqlite_transactions.py`
- Create: `tests/product_kernel/test_recovery_service.py`
- Modify: `HISTORY.md`

**Forbidden legacy imports:** legacy state/recovery/daemon modules.

**Approved legacy evidence:** none.

- [ ] **Step 1: Write RED transaction, idempotency, and restart tests**

```python
from agentdeck.adapters.sqlite import SQLiteStore
import pytest


def test_state_and_event_rollback_together(tmp_path) -> None:
    store = SQLiteStore.open(tmp_path)
    with pytest.raises(RuntimeError):
        with store.command("cmd_1", "confirm") as tx:
            tx.save_session({"session_id": "ses_1", "state": "running"})
            tx.append_event({"event_id": "evt_1", "kind": "confirmed"})
            raise RuntimeError("stop")
    assert store.count("product_sessions") == store.count("events") == 0


def test_repeated_command_returns_first_result(tmp_path) -> None:
    store = SQLiteStore.open(tmp_path)
    first = store.execute_once("cmd_1", "confirm", lambda tx: {"mission_id": "mis_1"})
    second = store.execute_once("cmd_1", "confirm", lambda tx: {"mission_id": "mis_2"})
    assert second == first == {"mission_id": "mis_1"}
```

```python
# tests/product_kernel/test_recovery_service.py
def test_restart_marks_unreconciled_running_attempt_interrupted(store, recovery_service) -> None:
    store.seed_running_attempt("att_1", acp_session_id="gone")
    report = recovery_service.reconcile()
    assert report.interrupted == ("att_1",)


def test_disconnect_after_side_effect_is_outcome_unknown(store, recovery_service) -> None:
    store.seed_running_attempt("att_1", effect_observed=True)
    report = recovery_service.reconcile()
    assert report.outcome_unknown == ("att_1",)
```

- [ ] **Step 2: Confirm RED and implement**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_sqlite_transactions.py tests/product_kernel/test_recovery_service.py -q
```

Expected: APIs absent. Add an atomic project writer lock before write mode.
execute_once() persists command result, state, and events in one transaction;
duplicate IDs return the stored result. Recovery uses only Store and Transport
Ports: a confirmed reconnect resumes, no-side-effect loss becomes interrupted,
and observed or uncertain side effects become outcome_unknown. tmux is never
consulted.

- [ ] **Step 3: Verify R1 and commit**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_kernel_*.py tests/product_kernel/test_sqlite_*.py tests/product_kernel/test_recovery_service.py tests/product_kernel/test_architecture.py -q
conda run -n agentdeck pytest tests/test_cli_structured_output.py tests/test_state.py -q
git add src/agentdeck/ports/store.py src/agentdeck/adapters/sqlite.py src/agentdeck/application/recovery_service.py tests/product_kernel HISTORY.md
git commit -m "feat: make product state atomic and recoverable"
```

## Phase R2 — ProductSession and first-run shell

### Task 11: Implement configuration precedence and read-only tool discovery

**Authority:** Design sections 5.1, 11.1, 16.

**Files:**
- Create: src/agentdeck/adapters/config.py
- Create: src/agentdeck/adapters/discovery.py
- Create: tests/product_kernel/test_config_adapter.py
- Create: tests/product_kernel/test_discovery_adapter.py
- Modify: HISTORY.md

**Forbidden legacy imports:** legacy config/doctor/setup modules.

**Approved legacy evidence:** executable names from the retained capability
inventory may inform fixtures; no code import.

- [ ] **Step 1: Write RED tests for precedence and non-mutating discovery**

```python
from agentdeck.adapters.config import ConfigResolver
from agentdeck.adapters.discovery import discover_tools


def test_config_precedence_is_session_project_global_discovery() -> None:
    resolver = ConfigResolver(
        discovered={"leader": "codex-cli", "model": "native-default"},
        global_values={"leader": "claude-cli"},
        project_values={"leader": "api:deepseek", "model": "deepseek-chat"},
        session_values={"leader": "codex-cli"},
    )
    assert resolver.resolve("leader").value == "codex-cli"
    assert resolver.resolve("model").value == "deepseek-chat"


def test_discovery_uses_given_path_without_writing_or_prompting(tmp_path) -> None:
    bin_dir = tmp_path / "bin"
    make_executable(bin_dir / "codex", "codex-cli 1.0")
    before = snapshot(tmp_path)
    facts = discover_tools(path=str(bin_dir), version_runner=fake_version_runner)
    assert facts["codex"].resolved_path == str(bin_dir / "codex")
    assert snapshot(tmp_path) == before
```

- [ ] **Step 2: Confirm RED and implement**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_config_adapter.py tests/product_kernel/test_discovery_adapter.py -q
```

Expected: missing adapters. ConfigResolver returns value and source. Discovery
uses shutil.which with the actual PATH, bounded version probes, and capability
metadata; it never installs, authenticates, edits PATH, writes source, or sends
a model prompt. Readiness states are missing, discovered, authenticated,
acp_available, and ready. Passive probes supply authentication and ACP facts;
executable presence alone never implies readiness.

- [ ] **Step 3: Verify and commit**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_config_adapter.py tests/product_kernel/test_discovery_adapter.py tests/product_kernel/test_architecture.py -q
git add src/agentdeck/adapters/config.py src/agentdeck/adapters/discovery.py tests/product_kernel HISTORY.md
git commit -m "feat: discover product backends without side effects"
```

### Task 12: Implement ProductSession setup and durable goal retention

**Authority:** Design sections 5.1–5.2, 10, 16, 17.2.

**Files:**
- Create: src/agentdeck/application/session_service.py
- Create: src/agentdeck/application/session_validation.py
- Create: tests/product_kernel/test_session_service.py
- Create: tests/product_kernel/test_session_service_quality.py
- Modify: src/agentdeck/adapters/sqlite.py
- Modify: src/agentdeck/adapters/sqlite_validation.py
- Modify: HISTORY.md

**Forbidden legacy imports:** ConversationSession, daemon, state, legacy setup.

**Approved legacy evidence:** none.

- [ ] **Step 1: Write RED product-regression tests**

```python
def test_goal_survives_setup_and_resumes(session_service) -> None:
    result = session_service.accept_text("Build an accessible page")
    assert result.mode == "setup_required"
    session_service.configure(leader="codex-cli", model="native-default",
                              permission="approve_for_me")
    resumed = session_service.resume()
    assert resumed.mode == "goal_ready"
    assert resumed.goal == "Build an accessible page"


def test_unavailable_provider_is_never_silently_selected(session_service) -> None:
    result = session_service.configure(leader="api:deepseek", model="deepseek-chat")
    assert result.accepted is False
    assert result.diagnostic.code == "leader_credential_unavailable"
    assert session_service.current().leader_backend is None
```

- [ ] **Step 2: Confirm RED, implement, verify**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_session_service.py -q
```

Expected: missing service. SessionService creates or loads one ProductSession,
persists every conversation turn, retains an open goal before setup, accepts
only discovered and validated selections, and resumes the retained goal
without asking the user to repeat it. Every mutation uses a stable command ID.

```bash
conda run -n agentdeck pytest tests/product_kernel/test_session_service.py tests/product_kernel/test_sqlite_transactions.py -q
git add src/agentdeck/application/session_service.py src/agentdeck/application/session_validation.py src/agentdeck/adapters/sqlite.py src/agentdeck/adapters/sqlite_validation.py tests/product_kernel/test_session_service.py tests/product_kernel/test_session_service_quality.py HISTORY.md
git commit -m "feat: retain goals across product setup"
```

### Task 13: Add deterministic slash commands and human presenters

**Authority:** Design sections 5.1–5.3, 15–16.

**Files:**
- Create: src/agentdeck/product/slash_commands.py
- Create: src/agentdeck/product/presenter.py
- Create: src/agentdeck/product/renderer.py
- Create: tests/product_kernel/test_slash_commands.py
- Create: tests/product_kernel/test_product_renderer.py
- Modify: HISTORY.md

**Forbidden legacy imports:** legacy router/cards/contracts/renderers.

**Approved legacy evidence:** command names in design section 5.2 only.

- [ ] **Step 1: Write RED parser and no-raw-JSON tests**

```python
from agentdeck.product.slash_commands import CommandKind, parse_command
from agentdeck.product.renderer import render


def test_slash_parser_is_exact_and_llm_free() -> None:
    assert parse_command("/permissions full-access").kind is CommandKind.PERMISSIONS
    assert parse_command("please /exit later") is None


def test_interactive_renderer_never_dumps_raw_json() -> None:
    text = render({"mode": "status", "state": "ready", "agents": []})
    assert text == "AgentDeck is ready.\nAgents: none"
    assert '{"mode":' not in text
```

- [ ] **Step 2: Confirm RED and implement**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_slash_commands.py tests/product_kernel/test_product_renderer.py -q
```

Expected: missing modules. Implement an exact deterministic parser for all
commands in design section 5.2. Presenter dataclasses carry human fields and
stable Diagnostic facts. Renderer has explicit cases for setup, status,
Mission Preview, running, approval, diagnosis, exit, and final result; unknown
modes fail closed instead of printing repr or JSON.

- [ ] **Step 3: Verify and commit**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_slash_commands.py tests/product_kernel/test_product_renderer.py -q
git add src/agentdeck/product tests/product_kernel HISTORY.md
git commit -m "feat: add deterministic product controls and rendering"
```

### Task 14: Build the foreground Product Shell and composition root

**Authority:** Design sections 5, 6, 16, 19 R2.

**Files:**
- Create: src/agentdeck/product/shell.py
- Modify: src/agentdeck/product/bootstrap.py
- Create: tests/product_kernel/test_product_shell.py
- Modify: HISTORY.md

**Forbidden legacy imports:** Product Shell imports only Application and Product;
composition root may import Adapters. No legacy shell or router.

**Approved legacy evidence:** none.

- [ ] **Step 1: Write a transcript RED test**

```python
def test_first_run_shell_retains_goal_and_resumes_after_setup(shell_harness) -> None:
    transcript = shell_harness.run([
        "Build an accessible page",
        "/leader codex-cli",
        "/model native-default",
        "/permissions approve-for-me",
        "/setup confirm",
        "/exit",
    ])
    assert "I saved your goal while setup completes." in transcript
    assert "Goal ready: Build an accessible page" in transcript
    assert "{" not in transcript


def test_help_status_and_setup_work_without_llm(shell_harness) -> None:
    transcript = shell_harness.run(["/help", "/status", "/setup", "/exit"])
    assert "Select Leader" in transcript
    assert shell_harness.leader.calls == []
```

- [ ] **Step 2: Confirm RED and implement**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_product_shell.py -q
```

Expected: missing shell. ProductShell owns input and delegates mutations to
Application services. bootstrap creates config, discovery, SQLiteStore, clock,
services, presenter, and shell through injected factories; tests never touch a
real terminal. The hidden agentdeck _product entry calls this composition root.

- [ ] **Step 3: Verify and commit**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_product_*.py tests/product_kernel/test_session_service.py tests/product_kernel/test_config_adapter.py tests/product_kernel/test_discovery_adapter.py -q
conda run -n agentdeck pytest tests/test_cli_structured_output.py tests/test_conversation_shell_cli.py -q
git add src/agentdeck/product tests/product_kernel HISTORY.md
git commit -m "feat: add foreground product session shell"
```

### Task 15: Make exit, interruption confirmation, and re-entry deterministic

**Authority:** Design sections 5.2, 10.4, 17.2, 18, 23.

**Files:**
- Modify: src/agentdeck/application/session_service.py
- Modify: src/agentdeck/product/shell.py
- Create: tests/product_kernel/test_product_reentry.py
- Modify: HISTORY.md

**Forbidden legacy imports:** daemon/background continuation and lifecycle.

**Approved legacy evidence:** none.

- [ ] **Step 1: Write RED exit/re-entry tests**

```python
def test_exit_persists_idle_session(shell_factory, project) -> None:
    first = shell_factory(project).run(["/exit"])
    assert "Session saved." in first
    second = shell_factory(project).run(["/status", "/exit"])
    assert "Restored session" in second


def test_exit_with_active_attempt_requires_exact_confirmation(shell_factory, project) -> None:
    shell = shell_factory(project, active_attempt="att_1")
    transcript = shell.run(["/exit", "no", "/status", "/exit", "yes"])
    assert "Exit will interrupt att_1" in transcript
    assert shell.transport.cancel_calls == ["att_1"]
    assert shell.store.attempt("att_1")["state"] == "interrupted"
```

- [ ] **Step 2: Confirm RED and implement**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_product_reentry.py -q
```

Expected: exit lacks a durable confirmation protocol. Persist the pending exit
action with active Attempt identity; only exact confirmation cancels through
Worker Port, records interrupted, flushes state, closes the writer, and exits.
Re-entry loads the latest nonterminal ProductSession and runs RecoveryService
before accepting input.

- [ ] **Step 3: Verify R2 exit gate and commit**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_product_*.py tests/product_kernel/test_recovery_service.py -q
git add src/agentdeck/application/session_service.py src/agentdeck/product/shell.py tests/product_kernel/test_product_reentry.py HISTORY.md
git commit -m "feat: persist product exit and reentry"
```

## Phase R3 — Leader and exact Mission Preview

### Task 16: Define the Leader Port and strict proposal validator

**Authority:** Design sections 8.3–8.4, 11.2–11.3, 17.1.

**Files:**
- Create: src/agentdeck/ports/leader.py
- Create: src/agentdeck/ports/leader_schema.py
- Create: src/agentdeck/application/leader_service.py
- Create: tests/product_kernel/test_leader_contract.py
- Create: tests/product_kernel/test_leader_service.py
- Modify: HISTORY.md

**Forbidden legacy imports:** legacy provider response parsers, orchestration,
Mission schema, and CLI leader code.

**Approved legacy evidence:** none.

- [ ] **Step 1: Write RED tests for strict untrusted proposal handling**

```python
from agentdeck.ports.leader import LeaderProposal, ProposalError


def test_proposal_rejects_unknown_and_missing_fields() -> None:
    payload = valid_proposal()
    payload["dispatch_now"] = True
    with pytest.raises(ProposalError, match="unknown field"):
        LeaderProposal.from_mapping(payload)


def test_proposal_cannot_confirm_or_raise_permissions() -> None:
    payload = valid_proposal()
    payload["permission_profile"] = "full_access"
    with pytest.raises(ProposalError, match="permission ceiling"):
        LeaderProposal.from_mapping(payload, ceiling="approve_for_me")


def test_one_bounded_schema_repair_preserves_category(fake_leader, leader_service) -> None:
    fake_leader.results = [invalid_schema(), valid_proposal()]
    result = leader_service.propose(request())
    assert result.repair_count == 1
    assert fake_leader.calls == 2
```

- [ ] **Step 2: Confirm RED and implement**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_leader_contract.py tests/product_kernel/test_leader_service.py -q
```

Expected: missing Port and service. Leader Port exposes propose_mission() with
user goal, compact project context, available Agents, permission ceiling, and
resolved model. LeaderProposal accepts an exact closed mapping; semantic
validation enforces project scope, fixed four-stage graph, known backend and
ACP route IDs, bounded budgets, nonempty evidence criteria, and no permission
expansion. LeaderService allows one schema-repair request and preserves
timeout, nonzero, authentication, transport, schema, semantic, cancellation,
and oversize diagnostic codes.

- [ ] **Step 3: Verify and commit**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_leader_contract.py tests/product_kernel/test_leader_service.py tests/product_kernel/test_kernel_mission.py -q
git add src/agentdeck/ports/leader.py src/agentdeck/ports/leader_schema.py src/agentdeck/application/leader_service.py tests/product_kernel HISTORY.md
git commit -m "feat: add deterministic product presenters"
```

### Task 17: Add the OpenAI-compatible API Leader adapter

**Authority:** Design sections 11.2–11.3, 16.

**Files:**
- Create: src/agentdeck/adapters/providers.py
- Create: tests/product_kernel/test_openai_compatible_leader.py
- Modify: HISTORY.md

**Forbidden legacy imports:** legacy providers, environment dump helpers, and
fallback model catalogs.

**Approved legacy evidence:** none.

- [ ] **Step 1: Write a local HTTP contract RED test**

```python
def test_openai_compatible_adapter_sends_exact_model_and_schema(http_server) -> None:
    adapter = OpenAICompatibleLeader(
        base_url=http_server.url,
        model="deepseek-chat",
        credential_source="DEEPSEEK_API_KEY",
        credential_resolver=lambda _: "secret",
    )
    proposal = adapter.propose_mission(request())
    captured = http_server.last_json
    assert captured["model"] == "deepseek-chat"
    assert captured["response_format"]["type"] == "json_schema"
    assert proposal.objective == "Build page"


def test_adapter_never_falls_back_when_model_or_credential_is_missing() -> None:
    with pytest.raises(LeaderUnavailable, match="exact model"):
        OpenAICompatibleLeader(base_url="http://local", model="", credential_source="X")
```

- [ ] **Step 2: Confirm RED and implement**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_openai_compatible_leader.py -q
```

Expected: adapter absent. Use stdlib urllib with bounded timeout and maximum
response bytes. Presets provide only base URL and credential-source label for
DeepSeek, Kimi, and GLM; Custom requires both. The request includes the exact
Mission JSON schema. The adapter returns proposal data or a typed Port error,
redacts credentials and bodies from errors, and never changes model/provider.

- [ ] **Step 3: Verify and commit**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_openai_compatible_leader.py tests/product_kernel/test_leader_contract.py -q
git add src/agentdeck/adapters/providers.py tests/product_kernel/test_openai_compatible_leader.py HISTORY.md
git commit -m "feat: add openai compatible leader adapter"
```

### Task 18: Add ACP-backed Codex and Claude Leader adapters

**Authority:** Design sections 11.1–11.3, 12, 17.

**Files:**
- Create: src/agentdeck/ports/transport.py
- Create: src/agentdeck/adapters/acp_transport.py
- Create: src/agentdeck/adapters/acp_leader.py
- Create: tests/product_kernel/fixtures/fake_acp_stdio_agent.py
- Create: tests/product_kernel/test_acp_transport.py
- Create: tests/product_kernel/test_acp_leader.py
- Modify: HISTORY.md

**Existing Task 21 authority (do not modify):**
`src/agentdeck/adapters/acp.py` owns ACP Worker mapping, tool-effect and
disconnect outcome classification, permission events, retries, cancellation,
and terminal Worker results. `tests/product_kernel/fixtures/fake_acp_agent.py`
is its in-process Worker fake. Task 18 must not move, extend, re-export, or
refactor either file. The official SDK is already pinned at
`agent-client-protocol==0.11.0`; this task performs no dependency mutation.

**Forbidden legacy imports:** direct PTY/prompt injection, pane capture, legacy
ACP mapping/client; no later composition task implicitly admits them.

**Approved legacy evidence:** official agent-client-protocol package API and its
installed type definitions; no AgentDeck legacy source.

- [ ] **Step 1: Write RED stdio ACP tests**

```python
def test_acp_leader_initializes_session_and_streams_proposal(fake_acp) -> None:
    leader = ACPLeader(fake_acp.command, model="native-default")
    result = leader.propose_mission(request())
    assert fake_acp.calls == ["initialize", "session/new", "session/prompt"]
    assert result.objective == "Build page"


def test_acp_leader_rejects_prompt_scraping_and_unbounded_output(fake_acp) -> None:
    fake_acp.mode = "oversize"
    with pytest.raises(TransportFailure, match="response_oversize"):
        ACPLeader(fake_acp.command, max_bytes=4096).propose_mission(request())
```

- [ ] **Step 2: Confirm RED and implement**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_acp_transport.py tests/product_kernel/test_acp_leader.py -q
```

Expected: the Transport Port, bounded stdio transport, ACP Leader adapter, and
stdio fake are absent. The existing `adapters/acp.py` and
`fake_acp_agent.py` are Task 21 Worker authority and remain untouched.
Transport Port carries initialize, new/resume session, prompt, cancel,
permission response, and typed update stream. `ACPStdioTransport` uses the
official Python SDK over bounded stdio and exposes a lazy injectable client
seam; it does not classify Worker effects, retries, disconnect outcomes,
permissions, or Worker events. `ACPLeader` preserves the existing synchronous
Leader Port, internally owns any asynchronous SDK lifecycle, and obtains
proposal data from a structured artifact/update agreed by the stdio fake, not
terminal text. Separate process/session objects are created per Agent Instance.
Capability absence fails closed. The physical split into `acp.py`,
`acp_transport.py`, and `acp_leader.py` changes no behavior authority and keeps
each file within the 500-line limit.

- [ ] **Step 3: Verify and commit**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_acp_transport.py tests/product_kernel/test_acp_leader.py tests/product_kernel/test_leader_service.py tests/product_kernel/test_acp_worker_contract.py tests/product_kernel/test_acp_worker_failures.py tests/product_kernel/test_fake_worker_contract.py tests/product_kernel/test_architecture.py -q
python -m compileall src tests -q
git diff --check
git add src/agentdeck/ports/transport.py src/agentdeck/adapters/acp_transport.py src/agentdeck/adapters/acp_leader.py tests/product_kernel/fixtures/fake_acp_stdio_agent.py tests/product_kernel/test_acp_transport.py tests/product_kernel/test_acp_leader.py HISTORY.md
git commit -m "feat: add acp backed cli leaders"
```

### Task 19: Connect natural-language goals to exact Preview revision and confirmation

**Authority:** Design sections 5.3, 8.4, 11, 13, 17.2.

**Files:**
- Create: src/agentdeck/application/mission_service.py
- Create: src/agentdeck/adapters/sqlite_mission.py
- Modify: src/agentdeck/adapters/sqlite.py
- Modify: src/agentdeck/product/shell.py
- Modify: src/agentdeck/product/bootstrap.py
- Modify: src/agentdeck/product/presenter.py
- Modify: src/agentdeck/product/renderer.py
- Create: tests/product_kernel/test_mission_service.py
- Create: tests/product_kernel/test_product_preview_flow.py
- Create: tests/product_kernel/test_sqlite_mission.py
- Modify: HISTORY.md

**Repo-truth prerequisite closure:** the Task 11 schema already contains
`missions`, `mission_versions`, and `tasks`, but the SQLite Adapter has no
command-bound save/load path for those aggregates; and the Task 14 composition
root can inject only `SessionService`, making a MissionService otherwise
unreachable from the real Product Shell. Task 19 closes only those seams through
the dedicated `sqlite_mission.py` helper, thin SQLite delegation, and an
injectable composition binding. `sqlite.py` must remain at most 500 lines and
must not absorb Mission validation. Tests use injected Leader/adapter factories
and never start a real provider. Task 26 remains responsible for binding real
Codex/Claude ACP readiness; this task adds no provider discovery or fallback.

**Forbidden legacy imports:** legacy preview/action/approval/plan machinery.

**Approved legacy evidence:** none.

- [ ] **Step 1: Write RED end-user Preview tests**

```python
def test_goal_becomes_human_preview_and_exact_confirmation(product) -> None:
    shown = product.say("Build an accessible page")
    assert shown.preview.objective == "Build an accessible page"
    assert shown.preview.content_hash in shown.text
    started = product.say(f"confirm {shown.preview.preview_id} {shown.preview.content_hash}")
    assert started.mission.version == shown.preview.version


def test_natural_revision_invalidates_old_confirmation(product) -> None:
    old = product.say("Build a page").preview
    new = product.say("Use Claude as reviewer and add mobile acceptance").preview
    assert new.version == old.version + 1
    rejected = product.say(f"confirm {old.preview_id} {old.content_hash}")
    assert rejected.diagnostic.code == "mission_preview_drift"


def test_open_goal_without_llm_is_retained_not_discarded(product_without_leader) -> None:
    result = product_without_leader.say("Build a page")
    assert result.mode == "setup_required"
    assert product_without_leader.store.session().pending_goal == "Build a page"
```

- [ ] **Step 2: Confirm RED and implement**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_mission_service.py tests/product_kernel/test_product_preview_flow.py -q
```

Expected: no application path exists. MissionService converts only a validated
LeaderProposal into MissionDraft, persists each Preview version, and confirms
through execute_once with Preview ID and hash. Revision sends current Preview
plus user delta to Leader, then revalidates the entire proposal. Product
renderer lists objective, scope, frozen Leader/model, four instances/roles,
dependencies, ACP routes, permissions, criteria, budgets, risks, version, ID,
and hash.

- [ ] **Step 3: Verify R3 and commit**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_leader_*.py tests/product_kernel/test_*leader.py tests/product_kernel/test_mission_service.py tests/product_kernel/test_product_preview_flow.py -q
conda run -n agentdeck pytest tests/product_kernel/test_product_*.py -q
git add src/agentdeck/application/mission_service.py src/agentdeck/product tests/product_kernel HISTORY.md
git commit -m "feat: connect goals to exact mission confirmation"
```

## Phase R4 — ACP Workers and deterministic execution

### Task 20: Define Worker Port, stable Worker Events, and shared conformance suite

**Authority:** Design sections 8.2, 8.5–8.6, 12, 17.1.

**Files:**
- Create: src/agentdeck/ports/worker.py
- Create: tests/product_kernel/worker_contract.py
- Create: tests/product_kernel/test_fake_worker_contract.py
- Modify: tests/product_kernel/fakes.py
- Modify: HISTORY.md

**Forbidden legacy imports:** legacy Message, Job, Reply, tmux transport.

**Approved legacy evidence:** none.

- [ ] **Step 1: Write the RED shared contract**

```python
# tests/product_kernel/worker_contract.py
WORKER_EVENT_KINDS = {
    "started", "progress", "tool_started", "tool_completed",
    "permission_requested", "artifact_changed", "message",
    "completed", "failed", "cancelled",
}


async def assert_worker_contract(worker) -> None:
    handle = await worker.start_task(task_request())
    events = [event async for event in worker.stream_events(handle)]
    assert events[0].kind == "started"
    assert events[-1].kind == "completed"
    assert {event.kind for event in events} <= WORKER_EVENT_KINDS
    assert all(event.agent_id == "agt_1" for event in events)
    assert all(event.task_id == "tsk_1" for event in events)
    assert all(event.attempt_id == "att_1" for event in events)
    assert [event.sequence for event in events] == sorted({e.sequence for e in events})
    assert (await worker.collect_result(handle)).status == "completed"
```

- [ ] **Step 2: Confirm RED and implement**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_fake_worker_contract.py -q
```

Expected: Worker Port and FakeWorker absent. Define start_task, stream_events,
respond_permission, cancel_task, collect_result. WorkerEvent is frozen and
contains event_id, session_id, Agent/Task/Attempt IDs, transport, sequence,
kind, timestamp, and redacted payload. FakeWorker scripts events and never
writes Store directly.

- [ ] **Step 3: Verify and commit**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_fake_worker_contract.py tests/product_kernel/test_architecture.py -q
git add src/agentdeck/ports/worker.py tests/product_kernel HISTORY.md
git commit -m "feat: add openai compatible leader adapter"
```

### Task 21: Implement ACP Worker event mapping and adversarial Fake ACP coverage

**Authority:** Design sections 12, 13, 17.1.

**Files:**
- Create: src/agentdeck/adapters/acp.py
- Create: tests/product_kernel/fixtures/fake_acp_agent.py
- Create: tests/product_kernel/test_acp_worker_contract.py
- Create: tests/product_kernel/test_acp_worker_failures.py
- Modify: HISTORY.md

**Forbidden legacy imports:** PTY input, tmux capture, legacy message capture.

**Approved legacy evidence:** official ACP SDK only.

- [ ] **Step 1: Add RED conformance and failure scenarios**

```python
@pytest.mark.asyncio
async def test_acp_worker_passes_shared_contract(fake_acp_worker) -> None:
    await assert_worker_contract(fake_acp_worker)


@pytest.mark.parametrize("scenario,code,outcome_known", [
    ("protocol_mismatch", "acp_protocol_mismatch", True),
    ("disconnect_before_work", "acp_disconnected_before_effect", True),
    ("disconnect_after_effect", "worker_outcome_unknown", False),
    ("duplicate_event", "acp_duplicate_event", True),
    ("out_of_order", "acp_sequence_violation", True),
    ("oversize", "acp_output_oversize", True),
    ("secret_output", "acp_sensitive_output_redacted", True),
])
@pytest.mark.asyncio
async def test_adversarial_acp_scenarios_are_typed(
    fake_acp_factory, scenario, code, outcome_known
) -> None:
    result = await run_worker(fake_acp_factory(scenario))
    assert result.diagnostic.code == code
    assert result.diagnostic.outcome_known is outcome_known
    assert "secret-token" not in result.serialized()
```

The fake server also scripts initialization, capabilities, new session,
streamed progress, tool events, two sequential permissions, completion,
cancellation, and invalid result payloads.

- [ ] **Step 2: Confirm RED and implement**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_acp_worker_contract.py tests/product_kernel/test_acp_worker_failures.py -q
```

Expected: ACP adapter lacks Worker methods/mapping. Map official ACP updates to
the stable WorkerEvent kinds. Reject duplicate/out-of-order sequence identities,
bound individual and total payload sizes, redact before persistence, preserve
raw protocol data only in process memory for decoding, and classify disconnect
using observed-effect state.

- [ ] **Step 3: Verify and commit**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_acp_*.py tests/product_kernel/test_fake_worker_contract.py -q
git add src/agentdeck/adapters/acp.py tests/product_kernel HISTORY.md
git commit -m "feat: map acp workers into stable events"
```

### Task 22: Implement the sequential ACP permission bridge

**Authority:** Design sections 8.7, 9, 12–13.

**Files:**
- Create: src/agentdeck/ports/approval.py
- Create: src/agentdeck/application/approval_service.py
- Modify: src/agentdeck/adapters/acp.py
- Create: src/agentdeck/adapters/sqlite_approval.py
- Modify: src/agentdeck/adapters/sqlite.py
- Create: tests/product_kernel/test_approval_service.py
- Create: tests/product_kernel/test_acp_permission_bridge.py
- Create: tests/product_kernel/test_sqlite_approval.py
- Modify: tests/product_kernel/test_acp_worker_contract.py
- Modify: HISTORY.md

**Repo-truth prerequisite closure:** the Task 11 schema contains `approvals`,
but the current SQLite Adapter cannot persist an approval aggregate; and the
Task 21 Worker event preserves request identity but not the stable normalized
effect/risk facts required by sections 8.7 and 9. Task 22 closes only these two
missing production seams. `adapters/acp.py` remains the sole Worker mapping and
outcome authority; `sqlite_approval.py` is an Adapter helper reached only from
the command-bound SQLite transaction. No Kernel/Application dependency on an
Adapter is admitted.

**Forbidden legacy imports:** legacy approval actions/cards and implicit CLI
approval flags in Application or Kernel.

**Approved legacy evidence:** none.

- [ ] **Step 1: Write RED tests for multiple permissions and lineage**

```python
@pytest.mark.asyncio
async def test_one_attempt_handles_multiple_permissions_in_order(harness) -> None:
    harness.worker.script_permissions("read_file", "write_file", "run_tests")
    result = await harness.run_attempt(profile="approve_for_me")
    assert [item.effect for item in result.approvals] == [
        "read_file", "write_file", "run_tests"
    ]
    assert len({item.permission_request_id for item in result.approvals}) == 3
    assert all(item.attempt_id == result.attempt_id for item in result.approvals)


@pytest.mark.asyncio
async def test_executor_cannot_review_itself(harness) -> None:
    harness.approval_reviewer.instance_id = harness.worker.instance_id
    result = await harness.run_attempt(profile="approve_for_me")
    assert result.diagnostic.code == "approval_reviewer_not_independent"


@pytest.mark.asyncio
async def test_next_task_waits_for_completion_and_handoff(harness) -> None:
    harness.worker.pause_after_permission = True
    await harness.tick()
    assert harness.worker.started_tasks == ["implementation"]
```

- [ ] **Step 2: Confirm RED and implement**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_approval_service.py tests/product_kernel/test_acp_permission_bridge.py -q
```

Expected: no bridge. ApprovalService evaluates each ACP permission request
against Attempt effective scope, persists request and decision with full
lineage, invokes human or independent Approval Port when required, durably
records the decision, then replies to that exact ACP request. Request and
decision are separate stable command transactions so no database transaction
is held across reviewer or Worker I/O. An Attempt may produce a finite
sequence; the next Task cannot start until terminal result validation and
Handoff commit. The ACP Worker maps the permission's tool kind into a bounded
normalized effect/risk fact without persisting raw tool input. Unknown effects
fail closed. SQLite writes the canonical request/decision into the existing
`approvals` table and the audit event in the same command transaction.

- [ ] **Step 3: Verify and commit**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_approval_service.py tests/product_kernel/test_acp_permission_bridge.py tests/product_kernel/test_sqlite_approval.py tests/product_kernel/test_acp_worker_contract.py tests/product_kernel/test_kernel_permissions.py tests/product_kernel/test_architecture.py -q
python -m compileall src tests -q
git diff --check
git add src/agentdeck/ports/approval.py src/agentdeck/application/approval_service.py src/agentdeck/adapters/acp.py src/agentdeck/adapters/sqlite_approval.py src/agentdeck/adapters/sqlite.py tests/product_kernel/test_approval_service.py tests/product_kernel/test_acp_permission_bridge.py tests/product_kernel/test_sqlite_approval.py tests/product_kernel/test_acp_worker_contract.py HISTORY.md
git commit -m "feat: bridge sequential acp permissions"
```

### Task 23: Implement the deterministic four-stage Execution Coordinator

**Authority:** Design sections 8.4–8.6, 13, 23.

**Files:**
- Create: src/agentdeck/application/execution_service.py
- Create: src/agentdeck/adapters/sqlite_execution.py
- Modify: src/agentdeck/adapters/sqlite.py
- Create: tests/product_kernel/test_execution_coordinator.py
- Create: tests/product_kernel/test_sqlite_execution.py
- Modify: HISTORY.md

**Repo-truth prerequisite closure:** the Task 11 schema already contains
`handoffs` and `evidence`, while the SQLite Adapter currently persists only
sessions, attempts, conversation turns, and approvals. Task 23 therefore adds a
dedicated `sqlite_execution.py` helper and thin `sqlite.py` delegation so the
terminal Attempt, typed Evidence, and Handoff can commit in one existing Store
command transaction. The helper validates exact Task/Attempt/Mission/Agent
lineage and immutable terminal facts; it does not implement scheduling,
semantic review policy, retry policy, or Task 24 behavior. `sqlite.py` remains
at most 500 lines.

**Forbidden legacy imports:** legacy mission orchestration, daemon worker loop,
action proposals, and pane transport.

**Approved legacy evidence:** none.

- [ ] **Step 1: Write RED scheduling and authority tests**

```python
@pytest.mark.asyncio
async def test_coordinator_runs_only_the_frozen_four_stage_graph(harness) -> None:
    result = await harness.run_confirmed_mission()
    assert harness.worker.started_tasks == [
        "implementation", "review", "revision", "acceptance"
    ]
    assert [h.source_attempt_id for h in result.handoffs] == [
        "att_impl_1", "att_review_1", "att_revision_1"
    ]


@pytest.mark.asyncio
async def test_worker_cannot_directly_dispatch_peer(harness) -> None:
    harness.review_result["next_agent_command"] = "dispatch codex"
    result = await harness.run_confirmed_mission()
    assert "next_agent_command" not in result.revision_task.canonical_payload()
    assert result.revision_task.created_by == "agentdeck"


@pytest.mark.asyncio
async def test_dependency_without_committed_handoff_never_starts(harness) -> None:
    harness.store.fail_on_handoff_commit = True
    result = await harness.run_confirmed_mission()
    assert result.diagnostic.code == "handoff_persistence_failed"
    assert harness.worker.started_tasks == ["implementation"]
```

- [ ] **Step 2: Confirm RED and implement**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_execution_coordinator.py -q
```

Expected: coordinator absent. ExecutionService reads the immutable confirmed
graph, starts only dependency-ready Tasks, creates one Attempt at a time,
persists started state before Worker I/O, consumes Worker Events, validates
result, writes terminal Attempt plus typed Evidence plus Handoff atomically,
then schedules the next Task. Claude proposals are data only; AgentDeck creates
the authoritative Revision Task from accepted in-scope findings and confirmed
scope.

- [ ] **Step 3: Verify and commit**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_execution_coordinator.py tests/product_kernel/test_acp_permission_bridge.py tests/product_kernel/test_sqlite_transactions.py -q
git add src/agentdeck/application/execution_service.py tests/product_kernel/test_execution_coordinator.py HISTORY.md
git commit -m "feat: coordinate the four stage coding mission"
```

### Task 24: Enforce review, revision, acceptance, and bounded retry semantics

**Authority:** Design sections 8.6, 13, 15, 17.

**Files:**
- Modify: src/agentdeck/kernel/execution.py
- Modify: src/agentdeck/application/execution_service.py
- Create: tests/product_kernel/test_review_revision_semantics.py
- Create: tests/product_kernel/test_execution_budgets.py
- Modify: HISTORY.md

**Forbidden legacy imports:** M2c semantic validators and live harness.

**Approved legacy evidence:** the retained M2c validation results may supply
failure examples only; no source or schema reuse.

- [ ] **Step 1: Write RED semantic/budget tests**

```python
def test_revision_task_contains_only_in_scope_evidence_backed_findings() -> None:
    result = materialize_revision(
        findings=[
            finding("f1", scope="src", evidence=("ev_diff",), blocking=True),
            finding("f2", scope="/outside", evidence=("ev_note",), blocking=True),
            finding("f3", scope="src", evidence=(), blocking=True),
        ],
        confirmed_scope=("src",),
    )
    assert [item.finding_id for item in result.findings] == ["f1"]
    assert [item.finding_id for item in result.rejected] == ["f2", "f3"]


@pytest.mark.parametrize("condition,retry", [
    ("transport_before_effect", True),
    ("worker_schema_invalid", True),
    ("known_test_failure", False),
    ("permission_denied", False),
    ("outcome_unknown", False),
    ("project_drift", False),
])
def test_retry_policy_is_bounded_and_semantic(condition, retry) -> None:
    assert RetryPolicy.default().decision(condition, ordinal=1).retry is retry
    assert RetryPolicy.default().decision(condition, ordinal=2).retry is False


def test_acceptance_maps_typed_evidence_to_every_criterion() -> None:
    with pytest.raises(ResultError, match="criterion mobile missing evidence"):
        validate_acceptance(criteria=("desktop", "mobile"),
                            mappings={"desktop": ("ev_browser",)})
```

- [ ] **Step 2: Confirm RED and implement**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_review_revision_semantics.py tests/product_kernel/test_execution_budgets.py -q
```

Expected: validators and retry policy absent. Add closed ReviewResult and
AcceptanceResult validators, semantic Revision materializer, one Leader repair,
two Attempts per Task, one reconnect before effect, one revision cycle, and one
acceptance Attempt. Denied permission, test failure, scope insufficiency, login
loss, drift, and outcome_unknown are attention/failure states, never blind
retry.

- [ ] **Step 3: Verify and commit**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_review_revision_semantics.py tests/product_kernel/test_execution_budgets.py tests/product_kernel/test_execution_coordinator.py -q
git add src/agentdeck/kernel/execution.py src/agentdeck/application/execution_service.py tests/product_kernel HISTORY.md
git commit -m "feat: enforce evidence backed execution semantics"
```

### Task 25: Build the Codex app-server to ACP bridge

**Authority:** Design sections 11–12, 17; ACP-only automatic communication.

**Files:**
- Create: src/agentdeck/adapters/codex_app_server.py
- Create: src/agentdeck/adapters/codex_acp_server.py
- Create: tests/product_kernel/fixtures/fake_codex_app_server.py
- Create: tests/product_kernel/test_codex_app_server.py
- Create: tests/product_kernel/test_codex_acp_bridge.py
- Modify: pyproject.toml
- Modify: HISTORY.md

**Forbidden legacy imports:** Codex PTY driver, pane capture, CLI prompt
injection, legacy ACP adapter.

**Approved legacy evidence:** official installed Codex app-server stable
JSON-RPC v2 protocol and generated schema for the frozen Codex build. Protocol
authority: [official Codex app-server README](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md).

- [ ] **Step 1: Write RED bridge tests**

```python
@pytest.mark.asyncio
async def test_bridge_translates_acp_session_to_codex_thread_and_turn(fake_codex) -> None:
    bridge = CodexACPServer(app_server_command=fake_codex.command)
    client = ACPWorker(bridge.command)
    await client.start_task(task_request())
    assert fake_codex.methods[:4] == [
        "initialize", "initialized", "thread/start", "turn/start"
    ]


@pytest.mark.asyncio
async def test_bridge_maps_server_permission_request_to_exact_acp_request(fake_codex) -> None:
    fake_codex.script_permission("perm_42", "writeFile")
    event = await next_kind(run_bridge(fake_codex), "permission_requested")
    assert event.payload["native_request_id"] == "perm_42"


def test_schema_or_version_drift_blocks_preflight(fake_codex) -> None:
    fake_codex.schema_digest = "unexpected"
    readiness = probe_codex_bridge(fake_codex.command, expected_digest="frozen")
    assert readiness.ready is False
    assert readiness.diagnostic.code == "codex_app_server_schema_drift"
```

- [ ] **Step 2: Confirm RED and implement**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_codex_app_server.py tests/product_kernel/test_codex_acp_bridge.py -q
```

Expected: bridge absent. CodexAppServerClient uses bounded JSONL stdio,
initialize/initialized, thread start/resume, turn start/interrupt, and stable
event/request mapping. CodexACPServer exposes official ACP outward and maps
sessions, streamed updates, cancellations, results, and server-initiated
permission requests inward. Add console script agentdeck-codex-acp. Pin and
probe the installed Codex version plus generated stable schema digest; any
drift is a blocker. Experimental app-server fields are never enabled silently.

- [ ] **Step 3: Verify and commit**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_codex_app_server.py tests/product_kernel/test_codex_acp_bridge.py tests/product_kernel/test_acp_worker_contract.py -q
git add src/agentdeck/adapters/codex_app_server.py src/agentdeck/adapters/codex_acp_server.py tests/product_kernel pyproject.toml HISTORY.md
git commit -m "feat: bridge codex app server through acp"
```

### Task 26: Bind Claude Agent ACP and Codex bridge into explicit adapter readiness

**Authority:** Design sections 11.1, 12, 15, 17.3.

**Files:**
- Modify: src/agentdeck/adapters/discovery.py
- Modify: src/agentdeck/product/bootstrap.py
- Create: tests/product_kernel/test_real_adapter_preflight_contract.py
- Modify: docs/migrations/product-kernel-legacy-reuse-register.md
- Modify: HISTORY.md

**Forbidden legacy imports:** existing runtime/acp.py, acp_client.py, acp_mapping.py
remain not admitted; no auth/install/global config mutation.

**Approved legacy evidence:** read-only executable/version facts only.

- [ ] **Step 1: Write RED readiness tests**

```python
def test_codex_ready_requires_cli_app_server_bridge_and_schema() -> None:
    facts = fake_tools(codex=True, app_server=True, bridge=True, schema=False)
    assert classify_codex(facts).ready is False
    assert classify_codex(facts).diagnostic.code == "codex_app_server_schema_drift"


def test_claude_ready_requires_cli_login_and_claude_agent_acp() -> None:
    facts = fake_tools(claude=True, authenticated=True, claude_acp=False)
    assert classify_claude(facts).ready is False
    assert classify_claude(facts).diagnostic.code == "claude_acp_missing"


def test_no_pty_fallback_is_offered() -> None:
    assert "pty" not in classify_claude(fake_tools(claude=True)).fallbacks
```

- [ ] **Step 2: Confirm RED and implement**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_real_adapter_preflight_contract.py -q
```

Expected: readiness does not express bridge requirements. Add passive readiness
facts and composition wiring for claude-agent-acp and agentdeck-codex-acp. The
test accepts injected probe results only; no real process runs. Record legacy
ACP files as reviewed and rejected in the reuse register because their
state/model coupling violates the new boundary; the new adapter uses official
SDK types directly. Discovery only reports passive readiness and never imports
or starts a transport. The composition root alone binds `ACPStdioTransport`
with `ACPLeader` or the existing `ACPWorker`; each Agent Instance receives its
own lazy process/session. Claude uses the verified `claude-agent-acp` argv and
Codex uses the Task 25 `agentdeck-codex-acp` argv. Readiness and preflight do not
start either process, and there is no PTY, legacy ACP, or prompt-injection
fallback. The injected composition test must prove the Worker remains the Task
21 `ACPWorker`, the Leader remains `ACPLeader`, and readiness performs zero
subprocess starts.

- [ ] **Step 3: Verify R4 and commit**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_*acp*.py tests/product_kernel/test_*execution*.py tests/product_kernel/test_*approval*.py tests/product_kernel/test_real_adapter_preflight_contract.py -q
conda run -n agentdeck pytest tests/test_acp_*.py tests/test_mission_*.py -q
git add src/agentdeck/adapters/discovery.py src/agentdeck/product/bootstrap.py tests/product_kernel docs/migrations/product-kernel-legacy-reuse-register.md HISTORY.md
git commit -m "feat: bind codex and claude acp readiness"
```

## Phase R5 — tmux real Agent observation

### Task 27: Define the Observer Runtime Port and deterministic tmux layout

**Authority:** Design sections 6, 14, 19 R5.

**Files:**
- Create: src/agentdeck/ports/runtime.py
- Create: src/agentdeck/adapters/tmux_observer.py
- Create: tests/product_kernel/test_tmux_layout.py
- Modify: HISTORY.md

**Forbidden legacy imports:** legacy tmux backend; no Task send, completion
inference, or database writes from the observer.

**Approved legacy evidence:** tmux command syntax may be characterized through
subprocess fixtures; no code reuse.

- [ ] **Step 1: Write RED layout and authority tests**

```python
def test_tmux_plan_has_overview_and_four_worker_panes() -> None:
    plan = TmuxObserver.plan(project_id="prj_1", instances=four_instances())
    assert [window.name for window in plan.windows] == ["Overview", "Workers"]
    assert [pane.role for pane in plan.windows[1].panes] == [
        "implementer", "reviewer", "reviser", "acceptance_reviewer"
    ]
    assert all("agentdeck observer" in pane.command for pane in plan.windows[1].panes)


def test_observer_has_no_send_or_completion_api() -> None:
    assert not hasattr(TmuxObserver, "send_task")
    assert not hasattr(TmuxObserver, "mark_completed")
```

- [ ] **Step 2: Confirm RED and implement**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_tmux_layout.py -q
```

Expected: Port/adapter absent. Runtime Port exposes create/select/close observer
workspace and takeover ownership only. TmuxObserver generates argv lists, never
shell strings. Overview and four-pane Workers windows are project-namespaced.
Pane commands contain only Observer session/instance IDs and read from the
Application event subscription; they do not open the database as writers.

- [ ] **Step 3: Verify and commit**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_tmux_layout.py tests/product_kernel/test_architecture.py -q
git add src/agentdeck/ports/runtime.py src/agentdeck/adapters/tmux_observer.py tests/product_kernel/test_tmux_layout.py HISTORY.md
git commit -m "feat: add deterministic tmux observer layout"
```

### Task 28: Render faithful cursor-safe, redacted Agent streams

**Authority:** Design sections 10.5, 14–15, 17.1.

**Files:**
- Modify: src/agentdeck/adapters/tmux_observer.py
- Create: src/agentdeck/product/observer.py
- Create: tests/product_kernel/test_observer_fidelity.py
- Create: tests/product_kernel/test_observer_redaction.py
- Modify: HISTORY.md

**Forbidden legacy imports:** pane capture reply extraction and raw protocol
logging.

**Approved legacy evidence:** none.

- [ ] **Step 1: Write RED fidelity/cursor/redaction tests**

```python
def test_reconnect_deduplicates_without_cross_agent_mixing() -> None:
    stream = ObserverStream(cursor_store=MemoryCursor())
    first = stream.render(events_for("agt_1", sequences=[1, 2]))
    second = stream.render(events_for("agt_1", sequences=[2, 3]))
    assert rendered_sequences(first + second) == [1, 2, 3]
    with pytest.raises(ObserverError, match="identity mismatch"):
        stream.render(events_for("agt_2", sequences=[4]))


def test_agentdeck_text_is_labeled_and_secrets_are_redacted() -> None:
    output = render_event(agent_message("token=sk-secret"))
    assert "[Agent agt_1]" in output
    assert "sk-secret" not in output
    assert "[REDACTED]" in output
    assert render_system("reconnecting").startswith("[AgentDeck]")
```

- [ ] **Step 2: Confirm RED and implement**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_observer_fidelity.py tests/product_kernel/test_observer_redaction.py -q
```

Expected: stream/renderer absent. Observer consumes redacted decoded
WorkerEvents through a read-only subscription, checks session/Agent/Task/
Attempt/transport/sequence identity, persists only the last acknowledged
cursor through the foreground Application writer, and labels AgentDeck text.
It never displays hidden reasoning or raw frames.

- [ ] **Step 3: Verify and commit**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_observer_*.py tests/product_kernel/test_acp_worker_failures.py -q
git add src/agentdeck/adapters/tmux_observer.py src/agentdeck/product/observer.py tests/product_kernel HISTORY.md
git commit -m "feat: render faithful agent event streams"
```

### Task 29: Add explicit takeover and validated return-control

**Authority:** Design sections 5.2, 9, 14–15.

**Files:**
- Modify: src/agentdeck/application/execution_service.py
- Modify: src/agentdeck/adapters/tmux_observer.py
- Modify: src/agentdeck/product/shell.py
- Create: tests/product_kernel/test_takeover.py
- Modify: HISTORY.md

**Forbidden legacy imports:** direct tmux send-keys automation and implicit pane
ownership.

**Approved legacy evidence:** none.

- [ ] **Step 1: Write RED ownership tests**

```python
@pytest.mark.asyncio
async def test_takeover_stops_automatic_input_and_records_owner(harness) -> None:
    await harness.takeover("att_1")
    assert harness.store.attempt("att_1")["state"] == "human_controlled"
    assert harness.worker.automatic_input_enabled is False
    assert harness.store.latest_event()["kind"] == "human_takeover"


@pytest.mark.asyncio
async def test_return_control_revalidates_runtime_and_project(harness) -> None:
    await harness.takeover("att_1")
    harness.project.change("unexplained")
    result = await harness.return_control("att_1")
    assert result.accepted is False
    assert result.diagnostic.code == "project_drift_before_return_control"
```

- [ ] **Step 2: Confirm RED and implement**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_takeover.py -q
```

Expected: ownership operation absent. /takeover targets an exact Attempt,
pauses coordinator input, transitions to human_controlled, and records lineage.
Return-control compares project evidence identity, ACP session state,
permissions, and event cursor; only a clean reconciliation returns running.
No pane output is used as proof.

- [ ] **Step 3: Verify R5 and commit**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_tmux_*.py tests/product_kernel/test_observer_*.py tests/product_kernel/test_takeover.py -q
git add src/agentdeck/application/execution_service.py src/agentdeck/adapters/tmux_observer.py src/agentdeck/product/shell.py tests/product_kernel/test_takeover.py HISTORY.md
git commit -m "feat: add explicit human agent takeover"
```

## Phase R6 — Diagnostics and recovery closure

### Task 30: Render complete Error Cards and deterministic diagnose output

**Authority:** Design section 15, sections 5.2 and 17.2.

**Files:**
- Modify: src/agentdeck/kernel/diagnostics.py
- Modify: src/agentdeck/product/presenter.py
- Modify: src/agentdeck/product/renderer.py
- Modify: src/agentdeck/product/shell.py
- Create: tests/product_kernel/test_error_cards.py
- Create: tests/product_kernel/test_diagnose_command.py
- Modify: HISTORY.md

**Forbidden legacy imports:** legacy card contracts and raw exception rendering.

**Approved legacy evidence:** retained failure category names may be fixtures;
old payload shapes are not authority.

- [ ] **Step 1: Write RED completeness and secrecy tests**

```python
@pytest.mark.parametrize("code", [
    "leader_authentication_failed", "acp_protocol_mismatch",
    "mission_preview_drift", "worker_outcome_unknown",
    "review_scope_invalid", "acceptance_evidence_missing",
    "permission_denied", "storage_recovery_failed",
    "tmux_observer_degraded",
])
def test_every_non_success_has_complete_error_card(code) -> None:
    card = present_diagnostic(diagnostic(code))
    assert all(getattr(card, field) for field in (
        "what_happened", "why", "completed", "not_completed",
        "protection", "recovery_actions", "identity"
    ))


def test_diagnose_json_is_stable_and_redacted(shell) -> None:
    output = shell.run_command("/diagnose --json")
    assert set(json.loads(output)) == DIAGNOSTIC_JSON_FIELDS
    assert "/Users/private" not in output
    assert "raw stderr" not in output
```

- [ ] **Step 2: Confirm RED and implement**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_error_cards.py tests/product_kernel/test_diagnose_command.py -q
```

Expected: presenter lacks complete Error Cards. Create a diagnostic catalog
mapping every Kernel/Application/Adapter failure to stable code, stage, cause,
impact, protection, recovery actions, retryable, outcome_known, and lineage.
Interactive output is plain language. --json emits a closed, versioned,
redacted contract and rejects unknown fields before printing.

- [ ] **Step 3: Verify and commit**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_error_cards.py tests/product_kernel/test_diagnose_command.py tests/product_kernel/test_product_renderer.py -q
git add src/agentdeck/kernel/diagnostics.py src/agentdeck/product tests/product_kernel HISTORY.md
git commit -m "feat: explain product failures with error cards"
```

### Task 31: Add end-to-end trace and sanitized support evidence

**Authority:** Design sections 8.6–8.7, 10.5, 14–15.

**Files:**
- Create: src/agentdeck/application/support_service.py
- Modify: src/agentdeck/product/slash_commands.py
- Modify: src/agentdeck/product/shell.py
- Create: tests/product_kernel/test_trace_support.py
- Modify: HISTORY.md

**Forbidden legacy imports:** legacy trace/cards/events CLI and raw log export.

**Approved legacy evidence:** none.

- [ ] **Step 1: Write RED lineage and bounded-export tests**

```python
def test_trace_links_mission_task_attempt_permission_handoff_evidence(service) -> None:
    trace = service.trace("mis_1")
    assert trace.path == (
        "mis_1", "tsk_impl", "att_impl_1", "hnd_impl",
        "tsk_review", "att_review_1"
    )
    assert trace.permissions[0].attempt_id == "att_impl_1"


def test_support_bundle_is_bounded_and_contains_no_raw_frames(service) -> None:
    bundle = service.support_bundle("mis_1")
    assert bundle.byte_count <= 256_000
    assert "raw_protocol" not in bundle.text
    assert "terminal_output" not in bundle.text
    assert "API_KEY" not in bundle.text
```

- [ ] **Step 2: Confirm RED and implement**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_trace_support.py -q
```

Expected: service absent. SupportService reads Store snapshots, verifies every
lineage edge and content hash, and emits bounded summaries of environment,
versions, diagnostics, events, decisions, and evidence identities. It never
reads secret sources, source contents, raw ACP frames, or terminal scrollback.

- [ ] **Step 3: Verify and commit**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_trace_support.py tests/product_kernel/test_sqlite_transactions.py -q
git add src/agentdeck/application/support_service.py src/agentdeck/product tests/product_kernel/test_trace_support.py HISTORY.md
git commit -m "feat: add auditable mission trace support"
```

### Task 32: Close outcome-unknown, observer degradation, and resume recovery

**Authority:** Design sections 10.4, 13–15, 17.

**Files:**
- Modify: src/agentdeck/application/recovery_service.py
- Modify: src/agentdeck/application/execution_service.py
- Modify: src/agentdeck/product/shell.py
- Create: tests/product_kernel/test_recovery_closure.py
- Modify: HISTORY.md

**Forbidden legacy imports:** daemon monitor, pane completion inference, blind
retry.

**Approved legacy evidence:** none.

- [ ] **Step 1: Write RED recovery matrix tests**

```python
@pytest.mark.parametrize("condition,action", [
    ("observer_down_worker_alive", "restart_observer"),
    ("transport_before_effect", "reconnect_once"),
    ("transport_after_effect", "human_reconcile"),
    ("login_lost", "reauthenticate_outside_agentdeck"),
    ("project_drift", "inspect_diff"),
])
def test_recovery_actions_are_condition_specific(condition, action, recovery) -> None:
    result = recovery.assess(condition)
    assert action in result.actions


def test_outcome_unknown_cannot_resume_or_retry_without_reconciliation(recovery) -> None:
    result = recovery.resume_attempt(attempt(state="outcome_unknown"))
    assert result.accepted is False
    assert result.diagnostic.outcome_known is False
```

- [ ] **Step 2: Confirm RED and implement**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_recovery_closure.py -q
```

Expected: recovery matrix incomplete. Implement explicit reconciliation
commands and state transitions. Observer degradation is a warning when Worker
transport remains healthy; outcome_unknown always needs human reconciliation.
A clearly pre-effect transport loss uses the single confirmed reconnect budget.
All recovery decisions persist events and are idempotent.

- [ ] **Step 3: Verify R6 and commit**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_*diagnos*.py tests/product_kernel/test_error_cards.py tests/product_kernel/test_trace_support.py tests/product_kernel/test_recovery_*.py -q
git add src/agentdeck/application/recovery_service.py src/agentdeck/application/execution_service.py src/agentdeck/product/shell.py tests/product_kernel HISTORY.md
git commit -m "feat: close product recovery semantics"
```

## Phase R7 — Deterministic and real Golden Product Gate

### Task 33: Pass a deterministic Fake four-stage product journey

**Authority:** Design sections 5, 13, 17.1–17.2, 19 R7, 23.

**Files:**
- Create: tests/product_kernel/test_four_stage_e2e.py
- Create: tests/product_kernel/fixtures/golden_goal.txt
- Modify: tests/product_kernel/fakes.py
- Modify: HISTORY.md

**Forbidden legacy imports:** all legacy runtime/orchestration/state modules.

**Approved legacy evidence:** none.

- [ ] **Step 1: Write the full RED product test**

```python
@pytest.mark.asyncio
async def test_fake_product_completes_exact_four_stage_journey(product_harness) -> None:
    session = product_harness.launch()
    session.say("Build the frozen local homepage fixture")
    session.configure(
        leader="fake-acp-leader", model="test-model",
        permission="approve-for-me",
    )
    preview = session.current_preview()
    result = await session.confirm(preview.preview_id, preview.content_hash)

    assert result.status == "completed"
    assert result.started_roles == (
        "implementer", "reviewer", "reviser", "acceptance_reviewer"
    )
    assert result.acceptance == "passed"
    assert result.handoff_count == 3
    assert result.evidence_criteria == set(preview.acceptance_criteria)
    assert product_harness.store.integrity_check() == "ok"
    assert product_harness.observer.fidelity_report().missing == ()
    assert product_harness.observer.fidelity_report().duplicates == ()
    assert product_harness.observer.fidelity_report().mixed == ()
```

The same test exits, recreates the composition root, inspects the restored
completed session and trace, and asserts every role uses a separate Agent and
ACP session.

- [ ] **Step 2: Confirm RED and make only integration corrections**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_four_stage_e2e.py -q
```

Expected: first failing cross-layer contract identifies the missing wiring;
do not weaken an invariant. Make the smallest corrections in the already
listed Product/Application/Adapter modules, add each corrected behavior as a
focused regression in its owning test file, then rerun the E2E.

- [ ] **Step 3: Verify and commit**

```bash
conda run -n agentdeck pytest tests/product_kernel -q
git add src/agentdeck tests/product_kernel HISTORY.md
git commit -m "test: prove deterministic four stage product journey"
```

### Task 34: Define the frozen website target and browser evidence adapter

**Authority:** Design sections 17.1, 18, 23.

**Files:**
- Create: src/agentdeck/adapters/browser.py
- Create: tests/product_kernel/fixtures/reference_homepage/index.html
- Create: tests/product_kernel/fixtures/reference_homepage/target-manifest.json
- Create: tests/product_kernel/test_browser_evidence.py
- Create: docs/validation/product-kernel-golden-gate.md
- Modify: pyproject.toml only if an optional Playwright extra is required
- Modify: HISTORY.md

**Forbidden legacy imports:** old live harness, screenshot parsers, target-site
assets, or unlicensed copied content.

**Approved legacy evidence:** retained validation report formats may inform
human-readable headings only.

- [ ] **Step 1: Write RED evidence-contract tests against a lawful local fixture**

```python
def test_browser_evidence_covers_fixed_viewports_and_interactions(browser, fixture_url) -> None:
    report = browser.verify(fixture_url, load_manifest("target-manifest.json"))
    assert [shot.viewport for shot in report.screenshots] == [
        (1440, 1200), (390, 844)
    ]
    assert report.interactions == {
        "navigation": "passed", "carousel": "passed", "responsive_menu": "passed"
    }
    assert all(item.content_hash for item in report.screenshots)


def test_repository_manifest_contains_no_copyrighted_reference_assets() -> None:
    manifest = load_manifest("target-manifest.json")
    assert manifest["source_assets"] == []
    assert set(manifest["tolerances"]) == {"pixel_ratio", "layout_shift_px"}
```

- [ ] **Step 2: Confirm RED and implement**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_browser_evidence.py -q
```

Expected: browser adapter absent. Browser Port accepts URL, viewports,
structure selectors, interaction checks, and tolerance rules; returns typed
screenshot hashes, structure facts, interaction results, and a visual-diff
summary. Use optional Playwright only inside this Adapter. Tests use the local
fixture. Document that real captures and copyrighted assets stay outside Git;
the repository stores rules, hashes, and sanitized evidence only.

- [ ] **Step 3: Verify and commit**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_browser_evidence.py tests/product_kernel/test_four_stage_e2e.py -q
git add src/agentdeck/adapters/browser.py tests/product_kernel docs/validation/product-kernel-golden-gate.md pyproject.toml HISTORY.md
git commit -m "feat: define golden browser evidence"
```

### Task 35: Implement and run the authorized read-only real preflight

**Authority:** Design sections 11.1, 17.3, 18, 21.

**Files:**
- Create: src/agentdeck/application/preflight_service.py
- Modify: src/agentdeck/product/slash_commands.py
- Modify: src/agentdeck/product/shell.py
- Create: tests/product_kernel/test_preflight_service.py
- Create: tests/product_kernel/test_preflight_read_only.py
- Create after authorized run: docs/validation/product-kernel-real-preflight.md
- Modify: HISTORY.md

**Forbidden legacy imports:** M2c preflight/live harness; no install, auth,
fallback selection, source generation, or global configuration.

**Approved legacy evidence:** executable/version expectations only.

- [ ] **Step 1: Write RED contract and filesystem-diff tests**

```python
def test_preflight_requires_frozen_build_model_and_authority(preflight) -> None:
    result = preflight.run(commit="", leader_model="", authority_digest="")
    assert result.ready is False
    assert result.blockers == (
        "frozen_commit_missing", "leader_model_missing", "authority_digest_missing"
    )


def test_preflight_is_read_only_for_project_source(preflight, project) -> None:
    before = tree_identity(project, exclude={".agentdeck/preflight"})
    result = preflight.run(**frozen_inputs())
    after = tree_identity(project, exclude={".agentdeck/preflight"})
    assert before == after
    assert set(result.facts) == PREFLIGHT_FACT_FIELDS
```

- [ ] **Step 2: Confirm RED and implement**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_preflight_service.py tests/product_kernel/test_preflight_read_only.py -q
```

Expected: service absent. Preflight records frozen commit/build, Python env,
resolved CLI paths/versions, passive login readiness, ACP capabilities, Codex
app-server schema digest, tmux, SQLite open/integrity, selected profile,
Leader/model, target manifest hash, and authority digest. Output is stable and
redacted. Project source identity before/after must match.

- [ ] **Step 3: Run deterministic verification and freeze a candidate commit**

```bash
conda run -n agentdeck pytest tests/product_kernel -q
conda run -n agentdeck pytest -q
python -m compileall src tests -q
git diff --check
git add src/agentdeck/application/preflight_service.py src/agentdeck/product tests/product_kernel HISTORY.md
git commit -m "feat: add readonly real product preflight"
git rev-parse HEAD
```

- [ ] **Step 4: STOP and request explicit real-preflight authorization**

The request must name the exact commit, exact Leader backend/model, permission
profile, authority digest, disposable project path, and target manifest hash.
Do not infer authorization from design or plan approval.

- [ ] **Step 5: After authorization, run exactly the inspected command**

```bash
conda run -n agentdeck agentdeck _product preflight --real \
  --commit "$AGENTDECK_AUTHORIZED_COMMIT" \
  --leader "$AGENTDECK_AUTHORIZED_LEADER" \
  --model "$AGENTDECK_AUTHORIZED_MODEL" \
  --permission "$AGENTDECK_AUTHORIZED_PROFILE" \
  --authority-digest "$AGENTDECK_AUTHORIZED_AUTHORITY_DIGEST" \
  --target-manifest "$AGENTDECK_AUTHORIZED_TARGET_MANIFEST" \
  --json
```

Export each named variable from the exact authorization and print the resolved
non-secret values for comparison before execution; unset or mismatched values
are a preflight blocker.
Capture only redacted facts and hashes in the dated validation report. If
ready=false, stop, write deterministic RED coverage for the actual blocker,
repair through a new commit, rerun full verification, and request a new exact
authorization. Never continue to live automatically.

- [ ] **Step 6: Commit the PASS evidence only after ready=true**

```bash
git add docs/validation/product-kernel-real-preflight.md HISTORY.md
git commit -m "test: record product kernel real preflight"
```

### Task 36: Run and accept the real four-Worker Golden Product Mission

**Authority:** Design sections 17.4, 18, 21, 23.

**Files:**
- Create: tests/product_kernel/test_golden_acceptance_contract.py
- Create after authorized run: docs/validation/product-kernel-golden-acceptance.md
- Modify: docs/handoff/current-development-state.md
- Modify: HISTORY.md

**Forbidden legacy imports:** M2c live harness and tmux prompt transport.

**Approved legacy evidence:** none. The disposable Mission itself is new
evidence.

- [ ] **Step 1: Write RED acceptance-report validator tests**

```python
def test_golden_report_requires_all_product_evidence() -> None:
    report = complete_report()
    validate_golden_report(report)
    for field in GOLDEN_REQUIRED_FIELDS:
        broken = complete_report()
        del broken[field]
        with pytest.raises(GoldenGateError, match=field):
            validate_golden_report(broken)


def test_four_workers_are_real_distinct_acp_sessions() -> None:
    report = complete_report()
    assert set(report["worker_backends"]) == {"codex-cli", "claude-cli"}
    assert len(set(report["agent_instance_ids"])) == 4
    assert len(set(report["acp_session_ids"])) == 4
```

- [ ] **Step 2: Confirm RED and implement report validation**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_golden_acceptance_contract.py -q
```

Expected: validator absent. The closed report requires build/test/browser
evidence, desktop/mobile screenshot hashes, visual diff, module/interaction
checks, real ACP identity and sequence reports, implementation-review-
revision-acceptance lineage, findings resolution, SQLite integrity, permission
lineage, tmux fidelity, diagnostics, exit/re-entry, final human-readable result,
and explicit human acceptance.

- [ ] **Step 3: Re-run the frozen pre-live gates**

```bash
conda run -n agentdeck pytest tests/product_kernel -q
conda run -n agentdeck pytest -q
python -m compileall src tests -q
git diff --check
```

The worktree must be clean and HEAD must equal the preflighted commit plus the
preflight evidence-only commit. Any source change invalidates preflight.

- [ ] **Step 4: STOP and request explicit live authorization**

Name the exact frozen source commit, evidence commit, Leader/model, authority
digest, target manifest hash, permission profile, disposable project path, and
the exact command. Confirm the user understands that real Codex/Claude work,
network access allowed by the chosen profile, file edits inside the disposable
project, tmux sessions, and model usage will occur.

- [ ] **Step 5: After authorization, run the inspected Mission once**

```bash
conda run -n agentdeck agentdeck _product golden run \
  --project "$AGENTDECK_AUTHORIZED_DISPOSABLE_PROJECT" \
  --leader "$AGENTDECK_AUTHORIZED_LEADER" \
  --model "$AGENTDECK_AUTHORIZED_MODEL" \
  --permission "$AGENTDECK_AUTHORIZED_PROFILE" \
  --target-manifest "$AGENTDECK_AUTHORIZED_TARGET_MANIFEST" \
  --authority-digest "$AGENTDECK_AUTHORIZED_AUTHORITY_DIGEST"
```

During the run, do not intervene unless the selected permission profile or an
Error Card requires it. AgentDeck, not a human script, must advance all four
stages. Validate SQLite, ACP, browser, tmux, exit/re-entry, and report hashes
after completion. A failed gate remains failed; do not edit evidence or rerun
without diagnosis, deterministic regression, a new frozen commit/preflight,
and renewed authorization.

- [ ] **Step 6: Obtain human product acceptance and commit evidence**

The human watches the Product journey and records accepted/rejected plus a
short reason. Only accepted plus all machine gates may set R7 PASS.

```bash
git add docs/validation/product-kernel-golden-acceptance.md docs/handoff/current-development-state.md HISTORY.md
git commit -m "test: pass real product golden gate"
```

## Phase R8 — Explicit migration and bare-entry cutover

### Task 37: Implement explicit legacy migration preview, backup, confirm, and report

**Authority:** Design sections 7, 10, 20.

**Files:**
- Create: src/agentdeck/adapters/legacy_state.py
- Create: src/agentdeck/application/migration_service.py
- Modify: src/agentdeck/product/slash_commands.py
- Modify: src/agentdeck/product/shell.py
- Create: tests/product_kernel/fixtures/legacy_state/
- Create: tests/product_kernel/test_legacy_migration.py
- Create: docs/migrations/product-kernel-state-migration.md
- Modify: docs/migrations/product-kernel-legacy-reuse-register.md
- Modify: HISTORY.md

**Forbidden legacy imports:** legacy state is parsed as external data; do not
import state.py/models.py or let old JSON/JSONL write into the new database.

**Approved legacy evidence:** sanitized fixture shapes recorded in the reuse
register with path, hash, characterization tests, and Adapter-only rationale.

- [ ] **Step 1: Write RED no-silent-import and rollback tests**

```python
def test_existing_legacy_state_only_produces_preview(project_with_legacy) -> None:
    result = migration.preview(project_with_legacy)
    assert result.writes == ()
    assert result.requires_confirmation is True
    assert not (project_with_legacy / ".agentdeck" / "agentdeck.db").exists()


def test_confirmed_migration_backs_up_verifies_and_reports(project_with_legacy) -> None:
    preview = migration.preview(project_with_legacy)
    report = migration.apply(preview.preview_id, preview.content_hash, confirm=True)
    assert report.backup_hash
    assert report.database_integrity == "ok"
    assert report.imported_counts["projects"] == 1
    assert report.skipped_items
    assert report.rollback_command


def test_drifted_preview_and_failed_verification_leave_no_authority_switch(project_with_legacy) -> None:
    preview = migration.preview(project_with_legacy)
    mutate_legacy(project_with_legacy)
    with pytest.raises(MigrationError, match="drift"):
        migration.apply(preview.preview_id, preview.content_hash, confirm=True)
    assert migration.authority(project_with_legacy) == "legacy"
```

- [ ] **Step 2: Confirm RED and implement**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_legacy_migration.py -q
```

Expected: adapter/service absent. Adapter parses only explicitly inventoried
legacy files into inert records. Preview lists sources, hashes, mappings,
unsupported/skipped data, backup target, and exact confirmation. Apply rechecks
hashes, writes immutable backup, imports through Application commands into a
new temporary database, verifies counts/foreign keys/integrity, atomically
renames it, and writes a report. Failure leaves legacy authority unchanged.

- [ ] **Step 3: Verify and commit**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_legacy_migration.py tests/product_kernel/test_sqlite_*.py tests/product_kernel/test_context_firewall.py -q
git add src/agentdeck/adapters/legacy_state.py src/agentdeck/application/migration_service.py src/agentdeck/product tests/product_kernel docs/migrations HISTORY.md
git commit -m "feat: add explicit product state migration"
```

### Task 38: Cut bare agentdeck over to Product Shell with bounded rollback

**Authority:** Design sections 5, 19 R8, 20, 23.

**Files:**
- Modify: src/agentdeck/cli.py
- Modify: src/agentdeck/product/bootstrap.py
- Create: tests/product_kernel/test_bare_entry_cutover.py
- Modify: tests/test_cli_structured_output.py only if bare-entry expectation is
  explicitly reclassified
- Modify: README.md
- Modify: README.zh-CN.md
- Modify: AGENTS.md
- Modify: AGENT.md
- Modify: CLAUDE.md
- Modify: docs/handoff/current-development-state.md
- Modify: HISTORY.md

**Forbidden legacy imports:** no new legacy dependency in Product/Application/
Kernel. cli.py remains a compatibility dispatcher only.

**Approved legacy evidence:** the existing no-subcommand branch and structured
subcommand dispatch are characterized before the one-line route switch.

- [ ] **Step 1: Write RED cutover/compatibility tests**

```python
def test_bare_agentdeck_launches_product_shell(cli_runner) -> None:
    result = cli_runner([], stdin="/exit\n")
    assert result.exit_code == 0
    assert "AgentDeck" in result.stdout
    assert "Session saved." in result.stdout


@pytest.mark.parametrize("args", [
    ["status"], ["doctor"], ["contract", "list"], ["leader", "chat", "--message", "status"],
])
def test_structured_subcommands_keep_their_existing_contract(cli_runner, args) -> None:
    before = legacy_characterization(args)
    after = cli_runner(args)
    assert (after.exit_code, after.stdout, after.stderr) == before


def test_legacy_shell_is_explicit_and_non_authoritative(cli_runner) -> None:
    result = cli_runner(["legacy-shell", "--help"])
    assert "rollback only" in result.stdout
    assert "not Mission authority" in result.stdout
```

- [ ] **Step 2: Confirm RED and implement**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_bare_entry_cutover.py tests/test_cli_structured_output.py -q
```

Expected: bare entry still launches the old route. In cli.main(), only the
no-subcommand branch changes to product.bootstrap.main(). Keep structured
subcommands byte-compatible. Add bounded agentdeck legacy-shell for rollback;
it must not open the new database as writer, create new Missions, or be
presented as current product. Remove hidden _product wording from user docs.

- [ ] **Step 3: Verify and commit**

```bash
conda run -n agentdeck pytest tests/product_kernel/test_bare_entry_cutover.py tests/test_cli_structured_output.py tests/test_conversation_shell_cli.py -q
git add src/agentdeck/cli.py src/agentdeck/product/bootstrap.py tests README.md README.zh-CN.md AGENTS.md AGENT.md CLAUDE.md docs/handoff/current-development-state.md HISTORY.md
git commit -m "feat: launch product shell from bare agentdeck"
```

### Task 39: Run the final release audit and freeze post-MVP boundaries

**Authority:** Design sections 20–23 and Product North Star.

**Files:**
- Create: docs/validation/2026-07-18-product-kernel-release-audit.md
- Create: docs/migrations/product-kernel-deletion-candidates.md
- Modify: docs/roadmap/product-north-star.md
- Modify: docs/roadmap/ultimate-goal-roadmap.md
- Modify: docs/handoff/current-development-state.md
- Modify: README.md
- Modify: README.zh-CN.md
- Modify: HISTORY.md

**Forbidden legacy imports:** no product source changes in this audit task.

**Approved legacy evidence:** Git history, current compatibility tests, and the
reuse register.

- [ ] **Step 1: Run static plan/authority audits**

```bash
rg -n "M2c|P1 Durable|ConversationSession.*authority|tmux.*transport" \
  README.md README.zh-CN.md AGENTS.md AGENT.md CLAUDE.md \
  docs/handoff/current-development-state.md docs/roadmap
rg -n "agentdeck\.(cli|state|models|conversation|daemon|mission)" \
  src/agentdeck/kernel src/agentdeck/application src/agentdeck/ports src/agentdeck/product
git diff --check
```

Expected: the first command has no active-authority claim; the second has no
matches; diff check passes.

- [ ] **Step 2: Run the complete release verification twice**

```bash
conda run -n agentdeck python -m compileall src tests -q
conda run -n agentdeck pytest tests/product_kernel -q
conda run -n agentdeck pytest -q
conda run -n agentdeck pytest -q
conda run -n agentdeck agentdeck doctor
```

Record exact counts, durations, environment, HEAD, and any skips. A flaky,
changed, or failed second full run blocks completion.

- [ ] **Step 3: Verify product behavior in a fresh local project**

```bash
tmpdir="$(mktemp -d)"
cd "$tmpdir"
conda run -n agentdeck agentdeck
```

Manually exercise /help, /status, /setup, /leader, /model, /permissions, an
open goal to Preview, cancellation before confirmation, /exit, and re-entry.
This is local deterministic smoke only; do not start another real Mission.

- [ ] **Step 4: Write the audit and deletion-candidate report**

The audit links the R7 PASS evidence, exact tests, migration, bare entry,
structured CLI compatibility, SQLite authority, ACP-only route, tmux observer,
Diagnostics, exit/re-entry, and human acceptance. The deletion report lists old
ConversationSession, router branches, M2c harness, duplicate orchestration, and
legacy write surfaces with replacement path, coverage, current callers,
rollback risk, and a separate future deletion commit. Delete none here.

The roadmap starts post-MVP in this locked order: background execution; Memory;
Skill Registry; governed self-improvement; browser workbench; broader Agents;
A2A; remote/mobile/WispTerm-class clients.

- [ ] **Step 5: Commit and invoke branch-finish verification**

```bash
git add docs README.md README.zh-CN.md HISTORY.md
git commit -m "docs: close product kernel rewrite"
git status --short
git log --oneline --decorate -10
```

Then use superpowers:verification-before-completion and
superpowers:finishing-a-development-branch. Do not merge or push without the
human's explicit integration choice.

## 4. Phase regression matrix

| Gate | Required command | Product fact proved |
|---|---|---|
| R0 | pytest test_architecture, test_context_firewall, test_dev_entry | New boundary exists; bare behavior unchanged |
| R1 | pytest test_kernel_*, test_sqlite_*, test_recovery_service | Pure invariants and one project authority |
| R2 | pytest test_product_*, test_session_service | Human shell works without an LLM |
| R3 | pytest Leader, Mission, Preview files | Untrusted proposals become exact confirmable Missions |
| R4 | pytest ACP, approval, execution files | ACP-only four-stage automatic orchestration |
| R5 | pytest tmux, observer, takeover files | Real streams are visible but non-authoritative |
| R6 | pytest diagnostics, trace, recovery files | Failure and recovery are specific and safe |
| R7 | full product_kernel suite, full suite, authorized preflight/live | Real product journey and evidence |
| R8 | full suite twice, fresh-project smoke | Bare-entry cutover and compatibility |

At the end of every phase, also run:

```bash
conda run -n agentdeck pytest tests/product_kernel/test_architecture.py tests/product_kernel/test_context_firewall.py -q
git diff --check
git status --short
```

## 5. Design-section coverage

| Design section | Implemented by Tasks |
|---|---|
| 1–4 decision, goals, non-goals | 1–3, enforced by architecture/context tests |
| 5 user experience | 11–15, 19, 29–30, 38 |
| 6 architecture | 1–4 and every architecture guard |
| 7 reuse policy | 3, 26, 37, 39 |
| 8 domain model | 4–8, 16, 20, 23–24 |
| 9 permissions | 6, 22, 29 |
| 10 persistence/recovery | 9–10, 12, 15, 31–32, 37 |
| 11 Leader/setup | 11–12, 16–19, 25–26 |
| 12 ACP-only orchestration | 18, 20–26 |
| 13 four stages | 7–8, 22–24, 33, 36 |
| 14 tmux observation | 27–29, 33, 36 |
| 15 diagnostics | 4, 21, 24, 30–32 |
| 16 configuration | 11–15 |
| 17 verification | every Task; full layers in 20–36 |
| 18 Golden gate | 34–36 |
| 19 rewrite phases | R0–R8 headings |
| 20 migration/deletion | 3, 37–39 |
| 21 discipline | every Task metadata, RED/GREEN, verification, commit |
| 22 post-MVP order | 39 only; no post-MVP implementation |
| 23 completion | 33–39 |

## 6. Explicit product and safety boundaries

- The product is foreground-first. Closing the terminal interrupts active work
  only after confirmation; background continuation is not smuggled into MVP.
- Automatic Codex/Claude work is ACP-only. There is no PTY prompt fallback.
- Codex uses the AgentDeck ACP bridge over the official stable app-server
  protocol; Claude uses claude-agent-acp. Schema/capability drift blocks start.
- tmux renders decoded events and supports takeover; it never transports tasks,
  infers completion, or owns state.
- One project-local SQLite database and one foreground writer are authoritative.
  No cross-project search, WAL, SHM, daemon, or multi-writer is introduced.
- API Leaders require exact provider, model, and credential source. No default
  DeepSeek, silent provider change, or model fallback exists.
- No real provider, login, install, global configuration, reference capture,
  preflight, or Golden Mission occurs without its exact gate authorization.
- Memory, Skills, self-improvement, GUI, A2A, broader Workers, background
  execution, and WispTerm-class clients remain after cutover.

## 7. Plan self-review checklist

Before approving execution, verify:

- [ ] Tasks 1–39 form one dependency-ordered R0–R8 construction path.
- [ ] Every Task names authority, exact files, forbidden imports, approved
  evidence, RED reason, smallest GREEN, tests, and one local commit.
- [ ] Kernel/Application never depend on Adapters or legacy modules.
- [ ] Product depends on Application, while bootstrap alone composes Adapters.
- [ ] Store is project-local, single-writer, rollback-journaled, transactional,
  idempotent, secret-minimal, and restart-safe.
- [ ] Leader and Worker schemas are closed and untrusted until validation.
- [ ] Four separate Worker Instances use four separate ACP sessions.
- [ ] Multiple sequential permission requests preserve exact lineage.
- [ ] Review prose cannot forge Revision Tasks or acceptance evidence.
- [ ] tmux failure and Worker failure remain distinct.
- [ ] Every failure category reaches a stable redacted Error Card.
- [ ] Real preflight and live commands are stopped behind separate exact human
  authorizations and new frozen identities after any code repair.
- [ ] Golden PASS includes machine evidence plus observed human acceptance.
- [ ] Bare-entry changes only after Golden PASS; structured commands remain.
- [ ] Post-MVP features are named but not implemented.
- [ ] No Task contains an unfinished marker or an instruction to weaken tests.

## 8. Execution handoff

This plan is the sole implementation plan for the Product Kernel Rewrite.
Execution begins only after human plan approval.

Choose one execution mode:

1. **Subagent-Driven Development (recommended):** remain in this task, execute
   one Task at a time with a fresh implementation subagent and two reviews
   (spec compliance, then code quality), while the primary agent owns tests,
   commits, phase gates, and user updates.
2. **Inline executing-plans:** remain in one agent context, execute three Tasks
   per checkpoint with the same RED/GREEN, verification, and commit boundaries.

In either mode, Task 35 real preflight and Task 36 real Mission stop again for
their exact authorizations. Plan approval cannot pre-authorize those live gates.
