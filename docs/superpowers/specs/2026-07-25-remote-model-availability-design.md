# 远端模型可用性探测评估（round 4 发现②，待 user 拍板）

- 日期：2026-07-25（round4-findings loop 切片 5 产物）
- 状态：**评估文档，不改代码**；方案 b 的实现需 user 显式授权后另立切片
- 现象：DeepSeek 在 round 2→4 之间下线 `deepseek-chat`（现仅
  `deepseek-v4-pro` / `deepseek-v4-flash`），`doctor` / `provider_health`
  只检查环境变量与本地命令，探测不到远端模型失效；live 中 `leader plan`
  首跑才暴露（且在切片 1 修复前是裸崩）。

## 硬约束（不可破）

`doctor` / `status` / `protocol status` / ACP preflight 必须保持只读且
**不得调用 provider**——这是项目反复固化的安全边界（CLAUDE.md 多处）。
因此"诊断命令自动探测远端模型"与边界直接冲突，属产品 fork。

## 方案对比

| 方案 | 内容 | 边界 | 成本/风险 |
|---|---|---|---|
| **a（已生效）** | 依靠切片 1 的干净错误面：远端拒绝时 `leader plan` 退出 1，`leader_errors[]` 与 stderr 携带远端 message（DeepSeek 400 的 message 本身列出当前支持的模型名） | 零新面；不碰只读边界 | 发现时机=首次真实调用；错误即文档 |
| **b（候选，需授权）** | 新增显式 `agentdeck leader probe --confirm`：对当前配置 provider/model 发一次最小 chat 请求（`max_tokens=1` 级别），回报 ready/模型有效性/远端错误 message；写一条审计事件；绝不在 doctor/status 内隐式触发 | 新增一条**显式、要 `--confirm`、花钱**的主动探测命令；doctor 只读边界不动 | 每次一次真实 API 调用（微量费用）；provider 矩阵各有探测方式，先只做 openai-compatible 族 |
| **c** | doctor `detail` / README 文案提示"模型可用性以远端为准，列表可能随时间变化；失效时见 leader_errors" | 纯文案 | 治标；可与 a/b 叠加 |

## 建议

**默认 a + c 立即成立（a 已随切片 1 落地，c 可并入下次文档整理）；
b 作为可选显式命令留给 user 决策。** 理由：走开环的入口是 `leader plan`，
失效在链条第一步即被干净拦截并给出可读修复提示（远端 message 直接写明
支持的模型），再加一层自动探测的边际收益低；若未来 GUI 需要"配置页即时
校验模型"，届时再实现 b（GUI 显式按钮 → `leader probe --confirm`）。

## 若批准 b 的实施要点（备忘）

- 仅 openai-compatible 族（DeepSeek 复用）；CLI-backed Leader 用
  `codex/claude doctor` 既有本地检查，不发网络请求。
- 请求体最小化（1 token），超时短（≤10s），错误路径复用切片 1 的
  `_bounded_http_error_detail`。
- 响应：`mode=leader_probe`、provider/model/ready/http_status/detail/
  audit 事件 `leader_probe_completed`；契约照惯例全量同步。
- 绝不被 doctor/status/preflight/workbench 隐式调用；GUI 只能渲染为
  显式按钮。
