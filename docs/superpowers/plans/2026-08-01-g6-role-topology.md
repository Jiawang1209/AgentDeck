# G6 Role Topology 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 GUI/TUI 一眼看出本项目把北极星六个角色层(frontdesk / planner /
orchestrator / coder / code_reviewer / round_reviewer)各自绑到了什么、
生命周期是什么、此刻什么状态、缺什么。

**Architecture:** 纯推导模块 `role_topology.py` 承担全部绑定逻辑(零 IO、
零 LLM、不 import cli/state/config 的写路径),CLI 与 workbench 两面共用同
一个 builder 和同一份 validator。绑定从**现有权威来源**推导(复用
`resolved_planner_backend` / `resolved_orchestrator_backend` /
`leader_backend_identity`),不新增配置面、不新增状态源。

**Tech Stack:** Python 3.12 标准库;`src/agentdeck/role_topology.py`(新)、
`src/agentdeck/cli.py`、`src/agentdeck/contracts.py`、pytest。

**Spec:** `docs/superpowers/specs/2026-08-01-g6-role-topology-design.md`

---

## 全局约束(每个任务都适用)

- 所有命令走 `conda run --no-capture-output -n agentdeck …`。
- **不得 `git push`**;commit message **不得**含任何 `Co-Authored-By` trailer;
  **不得** `git add` 任何 `.omc/` 路径。
- 每个 commit 必须同步 `HISTORY.md`(新条目加在**第一个** `## 2026-08-01`
  小节顶部;该文件合法地有三个同名标题,用第一个)。模板:
  Type / Motivation / What / Impact / Verification。
- TDD:先写失败测试、**运行确认失败**、再实现、再运行。
- **不得削弱任何既有测试**。既有测试是行为钉。
- 全路径**只读**:不写 state、不追加事件、不调 provider、不读写 tmux。

---

### Task 1: 纯推导模块 `role_topology.py`

**Files:**
- Create: `src/agentdeck/role_topology.py`
- Test: `tests/test_role_topology.py`(新建)

模块必须**零 IO**、不 import `cli`;可以 import `config` 的只读 helper。
输入是纯数据,便于把矩阵测透。

需要的公开符号:

```python
ROLE_TOPOLOGY_LAYERS = ("intake", "orchestration", "work", "acceptance")
ROLE_BINDING_KINDS = ("command", "logical_leader", "worker_agent")
ROLE_BINDING_STATUSES = ("bound", "unbound", "ambiguous")
ROLE_LIFECYCLES = ("persistent", "task_scoped", "on_demand")

# 北极星六层的静态骨架(顺序即展示顺序)
ROLE_SPECS = (
    {"role": "frontdesk",      "layer": "intake",        "binding_kind": "command",         "lifecycle": "persistent"},
    {"role": "planner",        "layer": "orchestration", "binding_kind": "logical_leader",  "lifecycle": "persistent"},
    {"role": "orchestrator",   "layer": "orchestration", "binding_kind": "logical_leader",  "lifecycle": "persistent"},
    {"role": "coder",          "layer": "work",          "binding_kind": "worker_agent",    "lifecycle": "task_scoped"},
    {"role": "code_reviewer",  "layer": "work",          "binding_kind": "worker_agent",    "lifecycle": "task_scoped"},
    {"role": "round_reviewer", "layer": "acceptance",    "binding_kind": "worker_agent",    "lifecycle": "on_demand"},
)

# role 字符串语义匹配(小写子串);与 [[agents]] 的 role 自由文本对齐。
IMPLEMENTATION_ROLE_HINTS = ("implement", "coder", "coding", "实现")
REVIEW_ROLE_HINTS = ("review", "审查", "复审")

def resolve_worker_role(agents, hints) -> tuple[str | None, str, list[str]]:
    """(agent_id, binding_status, candidates) —— 恰好一个命中即 bound;
    零个 unbound;多个 ambiguous 且 candidates 列全(绝不择一)。

    agents 是纯数据:[{"agent_id": str, "role": str}, ...]
    """
```

- [ ] **Step 1: 写失败测试** `tests/test_role_topology.py`

