# G6 role_bindings live 验收(2026-08-01)

Status: PASS(并由 live 暴露出一处低报,已当场修复并复验)
Scratch: `~/Desktop/agentdeck-live-scratch`
Commits: `fdcb5e18` → `9be5dfef`,全量 5083 passed / 3 skipped

## 为什么在这个项目验

空项目验不出什么——六层要么全默认要么全空。scratch 是**真配过的**项目,
而且恰好覆盖了几个最容易出错的组合:

```toml
[leader]              provider = "deepseek"
[leader.planner]      provider = "deepseek"    model = "deepseek-v4-pro"
[leader.orchestrator] provider = "claude-cli"  model = "claude-opus-5"   # 与 [leader] 不同后端
[review]              reviewers = ["reviewer", "planner"]                # 两人串行复审组
                      # round_reviewer 未配
[[agents]]            planner / coder / reviewer,coder 与 reviewer 均 running
```

## 结果:逐行对着 config 核

```
mode = role_bindings | layers = 6 | bound = 5 unbound = 1 ambiguous = 0 | split = True

frontdesk       intake         command         bound     agent=None      prov=None
planner         orchestration  logical_leader  bound     agent=None      prov=deepseek    model=deepseek-v4-pro
orchestrator    orchestration  logical_leader  bound     agent=None      prov=claude-cli  model=claude-opus-5
coder           work           worker_agent    bound     agent=coder      running  pane=%5
code_reviewer   work           worker_agent    bound     agent=reviewer   running  pane=%6
                                                          group_members=['reviewer','planner']
round_reviewer  acceptance     worker_agent    unbound   agent=None
                BLOCKER: set [review] round_reviewer to enable a dedicated acceptance reviewer
```

| 断言 | 依据 | |
| --- | --- | --- |
| `split_enabled = True` | 两个子段都配了 | ✓ |
| planner → `deepseek` / `deepseek-v4-pro` | `[leader.planner]` | ✓ |
| orchestrator → `claude-cli` / `claude-opus-5` | `[leader.orchestrator]` | ✓ |
| coder → `coder`,running,pane `%5` | tmux 真实状态 | ✓ |
| code_reviewer → `reviewer`(组首位) | `[review].reviewers[0]` | ✓ |
| round_reviewer `unbound` + 可执行 blocker | 确实未配 | ✓ |
| `logical_leader` 两层 runtime/pane 全 null | 必然性条款 | ✓ |
| `command` 层 provider/model/backend/transport 全 null | 必然性条款 | ✓ |
| 三个计数相加 = `layer_count` | 5+1+0 = 6 | ✓ |
| state.json + events.jsonl 逐字节不变 | 只读边界 | ✓ |

**orchestrator 那一行是重点**:它是与 `[leader]` **不同后端**的场景,正是
同期修掉的既有 bug(旧 `role_topology_card` 硬编码取 `config.leader.provider`,
自 G2 起一直报错)所在。新卡在真项目上报对了。

## live 暴露的低报(当场修复)

首次运行时 `code_reviewer` 只显示 `agent=reviewer`——而这个项目实际跑的是
**两人串行复审组**,第二审查员 `planner` 在整张拓扑图里是隐形的。GUI 照
这张卡画会画出一个单人审查节点。

这不是错误(绑定组首位是有意设计,与 `review_group` 同源;`candidates`
专表 fail-closed 歧义,不能拿来装组员),但**一张名叫拓扑的卡低报了实际
拓扑**——而这只有在真配过组的项目上才看得见,空项目验不出来。

修法是追加只读 `group_members`(`9be5dfef`),与 `candidates` 严格分工:

- `candidates` = **歧义**,仅 `binding_status == "ambiguous"` 时非空;
- `group_members` = **成员**,仅 `code_reviewer` 且配了 `[review].reviewers`
  时非空,顺序即串行派发顺序,**首位必须等于 `agent_id`**(该条款是
  validator 里的回归钉,保证"绑定组首位"与"成员列表"永不漂移)。

两个方向都钉住了:健康的组被画成 candidates(会让人误以为配错了)与真实
歧义被画成 group_members(会把 AgentDeck 明确拒绝绑定的东西说成一个组),
各有一个测试。复验:

```
code_reviewer  bound  agent=reviewer  candidates=[]  group_members=['reviewer','planner']
```

## 结论

G6 role_bindings 在真配置项目上全部断言成立,只读边界现场核过。
live 验收的价值在这一轮很具体:**它抓到的那处低报,单元测试和空项目
都验不出来——因为缺陷只在"配了多成员组"这个真实形态下才显形。**
