# AgentDeck

**一个 local-first、协议原生、可治理的多智能体工作台。**

AgentDeck 把一句自然语言目标变成一个可观看、可审计、可恢复的 Mission，再交给真实的 Codex、Claude 和其他 Agent 协作完成。

我们的产品方向很明确：

> 拥有 Hermes 般自然的交互体验、ACP 原生的通信能力、CCB 式真实多 Agent 协作，以及 AgentDeck 更强的编排与治理内核。

AgentDeck 不是多模型聊天壳，也不是终端模拟器。它是一个控制平面：理解目标、选择 Agent、冻结执行范围、请求一次有意义的确认、组织交接，在出现新风险时暂停，并保存完整工作历史。

## 目标体验

未来的主入口会非常简单：

```bash
agentdeck
```

在项目目录中，它会打开或恢复该项目的持续自然语言会话：

```text
You       › 让 Codex 实现这个功能，Claude 负责审查。
AgentDeck › 我准备了一个 6 步 Mission。确认后会在后台继续，新增权限时暂停。
You       › 批准。
AgentDeck › 已开始。你可以离开，完成或阻塞时我会记录并汇报。
```

在普通目录中，它会进入全局 Frontdesk，用于查找、创建和切换项目。实时多 Agent 终端工作区仍然存在，但只在需要观看或接管时打开，不再成为默认入口。

## 为什么是 AgentDeck

- **自然语言优先**：描述目标、询问状态，不必背诵编排命令。
- **真实 Agent 团队**：Codex、Claude、Gemini、OpenCode 等可以拥有独立角色和会话。
- **一次范围确认**：确认冻结 Mission 后，普通步骤自动衔接，不要求用户一直守在电脑前。
- **执行可治理**：新增权限、破坏性操作、计划漂移和风险升级会自动暂停。
- **结构化通信**：ACP 将成为首选 Agent transport；tmux 保留为可视 fallback。
- **后台可恢复**：项目 daemon 在客户端关闭后继续已确认任务，并从持久状态恢复。
- **学习可审计**：Skill 和 Memory 建议带有来源、预览、hash 和显式 apply/load 门槛。
- **一份事实，多种界面**：CLI、TUI、Desktop 和未来 IDE 客户端消费同一份 ProjectView 与事件契约。

## 当前进度

AgentDeck 已经具备本地控制平面的主要底座：

- provider-agnostic Leader planning；
- tmux 中的 Codex、Claude CLI Worker；
- role、dispatch、inbox、reply、ack 与 trace；
- approval-gated execution；
- ProjectView 与 GUI-ready contracts；
- Skill Registry 与受治理的 Memory suggestion；
- compact handoff 驱动的固定顺序工作流；
- 自然语言 Mission preview、一次整体确认、readiness、执行、状态和恢复。

下一代产品会在保留这些能力和审计历史的前提下，用协议原生 Runtime Kernel 替换脆弱的终端屏幕通信。

当前 Phase 1 已交付只读协议运行时模型与状态面；tmux 仍是默认执行 backend，ACP transport 尚未实现。

## 体验当前 CLI

AgentDeck 当前使用 Python 3.12 和 `agentdeck` conda 环境：

```bash
conda env create -f environment.yml
conda activate agentdeck
python -m pip install -e .
```

初始化项目并检查环境：

```bash
agentdeck project init
agentdeck doctor
agentdeck status
```

从自然语言开始：

```bash
agentdeck leader chat --message "让 Codex 和 Claude 一人一句接龙百家姓，共8轮"
```

AgentDeck 会先返回冻结的 Mission preview 和明确的确认入口，不会静默执行。

常用只读入口：

```bash
agentdeck workbench
agentdeck mission status --mission-id mis_xxx
agentdeck trace --id msg_xxx
agentdeck events --limit 20
agentdeck controls
```

`leader chat` 目前承担自然语言入口；未来默认 `agentdeck` 交互会话落地后，它仍会作为脚本化和调试接口保留。

## 产品架构

```text
Human / CLI / TUI / future Desktop or IDE
                    |
           AgentDeck Frontdesk
                    |
      Mission / Planner / Orchestrator
                    |
 Approval / Skill / Memory / Ledger / Recovery
                    |
       Protocol-Native Runtime Kernel
       /             |              \
  ACP native     ACP adapter      tmux fallback
       \             |              /
      Codex / Claude / Gemini / OpenCode / others
```

ACP 标准化 Agent 通信，但不会替代 AgentDeck 的 Mission、policy、scheduler、audit 和 recovery。

## 安全模型

- 计划在执行前必须可预览。
- 一次确认只授权冻结的 Mission 范围。
- 新权限和新风险会暂停执行。
- ACP permission request 必须经过 AgentDeck approval。
- Skill、Memory、prompt 和 role definition 都不是权限授权。
- 外源 Skill 与长期 Memory 不会被静默安装或写入。
- runtime 和通信事件可以追溯到 project、Mission、Agent、session 与 turn。

## 路线图与文档

- [产品北极星](docs/roadmap/product-north-star.md)
- [协议原生 V2 设计](docs/superpowers/specs/2026-07-11-agentdeck-protocol-native-v2-design.md)
- [自然语言 Mission 基线验收](docs/validation/2026-07-11-natural-language-mission-acceptance.md)
- [Protocol Runtime Contract](docs/contracts/protocol-runtime-schema.md)
- [当前开发状态](docs/handoff/current-development-state.md)
- [Contract 索引](docs/contracts/contract-index-schema.md)
- [架构文档](docs/architecture/)

详细命令、schema 字段、开发约束、参考项目分析和验证报告都保留在 `docs/` 与仓库的 Agent 指令文件中，不再占据产品 README。

## 开发

在项目环境中运行验证：

```bash
conda run --no-capture-output -n agentdeck pytest -q
conda run --no-capture-output -n agentdeck python -m compileall src tests -q
```

AgentDeck 仍在积极开发中。当前版本是可工作的控制平面底座；协议原生 daemon 和默认 `agentdeck` 持续会话是下一条产品主线，不会在完成前被写成已经交付的功能。