```python
from agentdeck.role_topology import (
    ROLE_BINDING_KINDS, ROLE_BINDING_STATUSES, ROLE_LIFECYCLES,
    ROLE_SPECS, ROLE_TOPOLOGY_LAYERS, IMPLEMENTATION_ROLE_HINTS,
    REVIEW_ROLE_HINTS, resolve_worker_role,
)


def test_role_specs_cover_the_six_north_star_layers_in_order():
    assert tuple(spec["role"] for spec in ROLE_SPECS) == (
        "frontdesk", "planner", "orchestrator",
        "coder", "code_reviewer", "round_reviewer",
    )
    for spec in ROLE_SPECS:
        assert spec["layer"] in ROLE_TOPOLOGY_LAYERS
        assert spec["binding_kind"] in ROLE_BINDING_KINDS
        assert spec["lifecycle"] in ROLE_LIFECYCLES


def test_closed_enums_are_closed():
    assert ROLE_TOPOLOGY_LAYERS == ("intake", "orchestration", "work", "acceptance")
    assert ROLE_BINDING_KINDS == ("command", "logical_leader", "worker_agent")
    assert ROLE_BINDING_STATUSES == ("bound", "unbound", "ambiguous")
    assert ROLE_LIFECYCLES == ("persistent", "task_scoped", "on_demand")


def test_resolve_worker_role_binds_a_single_match():
    agents = [{"agent_id": "coder", "role": "implementation"},
              {"agent_id": "reviewer", "role": "review"}]
    assert resolve_worker_role(agents, IMPLEMENTATION_ROLE_HINTS) == (
        "coder", "bound", [])


def test_resolve_worker_role_reports_unbound_with_no_match():
    agents = [{"agent_id": "reviewer", "role": "review"}]
    assert resolve_worker_role(agents, IMPLEMENTATION_ROLE_HINTS) == (
        None, "unbound", [])


def test_resolve_worker_role_reports_ambiguous_and_never_picks_one():
    """fail-closed:两个同为实现角色时绝不挑第一个。"""
    agents = [{"agent_id": "coder_a", "role": "implementation"},
              {"agent_id": "coder_b", "role": "coding"}]
    agent_id, status, candidates = resolve_worker_role(
        agents, IMPLEMENTATION_ROLE_HINTS)
    assert agent_id is None
    assert status == "ambiguous"
    assert candidates == ["coder_a", "coder_b"]


def test_resolve_worker_role_matches_case_insensitively_and_ignores_bad_rows():
    agents = [{"agent_id": "c", "role": "IMPLEMENTATION"}, "not a dict",
              {"agent_id": "x"}]
    assert resolve_worker_role(agents, IMPLEMENTATION_ROLE_HINTS) == (
        "c", "bound", [])


def test_review_hints_do_not_match_implementation_roles():
    agents = [{"agent_id": "coder", "role": "implementation"}]
    assert resolve_worker_role(agents, REVIEW_ROLE_HINTS) == (None, "unbound", [])
```

- [ ] **Step 2: 运行,确认失败**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_role_topology.py -q
```

预期:FAIL,`ModuleNotFoundError: No module named 'agentdeck.role_topology'`。

- [ ] **Step 3: 实现模块**

按上面的公开符号写。`resolve_worker_role` 要点:跳过非 dict / 缺 `agent_id`
的行;role 取 `str(item.get("role", ""))` 后 `.lower()`;命中判据是任一 hint
是 role 的子串;`candidates` 保持输入顺序。模块顶部写清楚它是纯推导、
零 IO、不 import cli/state。

- [ ] **Step 4: 运行,确认通过**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_role_topology.py -q
```

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/role_topology.py tests/test_role_topology.py HISTORY.md
git commit -m "feat: add the pure role topology derivation module"
```

---

### Task 2: 卡片 builder + `agentdeck roles` 命令 + 契约字段

**Files:**
- Modify: `src/agentdeck/contracts.py`(字段元组 + validator + example +
  discovery payload + `CONTRACT_INDEX_SPECS`)
- Modify: `src/agentdeck/cli.py`(builder + 命令 + argparse 注册)
- Test: `tests/test_role_topology_cli.py`(新建)、`tests/test_contracts.py`

契约符号(放在 contracts.py,与既有 card 字段元组同区):

```python
ROLE_TOPOLOGY_CARD_FIELDS = (
    "mode", "source_command", "layer_count", "bound_count",
    "unbound_count", "ambiguous_count", "split_enabled", "roles", "controls",
)

