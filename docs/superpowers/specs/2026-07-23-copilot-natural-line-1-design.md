# Co-pilot 结对模式 · 最小真实线(Line 1)设计

- 日期:2026-07-23
- 分支:`copilot-line-1`(基于 `p1-durable-mission-kernel`)
- 状态:设计已由 human 批准,待写实现计划

## 背景:为什么有这份设计

AgentDeck 的北极星是**顺着 agent 的天性用** —— 用真实 LLM/agent 当 Leader
调度多个真实 coding agent(Worker),在可见终端里分工协作,并保持可审计、
可审批、可恢复。

`codex/product-kernel-rewrite` 这条重写线在 leader-propose 环节做了一套**刚性
协议**(自定义 ACP artifact `agentdeck://leader/mission-proposal`、禁止 agent
用工具、逐字冻结 Leader 身份四元组),在真实 live 验证时崩了:真实 coding agent
(codex/claude)天性是**动手执行(发起 tool call)**,不是**返回结构化提案**;
codex 还在 embedded-resource 输入上直接报 `Internal error`,claude 则一直等
那个永不出现的 artifact 而超时。

复盘结论(human 已认同):**问题不在"用 agent 当 Leader"本身,而在重写把
最不确定的假设(真实 agent 能否走这套协议)推迟到最后才验证,且用契约把
agent 天性拧反了。** 规划层面的教训是**顺序错了**:应当先用真实 agent 把
最 risky 的一根线跑通,再往外加结构 —— 而不是先造齐一整套内核。

## 核心洞察

1. **想要的循环已经存在。** Desktop 原线(`p1-durable-mission-kernel`)的 CLI
   已实现完整的自然结对循环:`leader chat/plan` · `approval` · `dispatch` ·
   `capture-reply` · `inbox` · `trace` · tmux 可见 · ProjectView ·
   `ask/approve/autonomous` 三档 approval_mode。刚性 leader-propose 是 rewrite
   独有的,原线从来没有。
2. **三种角色 / 四种确认粒度 / 两种 Leader,不是三个产品,是同一根主线 + 几个
   旋钮**:
   - 自主度旋钮 = `approval_mode`(ask / approve / autonomous)
   - 确认粒度旋钮 = 每个 plan step 的 `requires_approval` + 风险标记
   - Leader 插槽 = leader provider(API / coding-CLI / fake)
3. **纪律**:"都有"是终态,也很便宜;但**第一根线只锁死一档、用真实 agent
   跑通**,其余全是随后拧旋钮,不是造新子系统。

## 目标(Line 1 范围)

在一个**真实的自包含小需求**上,用**真实 API Leader** + **两个真实
coding-agent Worker(可见 tmux 终端)**,亲手驱动**一整轮结对**并端到端跑通,
每步人工确认,全程在账本/trace 上、在 tmux 里看得见。

### 锁定档位(第一版固定,不给选项)

| 维度 | Line 1 锁定值 | 未来旋钮 |
| --- | --- | --- |
| 模式 | 结对 / 逐步确认 | 自主度旋钮(ask/approve/autonomous) |
| Leader | 真实 API 大模型(DeepSeek/Claude API) | provider 插槽(coding-CLI adapter 随后) |
| Worker | 2 个真实 coding agent(coder + reviewer),可见 tmux | 数量/角色可配 |
| 确认粒度 | 每次派活前确认一次 | 每 step `requires_approval` + 风险标记 |
| 实现路线 | A:在 Desktop 原线上"点活" | —— |

## 一整轮循环(数据流)

1. 你对 Leader 说要做什么(自然语言)。
2. Leader(真实 API)**用文本**拆出计划:step1 → coder,step2 → reviewer。
   AgentDeck 解析文本计划(不逼 agent 吐自定义 artifact)。
3. AgentDeck 把计划变成待确认项(approval)。你看第一步,点头 / 改。
4. 确认后 → `dispatch` 给 coder 的 tmux 终端;coder **自然地**读写文件、跑命令。
5. coder 干完 → `capture-reply` 回收产出入账。**Leader 汇总前必须重读它真写的
   文件**(项目铁律)。
6. Leader 提议第二步(reviewer 检查)→ 你确认 → `dispatch` reviewer。
7. reviewer 回复入账 → Leader 汇总 → 一轮结束。

全程:tmux 可见、消息账本 / `trace` 可审计、恢复(recovery)可用。

## 直接复用(Desktop 已有,不重造)

`leader chat/plan` · `approval` 队列与审批 · `dispatch` · `capture-reply` ·
`inbox` · `trace` · tmux 可见 runtime · ProjectView 摘要 · `approval_mode`
旋钮(本版锁在 `approve` / 逐步确认)。

## 真正要做 / 要验证的缺口

1. **接真实 API Leader**:验证 `leader plan --provider deepseek`(或
   `openai-compatible`)对着**当前配置的真实 worker** 拆出一个真正的、
   schema 合法的 2 步文本计划(step 1..n 连续、只用已配置 worker `agent_id`、
   role 与 worker 配置一致)。
2. **证明真实 worker 能跑**:一个真实 coding agent 被 `dispatch` 进 tmux,
   **自然执行**任务并产出可被 `capture-reply` 回收的结构化结果。
3. **证明整链在真实 agent 上活着**:approve → dispatch → capture → trace,
   逐步确认,端到端一整轮。
4. 上面三步撞现实时冒出来的**小胶水 / 修复**(不预造,遇到再补,每片 TDD)。

## 明确不做(YAGNI,留给后面拧旋钮)

- 另外 2 种模式(甲方/包工头、我自己当 Leader 手动派活)
- 另外 3 种确认粒度(整计划一次、每步、危险动作额外卡)
- coding-agent-as-Leader adapter(第二个 provider 插槽)
- rewrite 的 SQLite 迁移(除非 JSON state 真的疼,再前向移植)
- skills 自动化、GUI、remote / daemon、A2A

## 成功标准(达到即 Line 1 活了)

> 在一个真实自包含小需求上,用真实 API Leader + 2 个真实 coding-agent worker
> (可见终端),你亲手驱动一整轮结对(说 → 确认 → coder 执行 → 捕获 →
> 确认 → reviewer 执行 → 捕获 → 汇总),**每步人工确认**,全程在账本/trace
> 上、在 tmux 里看得见。**没有 fake leader,没有刚性协议。**

## 落地约定与安全边界

- 在新分支 `copilot-line-1`(基于 `p1-durable-mission-kernel`)上开发;**绝不
  push**,由 human 自己推。
- 每次开发同步更新 `HISTORY.md`,与代码/文档改动放进同一次 commit;commit
  **不带** `Co-Authored-By` trailer。
- 真实部分(真实 API key 调用、真实 coding-agent 派进 tmux、真实 live 一整轮)
  都是**人工授权门**:每次真跑前停下、报清确切参数、等 human 明确授权,绝不
  自行推断授权。
- 不绕过审批执行危险操作;Worker 写过文件后,Leader 汇总前必须重读相关文件。

## 后续路线(Line 1 之后)

Line 1 绿灯后,泛化就是**逐个暴露旋钮**:
1. 打开确认粒度旋钮(整计划一次 / 每步 / 危险动作额外卡)。
2. 打开自主度旋钮(approve → autonomous 有界自主 / ask 严格)。
3. 接第二个 Leader provider 插槽(coding-agent-as-Leader,文本解析)。
4. 按需前向移植 rewrite 的 SQLite / 迁移等确定性零件。

每一步都是**先在真实 agent 上验证一档,再拧下一个旋钮** —— 不再一次造齐。
