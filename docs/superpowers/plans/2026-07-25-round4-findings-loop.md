# Round 4 发现收敛循环（自主 /loop 执行，2026-07-25，顺序 ①④③⑤②）

来源：`docs/validation/2026-07-25-copilot-line1-round4-walkaway-loop.md`
五条 live 发现，user 拍板按 ①④③⑤② 顺序执行。

## 硬边界（同前轮）

- 绝不 `git push`；不调真实 LLM（provider 错误路径全部 mock）；不碰真实 tmux。
- 严格 TDD：RED → GREEN → 全量 `pytest tests/ -q` 绿（pipefail）+ compileall
  → HISTORY 同 commit；契约改动全量同步（schema docs、contracts.py、
  CLAUDE.md、README、测试）。
- 产品 fork 停下记阻塞跳下一切片；全部完成或阻塞时收尾
  （PushNotification + stop）。

## 切片清单（按序执行）

### [x] 1.（发现①）provider HTTP 错误不再裸崩 CLI

- 现象：DeepSeek 返回 400 时 `leader plan` 直接 traceback 崩溃，未写
  `leader_errors[]` / `leader_provider_failed` 事件，违反项目既有规则。
- 目标：`providers/openai_compatible.py` 的 HTTP 调用捕获
  `HTTPError`/`URLError`，读取错误体（截断、无密钥）并转为既有 provider
  失败类型，走现有 `leader_errors[]` + `leader_provider_failed` 记录路径，
  CLI 干净退出非 0；错误信息包含 HTTP 状态与远端 message（如 DeepSeek 的
  "supported API model names ..."，这同时缓解发现②的可见性）。
- 测试：mock urlopen 抛 400（带 DeepSeek 风格错误体）与 URLError 两例，
  断言非 0、无 traceback、leader_errors 落账、事件追加、不半写 plan。

### [ ] 2.（发现④）文件摄入与 gate 解耦

- 现象：coder 回复文件已就绪时，wave 因 reviewer blocked 停在 `blocked`，
  摄入条件（仅 `waiting_for_reply` 触发）未命中——与 recovery 遮蔽同类。
- 目标：run-loop 摄入改为按"本 plan 已派发未回复的 message 集合"驱动：
  从该 plan 的 dispatched approvals（含 message_id）减去已有 reply 的
  message，得到 awaiting 集；对其中 reply 文件已就绪者逐个摄入（有界，
  ≤awaiting 数），全部摄入后重算 review/gate 一次。gate 是什么不再影响
  摄入是否发生。绝不读 pane 语义不变。
- 同步：run-loop schema 摄入条件文字、CLAUDE.md run-loop 段；round 4
  wave2 场景（blocked + 文件就绪）做成 RED 用例。

### [ ] 3.（发现③）run-loop step 顺序守卫

- 现象：wave 把所有 approved-and-ready 审批一次全派，step 2 会在 step 1
  完成前派出，破坏顺序语义。
- 目标：单计划 wave 只派发"最早未完成 step"的 approved 审批；更晚 step
  的 approved 审批本 wave 记入 `skipped`（reason
  `awaiting earlier step completion`），保持 approved 不动。step 完成定义
  = 该 step 的审批已 dispatched 且其 message 已有 reply。`_run_loop_all`
  的每计划循环加同一守卫（不改共享预算/忙碌跳过语义）。摄入（切片 2）
  发生在派发前，使"step 1 文件就绪 → 同 wave 摄入后 step 2 即可派发"
  成立。
- 同步：run-loop / run-loop-all schema 顺序语义说明、CLAUDE.md 对应段；
  RED 用例=两步计划全 approved 双 agent running 时 wave 只派 step 1；
  step 1 回复入账后下一 wave 派 step 2；既有单步用例不回归。

### [ ] 4.（发现⑤）dispatch 送达校验（只读探测 + 设计说明）

- 现象：多行 dispatch prompt 尾部卡在 Claude Code composer，worker 静默
  空转，`waiting_for_input`（确认框启发式）探测不到。
- 目标（只读切片）：`agent capture` 新增派生字段 `composer_pending`
  （bool）+ `composer_preview`（命中的输入框首行或 null）：启发式=捕获
  文本中提示符行（`❯` 等已知 composer 标记）之后、状态栏/分隔线之前存在
  非空内容。纯读取派生，不写 state、不自动补回车、不重发。capture_card
  契约字段同步（fields/validator/example/schema/README/测试）。
  同文档记录送达校验的后续方向（dispatch 后自动探测/分段发送+提交确认）
  为待 user 决策的 fork。
- 测试：fake backend 输出含/不含 composer 内容两类用例 + 契约用例。

### [ ] 5.（发现②）远端模型下线探测——评估切片（偏产品决策）

- 现象：DeepSeek 下线 `deepseek-chat`，doctor/provider_health 只查环境
  变量，探测不到远端模型失效。
- 约束：doctor/status/preflight 必须保持只读且不得调用 provider（既有
  硬边界），因此"自动探测"与边界冲突，属产品 fork。
- 目标：写评估文档（方案对比：a=靠切片 1 的干净错误面出错时提示支持的
  模型列表（已实现）；b=新增显式 `agentdeck leader probe --confirm` 主动
  探测命令（一次最小 API 调用，需 user 授权设计）；c=doctor detail 文案
  提示"模型可用性以远端为准"），给出建议（a 默认 + b 作为可选显式命令），
  **不改 doctor 只读边界，不实现 b**，留 user 拍板。

## 阻塞

（运行中遇 fork 在此记录）

## 完成判定

五切片全部 `[x]`（或余项记阻塞）→ 全部 commit → PushNotification 汇总
→ stop。
