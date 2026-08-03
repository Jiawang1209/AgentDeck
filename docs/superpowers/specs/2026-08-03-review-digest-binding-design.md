# verdict 绑定被审终态:digest binding + post-review mutation 检测

Status: frozen(user 拍板 2026-08-03,在 DAG 一刀之后选定;并追加要求
"容纳 CCB 的能力,并在此基础之上有所提升")
Baseline: G5 verdict 入账、review 组 + any-fail-blocks 聚合、review 迭代闭环、
G4 任务级 worktree、`merge-on-complete` 的 `_verdict_merge_blocker`
——**均不重写**。

## 问题:merge gate 从不问"verdict 说的是不是我要合的这份代码"

今天 `_verdict_merge_blocker(store, plan_id)` 只读 `plan_verdict_summary`:
overall 是不是 pass、组齐没齐、有没有 verdict。这三问都是**关于判定本身**的。
没有任何一问是**关于被判定物**的。

于是这条链成立而无人察觉:

```
reviewer 审 coder 分支的 C1 → verdict pass 入账
              ↓（此后 C1 之上又多了提交:worker 写完 reply 继续干活、
                人手改、别的流程流入）
merge-on-complete 合并该分支的 C2 —— 拿着关于 C1 的 pass
```

**"审查之后又被改动"目前发现不了。** 这是一个真实的信任缺口:verdict 是
自动合并的唯一放行依据,而它与被放行的内容之间没有任何绑定。

同一个洞在**产物**那一层同样存在:artifact 记录只有 `path` / `kind` /
`status` / 时间戳,**没有指纹**。作为证据登记的文件事后被改写,系统一无所知。

## CCB 参照:先纠正一条不成立的断言

`docs/reference-analysis/2026-08-03-ccb-current-state-and-gap.md` 第 145 行断言:

> CCB 以"worktree digest 与绑定 digest 相同"作为终态判据,并做 post-review
> mutation 检测。

**该断言在本仓库所持 CCB 副本中无法证实。** 对
`References/claude_codex_bridge-main/` 全量检索 `custody` / `worktree digest` /
`post-review mutation` / `root verification`,零命中。本 spec 落地时必须同步
修正那一行(切片 ⑦)——它与该复研文档自己要纠正的毛病是同一类,只是这次是
我方在犯。

**CCB 实际做到的**(`docs/plantree/plans/agentic-loop-workflow/`,已核对原文):

- agent 只产出 artifact,**脚本(程序内核)决定 commit 还是 reject**;
  commit 时 `validate path / kind / state edge / required evidence`,再
  `record digest / actor / job / timestamp`;
- **幂等重入**:同一 `(task, loop, result, report digest)` 重复 bind/import
  是 no-op;
- **conflicting result or digest fails closed**——冲突时停住,既不重发也不
  判成功;
- 拒绝理由是**闭合枚举**(missing required artifact / illegal status edge /
  task already bound to another loop / stale lease / loop id mismatch /
  artifact kind not allowed / required evidence missing / terminal task
  cannot be modified),明令"脚本拒绝时不应该让 agent 猜测状态"。

即:**CCB 的指纹绑在产物上,不绑在 git 终态上。** 这恰好使两边互补。

## 设计:一条纪律,两个轴

> **判断必须绑定到被判断的那份东西的指纹;指纹对不上,自动路径 fail-closed,
> 人类显式命令永不受 gate。**

### 轴一 · artifact digest(容纳 CCB 已有的能力)

`reply` / `capture-reply` 登记 artifact 时记录 `content_hash`(sha256)与
`byte_count`。

- 计算发生在**写路径**上(登记那一刻)。**只读面继续一字不读产物文件**
  ——`agentdeck artifacts`、`artifacts_card`、trace 的既有边界一字不破。
- 重复登记同一 `(message_id, path)`:digest 相同 → **幂等**,不重复入账;
  digest 冲突 → **fail-closed 拒绝并点名**,绝不静默覆盖(CCB 原则)。
