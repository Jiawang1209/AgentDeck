# 审计:契约校验发生在不可逆副作用之后(2026-08-01)

Status: 已修 2 处(当下可达的全部),剩 10 处潜伏,本文是余账
起因: `/goal` 终审 Finding 3 —— `run-loop-host start` 在 spawn 之后才校验契约,
失败时命令退非 0 而宿主**真的在跑**,调用方于是谎称"宿主没起"。终审的收尾
一句是本次审计的理由:"根因是个模式,不是一次性问题。"

## 缺陷类

命令在**不可逆副作用之后**校验响应契约,于是校验失败 =
**命令报告失败,而效果已经发生**。调用方与人类据此相信"什么都没发生",
自然的补救是重试——而重试会把已经发生的效果再做一遍。

本仓库已有纪律"不得打印半坏 payload";本次补上互补的一条(已写入
`CLAUDE.md`):

> 会写 state、spawn 进程、往 pane 按键或合并分支的命令,必须在副作用
> **之前**校验。若 payload 本身描述该副作用而无法前置,失败路径必须如实
> 报告**已经发生了什么**,绝不能措辞成"它没有发生"。

## 审计方法:两个必须分开问的问题

这是本次审计最有价值的方法论产出:

| 问题 | 结果 |
| --- | --- |
| **(a) 校验失败时效果会不会残留?** | **7/7 实测确认**(注入失败校验器,效果照样留下) |
| **(b) 校验器在真实 payload 上会不会失败?** | **多数不会** |

多数站点的 payload 是字面量字典 + 闭合枚举取值 + **从被检查的那个列表自己
算出来的计数**——校验器今天是个**恒真式**。`run-loop-host start` 正属此类,
所以终审那条是**潜伏**缺陷:离变成谎言只差加一个字段、加一个枚举值,或者
嵌一张活状态派生的卡。

**真正当下可达的,是那些校验"活状态派生或 LLM 派生的嵌套卡"的站点。**

只问 (a) 会得到"12 处全是 bug"的过度警报;只问 (b) 会漏掉"改一个字段就
变成谎言"的脆弱性。两个都问才排得出优先级。

## 已修(当下可达的全部)

### 1. `_dispatch_approved_approval` —— tmux 按键 —— 已修 `1319d643`

`backend.send_input()` 把任务提示打进 worker 的 pane 之后,才校验一张从
`state["inbox"][agent_id]` **重建**的 inbox 卡,失败时**抛异常**。

后果链条是最难看的一条:run-loop 的派发循环把它当成"派发失败"接住,给一次
**真的发生了**的派发记 `run_loop_dispatch_failed`,gate 报 error 邀请重新
派发——**同一个 worker 被提示两次**。

红测还暴露出比审计描述更糟的形态:真正可达的生产路径不是校验失败,而是
渲染时 `AttributeError`(inbox 里一行 legacy 非 dict 记录)直接从 `cli.main`
崩出去,同样在 `send_input` 之后。

修法:inbox 卡是**一次已成功派发上的展示附加物**,展示失败绝不能把成功
变成报告失败。失败时降级为 `inbox_card: null` + 解释性 `blocker`,并追加
`approval_dispatch_inbox_card_unrenderable` 审计事件——降级仍可见,但不是
一次失败的派发。

### 2. `approval dispatch-ready --confirm` —— tmux 按键 × N —— 已修 `ed56c6b6`

批量循环**连 try/except 都没有**:任何异常在派发了一部分之后把整批撕掉,
不写 `approval_dispatch_ready_completed`、不出 JSON。人看到一个 traceback,
**不知道哪几个 worker 已经被提示过**,重跑即重复提示。

触发条件是真实的竞态:readiness 在批次规划时检查,而 pane 可能在检查与
派发之间消失。

修法:逐项容错,失败项变成 `status=failed` 的结果条目并继续后续项;始终
输出 JSON 与完成事件;`failed_count > 0` 时 `ok=false` 退非 0(沿用
`worktree merge-plan --confirm` 的既有部分失败先例)。blocker 措辞刻意是
"**可能**已部分完成"——因为 `send_input` 之后、`mark_approval_dispatched`
之前抛出时,worker 确实已经拿到提示;说"没发生"就又犯了本缺陷类。

