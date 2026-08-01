# G6 Role Topology 设计

Status: frozen(user 2026-08-01 授权"按北极星文件持续开发";G6 的验收标准
已由 `docs/roadmap/ultimate-goal-roadmap.md` 冻结,本文只定实现形态)
Phase: G6(北极星最后一相;G1–G5 均已落地)

## 目标(roadmap 原文)

> 在 ProjectView/workbench contract 中形成角色拓扑,让 GUI/TUI 能显示
> frontdesk、planner、orchestrator、coder、code_reviewer、round_reviewer
> 的 provider、生命周期、当前状态、阻塞点和下一步控制。

## 关键事实:六个角色不是同一种东西

这是整个设计的支点。北极星的六个角色在 AgentDeck 里由**三种不同的绑定
方式**承载,拓扑卡如果把它们拍平成一张 agent 表就会撒谎:

| 层 | 角色 | 绑定种类 `binding_kind` | 绑定来源 | 有 pane? |
| --- | --- | --- | --- | --- |
| 接待 | `frontdesk` | `command` | `agentdeck frontdesk` 命令本身 | 否 |
| 编排 | `planner` | `logical_leader` | `[leader.planner]`,回退 `[leader]` | 否 |
| 编排 | `orchestrator` | `logical_leader` | `[leader.orchestrator]`,回退 `[leader]` | 否 |
| 工作 | `coder` | `worker_agent` | `[[agents]]` 中 role 为实现类的项 | 是 |
| 工作 | `code_reviewer` | `worker_agent` | `[review].reviewers`,回退 role 为审查类的项 | 是 |
| 验收 | `round_reviewer` | `worker_agent` | `[review].round_reviewer` | 是 |

`binding_kind` 是闭合枚举 `("command", "logical_leader", "worker_agent")`。
它同时解释了**为什么某些字段必然为 null**:`logical_leader` 层永远
`pane_id=null` / `runtime_status=null`(它不是 pane,这一点在既有
`leader_backend` provenance 里已经冻结),`command` 层连 provider 都没有。

## 绑定推导:从现有事实推,歧义如实标注

本切片**不新增配置面**。绑定全部从已有的权威来源推导:

- `planner` / `orchestrator`:复用 `config.resolved_planner_backend()` /
  `resolved_orchestrator_backend()`(含 `[leader]` 回退规则),并复用
  `state.leader_backend_identity()` 做 normalized provenance——**绝不另
  写一份**。`config.leader_split_enabled()` 决定该层是显式配置还是继承。
- `code_reviewer`:`[review].reviewers` 非空时即为该组;否则回退到
  `[[agents]]` 中 role 含审查语义的项。
- `round_reviewer`:`[review].round_reviewer` 有值时绑定该 agent;
  否则**未绑定**(不假装由 code_reviewer 兼任)。
- `coder`:`[[agents]]` 中 role 为实现语义的项。

推导失败时的表达是**闭合的三态** `binding_status`:

| 值 | 含义 |
| --- | --- |
| `bound` | 恰好解析到绑定物 |
| `unbound` | 该层在本项目没有配置(例如没配 `round_reviewer`) |
| `ambiguous` | 解析到多个候选且无法凭现有事实择一 |

`ambiguous` 是 fail-closed 的体现:**绝不挑第一个了事**。三态都不是错误,
不阻断任何命令——拓扑是观察面,不是 gate。

## 交付面

三处,全部只读:

1. `agentdeck roles` —— 独立只读命令(与 `agentdeck controls` /
   `agentdeck continue` 同类),输出 `role_topology_card`,打印前经
   `validate_role_topology_contract()` 守门。
2. `workbench` 的 `role_topology_card` —— 同一个 builder,同一份 validator。
3. `agentdeck contract role-topology [--example]` + 第 44 个契约
   `docs/contracts/role-topology-schema.md` + `CONTRACT_INDEX_SPECS`。

### 卡片字段

```
mode, source_command, layer_count, bound_count, unbound_count,
ambiguous_count, split_enabled, roles[], controls[]
```

每个 `roles[]` item:

```
role, layer, binding_kind, binding_status, agent_id, provider, model,
backend, transport, lifecycle, runtime_status, pane_id, blocker,
candidates[], controls[]
```

- `layer`:闭合枚举 `("intake", "orchestration", "work", "acceptance")`。
- `lifecycle`:闭合枚举 `("persistent", "task_scoped", "on_demand")`——
  北极星表里的生命周期列(frontdesk/planner/orchestrator 常驻,
  coder/code_reviewer 任务级,round_reviewer 按需)。它描述**设计意图**,
  与 `runtime_status`(此刻 pane 实际状态)是两回事,不得混淆。
- `runtime_status` / `pane_id`:只对 `worker_agent` 非 null,取自
  ProjectView `agents[].runtime`(复用现有投影,不读 tmux)。
- `candidates[]`:仅 `ambiguous` 时非空,列出无法择一的候选 agent_id。
- `blocker`:仅 `unbound` / `ambiguous` 时非空,说明缺什么、该配哪里。

### 与既有卡片的分工

- `role_card`(既有):**每个配置 agent** 的 role/role_prompt/assign 入口。
  它是"我配了哪些 agent"。
- `role_topology_card`(新):**北极星六层** 各自绑到了什么。它是
  "我的项目补全了哪几层"。
- 两者的重叠字段(provider/agent_id)都从同一份 ProjectView 派生,
  拓扑卡**不新增状态源**,也不复制 role_prompt(那是 role_card 的职责)。

## 安全边界

- 全路径只读:不写 state、不追加事件、不记 chat turn、不调用任何 provider、
  不读取或写入 tmux、不 spawn/stop/dispatch。
- `controls[]` 只含 inspect 级入口(`agentdeck roles` / `agentdeck workbench`
  / 目标 agent 的 `agentdeck agent terminal --agent <id>`);含占位符的
  一律 disabled 带 blocker。
- 拓扑是**观察面,不是授权**:`binding_status` / `lifecycle` 不改变任何
  gate,不授权 dispatch,不影响 review 组、迭代预算或合并判定。
- 推导歧义一律标 `ambiguous`,绝不静默择一。

## 非目标

- 新增 `[roles]` 显式配置段(若将来需要,是独立切片;本切片先证明推导够用)。
- 让拓扑影响调度、审批或 merge gate。
- 在拓扑里做 provider 探活(readiness 是 `provider_health` 的职责)。
- frontdesk/broker 常驻进程化。

## 测试要点

- 三种 `binding_kind` 各自的字段必然性:`logical_leader` 的 pane 字段必须
  为 null;`command` 层无 provider。
- 三态矩阵:默认项目(无 `[review]`)→ round_reviewer `unbound` 且带
  blocker;配了 `[review].reviewers` → code_reviewer `bound`;两个同为
  实现角色的 agent → coder `ambiguous` 且 `candidates` 列全。
- `split_enabled` 为假时 planner/orchestrator 仍 `bound`(继承 `[leader]`),
  provider/model 等于 `[leader]` 的值。
- 只读:执行前后 `state.json` 与 events 逐字节相同。
- 契约:字段表/example/validator 一致;`contract list` 索引 44。
