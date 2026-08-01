# `/goal` 一句话走开 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把爬到自主度顶格所需的四条命令九个标志,压成 `goal preview` →
`goal start --confirm` 两步,其中**四次确认合并为一次信息完整的确认**,
而所有安全门一条不动。

**Architecture:** `goal` **不新增任何一种动作**。`preview` 复用
`leader plan` 的规划路径产出 plan 并渲染一张"将要授权"卡;`start` 依次
复用 `approval approve-plan --confirm` 与 `run-loop-host start --confirm`
的既有实现。两条命令都不翻 `approval_mode`、不改配置、不新增委托。

**Tech Stack:** Python 3.12 标准库;`src/agentdeck/cli.py`、
`src/agentdeck/contracts.py`、pytest。

**Spec:** `docs/superpowers/specs/2026-08-01-goal-one-shot-walkaway-design.md`
(已冻结,含 user 拍板的两点缺省)

---

## 全局约束(每个任务都适用)

- 所有命令走 `conda run --no-capture-output -n agentdeck …`。
- **不得 `git push`**;commit message **不得**含 `Co-Authored-By` trailer;
  **不得** `git add` 任何 `.omc/` 路径或未跟踪的 `AGENTS.md`。
- 每个 commit 同步 `HISTORY.md`(**第一个** `## 2026-08-01` 小节顶部)。
- TDD:先写失败测试、**运行确认失败**、再实现、再运行。
- **不得削弱任何既有测试**。
- **不得改动** `leader plan`、`approval approve-plan`、`run-loop-host start`
  三条既有命令的任何行为——`goal` 只是它们的调用方。

---

### Task 1: `agentdeck goal preview --task <text>` + 契约

**Files:**
- Modify: `src/agentdeck/contracts.py`(字段元组 + validator + example +
  discovery + `CONTRACT_INDEX_SPECS` 45)
- Modify: `src/agentdeck/cli.py`(builder + 命令 + argparse)
- Test: `tests/test_goal_cli.py`(新建)、`tests/test_contracts.py`、
  `tests/test_agent_cli.py`

契约符号:

```python
GOAL_PREVIEW_RESPONSE_FIELDS = (
    "ok", "mode", "task", "plan_id", "step_count", "steps",
    "budget", "delegations", "merge_on_complete", "release_boxes",
    "stop_conditions", "blocker", "confirm_command",
    "requires_explicit_user", "safety", "controls",
)

GOAL_PREVIEW_STEP_FIELDS = ("step", "agent_id", "role", "task")
GOAL_PREVIEW_BUDGET_FIELDS = (
    "max_waves", "max_waves_is_default", "interval",
    "max_review_rounds", "max_approvals",
)
```

要点:

- `mode == "goal_preview"`;`safety == "explicit_user"`;
  `requires_explicit_user is True`。
- **`max_waves` 缺省 300**,`max_waves_is_default` 为 `True` 时表示该值来自
  缺省而非用户输入——渲染层据此打印"↑ 缺省值,可用 `--max-waves` 改"。
  常量 `GOAL_DEFAULT_MAX_WAVES = 300` 是单一来源。
- `release_boxes` 缺省 `True`,`merge_on_complete` 缺省 `False`(user 拍板)。
- `delegations` 是当前**活跃**委托的 compact 摘要(复用既有
  `delegation list` 的数据源,不另查);它只是展示,不是授权。
- `stop_conditions` 是闭合列表,至少覆盖:复审通过待合并(缺省下的正常
  终点)、`human_gate`、复审预算耗尽、白名单外 agent 需审批。
- **`blocker`**:`approval_mode != "autonomous"` 时非空,内容必须是显式的
  `agentdeck policy set-mode --mode autonomous --confirm --allow-agent <id>
  --max-approvals <N>` 提示。此时 `confirm_command` 必须为 `null`——
  **`goal preview` 绝不代人翻这个开关**。
- preview **写 plan**(与 `leader plan` 等价,复用同一路径),但不批准、
  不派发、不启动宿主。
- 默认输出是**人类可读的渲染**(spec 里那个形状),`--json` 才给完整
  payload;两条路径同一份数据,JSON 打印前过 validator。