ROLE_TOPOLOGY_ROLE_FIELDS = (
    "role", "layer", "binding_kind", "binding_status", "agent_id",
    "provider", "model", "backend", "transport", "lifecycle",
    "runtime_status", "pane_id", "blocker", "candidates", "controls",
)
```

`validate_role_topology_contract(payload)` 必须校验:顶层字段齐全;
`mode == "role_topology"`;每个 `roles[]` item 字段齐全;`layer` /
`binding_kind` / `binding_status` / `lifecycle` 取值在闭合枚举内;
**`binding_kind != "worker_agent"` 时 `runtime_status` 与 `pane_id` 必须
为 null**(spec 的必然性条款);`binding_status != "bound"` 时 `blocker`
必须非空;`binding_status == "ambiguous"` 时 `candidates` 必须非空,
其余情形必须为空;三个计数必须与 `roles[]` 实际统计一致且相加等于
`layer_count`。

cli.py 的 builder `_role_topology_card(config, project_view)`:

- 骨架来自 `ROLE_SPECS`,顺序即展示顺序。
- `frontdesk`:`binding_status="bound"`、`agent_id/provider/model/backend/
  transport` 全 null、control 指向 `agentdeck frontdesk --message <text>`
  (含占位符 → disabled + blocker)。
- `planner` / `orchestrator`:provider/model 取
  `resolved_planner_backend(config.leader)` / `resolved_orchestrator_backend(...)`;
  backend/transport 复用既有 normalized provenance(与 `leader_backend`
  同源,**不另写**);`pane_id` / `runtime_status` 恒 null;
  `split_enabled = leader_split_enabled(config.leader)`。
- `coder`:`resolve_worker_role(agents, IMPLEMENTATION_ROLE_HINTS)`。
- `code_reviewer`:`config.review.reviewers` 非空 → 首位为绑定(其余进
  `candidates` 作为组成员展示,`binding_status` 仍为 `bound`);为空 →
  回退 `resolve_worker_role(agents, REVIEW_ROLE_HINTS)`。
- `round_reviewer`:`config.review.round_reviewer` 有值 → bound;
  否则 `unbound` + blocker `set [review] round_reviewer to enable a
  dedicated acceptance reviewer`。
- worker 层的 `runtime_status` / `pane_id` 取自 ProjectView `agents[]` 的
  runtime 投影(**不读 tmux**);未 spawn 时 `runtime_status` 照实为
  `not_running` 之类的既有取值,`pane_id` 为 null。
- `controls[]`:每个 item 至少一条 `kind=inspect`;`worker_agent` 且
  running 时可加 `kind=terminal` 指向 `agentdeck agent terminal --agent <id>`,
  未 running 必须 disabled 并给 blocker。

命令 `agentdeck roles`:只读,打印前过 validator,失败返回非 0 且不打印
半坏 JSON(与 `agentdeck controls` 同形)。

- [ ] **Step 1: 写失败测试**(`tests/test_role_topology_cli.py`)

至少覆盖:默认项目跑通且 `mode=="role_topology"`、六层齐全;
`round_reviewer` 在默认项目为 `unbound` 且 blocker 非空;
`logical_leader` 层的 `pane_id` / `runtime_status` 为 null;
配了 `[review] reviewers = ["reviewer"]` 后 `code_reviewer` 为 `bound`;
**只读**——命令前后 `StateStore(root).load()` 与 events.jsonl 逐字节相同;
validator 拒绝 `worker_agent` 之外的层带 pane_id;validator 拒绝
`unbound` 而无 blocker。

- [ ] **Step 2: 运行,确认失败**
- [ ] **Step 3: 实现**(contracts 字段/validator/example/discovery/index,
      cli builder + 命令 + argparse)
- [ ] **Step 4: 运行,确认通过**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_role_topology_cli.py tests/test_role_topology.py tests/test_contracts.py tests/test_agent_cli.py -q
```

注意:`CONTRACT_INDEX_SPECS` 增到 44 会打破 `test_contracts.py` 与
`test_agent_cli.py` 里对索引数量/名单的断言——**同步更新为 44 并保持断言
同样严格**(这是契约扩张,不是放宽),并在报告里说明。

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/contracts.py src/agentdeck/cli.py tests/ HISTORY.md
git commit -m "feat: add the role topology card and agentdeck roles command"
```

---

### Task 3: workbench 集成 + 契约文档 + 全量回归

**Files:**
- Modify: `src/agentdeck/cli.py`(workbench snapshot 加 `role_topology_card`)
- Modify: `src/agentdeck/contracts.py`(workbench `snapshot_fields` +
  `role_topology_card_fields` / `role_topology_role_fields` discovery;
  workbench validator 复用 `validate_role_topology_contract`)
- Create: `docs/contracts/role-topology-schema.md`
- Modify: `docs/contracts/contract-index-schema.md`, `README.md`,
  `CLAUDE.md`, `docs/handoff/current-development-state.md`
- Test: `tests/test_workbench*.py`(用既有文件名)

- [ ] **Step 1: 写失败测试**:workbench 输出含 `role_topology_card`,
      且该卡通过 `validate_role_topology_contract`;它与 `agentdeck roles`
      的输出**逐字段相同**(同一 builder 的回归钉)。
- [ ] **Step 2: 运行,确认失败**
- [ ] **Step 3: 实现 + 写契约文档**

`docs/contracts/role-topology-schema.md` 必须写清:发现入口、卡片字段表、
role item 字段表、四个闭合枚举、三态 `binding_status` 的含义与
**ambiguous 绝不择一**的 fail-closed 理由、`logical_leader` 层 pane 字段
必然为 null 的原因、以及安全边界(纯只读;拓扑是**观察面不是授权**,
不改变任何 gate、不授权 dispatch)。同时说明它与既有 `role_card` 的分工。

- [ ] **Step 4: 全量回归**

```bash
conda run --no-capture-output -n agentdeck pytest -q
conda run --no-capture-output -n agentdeck python -m compileall src tests -q
```

- [ ] **Step 5: 文档同步**:README 的 "What works today" 加一条;
      CLAUDE.md 加 role-topology 契约规则 bullet(照既有 contract 规则的
      写法);handoff 记录 G6 落地与 live 验证待办。
- [ ] **Step 6: Commit**

---

## 完成后

**不要 push。** 报告:commit 列表、全量测试数字、契约索引从 43 → 44 的
同步点、任何偏离计划之处及原因。
