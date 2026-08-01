# `/goal` 一句话走开设计

Status: DRAFT —— 等 user 过目(本切片是权限的**集中**,不是新增,值得看一眼再动代码)
提出者: user(2026-08-01)。原话要点:AgentDeck 该有 Hermes 那种渐进式披露;
完全放任的开发写一句 `/goal` 就好。

## 问题:梯子全在,但没有一处以梯子的形式呈现

AgentDeck 的自主度是连续可调的,而且每一档都已落地:

```
ask → approve → autonomous → run-loop → --follow → run-loop-host
手动                                                          走开
```

但要爬到顶格,今天得写四条命令九个标志:

```bash
agentdeck policy set-mode --mode autonomous --confirm --allow-agent coder --max-approvals 20
agentdeck leader plan --task "..."
agentdeck approval approve-plan --plan-id pln_xxx --confirm
agentdeck run-loop-host start --plan-id pln_xxx --confirm --max-waves 300 \
    --interval 10 --release-boxes --merge-on-complete
```

其中 `pln_xxx` 要人肉从上一条的输出里抄到下两条。这是**仪式**,不是安全。

## 原则(user 拍板):压缩确认,不是去掉确认

九个标志里有两类东西,必须区别对待:

| 类别 | 例子 | `/goal` 该怎么做 |
| --- | --- | --- |
| **仪式** | plan_id 人肉传递、interval 靠猜、同一件事确认四次 | 消灭 |
| **安全门** | `--confirm`、`--max-waves` 强制有界、autonomous 白名单 | **一个都不动** |

正确形态是把四次确认压成**一次信息完整的确认**。

## 设计

### 两步,而不是一步

`/goal` 必须是 preview → confirm 两步,原因是本仓库既有的安全边界原文:

> only the exact confirmed preview becomes frozen authority

所以不能让人确认一句还没变成计划的话。

**第一步 `agentdeck goal preview --task <text>`**(写 plan,不执行):
调用配置的 Leader 生成 plan(与 `leader plan` 同一条路径,不新增规划面),
然后把**整段将要发生的授权**一次性摊开:

```
将要授权:
  计划    pln_a1b2  (3 步)
    1. coder     实现 …
    2. reviewer  审查 …          ← [review] 两人组,planner 为第二审查员
    3. planner   审查 …
  预算    300 wave / 最多 2 轮返工 / 审批预算 20
  委托    node tests/*  (2 条活跃委托,均为只读验证前缀)
  合并    通过复审后自动合并到 main
  停下来找你的条件:
    · 遇到未委托的授权框(human_gate)
    · 复审预算耗尽而仍未通过
    · 白名单外的 agent 需要审批

确认后执行:
  agentdeck goal start --plan-id pln_a1b2 --confirm --max-waves 300
```

**第二步 `agentdeck goal start --plan-id <id> --confirm [预算标志]`**:
按顺序做三件**已经各自被 sanction 过**的事,不新增任何一种动作——
`approve-plan --confirm` → `run-loop-host start --confirm`,中间不停。

### 安全门逐条保留

- **必须 `--confirm`**;缺了拒绝、零写。
- **必须 `approval_mode == "autonomous"`**;不是则拒绝,并给出显式
  `policy set-mode` 命令。**`/goal` 绝不代人翻这个开关**——它是长期策略,
  不是一次目标的附属决定。这是本设计最重要的一条边界。
- **`--max-waves` 仍然强制有界**,无 unbounded 形态;缺省值必须在 preview
  里显示出来(信息完整的确认意味着预算是看得见的)。
- 白名单、审批预算、复审轮次预算、step 顺序守卫、文件通道回复、
  human_gate 停止——**全部原样继承**,`/goal` 一条都不放宽。
- **plan 绑定**:`goal start` 只接受 `--plan-id`,且该 plan 必须是
  `goal preview` 刚产出的那一个。确认绑定到**看过的那份计划**,不是绑定到
  一句话。

### 渐进式披露(user 的第二个诉求)

user 指出的另一面:43 个契约 + workbench 一屏全展开,是为**机器可发现性**
服务的,人面对它是信息过载。这不需要改契约层,需要的是**呈现层默认收窄**:

- `goal preview` 的默认输出就是上面那种**一句话 + 一个下一步**的形态,
  不是 JSON 倾泻;`--json` 才给完整 payload。
- 这一条同样适用于既有命令的未来演进,但**本切片只做 `goal` 两条命令**,
  不改任何既有命令的默认输出(那会是破坏性变更,须单独拍板)。
- `agentdeck frontdesk` 是这套呈现层的第 0 级,已经存在;`goal preview`
  是第 1 级。

## 安全边界

- `/goal` **不新增任何一种动作**。它只是把 `leader plan`、`approve-plan
  --confirm`、`run-loop-host start --confirm` 三条已存在且各自有门的命令
  串起来,并把三处确认合并为一处**信息完整**的确认。
- 不翻 `approval_mode`;不改配置;不新增委托;不代按授权框;
  不绕过 step 顺序守卫、复审预算或合并 gate。
- preview 写 plan(与 `leader plan` 等价)但**不批准、不派发、不启动宿主**。
- start 之后的一切由**未改动**的宿主 wave 引擎承担。

## 非目标

- 让 `/goal` 自动开启 autonomous 模式(**明确拒绝**:那等于用一次目标的
  确认换掉一个长期策略决定)。
- 无界宿主、多计划 `/goal`、远程 `/goal`。
- 改动既有命令的默认输出形态(渐进披露的推广是独立切片)。
- 用 LLM 决定预算或委托(预算是人给的数,委托是人显式 grant 的)。

## 待 user 拍板的两点

1. **`--max-waves` 的缺省值**。宿主强制有界是冻结不变量,但 `/goal` 是否
   可以有一个显示在 preview 里的缺省(建议 300,约合 8 小时 @10s 间隔),
   还是必须每次显式给?建议:有缺省但必须在 preview 里显示。
2. **`--release-boxes` / `--merge-on-complete` 是否默认开**。二者都已各自
   有门(委托必须显式 grant;合并受 verdict gate 扣住),但默认开等于
   把"走开"的含义调到最满。建议:`--release-boxes` 默认开(否则委托形同
   虚设),`--merge-on-complete` 默认**关**(合并进 main 应当是一次单独的
   点头)。