- **拒绝的粒度是 artifact 条目,不是整条 reply。** 冲突时该 artifact 不入账、
  记一条审计事件、在响应里点名冲突路径与两个 digest;reply 本身照常记录
  (沿用 G5 "无效 verdict 不阻断 reply" 的既有先例——回复是事实,产物登记
  是判断)。绝不静默把新内容顶掉旧记录。
- `digest_status` 是闭合枚举 `recorded` / `file_missing` / `read_failed`;
  非 `recorded` 时 `content_hash` 与 `byte_count` 为 `null`。
  **绝不把"没算出来"记成"算过了"**。

### 轴二 · tree digest(超出 CCB 的一层)

review step 派发建 worktree 后 `git rev-parse <base_branch>`,把
`worktree_base_commit` 记在 message 上,与既有
`worktree_path` / `worktree_branch` / `worktree_base_branch` 并列。

**为什么这是可证的**:worktree 正是从该 ref 检出的,所以这个 sha 就是
reviewer 目光所及的那棵树。**捕获点必须是派发时**,不是收 verdict 时——
reviewer 的 worktree 不会自动跟进,收 verdict 时再解析会把他从未看过的新提交
当成"他审过的",那是一条不成立的事实。

派发时记录对**所有** worktree step 统一进行(rework 步也有 base);
只有产出 verdict 的步会被 gate 查。

### 前置:先修 base 选取,否则并行组必然误报

`_plan_base_worktree_branch` 今天返回**前一个 step** 的分支。于是 review 组
成员 2 的 base 是成员 1 的分支。digest 一旦绑上去,成员 1 提交自己的审查
文档就会让 gate 报漂移——**每个并行组 plan 都扣住自动合并**,把刚落地的
DAG 扇出废掉。

改为:**review 组成员一律基于组前最近的非组成员 step 的分支。**

精确规则:从该组**首个成员**向前走,取最近一个"有任务分支且自身不是 review
组成员"的 step 的分支。

- 无 `[review]` 配置的项目:review step 本就没有组标记 → 走原臂 →
  **逐字节不变**(与 DAG 一刀同一条差分金样纪律,必须由差分测试钉住)。
- rework 步的 base **不动**——它读得到审查意见是好事。
- 组内两名成员由此 base 相同,digest 绑定平凡无歧义。

**它自己还修掉一个既有缺陷**:今天成员 2 基于成员 1 的分支,看得见成员 1 的
审查意见,而 `any_fail_blocks` 聚合正是以**独立判断**为前提。并行让这件事
碰巧不发生(两个 worktree 几乎同时创建,成员 1 还没提交);本改动让它
**结构性**不发生,而不是靠时序运气。

### 推导:纯模块,三态

新纯模块(零 IO、零 LLM、不 import cli/state/config)吃
「message provenance + 当前分支解析结果」,吐每个被审步的状态:

| 状态 | 含义 |
| --- | --- |
| `match` | 被审 commit 仍是该分支终点 |
| `drift` | 分支已前进或被改写——**审查之后又被改动** |
| `unverifiable` | 核不了:未记录(plan 早于本功能)、分支已不存在、非 git 仓库 |

git 解析是 I/O,留在 CLI 侧;纯模块只吃解析结果,与 `step_dag.py`、
`gate_preview.py`、`delegation_match.py` 同一形态。

**`unverifiable` 绝不显示成"已验证"。** 这是本 spec 最重要的一条呈现纪律:
本仓库反复在修的缺陷类型就是"显示了不成立的事实",而 `verified: false` 与
`drift: false` 读起来都像"没问题"。三态必须各自可见、各自有原因。

### 设门:只在自动合并上

`_verdict_merge_blocker` 追加一道检查:该 plan 任一被审步为 `drift`,或
记录了 base commit 却解析不了 → **扣住自动合并**,`plan_merge.mode` 出新值
`review_stale`,并交回显式人类命令。

- **人类 `worktree merge-plan --confirm` 永不受 gate**(与既有 verdict gate
  规则完全一致)。
