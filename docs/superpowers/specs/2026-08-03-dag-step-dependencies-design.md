# DAG 执行第一刀:依赖满足取代线性位置

Status: frozen(user 拍板 2026-08-03,方向来自 CCB 复研的"该学"第一条)
Baseline: run-loop 单 wave 引擎的 step 顺序守卫、review 组(串行叠加)、
G4 任务级 worktree、review 迭代闭环——**均不重写**。

## 问题:plan 是严格线性的,而北极星那张图不是

今天的守卫(`cli.py`,单 wave 引擎内)是:

```python
earliest_incomplete = min(step for 未完成的 approval)
for approval in approved:
    if approval.step != earliest_incomplete:
        hold("awaiting earlier step completion")
```

即**只派发编号最早的那个未完成 step**。后果:

```
Round 14 的 plan:  coder → reviewer → planner
墙钟             =  t1 + t2 + t3
```

而 reviewer 和 planner 审的是**同一份已完成的实现**,彼此毫无依赖——
它们本可以同时跑。北极星的分层图里 coder 与 code_reviewer 本就是分叉的
工作层节点;CCB 的 `A,B → C` 是同一个形状。

review 组做成"串行叠加"是当初**明写的妥协**(见
`2026-08-01-review-group-round-reviewer-design.md`),理由是回避并行派发的
复杂度。本切片重审那个妥协。

## 设计:把守卫从"线性位置"换成"依赖已满足"

守卫要守的**真正性质**从来不是"按编号排队",而是:

> **绝不派发输入尚未就绪的工作。**

"编号最早"只是这个性质在纯线性 plan 上的一个**充分但过强**的实现。换成
依赖满足是**严格更一般**的表述,而性质本身一字不变。

```python
ready = {step for step in approved if 该 step 的所有依赖都已完成}
```

"完成"沿用既有定义(`_step_is_incomplete` 一字不改):`rejected`,或
`dispatched` 且其 message 已有 reply。

## 依赖从哪来:程序推导,不让 Leader 生成

**这是本切片最重要的一条边界。**让 Leader 产出依赖图会:

1. 把"程序负责循环"这条北极星原则让出去一半;
2. 改动 provider plan schema,而那是四个 provider 共用、已 live 验证的面。

因此 `depends_on` 由**纯函数**从现有 step 标记确定性推导:

| 情形 | 依赖 |
| --- | --- |
| 普通 step N | `[N-1]`(N=1 时为 `[]`)——**与今天逐字节等价** |
| review 组成员 | 该组**首个成员之前**的那个 step(全组同一依赖 → 并行扇出) |

无 `[review].reviewers` 配置的项目里不存在组标记,因此**所有路径逐字节
不变**。这条必须由差分测试钉住。

推导放在纯模块 `src/agentdeck/step_dag.py`(零 IO、不 import cli/state),
**不新增持久化字段、无迁移**;结果只读投影进 `plan status`,让人看得见
哪几步可以并行。

## 这确实是一次放松,以及为什么它安全

对 review 组成员而言,新守卫比旧守卫**更宽**:它们从"排队"变成"同发"。
诚实地讲清为什么这仍然安全:

- 组成员审的是**同一份已经完成**的实现 step,它们的输入在派发前就已就绪;
- 组成员之间**没有**依赖——配置层已强制 `reviewers` 互不重复;
- verdict 的"**组完成才判定**"规则**一字不动**:仍要求全体成员回复后
  才聚合,所以并行不会让"先 fail 的成员开一轮、后审的再开一轮"双烧预算
  (那正是当初串行化想避免的事,而它其实由组完成规则守着,不是由串行守着);
- 每个 review step 各自从实现分支起 worktree,git 原生支持同 base 多
  worktree。

## 同 agent 冲突:一个 pane 绝不同时收两个任务

并行引入的**唯一新风险**。规则:

- 一个 agent 若已有**已派发未回复**的 message,本 wave 不再派给它;
- 命中者保持 `approved` 并记入 `skipped[]`,reason 明确区分于
  "awaiting earlier step completion";
- busy 集合在本 wave 内**随派发增长**(与 `run-loop --all` 的
  `_busy_agents` 同一规则,复用而非另写)。

## 安全边界(逐条不变)

- 审批门:每个 step 仍需各自的 approval,并行不合并审批。
- 白名单 + `max_approvals` + `max_review_rounds` 预算:一字不动。
- 绝不 force-spawn;目标 agent 无 running pane 仍记 `blocked`。
- 只认文件通道回复;绝不读 pane 推断完成。
- human_gate:awaiting 集现在可能有多个 agent,`human_gate_candidate` 取
  首个命中项(按 `config.agents` 声明序,该依赖已有注释钉住)——行为不变。
- `worktree merge-plan` 仍按 step 编号顺序合并:并行 step 编号仍唯一,
  顺序仍确定。

## 非目标

- **让 Leader 产出 DAG**(下一刀;本刀先证明引擎层可行)。
- 跨 plan 并行(`run-loop --all` 已是轮转,不在本刀)。
- 任意 DAG 形状:本刀只落地"线性 + review 组扇出"这一族确定性拓扑。
- Controller 式派发前校验、digest 绑定终态(CCB 复研的另两条,各自独立)。

## 测试要点

- **零行为变化差分**:无 `[review]` 配置的项目,单 wave payload 与今天
  逐字节相同(这是本刀最重要的一条钉)。
- 组扇出:`reviewers = ["reviewer","planner"]` 时,实现 step 完成后
  **同一 wave 内**两个 review step 一起派发。
- 依赖未满足:实现 step 未回复时,两个 review step 都被持留。
- 同 agent 冲突:构造两个就绪 step 指向同一 agent → 只派一个,另一个进
  `skipped[]` 且 reason 与顺序持留可区分。
- 组完成才判定:并行组只有一人回复时,verdict 不判定、迭代不触发。
- 纯函数矩阵:线性、组扇出、组在首位、多组、空 steps。
