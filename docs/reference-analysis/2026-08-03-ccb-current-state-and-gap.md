# CCB 现状复研与 AgentDeck 差距总账(2026-08-03)

Status: 本文取代 `claude-codex-bridge-main.md` 作为**当前**对照基准。
后者是较早的源码快照深读,其"源码结构地图/通信机制"细节仍有参考价值,
但**结论部分已过时**,不要再据它做方向判断。

证据来源与可信度(必须区分,否则会重犯本文修正的那个错):

| 来源 | 覆盖 | 可信度 |
| --- | --- | --- |
| CCB README(2026-08-03 抓取) | 定位、命令、provider 清单、移动端 | 高(官方原文) |
| `docs/agentic-loop-workflow-architecture.zh.md` | **loop 架构、人类门、verdict 聚合** | 高(设计文档原文) |
| `docs/ask-native-async-job-architecture.md` | `/ask` 作业生命周期 | 高 |
| `docs/` 文件清单 | 该项目documented 的领域面 | 高 |
| `claude-codex-bridge-main.md`(旧) | 源码级内部机制 | **可能过时** |

未读源码。凡涉及 CCB **实现**(而非设计)的断言,下文一律标注。

## 0. 先修正一条我此前的错误判断

上一轮我基于 README 层面的阅读说 CCB "agent 间转交是项目内信任式的、
没有审批门"。**这是错的。**

`agentic-loop-workflow-architecture.zh.md` 明确写有**两道显式人类门**:

1. **Frontdesk**(入口):做 user intake 分类、跨用户边界转达澄清、
   把不可恢复问题升级,并"对项目工作形成完整 Intake/Blocked Evidence…**并停止**"
   ——文档强调这是 human presence,不是自动化。
2. **Round Reviewer**(集成):做 root verification 与 round 级 gate,
   只有过了这道门,child outcome 才聚合回 Planner。

错误根因值得记:**README 描述产品,架构文档描述治理**。只读 README 会
系统性低估一个项目的严谨程度。这与本仓库反复在修的"显示了不成立的事实"
是同一类错误,只不过这次是**我**在做那件事。

## 1. 一句话定位对比

| | 自我定位(原文) |
| --- | --- |
| CCB | "A lightweight multi-agent TUI with a stable **cross-provider collaboration layer**" |
| AgentDeck | local-first、protocol-native 的**可治理**多智能体工作台 |

CCB 把重心放在**连接与协作**,AgentDeck 放在**可审计的执行治理**。

## 2. 惊人的收敛:两边的北极星几乎是同一张图

这是本次复研最重要的发现。CCB 的 loop 架构第一原则是:

> **角色产出语义,程序验证并提交权威**

AgentDeck 北极星第二条是:

> 模型负责语义,程序负责循环

**几乎逐字相同。**角色分层也高度重合:

| 角色 | CCB | AgentDeck |
| --- | --- | --- |
| Frontdesk | ✅ 入口人类门 | ✅ G1(只读分类,不调 provider) |
| Planner | ✅ | ✅ G2 `[leader.planner]` |
| Orchestrator | ✅ | ✅ G2 `[leader.orchestrator]` |
| Controller | ✅ 校验 schema/revision/capacity/digest 后提交 | ⚠️ 无独立角色,职责散在 approval + run-loop |
| Worker | ✅ 隔离 worktree | ✅ G4 任务级 worktree |
| Reviewer | ✅ Worker 只能 chain 指定 Reviewer | ✅ G5 + review 组 |
| **Round Reviewer** | ✅ 集成人类门 | ✅ `[review].round_reviewer` |

连 verdict 纪律都同构:CCB 的 closure 聚合明令"**不能把 mixed outcome
降格为 pass**",AgentDeck 是 `any_fail_blocks` + 组未齐则扣住自动合并。

**结论:这不是两条路线之争,是同一条路线上的两个实现。**

## 3. 真实差异

### 3.1 CCB 的 loop 更宏大,但**按其自述尚未 production-ready**

该架构文档收尾原文:**"still under active acceptance testing and not
production-ready"**。

它设计了 AgentDeck 尚无的东西:

- **DAG 节点**而非线性 plan(`A,B → C` 是 README 一级卖点);
- **Controller 层**:派发前校验 schema / revision / capacity / digest,
  "exactly one root Worker job per node";
- **chain-of-custody**:worktree digest → controller commit → Git DAG →
  root verification → round import,并明令"绝不把未验证或范围漂移的 tree
  提升为权威完成";
- **revision fencing** + exact-once 派发,unknown submission "停住而非
  重发或判成功"(与 AgentDeck 的 fail-closed 同源哲学);
- Detailer 分级(`local_detail_ready` / `planner_replan_required` /
  `needs_clarification` / `blocked`)。

### 3.2 AgentDeck 的 loop 更窄,但**已 live 跑通**

Round 13 证据:needs_changes verdict → 自动追加返工轮 → 越 gate 续跑 →
自动批准派发 → 复审 pass → 自动合并,**零人工**,证据在
`docs/validation/`。这是 AgentDeck 目前**唯一无法被"设计更完整"抵消**的
资产:它是被真实 provider、真实 tmux、真实 worktree 验过的。

