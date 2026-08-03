# 走开段距离测量 Runbook（2026-08-03）

目的：给这个项目最贵的那个未知数一个数字——**一次 `goal start` 之后，
AgentDeck 自己能走多远才需要人。**

## 0. 为什么需要这份 runbook

到 2026-08-03 为止，本项目有 32 份验证文档、14 个 live round，
自主度梯子每一档都已落地并单独验过。但**走开段长度这个数字不存在**。

它被意外测到过一次（Round 13/14：846 个 wave，834 个空转在一道没人按的
授权框上），而那一次偶然观测直接催生了 human_gate 与 gate-preview 两刀
开发。这说明这个数字很贵，也说明至今没有人有意去测它。

前面 14 轮丢掉这个数字的原因是同一个：**每一轮都有人在旁边把它推过去。**
所以本 runbook 的核心不是"怎么跑"，而是**怎么不救它**。

## 1. 测量定义

human_gate 落地之后，宿主会在被等待的 worker 卡在未授权框上时诚实停下。
因此走开段长度有了精确定义：

> **走开段长度 = 从 `goal start` 到宿主自停之间的 wave 数。**
> **走开段品质 = 停止时的 `stopped_reason`。**

一次有效测量产出**两个数 + 一份证据**，不是"感觉挺顺的"。

## 2. 每轮固定协议

### 2.1 前置（一次性，人在场）

```bash
conda activate agentdeck
cd <目标项目>                      # 不在 AgentDeck 自己的 repo 上跑
agentdeck project init             # 首次
agentdeck leader set-provider --provider codex-cli --model <model>
agentdeck doctor                   # configured_leader.ok 必须为 true
```

策略与授权（**只能由人做的两个决定**）：

```bash
agentdeck policy set-mode --mode autonomous --confirm \
  --allow-agent <id> --max-approvals <N>
agentdeck delegation list          # 确认这一轮打算放行的前缀
```

> 委托前缀**绝不批量授予**。逐条显式 grant，且只对本机只读验证前缀和
> 任务 worktree 内的 git 前缀。宽度由人选，见 CLAUDE.md 的 delegation 节。

### 2.2 起跑

```bash
agentdeck agent spawn-ready --confirm
agentdeck goal preview --task "<一句话任务>"
# 读完确认屏——特别是白名单、审批预算、步骤里标了"← 白名单外"的行
agentdeck goal start --plan-id <pln_xxx> --confirm
```

### 2.3 走开

**这一段唯一允许的动作是读。** 具体见第 3 节。

```bash
agentdeck run-loop-host status                 # 只读
tail -f .agentdeck/run-loop-host/host.log      # 只读
```

### 2.4 停下后取数

```bash
agentdeck run-loop-host status > round-N-status.json
```

记录下表（一轮一行，缺一项这轮就不算有效测量）：

| 字段 | 来源 |
|---|---|
| `wave_count` | status |
| `stopped_reason` | status，六值闭合枚举 |
| `human_gate.agent_id` / `.box_kind` / `.command` | status，仅人类门停止时 |
| `last_gate` | status |
| 实际墙钟时长 | host.log 首末行时间戳 |
| 空转 wave 数 | host.log 中未产生派发的 wave 计数 |

人类门停止时，追加一步（**只看不 grant**）：

```bash
agentdeck delegation gate-preview --agent <human_gate.agent_id>
```

把前缀梯子原样抄进记录。**这一轮不要根据它 grant**——grant 属于下一轮的
前置，混进本轮会污染本轮的停止原因。

## 3. 作废条件（本 runbook 最重要的一节）

走开段跑起来之后，出现下列**任何一项**，这一轮作废，数据不进汇总：

- 任何人（含 AI 助手）按了授权框
- 任何人向 worker pane 发送了输入（`agent send`、tmux send-keys、直接敲键）
- 任何人手动 `capture-reply` / `ack` / `approve` / `dispatch`
- 中途改了 `.agentdeck/config.toml`、grant 了新委托、或改了 allowlist
- 中途 `run-loop-host stop`（除非是为了终止一轮已作废的测量）

理由：这些动作每一个都在回答"人介入之后它能走多远"，而我们问的是
"人不介入它能走多远"。**看到 worker 卡住、看起来只差一个回车，也只记录
不动手**——那个回车就是我们要测的距离的终点，按下去距离就没了。

允许的动作只有三类：`run-loop-host status`、读 `host.log`、
`agent capture`（只读 pane，不发送）。

## 4. 停止分诊：拿到 `stopped_reason` 之后做什么

| `stopped_reason` | 含义 | 下一轮怎么调 |
|---|---|---|
| `human_gate` | 卡在未授权框上 | 看 gate-preview 梯子，**人选一条**前缀 grant，下一轮重跑 |
| `gate_reached` | wave gate 不再是 `waiting_for_reply` | 读 gate 是什么：complete 是成功走完；`needs_human_approval` 说明白名单太窄 |
| `budget_exhausted` | 撞到 `--max-waves` 仍在等回复 | 距离 ≥ 该上限，抬高上限重测；同时查空转 wave 占比 |
| `policy_revoked` | `approval_mode` 离开 autonomous | 通常是误操作，本轮作废 |
| `signalled` | 收到 stop | 本轮作废 |
| `engine_error` | wave 引擎抛异常 | **这是 bug，优先级高于继续测量**，先修 |

## 5. 汇总产出

跑满 3–5 轮之后产出两样东西：

1. **停止原因直方图** —— 六个 `stopped_reason` 各占几轮
2. **距离分布** —— 每轮 `wave_count`，以及空转 wave 占比

这张直方图直接回答"下一刀该切哪儿"。在拿到它之前，
`docs/handoff/current-development-state.md` 里列的三个下一刀候选
（Leader 产出 DAG / Controller 式派发前校验 / 其余）都是在猜瓶颈。

每轮记录写进 `docs/validation/2026-08-XX-walkaway-round-N.md`，
汇总写进本文件末尾的"测量结果"节。

## 6. 测量结果

（待填）

| 轮次 | 日期 | wave_count | stopped_reason | 证据 | 有效 |
|---|---|---|---|---|---|
| — | — | — | — | — | — |
