# Co-pilot Line 1 Live Runbook(人工授权）

目标：在真实项目里用**真实 API Leader** + **两个真实 coding-agent worker**
（可见 tmux 终端）跑通一整轮结对，每步人工确认。

## 安全边界（必须遵守）
- 每个 live 步骤都要人类显式授权；agent 不得自行推断授权、不得自动 push。
- 不安装/认证任何东西（无 npx/npm/pip 自动下载）；Leader API key 由人类预置。
- Worker 写文件后，Leader 汇总前必须重读它真写的文件。

## 前置
1. 激活环境并安装：
   ```bash
   conda activate agentdeck && python -m pip install -e .
   ```
2. 预置真实 Leader provider（二选一，由人类完成）：
   - DeepSeek：`export DEEPSEEK_API_KEY=... DEEPSEEK_BASE_URL=... DEEPSEEK_MODEL=...`
   - openai-compatible：`export AGENTDECK_LEADER_API_KEY=... AGENTDECK_LEADER_BASE_URL=... AGENTDECK_LEADER_MODEL=...`
3. 诊断就绪（只读，不调用 provider）：
   ```bash
   agentdeck doctor
   ```

## 一整轮结对（逐步确认）
1. 初始化项目并 spawn 两个 worker 的可见终端（planner/coder/reviewer 按需）：
   ```bash
   agentdeck project init
   agentdeck agent spawn-ready --confirm
   agentdeck agent ready          # 确认 running
   ```
2. 真实 Leader 用文本拆计划：
   ```bash
   agentdeck leader plan --task "<你的真实小需求>" --provider deepseek
   ```
   人工检查返回的 plan（step 1..n、agent_id 只用已配置 worker、role 对应）。
3. 生成待确认项，逐条审批第一步：
   ```bash
   agentdeck approval create-from-plan --plan-id pln_xxx
   agentdeck approval list
   agentdeck approval approve --approval-id apv_xxx
   ```
4. 派发第一步给真实 worker 的终端，并在 tmux 里观察它自然执行：
   ```bash
   agentdeck approval dispatch --approval-id apv_xxx
   ```
5. worker 干完后，从 pane 回收结构化回复（约定 worker 输出以 `status:` 起始）：
   ```bash
   agentdeck capture-reply --agent coder --message-id msg_xxx
   ```
6. Leader review 决定下一步，人工确认后再派下一个 worker：
   ```bash
   agentdeck leader review --plan-id pln_xxx
   ```
7. 全部回复入账后汇总（Leader 汇总前重读 worker 写的文件）：
   ```bash
   agentdeck leader summary --plan-id pln_xxx
   ```

## 成功判据
一整轮（说 → 确认 → coder 执行 → 捕获 → 确认 → reviewer 执行 → 捕获 → 汇总）
端到端跑通，每步人工确认，全程 tmux 可见、账本/trace 可审计，**用的是真实 API
Leader，没有 fake、没有刚性协议**。