AgentDeck 独有(据现有证据,CCB 文档未见对应物):

- **有界预算是硬门**:`--max-waves`(强制、无 unbounded 形态)、
  `[autonomous] max_approvals`、`max_review_rounds`。CCB README 与架构文档
  **均未提及 budget / spend cap**;它防失控靠**熔断与退避**
  (30s/60s/120s/5m/10m/30m,六次后开路)——那是**可靠性**手段,
  不是**授权**手段。两者的失效模型不同:CCB 防"抖动打转",
  AgentDeck 防"越权行动"。
- **scoped 授权委托**:预先授权一类授权框、按 agent 隔离、逐次审计、
  fail-closed 匹配,以及 `gate-preview` 的前缀宽度梯子。
  CCB 的模型是"每个 agent 都是完整原生终端,人可直接接管"——框由人按。
- **human_gate 诚实停止**:走开段区分"在等一个会来的回复"与
  "卡在一道永不自解的框上"。
- **机器可发现的 payload 契约**:45 个,每个有字段元组 + validator +
  example + 发现命令,**校验不过就不打印**。CCB 也有 contract 文档
  (`agent-message-timeout-retry-contract.md`、`ccbd-diagnostics-contract.md`
  等),但据文件名与 README 判断是**内部设计契约**,不是给 GUI 消费的
  运行时 payload 契约——**此点未读源码,标为待证**。

### 3.3 CCB 遥遥领先且**不必追**的部分

- **provider 广度**:17 个 CLI 家族(Codex/Claude/Gemini/Grok/Kimi/MiMo/
  Qwen/Cursor/Copilot/Crush/Kiro/Pi/Z.ai/OpenCode/Antigravity/Droid/…)
  vs AgentDeck 实际 4 个。旧研究第 8 节早就点名 provider-specific
  completion 是"最难维护"的一块。
- **移动端**:Flutter Android app + gateway + 语音 + 文件传输 +
  配对二维码 + Tailscale/中继。
- **Rich mode(WezTerm)**:文件树、文档编辑、媒体预览。
- **工程规模**:1611 commits、3.4k star、跨平台安装器、Rust 加速器。

在这些上追一个领先 1600 commit 的项目是硬碰,且不产生差异化。

## 4. 该学 / 是坑 / 不必追

### 应当学(有具体动作)

1. **DAG 节点执行**。这是 AgentDeck 最硬的能力缺口,而且是**北极星自己
   要求的**(分层图里 coder 与 code_reviewer 是分叉节点),不是为对标。
   现有 review 组"串行叠加"是当初为回避并行派发复杂度的**明写妥协**,
   可以重审。落法建议:step 增加显式 `depends_on`,把 step 顺序守卫从
   "线性位置"换成"依赖已满足"——严格更一般,且保留原本要守的性质
   (绝不派发输入未就绪的工作),其余门一条不动。
2. **Controller 层的"派发前校验"**。CCB 在提交 job 前校验 schema /
   revision / capacity / digest。AgentDeck 刚做完的晚校验审计正是同一个
   问题的另一面——我们是"校验发生在副作用之后",他们是"把校验前置成
   独立层"。可借鉴为一个显式的 dispatch precondition 层。
3. **digest 绑定终态**。CCB 以"worktree digest 与绑定 digest 相同"作为
   终态判据,并做 post-review mutation 检测。AgentDeck 目前靠 reply +
   verdict,**无法发现"审查之后又被改动"**——这是一个真实的信任缺口。

### 是坑(旧研究已点名,复研后仍成立)

- provider-specific completion 检测的长期维护成本;
- 多控制面(mobile/远程)扩大安全面;
- JSONL append-only 的膨胀(AgentDeck 已用 SQLite 影子迁移在处理);
- 模块层级过多导致的一致性 bug。

### 不必追

- provider 广度、移动端、Rich mode、Rust 加速器、工程规模。

## 5. 对方向的建议

AgentDeck 与 CCB **不是路线之争**,而是同一北极星下"设计完整度"与
"已验证深度"的分工不同。因此:

- **不要**以"我们有治理、他们没有"作为差异化叙事——**那不成立**。
- **应当**以"**窄而已验证的治理闭环 + 机器可发现的契约面**"作为立足点,
  并优先补 **DAG 执行**这一条北极星自己要求、且当前最限制"可行且好用"
  的缺口。
- provider 广度作为最低优先级,只在具体使用需要时逐个接。

## 6. 待证条目(未读源码,不得当作结论)

1. CCB 的 contract 文档是否包含 GUI 可消费的运行时 payload 契约与
   validator(本文按"内部设计契约"处理)。
2. CCB 是否存在等价于 `--max-waves` 的硬预算(README 与架构文档均未见)。
3. 仓库中 `mcp/` 目录的实际状态(GitHub 目录树可见,但 README 未提 MCP)。
4. `agentic-loop-workflow` 的实现完成度(文档自述 not production-ready,
   但未核实代码覆盖到哪一步)。
