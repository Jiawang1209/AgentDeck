# 宿主人类门诚实停止 live 验收(2026-08-01)

Status: PASS(首次真机即命中)
Scratch: `~/Desktop/agentdeck-live-scratch`
Plan: `pln_d68e79abe9ef`(Round 14 的 `.focus-slider` 键盘导航复审)
Spec: `docs/superpowers/specs/2026-08-01-host-human-gate-design.md`
Commits: `8774b599` → `5b7f496a`(7 个),全量 5024 passed / 3 skipped

## 场景:被验对象自己制造的教科书场景

Round 14 停在一个真实的人类门上——review 组的第二个成员 planner 想用
Playwright 打开页面亲眼核验方向键行为,弹出授权框;项目**零活跃委托**,
所以这道框谁也放行不了,只能人按。plan 状态:

| step | agent | 状态 |
| --- | --- | --- |
| 1 | coder | 已回复 |
| 2 | reviewer | 已回复(verdict pass,4/4 标准,score 92) |
| 3 | planner | 已派发、**无回复** ← 框卡在这里 |

awaiting 集因此恰为 `{planner}`,框恰在 planner 身上,恰好未委托——
本功能三个判定条件同时成立。**这不是构造出来的测试场,是被修复的那个
缺陷自己留下的现场。**

## 对照

同一个项目、同一个 plan、同一道框:

| | 旧行为(修复前实测) | 新行为(本次) |
| --- | --- | --- |
| wave 消耗 | 300 / 300(烧满) | **1** / 20 |
| 墙钟 | 2h29m | ~7s |
| stopped_reason | `budget_exhausted` | **`human_gate`** |
| 人拿到的信息 | 无 | 框在谁身上、是哪条命令、屏上提示 |

Round 14 全程 7 次宿主运行、846 个 wave 里 834 个空转(98%),最长连续
空转 610 个——全部是这一道框造成的。

## 三个证据面逐一核验

**1. `agentdeck run-loop-host status`**

```json
{
  "stopped_reason": "human_gate",
  "wave_count": 1,
  "human_gate": {
    "agent_id": "planner",
    "box_kind": "command",
    "command": "/Users/liuyue/.codex/skills/playwright/scripts/playwright_cli.sh open file:///…/index.html",
    "mcp_server": null,
    "mcp_tool": null,
    "waiting_hint": "Press enter to confirm or esc to cancel"
  }
}
```

**2. `host.log`**(append-only JSONL)最后三行按序为:
wave 1 的 payload(`stopped_reason=waiting_for_reply`,gate 自身仍如实
报告)→ `{"event": "human_gate", "wave": 1, …框证据…}` →
`{"event": "host_stopped", "stopped_reason": "human_gate"}`。

**3. 审计事件** `run_loop_host_stopped` 的 payload 内嵌同一份
`human_gate` 对象,与前两面逐字段一致。

## 安全边界现场核验

- **绝不代按**:本次运行的审计流里**零** `auth_box_released` 事件,
  那道框验证结束后仍原封不动停在 pane `%4` 上等人。
- **gate 诚实性不变**:wave 1 payload 自身仍报 `waiting_for_reply`——
  人类门是宿主循环层的判断,单 wave 引擎一字未动。
- **不触发自动合并**:本次未带 `--merge-on-complete`;单测已钉住
  `human_gate` 不是 `gate_reached`,该分支永不进入。
- **debounce 生效**:段首(wave 0)扫描记下候选,wave 1 后扫描确认同
  一道框才停——所以是 `wave_count: 1` 而非 0。

## 结论

功能按设计工作,且是在**它要解决的那个真实缺陷的现场**验证的。
本轮之后,走开段遇到人类门不再是"预算被静默吃掉、人一无所知",
而是"7 秒内停下,并指名道姓告诉人该去按哪一个"。

Round 14 本身仍待人工按下该框后收尾——本次验证有意**没有**代按。
