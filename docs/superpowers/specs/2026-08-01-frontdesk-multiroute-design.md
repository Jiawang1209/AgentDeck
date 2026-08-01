# G1 Frontdesk 增强设计:多路径分类 + 独立只读命令与契约

Status: frozen(user 拍板 2026-08-01:多路径分类 + 独立命令与契约,一起做)
Baseline: G1 Frontdesk 已达验收(见
`docs/roadmap/2026-07-24-north-star-gap-review.md` 已达成节),本设计是在
既有 `frontdesk_card` 之上的增强,**不重写基线**。

## 问题

1. **分类只兑现了一半**。`docs/roadmap/ultimate-goal-roadmap.md` 的 G1 承诺
   是"把用户原话整理为 intake summary、分类为 **plan/run/help/skill/memory
   等候选路径**,并推荐显式下一步命令";而现状 `_frontdesk_card`
   (cli.py:13221)只产出两档 `classification`(`planning_candidate` /
   `needs_goal`)与一条 `next_command`。用户说"看看现在什么状态"或"我想加个
   skill",前台都只会把它当成 plan 目标或让人去看帮助。
2. **唯一没有契约的卡面**。`frontdesk_card` 只能从 `leader chat` 的特定
   措辞进入,没有 `docs/contracts/frontdesk-schema.md`,`agentdeck contract
   list` 里也没有条目——GUI 无法机器发现这个面,而其余 42 个面都可以。

## 设计

### A. 多路径分类(确定性,不调 provider)

新纯模块 `src/agentdeck/frontdesk.py`(零 IO、不 import cli/state):

- 闭合路由枚举
  `FRONTDESK_ROUTES = ("plan", "run", "status", "help", "skill", "memory")`。
- `classify_frontdesk(message) -> list[dict]`:返回**按置信度降序**的候选,
  每项 `{route, label, command, confidence, rationale}`:
  - `plan`:可提取目标 → `agentdeck leader plan --task <goal>`
  - `run`:含"开始运行/开始执行/跑一下"等启动语气且可提目标 →
    `agentdeck run --task <goal>`
  - `status`:含"状态/进度/看板/现在怎么样" → `agentdeck status`
  - `skill`:含"技能/skill" → `agentdeck skills list`
  - `memory`:含"记忆/memory/记住" → `agentdeck memory suggestions`
  - `help`:含"帮助/能做什么",或**兜底**(无其它命中) →
    `agentdeck leader chat --message "帮助"`
  - 置信度是**确定性小整数档**(命中强措辞 = high、弱线索 = medium、
    兜底 = low),不是模型打分;`rationale` 只说命中了什么词,不做推理。
- 匹配一律**只读文本**:不查 state、不看 plan/approval、不读 tmux、
  绝不调用任何 provider。

### B. `frontdesk_card` 增强(向后兼容)

- 保留现有 8 个字段(`LEADER_CHAT_FRONTDESK_CARD_FIELDS`)语义与取值规则
  **逐字节不变**:`classification` 仍是 `planning_candidate` /
  `needs_goal`,`next_command` 仍按原规则产出。
- **新增** `candidates`(list,A 的输出)与 `route`(top 候选的 route)。
  `LEADER_CHAT_FRONTDESK_CARD_FIELDS` 相应增两项;既有 `controls[]` 保留,
  并为每个候选补一条同 route 的 control(`kind=route`、`safety=inspect`
  或该命令本身的 safety;含占位符的命令 disabled 并给 blocker)。
- 既有 leader chat 入口行为不变(仍记 chat turn 与审计事件)。

### C. 独立只读命令与契约

- `agentdeck frontdesk --message <text>`:**纯只读**——不写 state、不记
  chat turn、不追加事件、不调 provider、不读 tmux。与 `agentdeck controls`
  / `agentdeck continue` 同类。输出前经
  `validate_frontdesk_contract()` 守门。
  (可审计 chat turn 仍由 `leader chat` 入口承担,二者分工写进契约文档。)
- `docs/contracts/frontdesk-schema.md` + `agentdeck contract frontdesk
  [--example]` + `CONTRACT_INDEX_SPECS` 新条目(索引 42→43)。契约需公开
  card 字段、候选项字段、闭合 `routes` 枚举、置信度档位与安全边界。

## 安全边界

- 全路径只读:分类是纯文本匹配;命令面不写任何 state。
- 候选命令只是**建议文本**,不是授权:含占位符的一律 disabled 带 blocker;
  前台永不代为执行任何一条。
- 不调用 Leader provider(G1 验收标准原文),不创建
  plan/action/approval/message/job/inbox。
- `classification`/`route`/`confidence` 是路由展示,不改变任何 gate。

## 非目标

- 用 LLM 做意图识别(前台必须确定性、零成本)。
- 前台代为执行候选命令、或据分类自动进入其它 mode。
- 多轮澄清对话状态(前台一次性只读产出)。

## 测试要点

- 分类矩阵:六条路由各自的强/弱命中、多路由共存时的排序、兜底 help、
  空消息;`rationale` 不含用户原文以外的臆测。
- 向后兼容:既有两档 `classification` 与 `next_command` 取值逐字节不变
  (拿现有 leader chat 测试当回归钉)。
- 命令只读:执行前后 `state.json` 与 events 逐字节相同。
- 契约:字段表/example/validator 一致;`contract list` 索引 43;
  leader-chat 的 `frontdesk_card_fields` 同步新增两项。
