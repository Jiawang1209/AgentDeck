# `/goal` 一句话走开设计

Status: frozen(user 拍板 2026-08-01,两点缺省见文末)
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
          ↑ wave 上限为缺省值,可用 --max-waves 改
  委托    node tests/*  (2 条活跃委托,均为只读验证前缀,遇到即自动放行)
  合并    不自动合并——复审通过后停下来等你点头
          (想要自动合并显式加 --merge-on-complete)
  停下来找你的条件:
    · 复审通过,待你合并             ← 缺省下的正常终点
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

## 缺省(user 拍板 2026-08-01)

1. **`--max-waves` 有缺省 300,但必须在 preview 里显示。**理由:信息完整的
   确认不要求你每次手敲数字,只要求数字**不能隐形**。宿主"强制有界、无
   unbounded 形态"这条冻结不变量不受影响——`/goal` 永远带着一个具体的
   wave 上限进入宿主,只是这个数可以来自缺省而不是手输。preview 必须把它
   连同"可用 `--max-waves` 改"一起印出来。
2. **`--release-boxes` 默认开,`--merge-on-complete` 默认关。**
   放框默认开的理由:否则你显式 grant 过的委托形同虚设,而它本来就只放行
   命中活跃委托前缀的框(未命中一律不代按,现在还会以 `human_gate` 诚实
   停下)。合并默认关的理由:**合并进 main 应当是一次单独的点头**——跑完
   停在"复审通过,待合并",把 `worktree merge-plan --confirm` 留给人。
   想要最满的走开,显式加 `--merge-on-complete` 即可。

## 实现期修正(2026-08-01,落地时发现)

### 一、"三条命令"其实是四条:阶段 0 必须先建审批

本 spec 正文一路写作"`leader plan` → `approve-plan --confirm` →
`run-loop-host start --confirm`",落地时发现这条链**按原样写会每次都失败**:
`approval approve-plan` 只批准**已存在的** pending 审批,而一个刚从
`leader plan` 出来的 plan 一条审批都没有,approve 阶段必然报
"no pending approvals" 并停住。人手工爬这道梯子时同样要先跑一条
`approval create-from-plan`——真实的梯子是**四条命令,不是三条**;
连上 preview 里那次 `leader plan`,`goal` 覆盖的是五条。

因此 `goal start` 在批准之前插入**阶段 0**:该 plan 尚无审批时调用既有的
`agentdeck approval create-from-plan --plan-id <id>`;已有审批则一条不碰,
连事件都不写。这**不削弱"不新增任何一种动作"**——阶段 0 同样是一条早已
存在、早已被 sanction 的命令,同样是**被调用而非被复制**,只是本 spec
写作时把它数漏了。

### 二、门是五道,不是四道:活宿主必须在写之前判定

正文只列了四道门(`--confirm`、`autonomous`、已知 `--plan-id`、
`--max-waves >= 1`),"已有活宿主"不在其中——因为被复用的
`run-loop-host start` 自己就有单例互斥,看上去无需重复。但那道互斥发生在
**批准之后**:活宿主在跑时,`goal start` 会先把整个 plan 的审批批完,
再撞上宿主的"already running"而退非 0,留下**审批已批、宿主没起**的
半应用状态。这违背本仓库既有门契约的精神——**拒绝之前绝不发生变更**。

落地时因此补上**第五道门**,与另外四道一起在任何写之前判定:本项目已有
活宿主即拒绝,零写、零 spawn,stderr 点名在跑的 `plan_id` 与 `pid`,
并指向 `agentdeck run-loop-host status` / `agentdeck run-loop-host stop
--confirm`。存活判定**复用** `_host_liveness_or_none`(不另写第二套规则)。

**stale 记录不挡**:pid 已死的残留记录正是一次新的 `goal start` 该放行的
情形,与 `run-loop-host start` 对 stale 记录的既有处理同源;只有 `running`
才拦。这条修正**严格更保守**(它只增加拒绝,不放开任何路径),并让拒绝
重新变回原子的。
