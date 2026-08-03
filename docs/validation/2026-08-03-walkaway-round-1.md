# 走开段距离测量 Round 1（2026-08-03）

协议：`docs/validation/2026-08-03-walkaway-distance-measurement-runbook.md`

**这是本项目第一个走开段距离的数字。**

## 结果

| 项 | 值 |
|---|---|
| **走开段长度** | **11 wave** |
| **墙钟时长** | **100.3 秒**（15:39:00.5 → 15:40:40.9 UTC） |
| **停止原因** | **`human_gate`** |
| 预算使用 | 11 / 60 wave（未烧光） |
| 段内派发 | 1（step 1 → planner） |
| 段内捕获 | 0 |
| 有效性 | **有效**——段内全程无人介入 |

人类门证据：

```
agent_id  planner
box_kind  command
command   curl -L --max-time 30 -sS https://www.whu.edu.cn/
hint      Press enter to confirm or esc to cancel
指向      agentdeck agent terminal --agent planner
```

## 现场

- 被测版本：仓库工作树 @ `6447524f`（editable 安装；本轮改动只涉文档，代码同 `e3888a24`）
- 目标项目：`~/Desktop/agentdest-tmp-20260803`（全新 git 仓库，单次 seed commit）
- Leader：`codex-cli` / `gpt-5.6-sol`（DeepSeek key 未设，改用 CLI-backed）
- Workers：planner(codex, shared) / coder(codex, worktree) / reviewer(claude, shared)
- 策略：autonomous，白名单 `coder, reviewer`，`max_approvals=20`
- 委托：**零**（基线轮，故意不 grant）
- 合并：不自动合并
- 任务：复刻一个真实大学主页的布局与区块结构，文字图片一律占位自撰

## 预测 vs 实际

跑前写下的预测（见会话记录）：

1. **最可能：`human_gate`，停在 step 1 的第一道网络框上** → **命中**
2. 次可能：宿主认不出框而空转到 `budget_exhausted` → 未发生
3. 不太可能：`gate_reached` → 未发生

中途一度以为预测要被证伪（观察到 codex 内建 web search 不弹框就取数），
**那个中途判断是错的**：planner 随后仍然走了 `curl`，产生标准命令框，
AgentDeck 正确识别（`box_kind=command`）并诚实停下。教训记在 finding 1。

## 对照历史

Round 13/14 的偶然观测是 **846 wave 里 834 个空转**在一道没人按的框上。
本轮是 **11 停在 60 里**。human_gate 的四条判据 + 连续两次 debounce +
`human_gate_command` 指针，整条链路首次在真实走开段里端到端走通。

## Findings

### F1（确凿）CLI agent 的内建工具完全在授权模型之外

planner 先试浏览器运行时（无实例），随后走 codex **内建 web search**
取原站——**不弹任何框**。AgentDeck 的委托模型只认两种框：`$ command`
执行框与 MCP 工具框；CLI agent 自带的工具走它们**自己**的审批策略。
因此这类网络读：不需要委托（没有框）、不会成为人类门（没有框）、
**AgentDeck 既没授权也没观察到**。

注意与 shell 路径并存：同一个 planner 随后跑 `curl` 就**确实**产生了
命令框并被正确捕获。所以结论是"两条路并存，其中一条不经过门"，
不是"网络读一律不经过门"。

同日写进 `docs/contracts/delegation-schema.md` 的 known gap 一节
（"每一次网络读都是永久人类门"）据此**需要修订**：对 shell 路径成立，
对内建工具路径不成立。

### F2（确凿）gate-preview 梯子第 1 级的宽度陈述不成立

`gate_preview.py:263` 在 `unpinned_tail` 为空时渲染
`连带授权: (无——仅此一条命令)`；而 `delegation_match.py:240` 的匹配是
`rest.startswith(prefix)`，**没有等值特例**。因此 grant 整条命令做前缀后，
任何以它开头的命令同样命中：

```
grant:  curl -L --max-time 30 -sS <url>
命中:   curl -L --max-time 30 -sS <url> -o <任意路径>
命中:   curl -L --max-time 30 -sS <url> -d @<任意文件>
```

`-o` / `-d` 是 curl 自身参数而非 shell 重定向，`delegation_match` 的硬拒
（命令替换 / heredoc / 非 `/tmp` 重定向 / `eval` / 后台 `&` …）一条都不适用：
单段、无被拒构造、startswith 命中 → 自动放行。

`unpinned_tail` 算的是"相对**本次观测到的这条命令**还剩什么没钉住"——
这对第 2–5 级正确；到第 1 级被渲染成"仅此一条命令"，与匹配语义不符。

