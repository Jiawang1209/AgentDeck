# AgentDeck

**一个面向 Codex 与 Claude 受治理协作的 local-first 产品。**

[English](README.md)

AgentDeck 正在围绕一条简单、真实的产品路径重写：

> 运行 `agentdeck`，选择真实 Leader 和权限档位，用自然语言描述开发目标，
> 审阅一份人类可读的 Mission，确认一次，然后通过 ACP 让 Codex 和 Claude
> 完成实现、审查、修改与验收，同时在 tmux 中观看各 Agent 的真实工作流。

## 当前状态

Product Kernel Rewrite 是唯一有效的开发路线。新 Kernel 与旧实现并行开发；
现有 structured CLI 在切换前继续作为 legacy 兼容和调试入口，但它不是新产品
的内部架构。

前台 MVP 将提供：

- Codex CLI、Claude CLI 或 OpenAI-compatible API Leader；
- 显式模型选择和三档权限；
- 自然语言目标与精确 Mission Preview；
- Codex 实现、Claude 审查、Codex 修改、Claude 验收；
- Codex/Claude 自动通信全部通过 ACP；
- tmux 分窗格展示解码后的真实 Agent 事件；
- 每项目一个 SQLite 数据库，以及确定性退出和重新进入；
- 用普通语言呈现诊断，不再暴露含糊的内部失败标签。

退出终端后后台继续、Memory、Skill、自我进化、浏览器 GUI、A2A 和远程客户端
均属于 MVP 之后。

## 架构

```text
Product -> Application -> Kernel
                   \-> Ports <- Adapters

Legacy code -> 仅可进入已批准的 Adapter
```

Domain Kernel 掌握 Mission、权限、调度、handoff、evidence 和 recovery 规则。
ACP 是自动通信层；tmux 只是观察与显式人工接管入口，不是任务通信或完成权威。

## Golden Product Gate

只有真实四 Worker 验收通过后，裸 `agentdeck` 才会切换到新 Product Shell：

1. Codex 实现；
2. Claude 审查；
3. Codex 修改；
4. Claude 验收。

验收项目是对 IAE 首页冻结副本的本地复刻，必须证明真实 ACP lineage、tmux
可见性、浏览器与视觉证据、SQLite 恢复、安全退出/重入和人工产品验收。

## 开发

```bash
conda activate agentdeck
python -m pip install -e .
pytest -q
```

所有开发命令必须在 `agentdeck` conda 环境执行。Rewrite Design 与独立 TDD
计划分别通过人工审阅前，不得开始产品实现。

## 当前权威文档

- [Product Kernel Rewrite Design](docs/superpowers/specs/2026-07-18-agentdeck-product-kernel-rewrite-design.md)
- [产品北极星](docs/roadmap/product-north-star.md)
- [当前开发状态](docs/handoff/current-development-state.md)
- [终极目标路线](docs/roadmap/ultimate-goal-roadmap.md)
- [Legacy capability inventory](docs/migrations/2026-07-17-legacy-capability-inventory.md)

旧设计与旧计划已从当前工作树删除，避免它们意外成为实施权威；完整内容仍可从
Git 历史恢复。现有 contract 文档默认只描述 legacy 兼容接口，除非 Rewrite
Design 明确批准采用。
