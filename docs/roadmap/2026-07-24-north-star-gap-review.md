# 北极星差距盘点（2026-07-24，main @ Line 1 阶段合并后）

四路只读审计（G1–G6 分层角色 / Mission-daemon 主线 / Skill-Memory-Learning / 契约面与自主度）
对照 `product-north-star.md`（P0–P5）、`ultimate-goal-roadmap.md`（G1–G6）与 2026-07-23
Line 1 转向设计的综合结论。证据均为审计时 file:line 引用，此处只存结论。

## 总体判断

控制平面（底座）基本建成，产品承诺的端到端体验约走了 60%。G1/G3/G6 达验收，
G2/G4/G5 部分完成；Mission/daemon 骨架完整但完整四阶段 live 仍 BLOCKED；Line 1
用自然循环路线在真实 agent 上跑通完整一轮（round 2 PASS + 加固 A–F）；
GUI/远程/SQLite 等支柱确认缺失。

## 已达成

- 对话与 Mission 主线：裸 `agentdeck` 项目内会话、preview → 精确一次确认 → 冻结
  （三方 hash）→ daemon 续跑、九点崩溃矩阵、确定性重连；live 到"两步 transport PASS"。
- ACP client 完整且 fail-closed；真实 adapter 单 agent 验收 PASS；tmux 无静默
  fallback；Worker 身份 = session_id（pane 仅观察绑定）。
- G1 Frontdesk、G3 run-loop（含 `--all` 并行调度器）、G6 role_topology_card 达验收。
- Skill/Memory/Learning 层与 Phase F 几乎完全对齐（15 命令、allowlist、依赖
  pin/semver/lockfile、provenance、memory confirm 门、learn review 只读）。
- 契约面：38 contract、`workbench --watch` JSONL、events cursor、dashboard.py/tui.py
  纯 payload 参考客户端。
- 自主度梯度：ask/approve/autonomous（allowlist+预算）+ approval auto + run-loop +
  workflow，全部 `--confirm` 人工门。
- Line 1 实战：真实 DeepSeek Leader + 3 真实 coding agent 完整一轮自然循环 PASS。

## 核心差距（按严重度）

1. **产品成功测试未通过——两条执行叙事未收拢。** daemon 冻结路径的完整四阶段
   live 反复 BLOCKED；Line 1 自然循环路径打通了真实 agent 但每步要人在场。
   北极星要的"确认一次就能走开"落在两者中间空档。Line 1 后续旋钮（确认粒度、
   自主度）正是往这个空档走的路。
2. **G2**：planner/orchestrator 仅标签级拆分，共用同一 provider/model，无独立
   brief 数据模型。
3. **G4（最重）**：`workspace_mode=worktree` 仅 config 声明，无任务级 worktree
   隔离；生命周期无 released 阶段，任务完成不回收 pane/context，不追踪 skill
   snapshot/测试结果。
4. **G5**：review gate 二元化，无量化验收分数，未对 planner acceptance_criteria
   校验；reviewer/coder 上下文隔离无机制（Line 1 live 实证 reviewer 越权修 bug）。
5. **缺失支柱**（确认无雏形）：项目外 global Frontdesk；SQLite 状态权威（两份现成
   实现在 donor 分支：`codex/p1-durable-mission-kernel` +8863 行、
   `codex/product-kernel-rewrite` sqlite store）；Web 传输层/浏览器工作台；
   workbench 缺任务图、dispatch/report 时间线、通知卡；AgentDeck-as-ACP-agent；
   A2A/remote/roaming/通知/transcript 恢复/自动 install（多为明示后续/非目标）。
6. **软肋**：skill lockfile 仅 advisory 未 enforce；skills/memory/learn 缺专项
   测试文件；north-star/handoff 仍停在 7-17 P0–P5 叙事，与 7-23 Line 1 转向未
   reconcile。

## 建议优先顺序

1. 修文档错位（north-star/handoff 与 Line 1 路线 reconcile）。
2. 拧 Line 1 下两个旋钮：确认粒度（整计划一次）→ 自主度（有界走开）。
3. G4 任务级 worktree + release（兼治 reviewer 越权）。
4. SQLite 前向移植（材料在 donor 分支，等 JSON 真疼再动）。
5. GUI/web 层最后（契约面已就绪）。
