# Scoped 授权委托设计（已拍板，进入实施）

- 日期：2026-07-26（round 6/7 授权框数据驱动；user 同日拍板两决策）
- 已定决策：**E1**=执行机制走 AgentDeck 注册表 + 有界 watch 循环（不映射
  codex 原生 don't-ask-again：委托、匹配、释放全部落在 AgentDeck 审计链，
  provider 无关）；**E2**=委托长期有效直到显式 `delegation revoke`（风险由
  scope 本身收窄：命令前缀 + agent 绑定）。
- 数据来源：round 6 codex 7 框全为 `node tests/*` 前缀；round 7 6 框 =
  5 个只读验证前缀 + 1 个任务 worktree 内 git 写；Claude CLI auto-mode
  两轮零框（provider 不对称，注册表机制天然兼容）。

## 命令面（MVP）

1. `agentdeck delegation grant --agent <id> --prefix <cmd-prefix> --confirm`
   写入 authoritative `delegations[]`（`{delegation_id, agent_id, prefix,
   created_at, revoked_at=null}`）+ `delegation_granted` 事件；未知 agent、
   空前缀、缺 `--confirm`、重复的活跃 (agent,prefix) 一律拒绝零写。
2. `agentdeck delegation list` 只读列出委托（含 revoked，`active` 派生
   字段），零写、不碰 tmux。
3. `agentdeck delegation revoke --delegation-id <id> --confirm` 置
   `revoked_at` + `delegation_revoked` 事件；未知/已撤销拒绝。
4. `agentdeck agent boxes --agent <id>` 只读：capture 目标 pane，检测
   授权框、提取待批命令、匹配活跃委托，报告
   `{box_present, command, delegated, delegation_id, release_command}`；
   不写 state、不发送输入。
5. `agentdeck agent release-box --agent <id> --confirm` 单次释放：重新
   检测框并提取命令，仅当命中该 agent 的活跃委托才发回车，追加
   `auth_box_released` 事件（含 delegation_id 和命令全文）；无框、未命中、
   缺 confirm 一律拒绝零写。
6. `agentdeck boxes watch --confirm --iterations N --interval S
   [--agent <id>]` 有界轮询：检测→匹配→释放，逐次审计，汇总输出；
   必须 `--confirm` **且** `approval_mode=autonomous`（与 run-loop/
   approval auto 同级的 delegated 自动化门），iterations 必须有界。

## 安全边界

- 委托不是权限扩张：只允许"代按人类本会逐次按下的回车"，且仅限命中
  前缀的框；esc/拒绝路径永不自动化。
- push、跨 worktree、未知命令的框永远不命中（靠前缀语义收窄；文档明确
  建议 grant 只用于只读验证类前缀和任务 worktree 内 git 类前缀）。
- release-box 是人类显式单发（--confirm 即可）；watch 是 delegated 自动化
  （--confirm + autonomous 双门）。
- 所有释放都有 `auth_box_released` 审计事件，`agentdeck history` 可见。
- boxes/list 只读面绝不发送输入、不写 state。

## 实施切片

1. delegations[] 注册表 + grant/list/revoke（state writer 注册 + 事件）。
2. `agent boxes` 只读检测面（复用 capture 的 waiting_for_input 机制，
   加命令提取 + 委托匹配）。
3. `agent release-box --confirm` 单次释放 + 审计。
4. `boxes watch` 有界循环（autonomous 门）。
5. delegation contract 注册（schema doc + CONTRACT_INDEX_SPECS + validator
   + example + 测试）与 README/CLAUDE.md 同步。