- 老 plan 无记录(`unverifiable` 的 "not recorded" 子因)→ **gate 行为不变**,
  否则在飞的 plan 全被扣住。但只读面必须如实显示"没记录",不得显示"没问题"。
  这是本 spec 唯一一处刻意的 fail-open,必须在契约文档里写明理由。
- 无 worktree 的 plan(`worktree_base_branch` 为 null)→ 无可查项 →
  行为不变。

### 呈现:只读面提前可见

staleness 投影进 `agentdeck plan status` 与 `verdict_summary`(三面共享),
让人在走开段末尾撞上合并拒绝之前就能看见。只读投影只是 provenance,
**不授权 dispatch、不改审批语义、不改任何 gate**。

**实现偏差(2026-08-03,落地时记录)**:上一段写"投影进 `plan status` 与
`verdict_summary`"。二者都建在 `StateStore` 内,而 store **不得 shell out
调 git**(其余每个 store 方法都只碰自己的 JSON/SQLite)。因此实际形态是:
`plan status`(store 侧)只投影**已记录**的 `worktree_base_commit`;三态
**实时比对**由 **CLI 侧**以同级 `review_bindings` 块给出,与 merge gate
共用同一个 `_plan_review_bindings` helper。`verdict_summary` 保持不变。
**不要用"给 store 加一个 git 调用"来消除这条偏差。**

## 安全边界(逐条不变)

- 审批门、白名单、`max_approvals` / `max_review_rounds` 预算:一字不动。
- 绝不 force-spawn;绝不读 pane 推断完成;只认文件通道回复。
- 不调用 provider;不发送 tmux 输入。
- 只读面不读产物文件内容(digest 只在写路径算)。
- 不自动重审、不自动回炉——**发现事实与决定对策分开**。
- 新增字段全部是 provenance,不是授权。

## 非目标

- **自动重审 / 自动追加回炉轮**(漂移即重审是另一次拍板)。
- 审查**期间**漂移的独立诊断(捕获点选在派发时,它天然被算进 drift)。
- 非 worktree 模式(shared workspace)的内容校验。
- CCB 的 lease / revision fencing / exactly-once 派发——那属于
  "Controller 式派发前校验"那一刀,不在本刀。
- 产物内容的语义校验(我们只比指纹,不判断内容好坏)。

## 切片

1. base 选取修正 + 零行为变化差分钉(无 `[review]` 项目逐字节不变)。
2. 派发时记录 `worktree_base_commit`(纯 provenance,不设门)。
3. 纯模块三态推导 + 单元矩阵。
4. merge gate 接线(`review_stale`)+ 人类命令不受 gate 的回归钉。
5. artifact `content_hash` / `byte_count` 登记 + 冲突 fail-closed + 幂等重入。
6. 只读投影(`plan status` / `verdict_summary`)+ 契约 / README / CLAUDE.md。
7. 修正 CCB 复研文档第 145 行那条不成立的断言。

每片各自 commit + HISTORY 条目。

## 测试要点

- **零行为变化差分**:无 `[review]` 配置的项目,dispatch 与 wave payload
  逐字节不变(切片 1 的首要钉)。
- 组内两名成员 base **相同**,且都等于实现分支(不是彼此)。
- `worktree_base_commit` 等于建 worktree 那一刻 base 分支的 tip。
- 三态矩阵:match / drift / 未记录 / 分支消失 / 非 git 仓库。
- **drift 扣住自动合并**:verdict pass 之后往被审分支再提交一次 →
  `merge-on-complete` 出 `review_stale`,分支未被合并。
- **人类命令不受 gate**:同一状态下 `worktree merge-plan --confirm` 照常合并。
- artifact 重复登记:同 digest 幂等;**冲突 digest 拒绝且零写**。
- 只读面**零写**、且不读产物文件内容。
- **变异验证**:每条 fail-closed 断言都要能在规则被削弱时报红——
  且必须检查所挑数据路径不会让退化规则也恰好成立(DAG 一刀的教训)。
