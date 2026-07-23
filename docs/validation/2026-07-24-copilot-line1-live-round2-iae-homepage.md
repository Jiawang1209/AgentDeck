# Co-pilot Line 1 Live Round 2：复刻 IAE 首页（PASS）

- 日期：2026-07-23 晚 → 2026-07-24 凌晨
- 项目：`~/Desktop/agentdeck-live-scratch`
- Leader：真实 DeepSeek API（`deepseek-chat`）
- Workers：planner=codex、coder=codex、reviewer=claude（Claude Code），全部真实 CLI、可见 tmux
- 任务：用纯 HTML/CSS/JS 复刻 <http://www.iae.cas.cn/>（中科院沈阳应用生态研究所）首页，仅本地练习

## 结果：一整轮自然循环端到端 PASS

`leader plan`（DeepSeek 自然拆 3 步 planner→coder→reviewer）→ `approval create-from-plan`
→ 每步人工 approve + dispatch → worker 在可见 pane 自然执行 → 回复入账 → `leader review`
→ `leader summary`（`status: ready`，3 replies / 3 artifacts）。

- plan `pln_1c1536be2374`；replies `rep_604470569fe7` / `rep_1540e8ed3b98` / `rep_0cb051e91e97`
- artifacts：`iae-homepage-analysis.md`（191 行分析）、`index.html`（54KB 自包含，Chrome 实测）、
  `review-index-html-2026-07-23.md`（还原度逐项比对；reviewer 顺手修复 P1-1/P1-2/P2-1/P2-2）
- 步数放松（1..N）+ prompt 步数上界两项修复在真实 provider 上验证有效

## Live 发现（按优先级）

1. **capture-reply 对真实 agent TUI 全线失效（本轮最大缺口）**
   - codex：回复首行渲染为 `• status: completed`，`_extract_structured_reply` 的
     `strip().startswith("status:")` 匹配不上（差两个字符）。
   - Claude Code：TUI 清滚动区，pane 历史仅剩 ~36 行，`status:` 行滚出后**永久不可恢复**，
     任何 pane 解析都救不回来。
   - 两次都走了 runbook 兜底 `agentdeck reply` 手动入账（reviewer 回复系从其报告文件忠实重建）。
   - 结论：pane 抓取天然脆弱。短期切片=剥装饰符容错；长期方向=**文件通道回复**
     （worker 把结构化回复写进约定文件，capture 读文件而非 pane）。
2. **worker 内部确认门是真实交互层**：codex/claude 各自弹命令/工具授权框（curl、http.server、
   chrome-devtools），任务会静默停在框上。监视脚本靠识别 "Press enter to confirm" /
   "enter to submit" 才发现。产品应把"worker 等待人类输入"提升为一等 runtime 状态。
3. **`leader review` 对部分派发迟钝**：仅 step 1 有回复、后两步审批还 pending 时就返回
   `next_action=summarize`（"all dispatched steps have replies"），忽略未派发步骤。
4. **spawn 默认布局不可见**：垂直死叠 + 无标签，小窗口下 pane 被挤到 1 行高。本轮手工
   `select-layout tiled` + `select-pane -T <agent>` + `pane-border-status top` 后体验立好。
   应做成 spawn 后的默认行为。
5. **DeepSeek 步数方差**：同类任务一次返回 1 步（仅 planner），重跑丰富任务描述后返回 3 步。
   1..N 校验按设计放行，但"欠拆解"是真实模式。
6. **agent 天性再次得证**：reviewer 角色只被要求审查，却主动修了 4 个 bug、按用户全局
   CLAUDE.md 写了 `docs/DEVELOPMENT_HISTORY.md`。role prompt 不约束真实 agent 行为；
   账本以 artifact/hash 记录了实际变更（index.html SHA f3866c75… → 88ed49fa…）。
7. 运维小坑：tmux 查活要用项目 socket（`-L agentdeck-agentdeck-live-scratch`），默认 socket
   看不到；zsh 里 `echo ===` 触发等号展开会炸脚本。

## 人工授权记录

每个 live 步骤（重置、spawn、plan、每步 approve/dispatch、手动 reply、布局调整）均由 human
在对话中显式确认；worker 内部授权框均由 human 亲手按键（发送按键被权限层拦截，符合预期）。
