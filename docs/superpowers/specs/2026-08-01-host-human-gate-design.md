# Run-loop 宿主人类门诚实停止设计

Status: frozen(user 拍板 2026-08-01:人类门诚实停止;其余四处默认由实现方定,已在下文写死)
Baseline: run-loop 背景宿主(`docs/superpowers/specs/2026-07-30-run-loop-host-design.md`)
与 scoped 授权委托(`docs/contracts/delegation-schema.md`)均已落地并 live 验证。
本设计**不重写**任何一方,只停止丢弃一份已经拿到手的证据。

## 问题:一个 stopped_reason 兼职了两种状态

Round 14 的宿主日志(`~/Desktop/agentdeck-live-scratch/.agentdeck/run-loop-host/host.log`,
880 行,7 次宿主运行)实测:

| run | waves | 空转 | 墙钟 | 结束原因 |
| --- | --- | --- | --- | --- |
| 1 | 60 | 58 | 14m49s | waiting_for_reply |
| 2 | 8 | 7 | 1m45s | complete |
| 3 | 100 | 96 | 24m53s | waiting_for_reply |
| 4 | 28 | 26 | 6m47s | complete |
| 5 | 150 | 147 | 49m54s | waiting_for_reply |
| 6 | 200 | **200** | 1h06m | waiting_for_reply |
| 7 | 300 | **300** | 2h29m | budget_exhausted |

**846 个 wave 里 834 个完全空转(98%),真正有产出的只有 12 个;最长连续
空转 610 个 wave。**run 6 与 run 7 是 100% 空转——3 小时 37 分钟、500 个
wave 全部消耗在一道没人按的 Playwright 授权框上。

空转的定义是该 wave 的 `auto_approved` / `dispatched` / `captured_replies` /
`review_iterations` / `blocked` 全为空。

这些 wave 的行为**并没有错**:plan `pln_d68e79abe9ef` 的 step 3(review 组
的第二个成员 planner)已派发未回复,gate 如实报 `waiting_for_reply`。错的是
这个 gate 值同时承载了两种语义:

- **worker 正在思考** — 回复会自己到来,轮询是对的;
- **worker 停在一道未委托的授权框上** — 回复**永远不会自己到来**,
  再轮询一万次也一样。

宿主无法区分,于是把 `--max-waves` 这个"我授权多少自主工作量"的安全预算
喂给了一个人类门,而**人类那边一个信号都没有收到**。这也是 Round 13
记录的"预算尺度/空轮询烧 wave"那条发现,现在有了硬数据。

## 设计:停止丢弃已有证据

### 零新增能力面

`--release-boxes` 开启时,宿主在段首(wave 0)和每个 wave 间隙已经调用
`_scan_release_delegated_boxes(...)`。该函数返回 `(released, skipped)`,
其中 `skipped[]` 的每一项已经携带:

```json
{"agent_id": "planner", "command": "…playwright_cli.sh open file:///…",
 "box_kind": "command", "mcp_server": null, "mcp_tool": null,
 "reason": "no active delegation", "iteration": 7}
```

宿主现在把 `skipped` 丢进 `_`。**人类门检测不需要读任何新东西,只需要不再
丢弃这一份。**

因此:**检测只在 `--release-boxes` 开启时生效**。这是边界纪律而非取巧——
不带该标志的宿主一次 pane 都不读,这条不变量必须原样保留。走开模式本来
就要开它(Round 13 / 14 都开了)。

### 判定规则

一个 wave 间隙的扫描结果构成人类门候选,当且仅当:

1. 该 skipped 项的 `reason == "no active delegation"`(pane capture 失败的
   skip 不算——那是 runtime 抖动,不是人类门);**且**
2. 其 `agent_id` 在**本 plan 当前正在等待的 agent 集合**内。

第 2 条复用文件通道摄入已经算出的 awaiting 集(已派发且 message 无 reply
的审批对应的 agent)。别的项目、别的 plan、闲置 agent 身上的框不该停掉
这台宿主。该集合目前内联在 `_ingest_plan_reply_files` 里,需先**原样抽出**
为纯 helper:

```python
def _plan_awaiting(state, plan_id) -> list[tuple[str, str]]:
    """(message_id, agent_id) for this plan's dispatched-but-unreplied approvals."""
```

`_ingest_plan_reply_files` 改为调用它(行为逐字节不变,由既有测试钉住),
人类门判定复用同一份——**单一来源,绝不出现第二套 awaiting 定义**。

**debounce:同一道框连续两次扫描命中才判定。**"同一道框"= 同一
`(agent_id, box_kind, command 或 (mcp_server, mcp_tool))`。理由:框可能在
扫描的缝隙里刚弹出、下一轮就被委托放行;要求连续两次可防误停,代价是
最多多转一个 wave。状态只存在 serve 进程内存里(`last_gate_candidate`),
不落盘——宿主重启后重新计数是正确行为。

### 停止与上报

判定成立时,宿主以新的 `stopped_reason = "human_gate"` 结束本次运行:

- 加入闭合枚举 `RUN_LOOP_HOST_STOPPED_REASONS`(`src/agentdeck/run_loop_host.py`
  是单一来源),枚举因此变为六值。
- `host.json` 记录携带 `human_gate` 对象:`agent_id` / `box_kind` /
  `command` / `mcp_server` / `mcp_tool` / `waiting_hint` / `detected_at_wave`。
- `host.log` 追加一条 `{"event": "human_gate", …}`。
- 审计事件 `run_loop_host_stopped` 的 `stopped_reason=human_gate`,
  携带同一份证据。
- `agentdeck run-loop-host status` 渲染该对象,并给出人类下一步提示:
  到对应 pane 亲自查看并按键(命令由人执行,AgentDeck 不代按)。

`waiting_hint` 目前不在 `skipped[]` 里,需**追加**该字段(纯附加,既有
断言只点名 `reason`,`boxes watch` 的 `skipped[]` 形状向后兼容)。

### 与既有 gate 的关系

`waiting_for_reply` 的语义随之**更准确**:它从此只表示"确实在等一个会
自己到来的回复"。wave payload 自身**不变**——单 wave 引擎逐字节不动,
人类门是宿主循环层的判断。gate 诚实性只增不减。

## 安全边界

- **绝不代按**。检测只上报;放行仍然只走既有的精确委托匹配路径
  (前缀 / MCP 双侧等值),该路径本切片一字不改。
- **绝不新增 pane 读取**。不开 `--release-boxes` 的宿主行为逐字节不变。
- **绝不放宽授权**。人类门只会让宿主**更早停下**,不会让它多做任何事。
- **绝不写 plan / approval / runtime state**。只写 host 记录、host 日志与
  一条审计事件。
- 检测失败(解析不出框、扫描异常)一律**不判定**——fail-open 到既有
  轮询行为,宁可多转也绝不误停一个正常运行的走开段。

## 非目标

- `run-loop --follow` 的对称实现(前台有人看着;记为 follow-up)。
- 空转退避 / 动态间隔(user 拍板时明确未选)。
- 空转不计入预算(会失去墙钟上界)。
- 把人类门通过推送通知发给人(独立切片)。
- 让宿主替人做出授权决定的任何形式。

## 测试要点

- 判定矩阵:未委托框 + 在 awaiting 集 → 两次后停;只命中一次 → 不停;
  两次命中的是**不同**框 → 不停(重新计数);框在 awaiting 集之外的
  agent 上 → 不停;`reason` 为 pane capture failed → 不停。
- 不开 `--release-boxes` 时:零 pane 读取、行为与既有逐字节相同。
- `human_gate` 停止后:host.json / host.log / 审计事件三处证据一致;
  `status` 渲染出该对象;`stopped_reason` 在闭合枚举内。
- 单 wave 引擎 payload 在人类门场景下逐字节不变(引擎零改动的回归钉)。
- 契约:`docs/contracts/run-loop-host-schema.md` 的 stopped_reason 枚举与
  status 字段表同步;`agentdeck contract run-loop-host` 输出一致。