**为什么这条值钱**：该模块 docstring 明写它拒绝做危险命令模式检测，
理由是"部分检测器会让『没有警告』被读成『安全』，正是本项目花了很多切片
消除的那类**显示了不成立的事实**"。而它在同一屏上断言了第 1 级的宽度，
且那正是谨慎的人最可能选的一级。本轮真实的门就是一条 `curl`。

### F3（确凿）worker 能跑 `agentdeck` CLI，拦住它的是 codex 而不是 AgentDeck

planner 的 cwd 就是项目根，`.agentdeck/` 在那里，`agentdeck` 在 PATH 上。
本轮 planner 实际跑了 3 次 `agentdeck agent capture --agent coder`
（只读，跨 agent 观察，账本无记录）。每次都是 **codex 自己的授权框**
把它拦下来问人。

即：**目前保护 AgentDeck 审批门不被 AgentDeck 自己的 worker 绕过的，
是一个外部工具的权限模型。** 审批门的设计前提是"CLI 前面只有人类"，
而 worker 的 shell 就在项目根内。

**未确立**：本轮**没有**证据表明 worker 跑过写命令
（`capture-reply` / `approval dispatch` / `agent send` 在 200 行 scrollback
中均无出现）。15:49–15:51 的三笔写操作由人类在段外手动执行，已确认。

### F4（确凿）目录 trust 框报告 `waiting_for_input: False`

三个 pane 起来后都停在首次目录 trust 框上（codex 两个、Claude Code 一个），
而 `agent capture` 报告 `waiting_for_input: False`——检测器认的是执行框句式，
trust 框句式不同，认不出来。

**边界正确的一半**：trust 框**本来就不该可委托**（CLAUDE.md 明写不得由
worker 输入或静默 Enter 绕过）。要修的只有"如实报告这个 pane 在等输入"，
不是"让它可被放行"。

后果具体：走开段中若某 pane 停在 trust 框上，`box_kind` 解析不出 →
不构成人类门候选 → 宿主不会诚实停下，只会空转到预算耗尽。

## 另一个数字

从 `agent spawn-ready` 到走开段能开跑，中间隔着 **3 次人类按键**
（三个目录 trust 框），而 AgentDeck 对这三个框的报告都是
`waiting_for_input: False`。走开段还没开始，"人必须在场"的次数已经是 3。

## 段外（不计入测量，但结论重要）

15:49 之后由人类手动驱动，直到整个任务完成。**这部分不是走开段数据，
但它回答了另一个同样没被 live 验过的问题：机器本身通不通。**

产物：

```
index.html 111 行 / styles.css 143 行 / script.js 171 行
whu-homepage-static-replica-spec.md 18KB          ← planner 的 step-1 交付
tests/site-structure.test.mjs + tests/browser_matrix.py   ← coder 自建验收
结构：8 section / 3 nav / header / footer / 68 链接 / 91 class / 0 img（纯占位图形）
```

提交弧：

```
ac24a3d  feat: build responsive university homepage replica
099bda5  fix: address homepage review blockers              ← 复审打回
2087aa7  fix: restore video cover and breakpoint continuity  ← 第二轮修复
```

**这条弧是本次尝试最重要的结果**：implement → review → 返工 的完整闭环
在真实任务上跑了一遍。该闭环此前只有单元测试，从无 live 记录。

因此第一次尝试的结论是：

> **机器是通的。** 三 agent、计划、审批、worktree、复审打回、返工、
> 二次修复、产物入账，整链在真实任务上端到端成立。
> **不通的只有一件：无人值守地越过授权框。** 而那正是 AgentDeck
> 故意做不到的事。

未深究项（留待后续轮次）：

- `worktree_skipped` 显示 coder 首次 worktree 创建失败（第二次成功），
  产物最终落在主工作区而非任务分支；`git branch -a` 只见 `main`。
- 复刻**质量**未经核验。0 图纯占位是按简报要求，但"布局比例是否还原"
  需人眼判断。planner 自己声明过无可用浏览器、会把"源码可确认"与
  "仍需视觉复验"分开——该声明尚待人类兑现。

## 下一轮

按 runbook 的分诊表，`human_gate` → 看 `delegation gate-preview` 的前缀梯子，
**由人类选一条** grant，再重跑。本轮梯子已生成（5 级，`curl` 裸 token 标
`⚠ 最宽`）。选哪一级是人的判断，AgentDeck 不推荐、不排序、不预选——
**且在 F2 修复前，第 1 级的宽度描述不可信。**