## 余账:10 处潜伏站点(按 效果等级 × 可达性 排序)

全部 (b) UNREACHABLE-today,即校验器目前是恒真式;它们是**脆弱性**而非
现行谎言。改动这些 payload(加字段、加枚举值、嵌活状态卡)时必须同时
处理本条。

| # | 站点 | 效果等级 | 若失败,人会错误地相信 |
| --- | --- | --- | --- |
| 1 | `run-loop --follow` | tmux × N + **git merge** | "follow 跑失败了"——而任务分支**已经合并**,且 N 个 worker 被提示、N 道框被按 |
| 2 | `run-loop-host start` | process | "宿主没起"(终审原发现;调用方措辞已修,站点未动) |
| 3 | `goal start` | process + state | "什么都没批准"——而审批全批了、宿主在跑,且 `goal_started` **没被追加**,账本缺了解释宿主为何存在的那条 |
| 4 | `run-loop --plan-id --confirm` | tmux + state | "这一 wave 没推进"——而预算已消耗、worker 在跑(此处**部分诚实**:它确实追加了 `run_loop_contract_failed`) |
| 5 | `run-loop --all --confirm` | tmux + state | "并行 wave 失败了"——而每个活跃计划都已派出 worker |
| 6 | `run-loop-host stop --confirm` | process(SIGTERM) | "停止失败"——而信号已发,某条路径上宿主已退出且记录已清 |
| 7 | `workflow run\|resume --confirm` | tmux + state |(b) **PLAUSIBLE**:校验 `turns[]` 来自解析 worker 回复,活状态派生 |
| 8 | `mission run\|resume --confirm` | tmux + state |(b) **PLAUSIBLE**:mission 已 claim、worker 已 spawn、workflow 已跑 |
| 9 | `plan rework --confirm` / `skills lock` / `release` / `goal preview` / `run --task` / `reply` / `capture-reply` | state | 各自"失败了"——而 step 已追加 / lockfile 已落盘 / 轮次已推进 / plan 已带 `goal_preview` provenance 存在 / reply 已入账 |
| 10 | `leader chat` | state |(b) **CONFIRMED 可达**,但它是**全仓库最好的失败路径**:退出前把失败写进 `leader_errors[]` 与 `leader_chat_contract_failed`。它是"让失败路径说真话"的范本 |

### 建议的最小修法(前三)

- **`run-loop --follow`**:merge 是这里**唯一可以简单重排**的效果——先校验
  不含 `plan_merge` 的 payload,再合并,再挂上 `plan_merge` 打印。wave 效果
  无法重排,失败路径改为把 wave 数、已派 message id、放框数、合并结果打到
  stderr,并带进它本来就会追加的 `run_loop_contract_failed` 事件。
- **`run-loop-host start` / `goal start`**:除 `pid` 外 payload 在 spawn 前
  已完全已知——先校验一份 pid 占位的前置投影,spawn 后只填 pid 再打印。
  `goal start` 另需把 `goal_started` 移到校验**之前**追加,否则一次失败的
  打印会抹掉宿主为何存在的唯一账本证据。

## 覆盖(已检查且干净)

- **靠排序干净的范本**:`state.py:391` 的 `_require_valid_migration_contract`
  在 `_write_migration_backup` 与 state 写入**之前**调用——正确姿势仓库里
  本来就有。
- **靠无副作用干净(约 50 处)**:只读命令在打印前校验——`status`、
  `workbench`(含 `--watch`)、`controls`、`role bindings`、`frontdesk`、
  `inbox`、`trace`、`leader review/summary`、`approval list`、
  `run-loop-host status`、各 workbench 卡片 builder 等。
- **靠不校验干净**:`dispatch`、`agent spawn/stop/refresh`、`worktree
  merge/abandon/prune`、`delegation grant/revoke`、`skills import/create`、
  `memory apply`、`storage events-*`、`approval approve/reject`、
  `policy set-mode`、`ack`、`project init` —— 它们不校验契约,因此不会
  报告一次虚假的失败(但也因此没有 payload 守门,是另一回事)。