- [ ] **Step 1: 写失败测试**(`tests/test_goal_cli.py`)

至少覆盖:fake provider 下 preview 产出 plan 且 `mode=goal_preview`;
`max_waves == 300` 且 `max_waves_is_default is True`;显式
`--max-waves 50` 时 `is_default is False`;非 autonomous 项目 → `blocker`
非空且 `confirm_command is None` 且**没有**创建任何 approval;
`release_boxes is True` / `merge_on_complete is False`;`--json` 与默认渲染
同源;validator 拒绝 `blocker` 非空却仍给 `confirm_command` 的 payload。

- [ ] **Step 2: 运行,确认失败**
- [ ] **Step 3: 实现**
- [ ] **Step 4: 运行,确认通过**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_goal_cli.py tests/test_contracts.py tests/test_agent_cli.py -q
```

契约索引 44 → 45 会打破索引数量/名单断言——**同步更新并保持同样严格**
(契约扩张,不是放宽),在报告里说明。

- [ ] **Step 5: Commit**

---

### Task 2: `agentdeck goal start --plan-id <id> --confirm`

**Files:**
- Modify: `src/agentdeck/contracts.py`(start 响应字段 + validator + example)
- Modify: `src/agentdeck/cli.py`
- Test: `tests/test_goal_cli.py`

```python
GOAL_START_RESPONSE_FIELDS = (
    "ok", "mode", "plan_id", "approved_count", "host_pid",
    "max_waves", "interval", "release_boxes", "merge_on_complete",
    "status_command", "stop_command", "next_command",
    "requires_explicit_user", "safety",
)
```

要点:

- **四道门,任一不满足拒绝且零写零 spawn**:`--confirm`、
  `approval_mode == "autonomous"`、已知 `--plan-id`、`--max-waves >= 1`
  (缺省 300 也要过这一关)。
- 顺序:先 `approve-plan --confirm` 的既有实现批准该 plan 的全部 pending
  审批,再 `run-loop-host start --confirm` 的既有实现启动宿主。
  **两者都必须调用既有函数,不得复制其逻辑。**
- 若 approve 阶段失败,**绝不启动宿主**;若宿主启动失败,已批准的审批
  保持已批准(与人手工跑这两条命令的结果一致),响应如实报告。
- `mode == "goal_start"`;`safety == "delegated"`;
  `requires_explicit_user is True`。
- 追加一条 `goal_started` 审计事件(plan_id / approved_count / max_waves /
  release_boxes / merge_on_complete);宿主自己的 `run_loop_host_started`
  事件照常由既有实现追加,**不得抑制**。

- [ ] **Step 1: 写失败测试**

至少覆盖:四道门各自拒绝且零写零 spawn(用 monkeypatch 记录 spawn 调用);
成功路径下 approve 与 host start **都**被调用且顺序正确;approve 失败时
**宿主未被 spawn**;`goal_started` 事件已追加且宿主自己的事件也在;
默认 `release_boxes=True` / `merge_on_complete=False` 被透传给宿主。

- [ ] **Step 2-4: 红 → 实现 → 绿**
- [ ] **Step 5: Commit**

---

### Task 3: 文档 + 全量回归

**Files:**
- Create: `docs/contracts/goal-schema.md`
- Modify: `docs/contracts/contract-index-schema.md`、`README.md`、
  `CLAUDE.md`、`docs/handoff/current-development-state.md`

契约文档必须写清:两步为什么不能合成一步(引仓库既有安全边界原文
"only the exact confirmed preview becomes frozen authority");四道门;
**`goal` 绝不翻 `approval_mode`** 这条最重要的边界;两点缺省及其理由;
`delegations` 只是展示不是授权;以及 `goal` 不新增任何一种动作、
start 之后全部由未改动的宿主 wave 引擎承担。

- [ ] **Step 1: 全量**

```bash
conda run --no-capture-output -n agentdeck pytest -q
conda run --no-capture-output -n agentdeck python -m compileall src tests -q
```

- [ ] **Step 2: 文档同步 + Commit**

---

## 完成后

**不要 push。** 报告:commit 列表、全量数字、契约索引 44→45 的同步点、
`goal preview` 在 fake provider 下的实际渲染输出、任何偏离及原因。
