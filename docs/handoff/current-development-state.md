# AgentDeck Current Development State

Updated: 2026-08-01

**`/goal` 一句话走开已落地(2026-08-01,user 提出并拍板两点缺省,spec/plan
`docs/superpowers/{specs,plans}/2026-08-01-goal-one-shot-walkaway*`,
8 commits `84c73688`→`543f86e6`,全量 5113 绿)**:`agentdeck goal preview
--task <text>` → `goal start --plan-id <id> --confirm`,把爬到自主度顶格的
四条命令九个标志压成**一次信息完整的确认**。原则是 user 定的:**压缩确认,
不是去掉确认**——仪式(plan_id 人肉传递、interval 靠猜、同一件事确认四次)
消灭,安全门一条不动。`goal` **不新增任何一种动作**,只调用既有的
`leader plan` / `create-from-plan` / `approve-plan --confirm` /
`run-loop-host start --confirm`(被调用而非被复制)。**六道门**全部在任何写
之前判定(`--confirm`、autonomous、已知 plan、`--max-waves>=1`、无活宿主、
**plan 必须来自 goal preview**),任一不过零写零 spawn。缺省:`--max-waves`
300 但必须在 preview 里显示、`--release-boxes` 开、`--merge-on-complete` 关。
**落地与终审共修掉五处**:①"三条命令"其实是四条(`approve-plan` 只批已存在
的审批,必须先 `create-from-plan`——我 spec 数漏了);②活宿主不在门内导致
"先批准再被拒"的半应用状态(补第五道门);③**preview 显示了两条不 bind 的
约束**(`审批预算`/白名单只约束**本次确认之后**的自主自动批准,而本次确认
是一次性批准全部步骤)且完全没显示白名单——现已改为分行标注并在步骤上标出
白名单外 agent;④**plan 绑定四处文档都写了、代码一处没实现**(现已持久化
`source=goal_preview` 并加为第六道门);⑤宿主契约校验在 spawn **之后**,
失败时 `goal start` 谎称"宿主没起"(改为"可能已起,去查 status")。
**live 已验**:真项目 preview 渲染、白名单/预算分行、委托按 agent 分组、
plan 绑定门拒绝且零写零 spawn。**剩余需 human**:完整走开段(真 `goal start`
→ 宿主跑完)尚未 live;Round 14 那道框仍在 pane 上等人按。

**`/goal` 一句话走开已落地(2026-08-01,spec/plan
`docs/superpowers/{specs,plans}/2026-08-01-goal-one-shot-walkaway*`,
3 commits `84c73688` → `21e79b99` → 本条,全量 5096 绿 / 3 skipped)**:
自主度梯子(ask → approve → autonomous → run-loop → --follow → run-loop-host)
每一档早已落地,但没有一处以梯子的形式呈现——爬到顶格要写四条命令九个标志,
`plan_id` 还得人肉从上一条输出抄到下两条。新增 `agentdeck goal preview
--task <text>` → `agentdeck goal start --plan-id <id> --confirm` 两步,把
**四次确认压成一次信息完整的确认**,安全门一条不动。第 45 个契约 `goal`
(`docs/contracts/goal-schema.md`,`agentdeck contract goal`)。

要点与接缝:
- **`goal` 不新增任何一种动作**。`preview` 复用 `_generate_leader_plan` +
  `store.record_plan`(与 `leader plan` 同一条路径)只写 plan;`start` 依次
  **调用**(绝不复制)`approval create-from-plan` → `approval approve-plan
  --confirm` → `run-loop-host start --confirm` 的既有实现,三条既有命令
  行为逐字节未动,start 之后一切由**未改动**的宿主 wave 引擎承担。
- **落地时发现 spec 少数了一条命令**:spec 里的"四条命令"实际是五条——
  `leader plan` 之后必须先 `approval create-from-plan` 才有 pending 审批给
  `approve-plan` 批。因此 `goal start` 在该 plan 尚无审批时先调用这条既有
  命令(已有审批则一条不碰,连事件都不写),否则 approve 阶段必然失败。
- **最重要的边界**:`goal` 绝不翻 `approval_mode`。非 autonomous 项目的
  `goal preview` 只返回非空 `blocker`(内含显式 `policy set-mode` 命令)、
  `confirm_command=null`、next control disabled,不创建 approval、不改配置、
  不启动宿主;validator 硬拒 blocker 非空却仍给 confirm_command 的 payload。
- 缺省(user 拍板):`--max-waves` 缺省 300(单一来源 `GOAL_DEFAULT_MAX_WAVES`,
  `max_waves_is_default` 驱动渲染打出"↑ 缺省值,可用 --max-waves 改"——数
  可以来自缺省,但绝不能隐形)、`--release-boxes` 默认开、
  `--merge-on-complete` 默认关(缺省终点=复审通过,待你合并)。
- 呈现层渐进披露只在这两条新命令上生效:默认人类可读渲染,`--json` 才给
  完整 payload;**本切片不改任何既有命令的默认输出**(那是破坏性变更,
  须单独拍板)。
- **剩余需 human**:`goal` 尚无 live 验证记录——建议在真实项目上跑一次
  `goal preview` → `goal start`(会真的起后台宿主),确认渲染读起来确实是
  "一次信息完整的确认",并把结果记进 `docs/validation/`。Round 14 那道框
  仍在 pane 上等人按。

**G6 Role Topology 已落地——北极星最后一相收官(2026-08-01,spec/plan
`docs/superpowers/{specs,plans}/2026-08-01-g6-role-topology*`,11 commits
`fdcb5e18`→`1af718ff`,全量 5075 绿)**:`agentdeck roles` + workbench
`roles_card`(mode `role_bindings`)+ 第 44 契约 `role-bindings`,只读展示
北极星六层各自绑到了什么。设计支点=六个角色由**三种绑定方式**承载
(`command`/`logical_leader`/`worker_agent`),某些字段必然为 null。
**落地时暴露三件事**:①spec 漏查——workbench 早有一个形状不同的
`role_topology_card`(答"此刻状态"),两卡当时都发 `mode=role_topology`;
新卡改名 `role_bindings`,旧卡一字不动,分工写进 schema doc 开头。
②**既有真 bug**:旧卡 planner/orchestrator provider 硬编码取
`config.leader.provider`,自 G2 起一直无视 `[leader.*]` 覆盖(配 codex-cli
仍报 deepseek,GUI 可消费卡片在撒谎);已改走 `resolved_*_backend` 并有
回归测试。③**终审第五次命中 fail-open**:契约文档说 `bound`="恰好解析到
一个绑定",实际只成立"恰好命中一个 hint"——歧义只在单 hint 集内检测,
跨集漏掉,导致 coder 层能绑到 reviewer(真实现 agent 从图里消失)、或
两层绑到同一 agent(而本仓库的迭代设计明令禁止 coder 复审自己)。修法=
一次解析全部 worker 层,role 命中多层者是歧义证据,两层都 `ambiguous`;
另修 validator 未强制的"必然 null"条款与六层覆盖。**live 验收已 PASS**(`docs/validation/2026-08-01-g6-role-bindings-live.md`,
真配置项目十条断言逐行核过);live 当场暴露一处低报——两人复审组只显示
首位,已追加只读 `group_members` 修复(`9be5dfef`,与 `candidates` 严格分工:
前者表成员、后者表歧义,首位必须等于 `agent_id` 是 validator 回归钉)。
**剩余需 human**:Round 14 那道框仍在 pane 上等人按;
下一候选是 user 提的 `/goal` 一句话走开 + 呈现层渐进披露。

**G6 Role Topology 已落地(2026-08-01,北极星最后一相,spec
`docs/superpowers/specs/2026-08-01-g6-role-topology-design.md`,plan
`docs/superpowers/plans/2026-08-01-g6-role-topology.md`,3 commits
`fdcb5e18` → `72e5d2a3` → 本条)**:交付一个只读面回答"我的项目把北极星
六层各自补全成了什么"——`agentdeck roles` 与 workbench `roles_card` 是
**同一个 builder、同一份 validator、逐字段相同**的两面,加上第 44 个契约
`agentdeck contract role-bindings` / `docs/contracts/role-bindings-schema.md`。
设计支点是那句关键事实:六个角色**不是同一种东西**,闭合 `binding_kind`
(`command` / `logical_leader` / `worker_agent`)同时解释了哪些字段**必然
为 null**(logical Leader 永远没有 pane,intake 命令连 provider 都没有);
拍平成一张 agent 表就会撒谎。绑定全部从现有权威来源推导(
`resolved_planner_backend` / `resolved_orchestrator_backend` 的 `[leader]`
回退、`leader_backend_identity` 的 normalized provenance、`[review]`、
ProjectView `agents[]` runtime 投影),**不新增配置面、不新增状态源、不读
tmux**;推导歧义一律 `ambiguous` 且列全候选,**绝不静默择一**。纯推导逻辑
在零 IO 的 `src/agentdeck/role_topology.py`,可以脱离项目把矩阵测透。
**一个接缝值得记住**:workbench 里早已有一张 `role_topology_card`
(旧线的"每个协调角色/worker 此刻在做什么"),它的形状、validator 和一批
测试都还在。两张卡最初都发 `mode = "role_topology"`,GUI 无法区分;因此
新卡片已改名为 `mode = "role_bindings"` / 契约 `role-bindings`(2026-08-01
修复),workbench 里的键仍是 `roles_card`。三张角色卡的分工
(`role_card` = 我配了哪些 agent,`role_topology_card` = 谁此刻在做什么,
`roles_card` / `role_bindings` = 我补全了哪几层)写在契约文档开头。
**剩余需 human**:新面尚无 live 验证记录——建议在一个真实项目上跑
`agentdeck roles` 与 `agentdeck workbench`,确认六层绑定与该项目的
`[[agents]]` / `[review]` 实情一致(尤其 `ambiguous` 与 `unbound` 的措辞
是否足以让人知道该改哪个配置),并把结果记进 `docs/validation/`。

**宿主人类门诚实停止已落地并 live 验证(2026-08-01,spec
`docs/superpowers/specs/2026-08-01-host-human-gate-design.md`,plan 同名,
11 commits `8774b599`→,全量 5029 绿)**:动机是 Round 14 宿主日志实测——
846 个 wave 里 834 个完全空转(98%),run6/7 共 3h37m 100% 空转,全部烧在
一道没人按的 Playwright 授权框上;根因是 `waiting_for_reply` 兼职了
"worker 在思考"与"worker 卡在人类门"两种语义。做法的关键是**零新增能力
面**:`--release-boxes` 的扫描本来就把框证据算好放在 `skipped[]` 里,宿主
只是把它丢进了 `_`。新 `stopped_reason=human_gate`(闭合枚举第六值)带
六字段证据进 host.json / host.log / 审计 / status。**live 首验即 PASS**
(证据 `docs/validation/2026-08-01-host-human-gate-live.md`):同一现场
旧行为烧满 300 wave / 2h29m,新行为 **wave 1 / ~7s** 并指名道姓;零
`auth_box_released`(绝不代按,框仍原样等人)。**终审又抓到一条 fail-open**
(`d1aa64ec`,本 session 第四轮终审第四次命中):检测只要求"未委托 + 在
awaiting 集内",而 marker `Would you like to run` 在**已答复**的折叠框上
同样命中——全 None 身份恒等于自身会让 debounce 必然确认(误停健康走开段),
残留 `$ ` 行还能刮出假命令;修复加"待批证明"(活动选择器字形 `›1.`,与
MCP 提取器那道硬约束同源)与"身份非空"两条判据,收紧后同一现场复验仍
PASS。**教训**:此前每个 serve 级人类门测试都 mock 掉了扫描,所以 pane
文本→解析→候选整段从未端到端执行过,F1 才能活过七个 commit 和一次 live
PASS(live 那次恰好是真待批框,只走了 happy path);现已补三个喂真实 pane
文本、跑未 mock 扫描的端到端回归。**剩余需 human**:Round 14 那道框仍在
pane 上等人按(本功能有意不代按);`run-loop --follow` 的对称实现是
follow-up;下一个拍板候选是 user 提的 `/goal` 一句话走开 + 呈现层渐进
披露(本切片是它的前置:走开命令遇到人类门必须吭声)。

**宿主人类门诚实停止已落地(2026-08-01)**:spec
`docs/superpowers/specs/2026-08-01-host-human-gate-design.md`,plan
`docs/superpowers/plans/2026-08-01-host-human-gate.md`,7 commits
`8774b599`→`03374af1`(+ 本条 docs commit),全量 5024 passed / 3 skipped。
动机是 Round 14 宿主日志的硬数据:**846 个 wave 里 834 个完全空转
(98%,3h37m),run 6 与 run 7 是 100% 空转**——全部消耗在一道没人按的
Playwright 授权框上,而 `waiting_for_reply` 无法区分"worker 在思考"
(会自解)与"worker 停在人类门上"(永不自解),于是把 `--max-waves`
这个自主工作量预算喂给了一个人类门,人类那边一个信号都没收到。做法是
**零新增能力面**:`--release-boxes` 的扫描 `_scan_release_delegated_boxes`
本来就返回带全部证据的 `skipped[]`,宿主此前把它丢进 `_`;本切片只是
不再丢弃——awaiting 集限定(复用与文件通道摄入同一份 `_plan_awaiting`)
+ 同一道框连续两次 debounce → 新的第六个 `stopped_reason=human_gate`,
证据(六字段,单一来源 `HUMAN_GATE_FIELDS`)进 host.json / host.log /
`run_loop_host_stopped` 审计 / `run-loop-host status` / 契约五个面。单
wave 引擎 `_run_loop_single_wave` 一字未改;不开 `--release-boxes` 的宿主
一次 pane 都不读、行为逐字节不变;证据是 provenance 不是授权,AgentDeck
永不代按。**live 验证待办**:下一轮 round 应带 `--release-boxes` 重启一台
宿主,在 Playwright 框场景下确认看到 `human_gate` + 屏上证据而非烧满预算
(user 也可直接去那道框按回车放行,两条路径都能验证)。非目标(记为
follow-up):`run-loop --follow` 的对称实现、空转退避/动态间隔、把人类门
通过推送通知发给人。

**Round 13 live PASS(2026-08-01,user 拍板"先 1 再 2")**:review 迭代
闭环与 run-loop 背景宿主双目标全链验证,证据
`docs/validation/2026-08-01-copilot-line1-round13-iteration-host-live.md`。
Plan 2 零人工全链:needs_changes verdict → 自动追加 round 1(source=
run_loop)→ walk-away 越 gate 续跑 → 自动批准派发回炉 → coder 补
aria-live → 复审 pass → 自动 merge → 干净收官;board review_rounds=1。
Plan 1 对照验证非触发路径。宿主 detached/预算硬界/三态/重启/段首补扫
全过;委托归一化 composite 匹配 live 首验(fail-closed 两次拒未授权段)。
核心工作流"拆解→写→审→回炉→修→终评→合并"自此 live 已验证。发现四项
非阻塞:预算尺度(空轮询烧 wave,动态间隔候选)、只读验证前缀 starter
pack 候选、orchestrator 措辞给 reviewer 台阶会让严格 gate 失效(goal
措辞注意)、审查 worktree 变异残留。**拍板批次(2026-08-01,user 四项全授权)**:①只读前缀 starter pack
已落地(c45aaaed,纯文档,delegation-schema.md 新节);②SQLite 5d 已
落地(b34a9f54,审查 Approve,全量 4830 绿):`events_export_mode`
meta(fail-safe sync)、`storage events-export --confirm`、
`events-export-mode --mode sync|on_demand --confirm`、on_demand 下
append 跳过同锁导出(O(n²) 写消失)、events-diff 前缀比对+export_lag、
rollback 强制先导出、shadow-status 警示 rm state.db 在 on_demand 下
不再无损;scratch 尚未切 on_demand(等下轮 live 观察期)。③round_reviewer 独立角色 + 多 reviewer 串行聚合**已落地**
(spec `docs/superpowers/specs/2026-08-01-review-group-round-reviewer-design.md`,
plan 同名,6 commits 0f28d3db→9b8a9391,全量 4860 绿):user 拍板三决策
(串行叠加 review step / config `[review]` 段 / any-fail-blocks);
`[review].reviewers` 在 `_generate_leader_plan` 出口确定性展开(识别谓词
= reviewers[0] 的 role,跨角色组不误伤 planning step),`review_group.py`
纯模块做展开与聚合,`select_plan_verdict` 成为 `plan_verdict_summary` 与
迭代触发器的**单一来源**:带组标记时只认最新**完整**组(组未齐一律不
判定、不触发——防"先 fail 的成员开一轮、后 fail 的再开一轮"预算双烧),
组级幂等按最后一个成员 reply;`round_reviewer` 换掉迭代复审步的执行者,
追加的复审组自身也是组感知;`verdict_summary.group`
(size/complete/rule/members)与 plan status 的
`review_group`/`review_group_member` 是只读 provenance。**顺带修掉一个
严重既有 bug**(8273d70d):`_dump_config` 白名单式回写会静默吞掉
`[leader.planner]`/`[leader.orchestrator]`(G2,已 live 验证的功能)、
`[daemon]` 与新 `[review]`——`policy set-mode` 一次即丢;现改为保留式
回写。④**Leader 精修回炉已落地**(2026-08-01,spec
`docs/superpowers/specs/2026-08-01-leader-refined-rework-design.md`,plan
同名,3 commits 34a831e5→28bcf56d,全量 4943 绿):
`agentdeck plan rework --plan-id <id> --confirm --refine` 让配置的 Leader
provider 把审查意见提炼成回炉任务。user 拍板两点:**只给显式命令加
`--refine`**(run-loop / --all / --follow / host 一律无 refine 入口,
"run-loop 绝不调用 provider"这条已 live 验证的不变量完整保留)与
**失败回落确定性模板并如实报告**(闭合枚举 `unsupported_provider` /
`provider_error` / `invalid_output` / `state_changed`,命令仍退 0——
迭代不被 provider 抖动阻断)。接线关键:provider 调用在**锁外**
(锁内调 LLM 会阻塞全项目写入),CLI 先锁外纯推导拿 `triggered_by_reply`,
调用+校验后把结果作为 `rework_task_override` 交给 locked writer,writer
锁内**重新推导**且仅在 reply id 一致时采用,漂移则回落模板并记
`state_changed`。三处 provider 实现(CLI 基类 / API 基类 / fake)覆盖全部
provider,支持性用 `getattr` 探测(与 `plan_brief` 同模式)。落地 step 带
`task_source=leader_refined` provenance(模板路径不带该键)。
**G1 frontdesk 增强已落地**(2026-08-01,user 拍板"多路径分类+独立命令
与契约",spec `docs/superpowers/specs/2026-08-01-frontdesk-multiroute-design.md`,
4 commits a59eba37→47e5f2cf,全量 5005 绿):新纯模块 `frontdesk.py`
(闭合路由 plan/run/status/help/skill/memory,确定性三档置信度,零 IO、
不调 provider)兑现 roadmap 一直只做了一半的"分类为候选路径"承诺;
`frontdesk_card` 既有 8 字段**逐字节冻结**、`candidates[]`/`route` 追加在后
(差分验证 18 条消息零 mismatch);新增**纯只读** `agentdeck frontdesk
--message <text>`(连 config 都不加载,零写是结构性的)+ 第 43 个契约
`frontdesk-schema.md` + CLAUDE.md 规则。已知向后兼容瑕疵(已文档化):
`route` 可与 `next_command` 不一致(旧规则冻结所致),契约示例特意选了
该场景。**codex 精修 live 确认已收口**(2026-08-01,证据
`docs/validation/2026-08-01-codex-refined-rework-live.md`):真实
`codex exec` 直接调用 `CodexCliProvider.refine_rework_task()` 返回 316 字符
干净返工正文、零交互记录残留,`validate_refined_task()` 接受并由程序追加
固定尾句;终审遗留的唯一未验证项关闭,两个 CLI backend 的精修路径现在
都已确证。**队列已清空**——剩余全部需要 human:Round 14 的 playwright
授权框、以及新方向拍板。

**CLI provider 失败原因可观测已落地**(2026-08-01,b2ca1635 + 0e799272 +
6b816c26):新纯模块 `providers/cli_failure.py` 按"解析→分类→丢弃原文"
把失败分成闭合枚举 `credits_exhausted/auth_required/model_unavailable/
rate_limited/unknown`,错误与审计带 `exit_code` + `failure_reason`。
**能分类才分类**:`plan_brief` 与 claude planning(stdout 写私有文件,
失败时做尽力而为的有界诊断读)可给出原因;**codex planning 只报退出码**
——它的诊断在 **OS 边界**即丢弃,由对抗性测试
`test_codex_discards_subprocess_diagnostics_at_the_os_boundary` 加固
(我一度改成接管道被它当场拦下并已全部回退)。教训记入 HISTORY:
定向回归务必包含 `test_cli_structured_output.py` 与
`test_provider_openai_compatible.py`。
整体终审 **APPROVE**(1aa256c6 + 3776857f,全量 4866+):终审复现一个
**Critical fail-open**——组内一人 verdict 无效时整组塌缩成"无判定",
另一人的有效 fail 被丢弃且自动合并放行(单 reviewer 下会扣住);根因是
spec 的 `complete=false` 投影被实现丢掉。修复拆成**两个面**:触发面
仍要求整组完成(4000 状态差分证明零行为变化),展示/merge-gate 面走
`latest_group_status()` 对已报到成员聚合并标 `complete=false`,
`_verdict_merge_blocker` 一律扣住自动合并(人类 `worktree merge-plan
--confirm` 永不受 gate)。后续硬化:非法 `overall` 夹到 fail、展示面
不再依赖 approval、配置保留式回写改为"不可表示即报错"并支持 TOML 原生
日期时间。**review group live 验证待下轮 round**:scratch 配
`[review] reviewers = ["reviewer", "planner"]`,确认展开、串行执行、
any-fail 聚合只触发一轮、追加复审组同样成组、组未齐时合并被扣住。
已知非阻塞 follow-up:`_quote_toml` 对含换行的字符串会生成非法 TOML
(既有缺陷,`agent assign-role --role-prompt` 可触达);零 verdict 的
review 组仍沿用"无判定 = 不扣合并"的既有基线(是否收紧待拍板)。

**Review 迭代闭环已落地(2026-07-30 拍板 / 07-31 完成,subagent-driven
开发,11 commits d50df5ee→,全量绿)**:spec
`docs/superpowers/specs/2026-07-30-review-iteration-loop-design.md`,plan
`docs/superpowers/plans/2026-07-30-review-iteration-loop.md`。核心工作流
"Leader 拆解 → coder 写 → reviewer 审 → **fail/needs_changes 自动回炉 →
coder 修 → reviewer 复审** → pass → 自动 merge" 的动态迭代段闭合:纯模块
`review_iteration.py`(触发矩阵 fail-closed + 确定性回炉模板,零 LLM)、
locked writer `append_review_iteration`(唯一写点,已注册
AUTHORITATIVE_STATE_MUTATION_METHODS)、`[autonomous] max_review_rounds`
预算(默认 2,`--max-review-rounds` 覆盖,0=逐字节关闭)、追加 step 走
普通 pending 审批(零新授权面)、run-loop 引擎钩子 + 显式
`plan rework --confirm` 双面、第 42 契约 plan-rework、ProjectView
`review_rounds` + step provenance。四轮 subagent 审查修掉 3 个关键盘面:
①writer 未注册锁定注册表(并发丢写 + 守卫测试红);②**walk-away 链路
断裂**(追加后 gate=needs_human_approval 会终止 follow/host——修复:
"本 wave 追加迭代轮"时继续,有界,gate 诚实性不变);③`--all` pre-gate
skip 吞掉已入账 fail verdict(修复:complete plan 先跑钩子)。G5"gate
complete + 只扣 merge"语义 = 本闭环 `--max-review-rounds 0` 切片(旧测试
已隔离 + 新测试钉住组合默认)。**live 验证待下轮 Line 1 round**:真实
reviewer 打 fail → 自动追加(`plan_rework_appended` source=run_loop)→
下轮自动批准派发 rework → 文件通道回复 → re-review pass → 自动 merge;
与 run-loop-host 断线续跑同场验证。剩余拍板项:SQLite 5d 停同步导出、
round_reviewer 独立角色、多 reviewer 聚合、Leader 精修回炉任务(二期)、
G1 frontdesk 增强。

**Run-loop 背景宿主已落地(2026-07-30,user 拍板 detached 子进程 +
pidfile 单例方案,5 commits be30b457→,全量绿)**:spec
`docs/superpowers/specs/2026-07-30-run-loop-host-design.md`,plan
`docs/superpowers/plans/2026-07-30-run-loop-host.md`。
`agentdeck run-loop-host start|status|stop` 把**未改动**的
`_run_loop_single_wave` 放进 detached 子进程(`start_new_session`,
stdio DEVNULL),解决 round 12 操作者手动重启 `--follow` 段八次的
walk-away 截断痛点。start 四道 gate(--confirm + autonomous + 显式
`--max-waves >= 1` + 已知 plan)+ 活 pid 单例;serve 每 wave 重读
config(approval_mode 离开 autonomous → `policy_revoked` 远程刹车),
SIGTERM 完成当前 wave 再停,`engine_error` 只记异常类型;stop 有界
SIGTERM 绝不 SIGKILL;`.agentdeck/run-loop-host/host.json`(原子替换)
+ host.log(append-only JSONL)+ `run_loop_host_started/stopped` 审计;
闭合 `stopped_reason` 枚举(gate_reached/budget_exhausted/
policy_revoked/signalled/engine_error)单一来源在
`src/agentdeck/run_loop_host.py`;契约
`docs/contracts/run-loop-host-schema.md` + `agentdeck contract
run-loop-host`(index 41 项)。M2 Mission daemon 零触碰(diff 审计
零输出)。**live 验证待下轮 Line 1 round**:start 宿主→断开客户端→
确认 wave 继续、status/stop 行为、host.log 可读性。剩余拍板项:
SQLite 5d 停同步导出、round_reviewer 独立角色、G1 frontdesk 增强。

## Active slice — G2 planner/orchestrator split (自主 loop 推进中)

2026-07-28 user 拍板开工 G2(北极星第二圈深度差距 #2)。设计已冻结:
`docs/superpowers/specs/2026-07-28-g2-planner-orchestrator-split-design.md`
(S1 完成)。切片顺序 S2 config+数据模型(**完成**:
`LeaderSubroleConfig` + `[leader.planner]`/`[leader.orchestrator]`
解析 fail-closed + `resolved_*_backend()` 回落 helper,10 例 TDD)→
S3 planner 段(**完成**:`providers/planner_brief.py`——
`planner-brief/v1` validator fail-closed、snapshot+content_hash、
JSON-only 无 agent 指派 prompt 模板,22 例 TDD,暂无消费方)→ S4
orchestrator 段(**完成**:`orchestration/split_planning.py`
`run_split_planning()` 两段串联落单条 plan、`SplitPlanningError`
分段失败语义;`LeaderPlanRequest.planner_brief` 可选透传;fake
provider `plan_brief` 先行——真实 provider 的 brief 能力与 prompt
接线留待后续)→ S5a CLI 接线(**完成**:`_generate_leader_plan`
共享 helper 接进 leader plan / run --task / leader chat 三路径,
override 旁路拆分,`record_plan` 落 planner_backend/
orchestrator_backend/planner_brief 三 provenance,失败审计带
stage)→ S5b 只读暴露+契约同步(**完成**:plans.items 投影 + trace
plan 三键 null 占位、`PROJECT_VIEW_PLAN_ITEM_FIELDS`/validator/
example fixture、project-view-schema.md、README)→ S6
acceptance_criteria 只读展示(**完成**:`leader_review()` 出口注入 +
`run_progress` 顶层镜像,review/run contract 字段表、validator、
example、文档同步)。

**G2 S1–S6 + 后续 A/B/C 全部完成。** `[leader.planner]`/
`[leader.orchestrator]` 现可配置任意真实 provider 组合(live 双
backend 验证仍待 user 在场)。

**当前活跃:G5 量化验收(user "继续" 授权,2026-07-28,自主 loop)。**
设计已冻结:`docs/superpowers/specs/2026-07-28-g5-quantified-review-design.md`
(V1 完成)。切片 V2 verdict schema+解析器(**完成**:
`review_verdict.py`——`review-verdict/v1` fail-closed validator、
`parse_verdict_line` 零行=None、`align_verdict_with_criteria` 产出
verdict_summary 形状,29 例 TDD,暂无消费方)→ V3 reply 通道入账
(**完成**:`record_reply` 单点解析覆盖四调用方,有效落 reply.verdict
+ `review_verdict_recorded`,无效不阻断入账 + `review_verdict_invalid`,
ProjectView `replies.items[]`/trace reply 投影 null 占位暴露,契约与
文档同步)→ V4 review/summary/run 摘要面(**完成**:
`plan_verdict_summary()` 最新有效 verdict 对齐 criteria,
`leader_review` 出口注入 + run_progress/summary 镜像,共享
`_validate_verdict_summary` 进三 validator,契约/example/README
同步)→ V5 review prompt 注入(**完成**:`build_dispatch_prompt`
可选 `review_criteria` 段 + approval dispatch 在既有 review-step
信号(base_branch 非 None)且 plan 有 criteria 时注入;非 review
step/无 criteria/直接 dispatch 逐字节不变)。

**G5 V1–V5 全部完成。** 待 human 拍板的 STOP fork:verdict 驱动
gate(`overall=fail` 阻止 merge-on-complete/下一轮)、round_reviewer
独立角色与 provider binding、多 reviewer 聚合。

**Round 11 live PASS(2026-07-28 晚,user 授权操作者驱动)**:
`docs/validation/2026-07-28-copilot-line1-round11-g2g5-live.md`——
G2 双 backend(DeepSeek brief 一次过 validator + claude-fable-5 拆
步)与 G5 verdict 全链(注入→reviewer 自然输出 pass/92→三面摘要)
一次通过;走开链路四连 PASS;影子零 diff 3/3。live 发现:
①跨 provider model 回落陷阱(**已闭环** 5f4957fc,config 加载
fail-closed);②第五类 MCP tool 授权框(**已落地**,见下段);
③follow 段首补扫框(**已闭环** af2a5724)。scratch config 已留
双子段(orchestrator 显式 model=claude-fable-5)。硬承诺:所有
gate 行为零变化,无 `verdict:` 行时逐字节不变;verdict 驱动 gate、
round_reviewer 独立角色、多 reviewer 聚合列 STOP fork。其余待排期或
human 拍板:
②G5 量化验收(round_reviewer、按 acceptance_criteria 打分);
③live 双 backend 验证(需 user 在场跑 Line 1 round);④spec 标注的
STOP fork(orchestrator 工具调用、briefs 独立集合、G1 frontdesk
增强、SQLite 5c cutover)。硬兼容承诺:`[leader.planner]`/`[leader.orchestrator]`
子段都缺省时行为逐字节不变。STOP fork(等 human):orchestrator 工具
调用、briefs 独立集合、G1 frontdesk 增强、SQLite 5c cutover。

**MCP tool 委托 scope 已落地(2026-07-29/30,user 拍板 (server,tool)
粒度 + 单表 kind 判别方案 A,5 commits af7023ed→5bef5457,全量 4732
绿)**:spec `docs/superpowers/specs/2026-07-29-mcp-tool-delegation-scope-design.md`,
plan `docs/superpowers/plans/2026-07-29-mcp-tool-delegation-scope.md`。
`delegations[]` 加 kind(缺省读作 `command_prefix` 兼容旧记录 |
`mcp_tool` 带 mcp_server/mcp_tool),`delegation grant` 加
`--mcp-server/--mcp-tool` 互斥形态(grant 时按提取器字符集
`[A-Za-z0-9_-]+` 校验,防"grant 成功但哨兵永不放行"的静默空转),
fail-closed 提取 + (server,tool) 精确等值匹配,boxes/release-box/
watch/run-loop --release-boxes 共享路径全覆盖,`auth_box_released`
增 box_kind/mcp_server/mcp_tool/waiting_hint 审计证据。三轮
subagent 审查共修掉 6 个复现级 fail-open 盘面,最终不变量比原设计
更强:双提取器都经 `_pending_box_region` 区域锚定到当前挂起框
(陈旧框句子/`$ ` 行永不外溢),MCP 句尾 `?` 必须紧跟活选择器
`›1.`(仅当预选项=选项 1——裸回车真正会按的那一项——才可能放行)。
放行不变量不变:裸回车、绝不选 2/3、未命中绝不代按、逐次审计;
指引=只对只读性 MCP 工具 grant(hover/press_key/screenshot 类),
绝不对 navigate/fill/evaluate_script 类。

**Round 12 live PASS(2026-07-30,user 在场逐框授权,证据
`docs/validation/2026-07-30-copilot-line1-round12-mcp-delegation-live.md`)**:
MCP 委托全链 live 首验通过——真实框逐字捕获暴露转写措辞两处不符
(tool 名带引号、句子与选择器间隔参数行),发布版提取器 36 轮
fail-closed 零误按(最强安全证据),TDD 修复(b14f56ed:真实框式
正则 + gap 桥接排除 + 提取窗改全捕获 pending-box region + watch
pane 丢失容错)后 `boxes watch` 累计放行 4 框(hover×3+press_key×1,
两条委托均命中),审计五字段齐全。载体任务 F2 走开环第五连 PASS
(G2 双 backend、G5 verdict pass/92 4/4、verdict gate 放行自动
merge 4da4f73、文件通道 3/3、影子/导出双 in_sync、全量 4735 绿)。
live 发现:shell 包装(env 前缀/for 循环)逃逸前缀委托=委托匹配
归一化新拍板项候选;codex spawn 后立即 dispatch 有启动竞态;
`--virtual-time-budget` 无头 Chrome 会挂住。剩余拍板项:SQLite 5d
停同步导出(建议先攒导出零漂移证据)、round_reviewer 独立角色、
G1 frontdesk 增强、daemon 背景续跑收拢。

**委托匹配归一化已落地(2026-07-30,user 拍板"全套:env+循环+多段链"+
方案 A 独立纯模块,4 commits 46e91a36→31740761,全量 4761 绿)**:spec
`docs/superpowers/specs/2026-07-30-delegation-match-normalization-design.md`,
plan `docs/superpowers/plans/2026-07-30-delegation-match-normalization.md`。
新纯模块 `src/agentdeck/delegation_match.py`(硬拒绝扫描→引号感知顶层
拆段→控制词/重定向/env 剥离→固定胶水白名单 v1→逐段覆盖匹配),
`cli.py` 新 wrapper `_match_delegation_with_provenance` 按形态路由
(`is_composite_command`:多段/含重定向/解析不了 → 只走归一化;干净单一
命令仍走原平前缀臂,`_match_active_delegation` 逐字节不变),三面 + 两处
`auth_box_released` 审计增 `match_kind`(prefix|composite|mcp_tool|null)
和 composite 专属 `matched_segments[]`。语义:每段必须命中该 agent 活跃
前缀或属胶水,且至少一段命中真实委托;`node tests/x; rm -rf /` 因 rm 段
不沾而拒。**两轮 subagent 审查共修掉 4 个 fail-open**:①计划原定"平前缀
先行"本身不安全(裸 startswith 会让危险尾段搭首段的车放行,实现者按 spec
危险边界条款自主纠正为按形态路由);②非 2 号 fd 前缀重定向(`1>>` `3>`
`10>`)漏过 /tmp 约束;③shell 认的词粘连重定向(`echo foo>/etc/evil`)
形如普通参数;④单一命令含重定向不受约束(先于本功能存在)。现所有形态
一致受 `/tmp` 约束,`>` 只允许出现在被识别并校验的重定向里。round 12
三样本:env 前缀/for 循环命中,多段链因 `node --check` 段无委托仍人工
(归一化绝不放宽前缀含义,人类可显式补 grant)。**live 验证待下轮 round**
(观察 for 循环/env 前缀框是否自动放行、审计 provenance 是否可读)。

背景:Line 1 走开链路 round 8–10 三连 PASS;SQLite 5a/5b 落地
(events 双写 + events-diff),影子零 diff 证据 2/2,5c 等拍板;GUI
三刀落地实战可用;缺陷池 F2–F4 低级项 + **F5(新,2026-07-28)**:
`test_m2c_live_acceptance.py::test_probe_fast_exit_after_scope_seal_failure_emits_no_signal`
偶发 flaky——断言 `spawned[0].returncode == 0` 对 fast-exit 子进程存在
未 wait 的竞态,负载高时 returncode 尚为 None;干净 main 树可复现失败、
单测重跑即过,与 G2 改动无关。测试属 M2c 冻结证据区 harness,是否加
`wait(timeout=...)` 稳定化待 human 拍板。每轮 live 收尾三连检
(shadow-diff + events-diff + 测试)照旧。

## Active goal — Line 1 co-pilot route: knob-by-knob convergence to the north star

2026-07-23 路线转向（human 批准）后，活跃开发线是 **Line 1 co-pilot 自然循环
路线**：真实 API Leader + 真实 coding-agent Worker 在已有自然循环上跑通，再
逐个拧旋钮泛化。设计与关系说明见
`docs/superpowers/specs/2026-07-23-copilot-natural-line-1-design.md` 与
`docs/roadmap/product-north-star.md` 的 "2026-07-24 route status" 节。

当前状态（2026-07-24 夜）：

- Line 1 round 2 真实全环 PASS（DeepSeek Leader + codex×2 + Claude Code，
  `docs/validation/2026-07-24-copilot-line1-live-round2-iae-homepage.md`）；
  加固切片 A–F 全部落地（spawn tiled 布局、waiting_for_input、review 部分派发
  守卫、文件通道回复、意图路由劫持修复族）。
- Round 3 live 返工半途：任务已派给 coder（`msg_dbbc6d7aa142`，scratch 项目
  `~/Desktop/agentdeck-live-scratch`），待 user 在场授权 capture-reply 回收、
  可选 reviewer 复核、summary 与 finding 文档。
- 北极星差距总账：`docs/roadmap/2026-07-24-north-star-gap-review.md`；
  隔夜自主收敛循环计划（文档 reconcile、整计划一次批准、reply 文件就绪
  显性化、worker released 阶段、任务级 worktree spec）：
  `docs/superpowers/plans/2026-07-24-north-star-gap-loop.md`。

下一刀（按 gap-review 优先序）：Line 1 确认粒度旋钮（整计划一次批准）→
自主度旋钮（reply 文件通道就绪感知）→ G4 worker released 阶段 →
任务级 worktree spec（实现待 user 拍板）。SQLite 前向移植与 GUI/web 层
是显式 fork，等 user 决策。

下方从 P0 exit review 起的全部内容保留为历史证据层，不再是活跃调度门。

## Superseded goal — AgentDeck P0 exit review (2026-07-18)

The evolutionary-kernel route remains authoritative: preserve and converge
the existing Conversation, ProjectView, daemon, ledger, approval, ACP/tmux,
skill, memory, and learning foundations around one durable Mission authority.
The historical M2c mega-harness is evidence, not the active development route
or a release veto.

## P0 exit status

P0 documentation, inventory, migration design, validation strategy, and
deterministic baseline evidence are complete and frozen for human review. The
seven durable P0 documents are:

- [V1 product requirements](../product/agentdeck-v1-prd.md) — product promise,
  user journey, authority boundary, non-goals, and V1 acceptance contract.
- [V1 kernel reset architecture](../architecture/agentdeck-v1-kernel-reset.md)
  — unified domain model, ProjectDaemon authority, recovery, adapters,
  Verification, ProjectView, and governed learning boundaries.
- [V1 state migration](../architecture/agentdeck-v1-state-migration.md) — the
  Task 4 decision that `.agentdeck/state.db` becomes the sole structured-state
  authority through preview, backup, verified cutover, and bounded rollback;
  filesystem content remains outside SQLite.
- [Legacy capability inventory](../migrations/2026-07-17-legacy-capability-inventory.md)
  — Task 5 retain/refactor/compat/archive/remove/missing classification. It
  preserves useful Conversation, daemon, ledger, governance, ACP/tmux,
  ProjectView, skill, memory, and learning behavior while removing legacy
  authority assumptions rather than performing a greenfield rewrite.
- [M2c test migration matrix](../migrations/2026-07-17-m2c-test-migration-matrix.md)
  — Task 6 maps useful M2c safety evidence into deterministic, conformance,
  real-smoke, and Golden Mission owners; M2c is not a release veto or retry
  target.
- [V1 validation strategy](../validation/agentdeck-v1-validation-strategy.md)
  — Task 7 defines the deterministic commit gate, shared adapter conformance,
  bounded real smoke, Golden A/B, rerun safety, and release evidence rules.
- [P0 deterministic baseline](../validation/2026-07-17-p0-baseline.md) — Task 8
  records Python 3.12.13, compile exit 0, focused `304 passed` in 33.81s pytest
  time / 34.66s wall time, and default full `4461 passed, 3 skipped` in
  227.70s pytest time / 228.68s wall time.

The pre-freeze scope audit from `f3968720` through `118d0075` shows zero changes
under `src/agentdeck`, `tests`, or `.agentdeck`. Task 9 then froze the P0 exit
documents in docs-only commit `3d564ddc`. The continuation-alignment follow-up
is also docs-only by exact diff. Task 10 must freshly run
`git diff --name-only f3968720..HEAD -- src/agentdeck tests .agentdeck` and
require zero output; no not-yet-known final P0 SHA is embedded here.

P0 did not run a real provider, ACP adapter, tmux session, preflight, or live
Mission, and it did not merge or push. The deterministic daemon tests used
only repository fakes. These facts prove the P0 documentation baseline only;
they do not claim V1, real-adapter, Golden, migration-runtime, or release
readiness.

## Next gate

A human reviews the frozen P0 evidence. P1 implementation remains locked and
is not authorized by P0 completion. The program-level P1 specification already
defines its intended scope; only after explicit human approval may any newly
unresolved product choice use `brainstorming`, and the separate P1 Durable
Mission Kernel task-level TDD plan must then be created with `writing-plans`.
Neither that task-level plan nor P1 implementation is written or executed in
this P0 exit step.

The historical M2c attempts below remain immutable evidence. They are no
longer the active development route, a release veto, or authority to rerun a
consumed live node.

## Superseded M2c goal — bounded sequential permission acceptance

> Everything under this superseded heading is retained only as historical M2c
> evidence. Later same-level headings in that retained material do not override
> the active P0 authority at the top of this file.

At the time of this retained M2c evidence, the authoritative route was the
approved bounded sequential permission design and plan dated 2026-07-17. It superseded the historical next
actions below without rewriting their evidence. The exhausted `e83dcc48...`
live run showed two permission base records in one step-1 Claude ACP attempt;
the harness incorrectly assumed the first confirmation completed
implementation and the second permission belonged to revision. AgentDeck's
product communication layer already supports multiple sequential permissions
per attempt, so this blocker is a harness cardinality defect, not a newly
proven ACP product defect.

Tasks 1–8 of the approved plan are implemented on the isolated feature branch.
The harness now derives effective state from append-only transitions, validates
exact Mission/attempt/session/turn/transport lineage, drives one to four
permissions per Claude attempt through separate public preview/confirm
transactions, requires reply plus canonical handoff before stage progression,
holds the first revision permission unchanged through takeover/return-control,
and validates four-stage completion with two to eight total permissions. The
focused integration passes `27 passed, 345 deselected in 2.22s`; confirmation,
diagnostic, lineage, driver, and four-stage tests are independently GREEN.
Compile, diff, and `src/agentdeck/**` zero-change scope audits pass.

Task 10 deterministic verification is complete. Focused sequential-permission
coverage passes `50 passed, 322 deselected in 0.46s`; complete non-live M2c
passes `370 passed, 2 skipped in 102.28s`; product/Conversation/contract/
provider regressions preserve `851 passed in 4.51s`. Conda compile, diff,
`src/agentdeck/**` zero-change, tracked-runtime-state, process, tmux, worktree,
and leakage/residue audits pass. A requirement-by-requirement local review
found no issues and confirmed exact preview/confirm/effect authority,
attempt-local sequential progression, reply-plus-handoff stage gates,
takeover/return-control exclusion, transition-derived effective state, closed
diagnostics, and no timeout/retry/fallback/global-setting/product-source
change. The Task 10 candidate is frozen at
`df25532d0bd4fb9c8dd57fd119607a05411d11db`. Two fresh detached-worktree full
suites at that exact SHA pass serially with identical counts: `4461 passed, 3
skipped in 250.94s` and `4461 passed, 3 skipped in 245.06s`. The skips are the
opt-in real ACP, designated preflight, and real four-stage nodes. Both
worktrees were removed and process/daemon/ACP/tmux/worktree residue audits are
empty. The explicit installed-input audit preserved Leader `gpt-5.5`,
`m2c-tool-authority/v3`, `m2c-live-preflight/v6`, digest
`sha256:b194c3b4ccbfa3ba2b534bf9cb51e59ecbc077e2576c6eea8ba343f26cc83ffa`,
`loader_failures=[]`, and `claude_auth_ready=true` without PATH substitution.
The separately authorized strict v6 preflight then ran exactly once at frozen
`df25532d0bd4fb9c8dd57fd119607a05411d11db`, Leader `gpt-5.5`, and the exact
authority digest. It passed `1 passed in 17.39s` with `ready=true`,
`blockers=[]`, `failures=[]`, schema `m2c-live-preflight/v6`, and authority
`m2c-tool-authority/v3`. The detached checkout was removed; process, daemon,
ACP, tmux, temporary-root, worktree, and repository-status audits are empty.
This preflight authority is consumed and must not be rerun. The separately
authorized real four-stage Mission then ran exactly once on the same frozen
SHA/model/digest and failed `1 failed in 69.69s` with the closed result
`stage=live_acceptance`, `code=live_setup_failed`. The outer guarded wrapper
removed the disposable project and intentionally suppressed the original
unexpected exception; no Mission/attempt/permission cardinalities were
available, so this evidence does not identify ACP, Leader, or a Worker as the
root cause. The detached checkout was removed and process, daemon, ACP, tmux,
live-root, worktree, and repository-status audits are empty. This live
authority is consumed and must not be retried. M2c remains **BLOCKED** and M3
remains locked. The only next gate is the smallest evidence-led
brainstorming/spec/plan cycle that closes this `live_setup_failed`
observability boundary before any new deterministic RED/GREEN and freeze. Do
not increase timeouts, retry, auto-approve, merge, push, install, change
authentication/global settings, or begin M3.

The separately authorized live Mission on frozen
`79d8160eb60ad4e8bfb37ff43615f099afd9edc5`, Leader `gpt-5.5`, and authority
digest `sha256:b194c3b4ccbfa3ba2b534bf9cb51e59ecbc077e2576c6eea8ba343f26cc83ffa`
ran exactly once and failed `1 failed in 110.98s` as
`first_attempt_terminal_contract_invalid`. Step 1 reached a succeeded Claude
ACP attempt and one validated Worker reply, but zero permission requests and
zero handoffs; no later stage was admitted. The disposable checkout/project
and all process, daemon, ACP, tmux, worktree, and temporary-root residues were
removed. This SHA/model/digest authority is exhausted and must never be
retried.

Root-cause evidence shows that the installed Claude ACP adapter derives session
permission mode from merged Claude settings. The existing Phase 2 real ACP
fixture pins disposable project-local `permissions.defaultMode=default`, while
the M2c harness did not, allowing an unrelated user-level permissive mode to
bypass the permission bridge. No user settings content was read or changed.
The approved harness-only correction now creates exact bytes
`{"permissions":{"defaultMode":"default"}}\n` at
`.claude/settings.local.json`, with `.claude` mode `0700` and the regular file
mode `0600`. Exclusive/no-follow creation returns a path-free identity/content
seal. The harness revalidates it before Mission creation, around both human
permission confirmations, around takeover/return-control, and after Mission
completion. Pre-existing paths and content/mode/inode/kind/symlink/directory/
extra-entry drift fail closed as `claude_permission_settings_invalid`; the
helper never reads `Path.home()` or `CLAUDE_CONFIG_DIR`.

The independent RED commit reproduced the missing file. GREEN then exposed
and fixed one FIFO edge where file kind had to be rejected before `open()`.
Focused permission/setup coverage passes `28 passed`; complete non-live M2c
passes `337 passed, 2 skipped in 95.98s`; product/Conversation/contract/provider
regressions pass `851 passed in 4.28s`. Compile, diff, `src/agentdeck/**`
zero-change, process, worktree, temporary-root, and tracked-runtime-state audits
pass. The harness-only implementation is frozen at
`e83dcc482d2403f613485d06eff75ff99ffe733f`. Two fresh complete suites at that
exact SHA and a new installed-input audit are required before one newly
authorized strict v6 preflight. No new
preflight/live authority exists yet. M2c remains **BLOCKED** and M3 remains
locked.

Both authoritative full suites now pass in separate fresh detached worktrees
at frozen `e83dcc48...`: `4428 passed, 3 skipped in 267.02s` and `4428 passed,
3 skipped in 251.95s`. They were run serially through the required `agentdeck`
conda environment; the three skips were exactly the opt-in real ACP,
designated preflight, and real four-stage Mission nodes. Two earlier parallel
direct-interpreter probes were discarded as evidence after traceback proved
their child PATH omitted the conda `agentdeck` command and concurrent load
exceeded fixed five-second launcher bounds; they did not change the frozen
checkout. All four temporary worktrees and their process/daemon/ACP/temp roots
were removed.

The follow-up read-only input audit resolved the same explicit local Codex,
local Node, Homebrew tmux, Claude, and metadata-selected Claude ACP entrypoint
as the last ready authority. It reconstructed authority v3 digest
`sha256:b194c3b4ccbfa3ba2b534bf9cb51e59ecbc077e2576c6eea8ba343f26cc83ffa`
with no loader failures; closed Claude auth readiness remains exit-zero and
logged-in. A conda-PATH tmux candidate produced a different digest and was
rejected rather than silently substituted. The next gate is one newly
authorized strict v6 preflight naming frozen SHA `e83dcc48...`, Leader
`gpt-5.5`, and the exact `b194c3...` digest. At that checkpoint, no designated
preflight had run for this candidate.

The separately authorized designated strict v6 preflight then ran exactly once
on frozen `e83dcc482d2403f613485d06eff75ff99ffe733f`, Leader `gpt-5.5`, and
authority digest
`sha256:b194c3b4ccbfa3ba2b534bf9cb51e59ecbc077e2576c6eea8ba343f26cc83ffa`.
It passed `1 passed in 16.57s` with `ready=true`, `blockers=[]`, `failures=[]`,
`m2c-live-preflight/v6`, and `m2c-tool-authority/v3`. The detached checkout was
removed; process, daemon, ACP, worktree, and temporary-root audits were empty.
This preflight authority is consumed and must not be rerun. One new, separate
human authorization naming this same frozen SHA, Leader model, and exact digest
is now required for the sole real four-stage Mission. M2c is not yet PASS and
M3 remains locked.

That separately authorized real Mission ran exactly once and failed `1 failed
in 252.35s` as `third_stage_safe_window_timeout`. Closed durable evidence at
the terminal showed one step-1 `claude-worker` ACP attempt in `ambiguous` /
`acp_prompt`, two permission requests both still `pending`, zero validated
Worker replies, and zero handoffs. No later attempt was admitted. This does not
match the harness assumption that the first explicit confirmation completes
the implementation attempt and that the second permission belongs to the
revision attempt. The detached checkout and disposable project were removed;
process, daemon, ACP, tmux, worktree, and temporary-root audits were empty.
This SHA/model/digest live authority is exhausted and must not be retried.

The next action is systematic root-cause analysis of permission request
lineage and confirmation selection for multiple sequential ACP permissions in
one attempt. No timeout increase, blanket approval, retry, evidence rewrite,
or product-source change is authorized. M2c remains **BLOCKED** and M3 remains
locked pending a new minimal brainstorming/spec/plan/TDD/freeze cycle.

The exhausted authority at frozen `284d8f62...` reached Mission Preview and
daemon admission, then the live harness read the consume ledger before the
synchronous confirmation turn had completed. Product tracing proved the event
commit and prompt 3 are causally after `preview_executor` returns; no Worker
attempt or effect occurred.

The approved minimal repair changes only
`tests/test_m2c_live_acceptance.py`: after daemon admission, the harness waits
for the existing bounded third prompt and then still requires exactly one
Mission-specific `conversation_preview_consumed` event. Sleep, timeout
inflation, retry, fallback, event fabrication, and product-source changes were
rejected.

The new implementation is frozen at
`690f0baf6efad6ad5608edaf10cf396da2729521`. RED reproduced the exact race (`1
failed, 291 deselected in 3.92s`); confirmation/cardinality GREEN passed `9
passed`; focused authority/preview coverage passed `55 passed`; complete
non-live M2c passed `293 passed, 2 skipped in 86.41s`; product regressions
passed `851 passed in 4.23s`. Compile, diff, current-slice
`src/agentdeck/**` zero-change, leakage, process, and temporary-root audits
passed. At that freeze point, the two detached-worktree full suites remained.
No new real preflight, live Mission, provider, ACP/tmux session, daemon,
install, login, merge, or push had run. M2c remains **BLOCKED** and M3 remains
locked.

Both fresh detached-worktree suites on frozen `690f0baf...` now pass: `4384
passed, 3 skipped in 222.29s` and `4384 passed, 3 skipped in 211.96s`. The skips
were exactly the opt-in real ACP, designated v5 preflight, and real four-stage
Mission nodes. Both worktrees were removed; the frozen implementation remained
unchanged; process, daemon, worktree, and temporary-root audits were empty. The
next gate is a read-only installed-input audit followed by one new real v5
preflight using Leader `gpt-5.5`.

That audit found no drift. The new designated v5 preflight ran exactly once on
frozen `690f0baf...` and passed `1 passed in 15.92s` with `ready=true`, empty
blockers/failures, authority v3, and digest
`sha256:b194c3b4ccbfa3ba2b534bf9cb51e59ecbc077e2576c6eea8ba343f26cc83ffa`.
Its detached worktree was removed and residue audits were empty. The one
same-SHA/model/digest real four-stage Mission is now the only remaining M2c
gate for this candidate.

That live node ran exactly once and failed `1 failed in 224.33s` with
`code=first_permission_timeout`. Preview consumption and prompt-3 convergence
passed; one step-1 `claude-worker` ACP attempt existed, but it reached durable
`attempt_state=ambiguous` with zero permission requests, replies, or handoffs.
The harness waited the full first-permission bound instead of terminalizing on
that durable attempt state. The checkout and disposable project were removed;
process, daemon, ACP, tmux, worktree, and temporary-root audits were empty.
This authority is exhausted and will not be retried. The next action is a new
minimal root-cause/spec/plan/TDD/freeze cycle around first-attempt ambiguous
terminal observability; M2c remains **BLOCKED** and M3 remains locked.

That terminal-observability candidate is now frozen at
`3b2b3ae18dec745e56ff1920c3a401c9518515ec`. It changes only the live harness:
pending permission returns normally, while exact terminal attempt state maps to
a finite leak-free stage. RED failed because the observer was absent; focused
GREEN passed `55`; strict/package/launcher/live aggregate passed `109`;
complete non-live M2c passed `310 passed, 2 skipped in 80.97s`; product
regressions passed `851 passed in 4.48s`. Compile, diff, current-slice source,
leakage, process, daemon, ACP, worktree, and temporary-root audits passed. Two
fresh complete suites remain before a new real preflight.

Both complete suites on frozen `3b2b3ae1...` now pass: `4401 passed, 3 skipped
in 207.30s` and `4401 passed, 3 skipped in 210.93s`. Only the three explicit
real nodes skipped. Both worktrees were removed; frozen implementation files
were unchanged; process, daemon, ACP, worktree, and temporary-root audits were
empty. A fresh installed-input audit and one new v5 preflight are next.

The audit found no drift. The designated v5 preflight ran exactly once on
frozen `3b2b3ae1...` with Leader `gpt-5.5` and passed `1 passed in 15.55s` with
ready authority v3 and empty blockers/failures. The digest is
`sha256:b194c3b4ccbfa3ba2b534bf9cb51e59ecbc077e2576c6eea8ba343f26cc83ffa`.
The checkout was removed and residue audits were empty. One same-authority live
Mission is the next gate.

That live node ran exactly once and failed `1 failed in 48.97s` as
`first_attempt_acp_prompt_ambiguous`. It proved ACP session admission succeeded,
then the first `prompt()` terminalized before permission with one durable
ambiguous attempt and zero permissions, replies, handoffs, or effects. Cleanup
and residue audits were empty; this authority is exhausted. The next minimal
cycle must classify the safe underlying ACP prompt failure (for example closed
timeout/EOF/protocol/process/auth categories) without retaining stderr, Prompt,
paths, or provider output. M2c remains **BLOCKED** and M3 remains locked.

The follow-up read-only auth audit found the concrete external blocker: the
exact configured Claude CLI currently reports `loggedIn=false`,
`authMethod=none`, and no supported Anthropic API/auth/OAuth environment
credential is present. ACP session admission therefore succeeds locally, but
the first provider prompt has no usable authentication and terminalizes before
permission. No account identity, token, config content, login, or global change
was performed. Human Claude login is now required. After login, continue with
an auth-readiness preflight design/TDD cycle so logged-out state becomes a
preflight blocker instead of consuming live authority.

That harness-only correction is now implemented under strict preflight v6.
The exact sealed Claude executable runs only bounded `auth status --json`;
readiness requires exit-zero `loggedIn=true`, while every logged-out, malformed,
duplicate-key, missing/wrong-typed, or nonzero result becomes the closed
`claude/auth-status/claude_auth_unavailable` failure. No raw response, account
field, environment value, secret, or path is projected. Authority remains v3
and its digest is independent of mutable login state. RED reproduced the v5
false-ready result; focused v6/auth GREEN selected 11 cases and passed. The
complete deterministic/full verification and frozen SHA remain next. Human
login is still required before a single new real v6 preflight; old v5 evidence
cannot authorize it. M2c remains **BLOCKED** and M3 remains locked.

The auth-readiness implementation is frozen at
`79d8160eb60ad4e8bfb37ff43615f099afd9edc5`. After correcting one test-only
single-character `repr` assertion collision, a fresh complete M2c run passed
`320 passed, 2 skipped in 119.98s`; product/Conversation/contract/provider
regressions passed `851 passed in 4.36s`. Compile, diff, current-slice
`src/agentdeck/**` zero-change, durable leakage, process, worktree, and
temporary-root audits passed. The next deterministic gate is two complete
suites in fresh detached worktrees at that exact SHA. No real v6 preflight or
live Mission has run for this candidate.

Both fresh complete suites at frozen `79d8160e...` now pass: `4411 passed, 3
skipped in 259.45s` and `4411 passed, 3 skipped in 256.80s`. The skips were
exactly the three opt-in real nodes. Both detached worktrees were removed;
process, daemon, ACP, worktree, and temporary-root audits were empty. Human
Claude login is now the only prerequisite. After login, re-audit the exact
installed inputs and run one newly authorized strict v6 preflight with Leader
`gpt-5.5`; only a ready v6 result may support a separate one-shot live Mission.
M2c remains **BLOCKED** and M3 remains locked.

Claude authentication has now been restored and verified only through closed
status fields. The installed-input audit reconstructed unchanged authority v3,
Leader `gpt-5.5`, preflight v6, all five logical inputs, metadata-selected
`dist/index.js`, and digest
`sha256:b194c3b4ccbfa3ba2b534bf9cb51e59ecbc077e2576c6eea8ba343f26cc83ffa`.
The one designated v6 preflight on frozen `79d8160e...` then passed `1 passed in
16.27s` with `ready=true` and empty blockers/failures. Its checkout and all
audited residues were removed. That preflight may not be rerun. One separate
human authorization naming the frozen SHA, Leader `gpt-5.5`, and exact digest
is required for the sole real four-stage Mission. M2c is not yet PASS and M3
remains locked.

That live Mission ran exactly once and failed `1 failed in 110.98s` as
`first_attempt_terminal_contract_invalid`. It reached a succeeded first
`claude-worker` ACP attempt and one Worker reply, but produced zero permission
requests and zero handoffs; later stages were not admitted. Cleanup and all
residue audits were empty. This SHA/model/digest authority is exhausted.

The installed adapter and existing Phase 2 real ACP test identify the missing
deterministic input: adapter permission mode is resolved from merged Claude
settings, while the Phase 2 disposable project explicitly pins
`.claude/settings.local.json` to `permissions.defaultMode=default` to prevent a
user-level permissive/auto mode from bypassing permission bridging. M2c never
writes or seals that project-local setting. No user configuration content was
read or changed. The next action is a minimal brainstorming/spec/plan/TDD cycle
for project-local Claude permission-mode authority. M2c is not PASS and M3
remains locked.

### Historical M2c authority cycles

Before consuming the authorized designated preflight for frozen implementation
`fda1a69194e67b50afe0c2b9f4e7f29c195af400`, a read-only command audit found a
specification defect: installed
`@agentclientprotocol/claude-agent-acp@0.58.1` declares its official executable
as `bin["claude-agent-acp"] = "dist/index.js"`, while the frozen M2c authority
hard-codes nonexistent `dist/claude-agent-acp`. Synthetic packages had copied
the same incorrect assumption, so deterministic and full-suite verification
could not reveal the real-package mismatch.

No designated pytest node, provider, ACP/tmux session, daemon, install, login,
or global change ran; the authorization was not consumed. The human approved a
metadata-bound correction at
`docs/superpowers/specs/2026-07-17-m2c-acp-package-entrypoint-authority-design.md`.
It derives and seals the official npm bin entrypoint, binds its canonical
relative path into `m2c-tool-authority/v2`, and advances the strict designated
response to `m2c-live-preflight/v4`. M2c remains **BLOCKED** and M3 remains
locked until a new RED/GREEN, frozen SHA, double full suite, real preflight,
and real four-stage Mission all pass.

The metadata-bound deterministic implementation is now present on this feature
branch: official object/string npm bin selection, duplicate-safe bounded JSON,
strict package-relative path validation, full package/runtime drift sealing,
explicit environment binding, authority v2, preflight v4, and the controlled
Node launcher all use the same selected entrypoint. Focused RED first proved
the real mismatch (`3 failed`); GREEN and safety/loader/launcher matrices then
passed `16`, `31`, and `21 passed` respectively, with the one real designated
node still skipped. The wider checks now pass: focused authority/package/launcher
coverage is `61 passed, 1 skipped in 20.78s`, complete non-live M2c is `266
passed, 2 skipped in 64.78s`, and product/Conversation/contract/provider
coverage is `851 passed in 4.91s`. Compile, diff, `src/agentdeck/**` zero-change,
durable-wording, process, and temporary-root audits passed. This documentation
commit freezes the implementation; two fresh detached-worktree full suites on
its unchanged SHA remain before the one real v4 preflight. No real preflight,
live Mission, provider, installed ACP/tmux execution, or daemon has run.

The new implementation authority is frozen at
`582fc2c7f3b344b5310d254d017e461d68f806f6`. Two fresh detached worktrees on
that unchanged SHA passed the complete suite: `4357 passed, 3 skipped in
199.07s` and `4357 passed, 3 skipped in 200.45s`. The skips were exactly the
opt-in real ACP, designated M2c preflight, and real four-stage Mission nodes.
Both worktrees were removed; implementation/SOP remained byte-unchanged after
freeze; process and temporary-root audits were empty. The next action is the
one real v4 read-only preflight using Leader `gpt-5.5` and the installed
metadata-selected package entrypoint.

That pre-command package audit found two npm-generated internal symlinks under
`node_modules/.bin`; both resolve lexically to regular executable files already
inside the same package. Because frozen `582fc2c7...` rejects every symlink, the
real preflight was not started and its one-shot authority was not consumed.
The user's delegated completion goal approved a minimal closed-link correction
at `docs/superpowers/specs/2026-07-17-m2c-acp-package-internal-symlink-authority-design.md`:
only stable relative `.bin` links to regular manifest files are accepted,
without following them; their text and runtime identity are sealed in authority
v3 and strict preflight v5. M2c remains **BLOCKED** and M3 remains locked.

The closed-link deterministic implementation is now present: Python package
sealing and the generated mode-0500 launcher both record stable non-following
link manifests, validate exact `.bin` lexical closure, and reject every unsafe
location, target, chain, or link/target drift. Focused RED proved the installed
layout gap; package, safety, and launcher GREEN sets passed `33`, `19`, and `24
passed` respectively. Wider non-live/product regression and the new freeze
cycle remain before any real v5 preflight.

Those wider checks now pass: strict/package/launcher coverage is `37 passed,
254 deselected in 36.43s`, complete non-live M2c is `289 passed, 2 skipped in
95.77s`, and product regressions are `851 passed in 4.86s`. Compile, diff,
`src/agentdeck/**` zero-change, process, and temporary-root audits passed. This
documentation commit freezes the closed-link implementation; two complete
detached-worktree suites remain before real preflight.

The new closed-link implementation authority is frozen at
`284d8f62a9121a0d0351938aee1f716b3ebd198e`. Two fresh detached worktrees on
that unchanged SHA passed `4380 passed, 3 skipped in 205.38s` and `4380 passed,
3 skipped in 209.27s`. The skips were exactly the opt-in real ACP, designated
v5 preflight, and real four-stage Mission. Both worktrees were removed;
implementation/SOP diff from freeze is empty; process and temporary-root audits
found no residue. The one real installed-package v5 preflight is now the next
gate.

The designated v5 preflight then ran exactly once on frozen `284d8f62...` with
Leader `gpt-5.5` and passed `1 passed in 16.32s`: `ready=true`, `blockers=[]`,
`failures=[]`, authority v3 digest
`sha256:b194c3b4ccbfa3ba2b534bf9cb51e59ecbc077e2576c6eea8ba343f26cc83ffa`,
and all five tools ready. Its detached worktree was removed and residue audits
were empty. The one same-SHA/model/digest real four-stage Mission is now the
only remaining M2c gate.

That same-SHA/model/digest live node ran exactly once and failed `1 failed in
47.71s` with `stage=live_acceptance`,
`code=mission_preview_not_consumed_exactly_once`. One plan and Mission plus
daemon admission existed; attempts, permissions, Worker replies, and handoffs
were all zero. Only bounded PTY byte-count/truncation/hash evidence was retained.
The checkout was removed and residue audits were empty.

Static data-flow tracing identified a harness ordering race: confirmation calls
the daemon admission executor before committing the conversation's
`conversation_preview_consumed` event and rendering prompt 3. The live harness
waited only for admission, then immediately counted events, so it could stop the
PTY during that valid commit window. Frozen authority `284d8f62...` is exhausted
at preflight/live `1/1` and cannot be retried. A new minimal preview-consumption
convergence spec/TDD/freeze cycle is required; M2c remains **BLOCKED** and M3
remains locked.

The native-schema provenance persistence correction is implemented and frozen
at `7a76ada81938be3ba0720a7c2f5a540b4beebb3e`. Semantic Mission previews now
preserve the exact validated eleven-field generation envelope. StateStore
strictly distinguishes ordinary nine-field and semantic eleven-field shapes,
revalidates proposal-stripped required/input authority version/hash, and keeps
ordinary/semantic native schema families distinct. ProjectView, trace, and
Leader-status contracts and discovery expose both shapes while preserving the
compatible base-nine metadata.

Legal Leader proposals retain two explicit meanings: generation provenance
hashes the proposal-stripped required/input authority, while the compact
ProjectView semantic authority card hashes the complete compiled output
authority. Stored plan, ProjectView, and trace use one exact generation
envelope; clients must not compare the two authority hashes directly.

Fresh verification on the frozen SHA passed:

- Mission/Conversation/binding/acceptance: `211 passed in 5.77s`;
- Provider/schema/contracts/non-live M2c: `1125 passed, 1 skipped in 56.32s`;
- independent spec review: compliant;
- independent code-quality review: no Critical, Important, or Minor findings;
- full suite 1: `4283 passed, 2 skipped in 194.36s`;
- full suite 2: `4283 passed, 2 skipped in 203.12s`;
- compile, diff, scope, marker, cleanup, and residual audits: PASS.

The detached verification checkout was removed. The feature worktree remained
clean; live pytest/AgentDeck daemon matches and current live/tool roots were
zero. No Provider, live Mission, ACP session, managed tmux session, install,
login, global configuration, push, or merge ran during the correction cycle.

The human then bound exact Leader model `gpt-5.5` and authorized exactly one
read-only preflight on frozen implementation
`7a76ada81938be3ba0720a7c2f5a540b4beebb3e`. That node ran once and passed
`1 passed in 4.24s`, returning `schema_version=m2c-live-preflight/v2`,
`ready=true`, `blockers=[]`, and four ready tools: Codex CLI `0.131.0`, Claude
Code `2.1.211`, Claude Agent ACP `0.58.1`, and tmux `3.7`. The model card was
exactly `{provider: codex-cli, model: gpt-5.5, source: explicit, ready: true}`.
The detached preflight checkout was removed, and follow-up audits found no
matching checkout, pytest/daemon process, or M2c live root.

For this SHA, preflight count is exactly `1`. The human separately authorized
exactly one real four-stage live Mission on the same frozen SHA and model. That
node ran once, exited `1`, and reported `1 failed in 14.36s` with the fixed
diagnostic `stage=live_acceptance`, `code=preflight_blocked`. It stopped before
project initialization, Mission Preview, model invocation, daemon admission,
ACP/tmux Worker execution, permission handling, handoffs, or artifact effects.
It was not retried.

The detached live checkout and disposable strict-basename tool mirror were
removed. No matching live root, pytest/daemon process, or staged mirror
remained. The feature worktree stayed clean before this evidence update; no
install, login, global configuration/auth/permission change, user tmux
inspection, push, or merge occurred.

The designated preflight and live internal preflight did not use the same
executable authority: the former used PATH-discovered installed tools, while
the live SOP required explicit strict-basename mirror paths. The current
harness collapses the internal result to `preflight_blocked`, so the exact
allowlisted blocker cannot be recovered without another external execution.
No inference is made about which tool or probe failed.

For frozen SHA `7a76ada...`, preflight/live counts are now exactly `1/1`; both
authorizations are exhausted and neither may be rerun. Historical SHA
`75f0366d...` also remains exhausted at `1/1`. M2c remains **BLOCKED** and M3
remains locked. The human-approved design is now written at
`docs/superpowers/specs/2026-07-17-m2c-tool-authority-binding-design.md`. It
binds designated preflight and live through one content-addressed authority
covering model, Codex, Claude, tmux, Node, and the complete Claude Agent ACP
package tree; it also projects only closed `tool + probe + code` diagnostics.
The human approved the written spec. The detailed, self-reviewed TDD plan is at
`docs/superpowers/plans/2026-07-17-m2c-tool-authority-binding.md`; it divides
the work into deterministic authority, package-tree, preflight-v3, live
admission, diagnostic closure, controlled Node/ACP, SOP, and frozen
verification commits. Inline RED/GREEN implementation is now authorized.
Tasks 1-7 are now implemented in the M2c harness/SOP: deterministic authority,
complete ACP package sealing, strict preflight v3, pre-root digest admission,
closed failure projection, controlled Node/ACP execution, and the separately
gated designated node. No `src/agentdeck/**` behavior changed. Focused
RED/GREEN checks are passing, and the complete non-live M2c file passed
`238 passed, 2 skipped in 67.79s`; the skips were exactly the gated real
designated preflight and real live node. The focused authority matrix passed
`44`, and product/conversation/contract/provider regressions passed `851`.
Compile, whole-slice diff, `src/agentdeck/**` zero-change, durable-evidence,
process, and live-root residual audits passed. This documentation commit
froze the implementation at
`fda1a69194e67b50afe0c2b9f4e7f29c195af400`. Two fresh detached worktrees on
that unchanged SHA passed the complete suite: `4329 passed, 3 skipped in
204.59s` and `4329 passed, 3 skipped in 206.95s`. The three skips were exactly
the opt-in real ACP, designated M2c preflight, and real four-stage M2c nodes.

An earlier verification attempt using relative `PYTHONPATH=src` was discarded:
a daemon subprocess changed cwd to its disposable project and could not resolve
that relative source path, so an existing daemon acceptance admission returned
false. The exact node passed `1 passed in 11.26s` when only the source path was
made absolute. The contaminated detached worktree was removed, a fresh one was
created, and both counted full suites then passed. This changed no implementation.

Both verification worktrees were removed. Final audits found no matching
pytest/AgentDeck daemon process and no authority-suite, live, or four-stage
temporary root. No real designated preflight, live Mission, provider,
ACP/tmux Worker, install, login, global change, merge, or push ran. M2c remains
**BLOCKED** and M3 remains locked. The next gate requires separate human
authorization naming frozen SHA `fda1a691...` and the exact Leader model for
one designated read-only preflight. If and only if that result is
`ready=true`, `blockers=[]`, and `failures=[]`, a later live authorization must
separately name the same SHA, model, and exact returned authority digest.

## Historical 75f provenance blocker evidence

The approved target-exclusivity and pytest-report-redaction TDD plan is
implemented and verified. The implementation authority is frozen at
`75f0366d4d5619b29c77f10949365f43d46185b1`; the later documentation evidence
commit is not implementation authority. Required targets are Candidate-wide
exclusive, new proposal targets are Mission-wide unique, code-specific
same-Leader regeneration is shared by API and CLI providers, and bounded PTY
bytes cannot enter default pytest reports through dataclass representation.

Focused semantic/Provider coverage passed `740`; the complete non-live M2c file
passed `192 passed, 1 skipped in 46.41s`. Two independent detached-checkout full
suites on the unchanged frozen SHA passed `4266 passed, 2 skipped` in `199.05s`
and `186.08s`. Compile, diff, scope, sentinel-leakage, cleanup, and residual
audits passed. No provider, ACP, tmux, preflight, or live Mission ran during
this implementation verification.

The human explicitly bound Leader model `gpt-5.5` to frozen implementation
`75f0366d4d5619b29c77f10949365f43d46185b1` and authorized exactly one read-only
preflight. It ran once and passed `1 passed in 3.75s` with `ready=true`,
`blockers=[]`, `source=explicit`, Codex CLI `0.131.0`, Claude Code `2.1.211`,
Claude Agent ACP `0.58.1`, and tmux `3.7` all ready. The detached checkout was
removed; the feature worktree remained clean; residual audit found zero live
pytest/AgentDeck daemon processes and zero M2c live roots.

After separate human authorization, the real four-stage node ran exactly once
on the same frozen SHA/model and was not retried. It failed `1 failed in
48.26s` at `stage=live_acceptance`, `code=native_schema_provenance_missing`.
The snapshot had `plans=1`, `missions=1`, and zero attempts, permissions,
Worker replies, and handoffs. The bounded PTY evidence retained only
`byte_count=1438`, `truncated=false`, and
`sha256=4d261e29ad7cf2b3a5d19b899eb0cc734c8e86f19ec71e55731e39a2c6b706fa`.
No terminal text was copied into durable evidence.

The guarded harness and outer cleanup removed the live checkout and disposable
tool mirror; the feature worktree remained clean; current-run process matches
were zero. One `/private/tmp/agentdeck-m2c-path-verify-954b868c` directory
predated this run (`mtime=2026-07-16T00:29:09+0800`) and was left untouched.

Code inspection identifies the precise persistence break:
`create_mission_preview_from_candidate()` validates semantic
`leader_generation`, then explicitly replaces it with `None` under a stale
Task 7/Task 8 handoff comment. The resulting semantic plan and Mission are
durable, but their plan record lacks the native-schema provenance required by
the live gate. Existing provenance tests cover native non-semantic previews,
not native semantic previews.

For this SHA, preflight count is exactly `1` and live count is exactly `1`.
Neither may be rerun. M2c remains **BLOCKED** and M3 remains locked. The next
gate is an approved deterministic RED/GREEN fix that preserves the already
validated semantic generation envelope in the plan record without changing
plan hash, semantic authority, confirmation, ACP/tmux, or permission behavior.

## Historical previous frozen live evidence

Leader Preview observability is frozen at
`9db5b476f885cfcf68a55cbf59673a2d908d3fce`. Its complete non-live harness
passed `186 passed, 1 skipped in 42.69s`; two independent unchanged-SHA full
suites passed `4219 passed, 2 skipped` in `185.64s` and `191.59s`. The one
human-authorized read-only preflight for Leader model `gpt-5.5` passed with
`1 passed in 4.19s`, `ready=true`, `blockers=[]`, and all four tools ready.
That preflight must not be rerun.

After separate explicit authorization naming the frozen SHA and model, the
sole opt-in real four-stage node ran exactly once. It exited `1` with
`1 failed in 52.39s` and was not retried. The first unmet gate is
`stage=live_acceptance`, `code=leader_schema_before_preview`. The exact durable
Leader terminal is `stage=schema`, `diagnostic_code=semantic_effect_conflict`,
`attempt_count=2`, and `constraint_mode=native_json_schema`.

The same snapshot had `plans=0`, `missions=0`, `mission_attempts=0`,
`permission_requests=0`, `mission_worker_replies=0`, and
`mission_handoffs=0`. Its closed ledger classification was
`permission_state_inconsistent`, with zero permissions and unknown lifecycle
fields. The run therefore stopped before Mission Preview creation or
confirmation, daemon admission, ACP/tmux Worker execution, permission,
disconnect/reconnect, takeover/return-control, handoff, lineage, or artifact
effects. This is not a partial four-stage PASS.

Bounded PTY identity was `byte_count=608`, `truncated=false`, and
`sha256=cbc80281637c6d93de32e51d883339c5095b1a38ae4c1e2c518345fa96e8560a`.
The allowlisted failure JSON retained no terminal text. However, pytest's
traceback rendered `_PtyTail` through its default dataclass representation and
showed raw tail bytes in ephemeral test output; the existing deterministic
leakage test covers `str(exception)`, not pytest report rendering. Do not copy
those bytes into durable evidence, and do not claim transcript-safe pytest
failure output until a separately approved TDD slice closes that boundary.

The harness emitted no cleanup-failure note. The frozen checkout remained
clean; the detached checkout and disposable tool mirror were removed; audit
found zero current-run live roots, live pytest/AgentDeck daemon processes, or
staged mirrors. Four tmux sockets created on July 14 predated this July 16 run,
were outside its isolated live root, and were left untouched. No install,
login, global config/auth/permission change, user tmux inspection, or second
live attempt occurred.

M2c remains **BLOCKED**, not partial PASS, and M3 remains locked. This prior
failure motivated the now-completed target-exclusivity and pytest-redaction
slice. It is superseded for active routing by frozen implementation
`75f0366d4d5619b29c77f10949365f43d46185b1`. That implementation has now used
its one preflight and one live authorization and stopped at
`native_schema_provenance_missing`; neither may be rerun. That persistence fix
is now complete at the frozen SHA recorded above, whose own preflight/live
cycle is also exhausted at `1/1` after `preflight_blocked`. The active route is
the new same-executable-authority and closed internal-preflight-diagnostic
design/TDD cycle described at the top of this file.

## Natural-language Mission Phase 0 baseline — accepted

The fresh-project strict two-message Codex/Claude acceptance completed all eight frozen sequential steps as Mission `mis_1d5c2a569173`, plan `pln_c13709530632`, and workflow `wfr_7d309ae9c507`. Mission status, ProjectView status, workbench, and the event ledger agree on `completed/current_step=8`; the audit contains one `mission_confirmed` and eight `workflow_step_completed` events. First-run trust remained an explicit human setup boundary. Two real readiness false negatives were converted to strict regression tests before minimal fixes. Verdict: **PASS**. Durable evidence: `docs/validation/2026-07-11-natural-language-mission-acceptance.md`.

## Protocol-native Phase 1 model — complete

Phase 1 adds pure transport capability, agent session, protocol turn, transport update, and permission request records; append-only persistence with audited lineage; compact ProjectView summaries; the versioned `protocol-runtime/v1` discovery contract; read-only `agentdeck protocol status`; and runtime capability metadata. `agentdeck contract protocol-runtime --example`, `agentdeck protocol status`, ProjectView, and the contract index expose the implemented observation surface.

tmux remains the active default backend. Its capability metadata describes only the observable fallback it actually provides; it is not ACP-compatible metadata and does not authorize execution. Existing tmux dispatch does **not** automatically emit protocol records. Phase 1 has not implemented an ACP backend or adapter subprocess, automatic emission, a project daemon, a backend switch, or a provider-native permission bridge.

Phase 2 subsequently delivered one human-approved foreground ACP vertical slice. It does not change the Phase 1 boundary for existing tmux dispatch or imply that Mission/workflow now use ACP.

## Sequential workflow core — implemented

### Built-in sequential-handoff planning skill — implemented and accepted

`planning_guidance[]` is now a bounded audited skill field (maximum eight entries, 240 characters each) that follows explicit load records into ProjectView and plan provenance. Only guidance from an `agent_id=leader` load enters API/CLI Leader prompts; full `content_snapshot` remains excluded. Existing skills default to an empty list.

The generic built-in `sequential-handoff` skill (`version=1.0.0`) shapes fixed consecutive plans, explicit compact handoffs, per-step evidence/failure conditions, and a workflow preview → human-confirmed run summary. It is never auto-loaded, never injected into Workers, grants no execution permission, and rejects parallel/DAG/cycle/dynamic-step workloads.

GREEN/counterexample evaluation and full regression are complete. The isolated real acceptance used Codex and Claude Workers in one resumable run (`wfr_d1bd55232a66`): all eight alternating turns completed and produced the expected opening 32 surnames. The durable evidence is `docs/validation/2026-07-10-codex-claude-baijiaxing-handoff.md`.

The real run also hardened terminal interoperability: echoed prompt templates no longer correlate as replies; known Codex/Claude TUI bullets are normalized; partial streaming blocks wait instead of failing early; tmux multiline paste pauses briefly before submit; and send failures persist `stopped/pane_lost` instead of leaving a crashing `running` workflow. Operator setup still must clear first-run trust prompts and provide panes large enough to retain the structured reply token.

The generic A→B→C handoff engine is implemented and committed. It is intentionally separate from ordinary `run-loop`:

- `agentdeck workflow preview --plan-id <id> [--timeout <seconds>]` is read-only and derives a hash-pinned ordered chain plus stored-runtime blockers without inspecting tmux.
- `agentdeck workflow run --plan-id <id> [--timeout <seconds>] --confirm` performs one foreground, bounded run after a single explicit confirmation.
- `agentdeck workflow status --run-id <id>` is read-only; `agentdeck workflow resume --run-id <id> --confirm` resumes the frozen chain and does not repeat a dispatched or completed step.
- Every active Worker reply must carry the exact `handoff_token` and structured status/summary/verification/risks/next_steps fields. Only compact validated handoff data reaches the next Worker.
- State is persisted under `workflow_runs[]`; existing message/reply/artifact lineage and workflow audit events remain inspectable.
- Contract discovery is `agentdeck contract workflow --example`; the durable contract is `docs/contracts/workflow-schema.md`.

Safety boundary: workflow execution never expands the plan, spawns agents, calls a Leader provider, auto-acks inbox items, or grants worker permissions. Plan drift, unavailable runtime, pane loss, invalid reply, timeout, blocked, and failed stop the chain. Ordinary approval/dispatch/capture-reply/run-loop behavior is unchanged.

Deferred: DAG/cycle semantics are not part of this linear workflow core.

## Golden demo guide slice — implemented

The end-to-end golden demo lane now has its first guide slice implemented and committed:

- `agentdeck demo golden` is a read-only, state-aware operator guide for the golden demo. It derives current status from existing project/workbench facts and recommends explicit next commands for provider/setup, approval, dispatch, review gate, release, and already-released states.
- `agentdeck contract demo` / `agentdeck contract demo --example` expose the GUI-ready demo guide contract and stable example payload; `docs/contracts/demo-schema.md` documents the response fields, step fields, statuses, and safety values.
- The implementation was covered by focused contract/CLI tests and read-only/no-runtime-mutation assertions in the implementation slices. The guide does not execute recommended commands, call providers, read tmux, or mutate runtime/state.

### Deterministic golden-demo rehearsal — covered

The golden path now has one contiguous pytest rehearsal in addition to focused state tests. It drives a single temporary project through fake-Leader planning, explicit approval, fake-runtime dispatch, captured reply/artifact, code review, round review, and explicit release while checking `agentdeck demo golden` at every checkpoint. This is test-only coverage: no production command, function, runtime backend, or contract was added.

Lane guidance: this supports the **end-to-end golden demo first**. Remote skill / marketplace work remains a later product fork/lane and should not be started as part of golden demo docs cleanup.

## Skill 生态 lane 进度 — A + B(只读/auto/ver/semver) + lockfile 完成，⏸ loop STOP（next remote/C）

用户定了 "先 A 再 B"、"先 B-auto 再 B-ver"，选了 semver 范围，再选了 lockfile，loop 已推进到 lockfile 落地。已完成并提交：
- **只读可见性 4 片**：`skills catalog --source <dir>` → `[skills] allowed_sources` + `skills sources` + `source_allowlisted` → workbench `skills_catalog_card` → 自然语言 `mode=skills_catalog`。
- **A — allowlist 强制拦截**：`skills import` opt-in 强制（`--allow-unlisted` 逃生阀，空清单向后兼容，审计 `skill_imported.allowlisted`/`.allow_unlisted`，`import-preview` 只读回显）。
- **B1/B2 — 依赖只读**：`skills deps --name <name>`（依赖树/missing/循环/拓扑序）；`skills load-preview` 回显 `unmet_dependencies`。
- **B-auto — 依赖 load（preview + 显式确认）**：`skills load-plan`（只读预览）+ `skills load --with-deps --confirm`（deps-first 逐条 load，缺失/环拒绝零写，绝不 auto-import/静默；单 skill load 不变）。
- **B-ver — 依赖版本约束（content-hash 锁定）done**：`depends_on: [name@sha256:<hex>]` 锁定内容 hash（纯 `name` = 任意版本，行为不变）。`skills.py` 新增纯 `_parse_dep`；`resolve_skill_dependencies` 新增 `version_mismatch: [{name,expected,actual}]` blocker 类别（pin 与实际 `content_hash` 不符，blocker leaf 不递归）。`skills deps` / `load-plan` 输出 `version_mismatch`（加入两个 contract 字段 + validator），`load-plan.blockers` 加 `"version mismatch: <name> expected <pin>"`，`can_load` 因此为 false，`skills load --with-deps --confirm` 像 missing/cycle 一样硬阻断、零写。纯 hash、本地、确定性、无网络。Design + plan: `docs/superpowers/specs/2026-07-09-skill-dep-version-pinning-design.md`、`docs/superpowers/plans/2026-07-09-skill-dep-version-pinning.md`。
- **semver — 依赖 semver 范围 done**：skill `SKILL.md` frontmatter 声明 `version: X.Y.Z`（默认 `0.0.0`，加入 `SkillSnapshot.summary()` + `SKILLS_SKILL_ITEM_FIELDS` + example fixture）。`depends_on: [name@<spec>]` 中 `<spec>` 不以 `sha256:` 开头即为 semver 范围，与依赖 `version` 比对。`skills.py` 新增纯 stdlib `parse_version` + `version_satisfies`（支持 bare/`==` 精确、`>= > <= <`、caret `^`、逗号 AND；`MAJOR[.MINOR[.PATCH]]` 缺省补 0；不支持/无法解析一律 fail-safe False）。`resolve_skill_dependencies` 分类 spec：`sha256:` → 内容 hash，否则 → `version_satisfies`，不满足记入 `version_mismatch`（新增 `reason` 键，`name/expected/actual` 与 B-ver 兼容）作为 blocker leaf 不递归；`version_mismatch` 继续经 `skills deps` / `load-plan` blockers / `load --with-deps` 硬阻断、零写。`sha256:` pin 和纯 `name` 逐字节不变。纯 stdlib、本地、确定性、无网络、无第三方库。Design + plan: `docs/superpowers/specs/2026-07-09-skill-dep-semver-design.md`、`docs/superpowers/plans/2026-07-09-skill-dep-semver.md`。
- **lockfile — 依赖锁文件 generate + read-only verify done**：`agentdeck skills lock --name <name>` 显式冻结已解析依赖树（复用 `resolve_skill_dependencies` + `discover_skills`，deps-first `order` 去 root，逐个 pin `content_hash`+`version`）到专用 `.agentdeck/skill-locks/<name>.json`（`discover_skills` 不拾取），写 lockfile + `skill_locked` 事件；有 missing/cycle/version_mismatch 拒绝零写，未知 skill 非 0。`agentdeck skills lock-verify --name <name>` 全只读 diff（`changed`/`added`/`removed`/`blockers`/`in_sync`），无 lockfile → `locked=false`+hint+退出 0，不写 state、不改 lockfile。lockfile 本切片是 **advisory** drift 检测，不改变 `deps`/`load` 解析（enforce 是后续切片）。contracts.py: `SKILL_LOCK_*_RESPONSE_FIELDS` + `validate_skill_lock*_contract` + 发现字段。本地、无网络、无第三方库。Design + plan: `docs/superpowers/specs/2026-07-09-skill-dep-lockfile-design.md`、`docs/superpowers/plans/2026-07-09-skill-dep-lockfile.md`。

⏸ **loop STOP —— 剩余依赖项是产品 fork，需先 STOP + 询问 human，不得单方面开工**：
- **remote / marketplace 依赖（C）**——联网远程解析/抓取/签名/供应链/registry 格式，local-first 边界外，需 human 显式 opt-in 的专门设计对话（自己的 brainstorm→spec→plan），绝不在 loop 里开工。lockfile enforce（让 `deps`/`load` 消费 lock 改变默认解析）也是后续独立切片，非本 loop。

中文小结：lockfile generate + read-only verify 已实现并提交。`agentdeck skills lock --name <name>` 把某 skill 当前解析出的依赖树冻结成 `.agentdeck/skill-locks/<name>.json`（每依赖 name+content_hash+version），并追加 `skill_locked` 审计事件；不可解析树（缺失/循环/版本不符）会被拒绝且不写任何文件或事件。`agentdeck skills lock-verify --name <name>` 全只读，报告 lockfile 与当前解析的漂移（changed/added/removed/in_sync），不改任何状态或 lockfile。lock 本切片是 advisory，不改变 `deps`/`load` 的解析行为。到此 skill 依赖 lane 的本地确定性约束（hash pin / semver range / lockfile）都做完了。**⏸ 下一步是 remote/C（联网/签名/供应链/registry），必须 STOP + 问你，绝不在 loop 里做**；lockfile enforce 亦是后续独立切片。

## M2c development history and prior evidence

The material below records earlier M2c checkpoints and is superseded for
active routing by the current goal above.

**Task 13 semantic M2c harness conversion is implemented; it becomes frozen evidence authority only after unchanged-SHA verification.**
The complete non-live harness passes `110` tests with one explicit opt-in live
skip. The live confirmation path no longer treats Leader-authored free-text
phase/token matches as authority: before confirmation it validates
`mission-semantic-authority/v1`, the unique atomic revision before/after state,
byte-equal fresh compilation, four semantic-step and task hashes against the
authoritative snapshot, the exact authority/task/policy/generation confirmation
digest, and zero attempts, permissions, Worker replies, and handoffs. The old
token checks remain test-only mutation helpers. This revision has not run the
Task 13 frozen double full suite or the single read-only preflight, and has not
entered Task 14 or made a live attempt.

AgentDeck remains the control plane around LLM reasoning, not its replacement:
required user authority, visible Leader proposals, unresolved facts, and
confirmed frozen authority are distinct; one Mission confirmation remains
independent from runtime permissions; ProjectView exposes only compact,
non-authorizing provenance. A2A, remote execution, GUI redesign, and a terminal
emulator are out of scope. M2c remains **BLOCKED**, M3 remains locked, and the
next route is to take the exact commit containing this handoff, verify that SHA
twice, then run the designated read-only preflight exactly once. Task 14 requires new
human authorization even if that preflight is ready.

**Phase 3 M2 implementation Tasks 1–14 are integrated into `main`; the active goal remains the approved Phase 3 M2c acceptance closure.** The latest evidence authority is frozen commit `1a22618ba083a76f4a21ffc7ebc7a3e513e4aae6` on branch `codex/m2c-probe-readonly`. Its non-live focused harness passed `97` tests with `1` explicit live skip; two independent full-suite runs passed `3406` tests with `2` skips in approximately `148.23s` and `146.26s`; compileall passed and the diff was clean. The read-only preflight exited `0`, passed `1` test in `16.15s`, and reported `ready=true`, `blockers=[]` with Codex CLI `0.131.0`, Claude CLI `2.1.208`, Claude Agent ACP `0.58.1`, and tmux `3.6a`.

The strictly single live attempt exited `1` with `1 failed` in `49.50s` and was not retried. It stopped before confirmation with `code=native_schema_task_authority_invalid` and `classification=leader_task_authority_missing`. Of the closed seven `task_authority` fields, only `revision_transition=false`; `phase_order`, `worker_order`, `artifact_all_steps`, `implementation_draft`, `review_target`, and `acceptance_target` are `true`. Leader-generated revision task did not simultaneously preserve both `draft-v1` and `accepted-v2`. This does not establish which token was absent, whether both were absent, or why the Leader output lost the requirement.

The snapshot cardinalities are `plans=1`, `missions=1`, `attempts=0`, `permissions=0`, `replies=0`, and `handoffs=0`. Before confirmation the run reached no ACP, permission, Worker, tmux, scheduler, or artifact effect, so the next work must not pivot to a permission or ACP repair. The bounded PTY evidence is `byte_count=11`, `truncated=false`, `sha256=066523e516460e23c045358c6736f76f2fecd1022157b11c679ae69715c0c734`; the hash is identity only and cannot explain terminal text. The harness failure had no cleanup-failure note; the outer mirror/path was removed; post-run audits found zero mirror/live-pytest/agentdeck-daemon process matches, zero M2c temp-directory matches, and zero M2c tmux-session matches. No absent `cleanup=complete` or `residual_process_count` field is invented.

M2c is **BLOCKED**, not a partial PASS, and M3 remains locked. At the conclusion of the single live attempt, the required next gate was a new brainstorming/spec/plan round for Leader revision task semantic authority before deterministic RED/GREEN, a new commit, a fresh full suite, and a fresh `ready=true` / `blockers=[]` preflight could authorize one new single live attempt. The brainstorming and design portion of that gate is now complete as recorded below. Do not retry automatically.

The brainstorming and segmented design for that gate are human-approved and written as `docs/superpowers/specs/2026-07-15-leader-semantic-authority-design.md`. The chosen direction is a general `mission-semantic-authority/v1` control plane, not M2c-specific token hardcoding: AgentDeck conservatively extracts required user authority, the Leader returns structured authority references and visible proposals, AgentDeck validates and deterministically compiles Worker tasks, and one exact preview confirmation freezes the executable scope. The design preserves separate runtime permission gates, allows one same-Leader bounded regeneration, keeps sensitive values reference-only, and requires hash-stable dispatch/recovery provenance. The detailed TDD implementation plan is `docs/superpowers/plans/2026-07-15-leader-semantic-authority.md`; Tasks 1–12 and the Task 13 harness conversion are implemented. The immediate next action is frozen verification and the one designated read-only preflight. Task 14 still requires a separate explicit live authorization after a ready preflight.

The formal design is `docs/superpowers/specs/2026-07-14-agentdeck-m2c-closure-design.md`, and the Subagent-Driven TDD execution follows `docs/superpowers/plans/2026-07-14-agentdeck-m2c-closure.md`. Tasks 1–10 are implemented on the isolated closure branch. Both design and implementation start from `docs/roadmap/product-north-star.md`: AgentDeck—not the Leader model, ACP, or tmux—owns frozen Mission authority, scheduling, governance, recovery, and audit.

The Task 11 frozen-commit preflight at `650d6fc4` found Codex CLI `0.131.0`, Claude CLI `2.1.208`, Claude Agent ACP `0.58.1`, and tmux `3.6a` ready, but returned `ready=false` with the sole fixed blocker `probe_wrote_files`. The opt-in live node was not run. Thus the real implementation → review → revision → acceptance Mission, disconnect/reconnect, two explicit ACP permissions, tmux visibility, takeover/return-control, four canonical handoff evidence rows, three inter-stage links, artifact, ledger, trace, and snapshot agreement are not reached. Staging and live temporary roots have zero residuals; no installation, login, authentication, or global-setting change occurred. M2c remains **BLOCKED**, and the earlier two-step real transport PASS must not be promoted to four-stage acceptance.

Task 12 closes only the deterministic verification and handoff boundary; it does not change that live verdict. Fresh focused gates pass `389` Leader tests, `1134` Mission/contract tests, and `349` daemon/governance/recovery tests. The full suite passes `3348` tests with `2` explicit skips; `python -m compileall -q src tests` and `git diff --check` both exit 0. Self-review confirms one canonical Leader schema source, native Codex and Claude coverage, AgentDeck-owned semantic authority, no provider/model/transport fallback, no local intent-repair path, same-Leader deadline-bounded regeneration, compact non-durable raw-output boundaries, contract-valid ProjectView provenance, deterministic four-stage acceptance, and cleanup-as-evidence. All `41` branch commits, including this handoff boundary, include `HISTORY.md`; no `.agentdeck/` runtime state is tracked; the closure diff contains no A2A, remote, global-roaming, Workspace Client, or terminal-emulator scope; and the user-owned main-checkout `.omc/` changes and untracked `AGENTS.md` remain outside this clean worktree and were not staged or modified here.

At that earlier checkpoint, the live acceptance authority was frozen commit `650d6fc4`. Its preflight had the sole blocker `probe_wrote_files`, and the number of opt-in live attempts was exactly `0`. Therefore M2c was not complete, the M2 `/goal` could not be closed, and M3 remained locked. Its then-next gate was to identify which capability probe wrote inside the isolated roots, capture that behavior in a deterministic regression, apply the smallest in-scope fix, freeze a new commit, and run a new read-only preflight. The opt-in live node could not run until that new frozen preflight was ready; an unknown external effect could never be retried. This paragraph is retained as historical BLOCKED evidence and is superseded for active routing by the latest frozen evidence above.

Leader planning failure truth is now compact and durable. CLI Leaders emit only typed allowlisted stages (`timeout`, `nonzero`, `json_parse`, `schema`, `cancelled`, `oversize`); Gateway/session propagation never persists raw stdout/stderr, prompts, argv, paths, or exception text. Failed/cancelled turns immediately commit their terminal transition plus `conversation_turn_terminal.stage`, allowing reconnect/acceptance clients to stop without waiting for a nonexistent Mission. Natural-language planning freezes Worker selection and step count once before the Leader call, carries them explicitly through `LeaderRequest` and `LeaderMissionCandidate`, verifies the Gateway returned the same authority, and lands the preview without reparsing the redacted durable message. Session planning and legacy landing share one conservative explicit-count parser covering Arabic/Chinese round phrases, Chinese step phrases, and English digit/one-through-ten `steps`; ambiguous ordinals, labels, agent counts, and unrelated number prose retain compatible defaults. `MAX_MISSION_STEPS=64` is the source of truth across parsing, candidate authority, normalization, and plan validation. Explicit 0/1, counts above 64, unsupported/oversized Chinese numerals, and huge ASCII tokens fail safely before Leader invocation and become durable `schema`; they are never clamped into a different authorized plan. Open natural tasks therefore remain valid when `mission_intent` is absent, an explicitly unrequested third Worker stays outside prompt and confirmation scope, and planning/landing counts remain identical. Legacy direct candidates without authority retain message-derived compatibility. Candidate validation or pre-commit landing failure becomes the fixed typed `schema` terminal, never an unhandled exception that strands a turn in `waiting_leader`. If the atomic commit call raises, preview recovery checks exact durable plan, Mission, binding, turn-transition, and audit-event facts rather than guessing from exception type; a complete post-save commit returns the same payload with pending outbox preserved, while partial/unprovable state remains failed. Project-wide latest-pending recovery survives a new conversation session, so reconnect/retry blocks duplicate plan, Mission, and binding creation.

Final review closes three adjacent recovery ambiguities. Numeric-shape detection scans every Chinese and English explicit count before choosing an answer: standalone `两` is integer two, unsupported `两百`-style/financial-Chinese, decimal, signed/full-width, out-of-range, or huge tokens fail, and multiple valid quantities must agree regardless of language order; repeated equal cross-language counts remain valid and non-count prose still uses the compatible default. Invalid/conflicting input becomes durable `schema` before any Leader call. When exact preview proof fails after the durable turn has already reached a terminal or otherwise drifted state, the session validates that durable turn fact and returns a fixed `stage=durable_state` fail-stop response; it never rewrites terminal history, presents an unproved preview, repeats domain effects, or lets the internal exception escape, and records only a compact recovery-blocked audit when safe. Exact event proof permits one identical copy in each of journal and outbox for crash replay, but rejects duplicate identities or content drift within either channel.

Fresh final-review gates: focused Mission/session `74 passed`; conversation/provider/acceptance `223 passed`; complete daemon suite `963 passed`; compileall and diff checks pass.

Mixed-version state authority is closed at the remaining filename-replacement window. Each state/journal effect proves the held legacy lock still names `protocol-mutation.lock` before and after I/O. Immediately before atomic replace it re-resolves the currently named legacy authority; after a lock-name replacement it acquires that lock in old-then-current deterministic order, repeats the exact target content+inode CAS while holding both locks, and fails closed before installing current bytes. State commit retains the exact displaced canonical descriptor; after later drift it waits on the replacement filename lock and restores only if canonical still contains AgentDeck's exact installed inode. An explicit effect-installed marker prevents a pre-effect guard failure from re-flocking its already-held replacement inode or treating an older writer's byte-identical/different-inode replacement as AgentDeck's effect. An older in-place write is therefore recovered from the retained inode, while an already atomic older replacement is preserved and the current mutation fail-stops. Journal commit uses the same conditional recovery and keeps the outbox pending. Ten focused race cases include independent-process three-second timeout checks for byte-identical older state/journal replacement, atomic older replacement after initial CAS but before the current replace, and the `963`-test daemon suite passes.

Task 14 adds a real-SIGKILL nine-boundary crash matrix and one disposable product acceptance whose first bare `agentdeck` PTY creates a natural-language Mission preview, confirms that exact preview, and emits a strict validated five-field Mission run card before disconnecting; a second bare PTY reconnects at the ACP permission pause, renders recovery, and drives exact permission preview/confirm. The run proves ACP-before-tmux handoff ordering, compact ProjectView/contracts/ledger/events/hash/file agreement, and zero durable transcript/secret markers. Daemon ACP streamed chunks now persist only canonical content hash and byte count; the raw bounded fragments remain process-local until canonical handoff extraction. Crash cardinality is keyed by durable `(mission_id, step_id, agent_id)` and inspected only after a real restart applies one complete first non-idle scheduler cycle. Startup owns and reaps a spawned daemon if readiness/probing fails; one collect-all guard then attempts every process kill/wait, thread join, endpoint reconciliation, and temporary-project removal even after an earlier cleanup error, attaching cleanup diagnostics without masking the primary failure. Injected regressions prove both exception-safety paths.

Final-review hardening makes takeover worktree evidence bounded before content access. `.git` and `.agentdeck` are pruned before descent; traversal stops after 4,096 non-excluded entries; file size is checked against the 32 MiB aggregate limit before an anchored no-follow open; accepted files are hashed in chunks and their device/inode/type/size/mtime plus path identity are revalidated afterward. Escaping links, symlink swaps, special files, and concurrent replacement fail closed without reading an external target. The four dedicated regressions and the 201-test governance/service/recovery/reconnect group pass.

Recovery truth is deliberately conservative: daemon loss after tmux dispatch but before receipt, and daemon loss while ACP permission is pending, classify the active attempt as `ambiguous` to prevent replay; the permission record remains pending for inspection. Force-stop persists Mission `interrupted` while retaining only the exact current RecoveryFacts attempt's force-stop unknown effect as `ambiguous`. Status/run/workbench expose the same fail-closed five-field `daemon_admission` provenance, and `admitted` authority additionally requires exact five-field shape plus one identical canonical sha256 across the Mission, execution snapshot, and admission record. Drift is `incomplete` and disables resume; valid daemon Missions do not fabricate a legacy `workflow_run_id`.

Fresh final gates: daemon suite `963 passed` and full suite `2928 passed, 1 skipped`. The earlier two-step real transport rehearsal remains **PASS**: Codex CLI Leader, Claude ACP Worker A and Codex tmux Worker B proved permission, compact handoff ordering, disconnect-safe completion and reconnect. The stronger approved four-stage M2c rehearsal is **BLOCKED**: on frozen commit `be4dee08`, two independent fresh bare-client attempts both ended durably at `leader_schema` before preview creation. Each retained zero plans, Missions, attempts, permissions and Worker effects; both projects, daemons and tmux sockets were fully cleaned without install/auth changes. Do not reinterpret the two-step PASS as full M2c acceptance.

Task 13 reconnect/migration truth: ProjectView is the single observation source for the compact `mission_recovery` card; conversation reconnect, workbench, and real bare `agentdeck` reuse the same strictly validated object before continuous UI startup, while a project without a Mission remains quiet. Classification/decision, Mission/attempt/step lineage, controls, traces, and the inspect-only workbench entry are exact; invalid cards produce no partial JSON. Existing-project migration begins with zero-write `agentdeck project migration-preview`, whose exact state-byte hash, additive changes, legacy inspect-only records, expiry, digest, consume-once identity, backup path, and confirmation command bind the only write path. Exact-source revalidation, sanitized project-local backup, and atomic state replacement occur under the protocol mutation lock, preventing concurrent authoritative lost updates. No-follow project-relative directory traversal rejects symlink/non-directory backup paths; backup, commit, and rollback fsync their durability boundaries. `agentdeck contract migration` exposes the strict GUI-ready preview/confirmed schema through contract index and workbench discovery. A legacy Mission without complete frozen authority remains historical/inspect-only; reconfirmation creates a new Mission preview rather than rewriting history.

Task 13 spec re-review closure makes validation authoritative before effects: malformed legacy Mission ids are rejected before command derivation, preview/confirmed digests are recomputed from canonical facts, target changes are restricted to approved M2b additive paths/value schemas, and confirmation validates its response inside the lock before backup or state replacement. Recovery progress requires a complete contiguous completed-step prefix, exact next-step positioning when active, and ordered unique recent-result lineage; legacy foreground progress without frozen steps is reported only to the provable prefix.

Task 13 quality closure anchors project/`.agentdeck`/`state` through no-follow directory descriptors before acquiring the shared `.agentdeck/state/protocol-mutation.lock`. Migration state read/temp/rename/rollback/fsync operations use the anchored state descriptor, backup uses the same anchored deck descriptor, and post-lock inode revalidation rejects state-directory replacement before effects. Lock-wait expiry is freshly rechecked before backup/save. Migration preview now reports `ready`, read-only `noop` for fully migrated state, or fail-safe `blocked` for partial/inconsistent markers; only `ready` exposes an enabled confirmation command.

Task 13 mutation closure makes the anchored `.agentdeck/state/protocol-mutation.lock` the global authoritative `state.json` transaction and preserves that original inode for mixed-version exclusion. All 68 public transitive `StateStore` writers acquire it before their first load and hold it through atomic save; same-thread nesting is reentrant, internal helpers reuse the outer transaction, and the mission-only execution lock is gone. A static AST call-graph audit keeps the explicit registry complete. Plain dict results carry a branch-safe transient token backed by weak, small source facts: deepcopy clones the initial provenance, and successful save replaces only the saving dict's token, so shallow/deep copied branches cannot refresh stale originals. Unrelated loads cannot evict a live snapshot, garbage collection reclaims metadata, and no full state is retained; serialization and ProjectView never expose the token. Stale public saves after migration or another process commit still fail closed. Default layout creation now uses trusted no-follow dir-fd traversal, exclusive regular-file creation, and fsync for `.agentdeck`, `state`, events, approvals, and the lock; symlink/non-directory nodes fail without external writes. Config-only reads remain zero-write and the first legitimate mutation safely creates a missing state directory.

Fresh Task 13 verification is 370 focused authoritative-state/daemon CLI/Mission tests, 1,852 required broad daemon/conversation/contracts/agent-CLI/dashboard tests, and 2,747 full-suite tests passing with one skip. Compileall, `git diff --check`, and the no-temporary-daemon/ACP-worker process audit pass.

Task 12 authority truth: every proposed effect is evaluated by independent frozen-scope, permission-policy, and runtime-ownership gates. Client controller possession, ACP recommendations, Worker text, and role context never grant permission. Pending permissions are derived only through the current attempt's durable permission binding and append-only protocol transition lineage, so the scheduler waits for a human and does not advance another Worker. The production daemon ACP sink queues session/turn/update/permission/binding writes through the single service owner and waits on an exact permission/attempt/session waiter. PermissionRequest, its transport update/turn transition, attempt binding, and audit outboxes use one StateStore lock and one atomic save; save failure is full-tree zero-write, exact retry is byte-stable, and conflict is zero-write. Confirmed human decisions wake only that live request, daemon close clears waiters, and restart keeps unknown in-flight ACP admission ambiguous rather than fabricating resume. Governance previews for takeover, return-control, reroute, permission decision, Mission pause/resume/cancel, and force-stop bind canonical facts, expiry, controller generation, and consume-once state; record/consume is durable and audited, while drift, expiry, replay, or generation mismatch is zero-write. Human-owned Workers block automated prompt, takeover requires a safe boundary, return requires reconciliation, and a frozen attempt cannot be rerouted. Governed mutations revalidate authority at execution inside the Task 11 service queue. Lease-gated production `mission.pause`, `mission.resume`, `mission.cancel`, `permission.decide`, `worker.takeover`, `worker.return-control`, `worker.reroute`, and `daemon.force-stop` RPCs derive current facts and use two-call preview/confirm flows; confirmation atomically consumes the preview with the domain transition and audit outboxes. Normal pause/resume/cancel require an idle attempt boundary. Reroute becomes a durable future-attempt override consumed by attempt preparation/readiness and is rejected once an attempt exists. Force stop interrupts only provably unsent attempts and preserves unknown external outcomes as ambiguous before response-drain shutdown; normal stop continues to reject active work.

The final authority/lifecycle repair makes resume/cancel work across separate CLI processes without retaining a lease credential. A deterministic root/Mission/action logical controller acquires generation N for preview, explicitly releases it, and reacquires N+1 for confirm. The StateStore confirmation mutation requires the same logical client and daemon instance, exact N+1 succession, and durable `controller_lease_released` evidence for hashed generation-N lineage before it revalidates facts, consumes the preview, and changes Mission state. Intervening control, expiry/takeover, restart, replay, and fact drift are zero-write failures. ACP shutdown now distinguishes external work from registered Worker cleanup: open/closing/closed state rejects new external work during close, cancels Workers, pumps cleanup until their `finally` blocks settle, drains, and closes the server last. Close is shielded and shared across concurrent callers, cleanup save failure is explicit after resource cleanup, durable session history—not sink memory—drives idempotent busy/ready-to-disconnected persistence, and cancellation during ACP initialize/new-session/activate performs bounded close plus session disconnect. A durably accepted force-stop always requests process shutdown even if the following lease release/flush reports failure; ordinary stop remains unchanged.

Post-review closure makes those boundaries literal in production. After force-stop has committed `stopping`, controller reload, release construction, flush, and response-path cleanup are all inside the shutdown-guaranteeing finalizer. Worker cleanup queue items are accepted only from a task currently registered in the service Worker set, including while OPEN, so an ordinary task cannot forge cleanup before close. ACP admission and prompt cancellation now share a genuinely wall-clock-bounded close-then-disconnect sequence: timeout never waits for a coroutine that swallows cancellation, overdue tasks are explicitly terminated/tracked with consumed outcomes, close-side `CancelledError` still proceeds to bounded disconnect, and cleanup errors never replace the triggering cancellation. The daemon sink submits disconnect authority synchronously on the registered Worker task and publishes the exact persisted session/turn identity before the activation Future resumes, closing the activate-return cancellation window without non-unique native-session lookup.

Final quality closure removes the remaining precedence and liveness shortcuts. Predecessor controller evidence must be the exact singleton state `released`; journal/outbox release+expiry conflict is a zero-write blocker. ACP cleanup re-raises outer cancellation, returns structured close/disconnect status, shields the service-owned durable Future, and makes normal completion fail into existing attempt ambiguity unless durable disconnect succeeds, so no succeeded reply can coexist with a busy/ready session. Activation publishes its exact new session/turn IDs before the service Future resumes and disconnect never falls back to non-unique native identity. A single-operation admission reservation plus pending-cleanup health gate bounds retained cleanup work and resets on factory failure. Service close has a five-second default Worker grace; expiry still closes the server, reports an explicit health failure, consumes eventual task exceptions, and preserves external-outcome facts for restart recovery. Finally, an already durable force-stop remains an accepted `stopping` RPC even if controller cleanup fails: the response carries only a compact credential-free cleanup/restart diagnostic, the durable `daemon_force_stopped` audit remains recovery truth, and process shutdown is still requested.

Repeated cancellation is also fail-closed at the exact authority edge. When a second cancellation interrupts the close wait, `_close_then_disconnect` retains the cancellation-resistant close task, marks close failed, synchronously calls the daemon sink's `begin_disconnect` while still on the registered Worker, retains that durable cleanup awaitable, and only then re-raises cancellation without another await point. Both admission and prompt tests prove the original task remains cancelled, the exact persisted session reaches `disconnected`, and retained close work returns to zero after the test releases the stubborn adapter.

Daemon process teardown now has two explicit bounded layers. `_serve_daemon` always attempts service/server close, durable `stopped` recording, and owned endpoint metadata removal; a close/grace failure is re-raised only after the latter cleanup attempts, so no stale socket or PID metadata survives that failure. The hidden `daemon serve` command alone uses a dedicated event loop: once the main serve coroutine finishes, it cancels pending tasks, waits a fixed five-second grace, consumes completed exceptions, and closes the loop after a compact `pending_task_count` diagnostic if cancellation-resistant work remains. That diagnostic is not proof of task termination. It deliberately preserves submitted/busy durable facts for restart ambiguity, while reachable ACP transport cancellation continues through its existing bounded close/terminate-to-kill plus exact durable disconnect path. No process-group signal is introduced, and all other CLI async entrypoints retain `asyncio.run`.

The managed tmux edge is now bounded before it enters `asyncio.to_thread`: all ten `TmuxBackend` subprocess calls share an explicit five-second timeout, including private-buffer cleanup. `subprocess.run` kills and waits for its direct child on timeout; the transport maps admission/capture timeout to `WorkerTransportError`, so the coordinator retains admission ambiguity or records a failed completion and the scheduler blocks. `agentdeck doctor` catches the same timeout at the backend boundary and emits valid non-success JSON containing only `tmux command timed out`; it never prints the timed-out argv/path or a traceback. Return-control pane verification explicitly treats `TimeoutExpired` as unverifiable runtime evidence, which enters the existing persisted reconciliation-ambiguity path and leaves the takeover baseline active. The real daemon acceptance puts a permanently blocking fake `tmux` on `PATH`, reaches it through the production ProjectView/scheduler/transport path, sends SIGTERM to the real daemon entrypoint, and verifies every main/cleanup fake-tmux PID, daemon PID, socket, metadata file, and detached reaper is gone within timeout plus shutdown grace and margin. This guarantee is intentionally limited to managed transport subprocesses: AgentDeck does not claim it can kill an arbitrary Python thread or unmanaged descendant, and it does not use `killpg`.

That acceptance also closed two adjacent truth gaps. The daemon scheduler and transport factory now read the actual `ProjectView.agents` dataclass field, rather than failing closed on a dict-only `.get` and making tmux execution unreachable. Public `StateStore.save()` now delegates to the existing temp-file/fsync/replace boundary; a deterministic paused-writer regression proves concurrent readers see the complete old JSON until the complete new JSON is atomically installed. Both public `save` and `_atomic_save` fault-injection seams retain their prior behavior.

The repeated full-suite background acceptance exposed one adjacent daemon read/write race rather than a retry-only flake: protocol-event outbox drain held the mutation lock but cleared `state.json` through the legacy truncate/write helper, while external readers intentionally do not take that lock. The lock-owned clear now uses atomic replacement, preserving the existing retry/no-duplicate journal semantics and preventing ProjectView/acceptance readers from seeing an empty file.

Focused TDD evidence includes four Mission RED-to-GREEN cases, five ACP shutdown/admission-cancellation RED-to-GREEN cases, force-stop RED-to-GREEN with ordinary-stop compatibility, a real StateStore + daemon sink + ACP transport shutdown/restart reconciliation case, and a detached-daemon two-process Mission preview/confirm acceptance with no residual endpoint or reaper.

Task 12 review closure tightens four boundaries. The live ACP response edge now treats human approval as policy input only: before selecting `allow_once`, it atomically revalidates the exact `acp` attempt / `acp-adapter` AgentSession / prompt turn / permission binding and runs real frozen-scope, policy, and ownership gates, recording the result. Startup uses the same strict provider/workspace/capability/dispatch lineage and rejects corrupt bindings before scheduler activation. Restart classification is transport-derived: tmux may rely on durable observable receipts, but an active ACP submitted/running connection is ambiguous before permission state, and that persisted blocker is consumed by the live scheduler. Takeover persists a bounded generation-bound baseline for session/turn lineage, artifacts, and a hashed worktree manifest; return-control requires exact bounded `reported_changes`, unchanged protocol/artifact authority, a safe boundary, and an execution-time rescan matching its preview. Missing reports, drift, or unsafe filesystem evidence fail closed, while successful return consumes the baseline and retains its report.

The Task 12 spec re-review additionally forbids empty or merely self-hashed runtime evidence. ACP takeover/return requires one configured target Worker and one exact ready `acp-adapter` session with matching provider/project workspace/native identity/capabilities and no active turn. tmux requires the exact running project binding plus a read-only `pane_exists` verification through that project's configured socket/session; it never probes another project. Projection, gate, or confirmation failure keeps the baseline active and atomically appends an exact ambiguous `worker_reconciliation_decisions[]` record with conversation/recovery audit evidence. ProjectView conversation blockers and matching scheduler facts expose that decision until a fully verified return resolves it. ACP `allow_once` is also durably consume-once: permission/tool-call/effect consumption commits before the allow response, exact replay is byte-stable `permission_consumed`, conflicting lineage fails closed, save-before-response failure grants nothing, and post-commit retry denies rather than risk a duplicate external effect.

Final Task 12 review closure removes the remaining authority and lifecycle gaps. Frozen/admitted Mission resume and natural-language confirmation no longer enter the foreground runner: daemon Mission resume obtains a controller-lease-bound exact preview and confirms only the returned `gov_*` command, while incomplete frozen authority is inspect-only and snapshot-less M1 records retain explicit legacy compatibility. Frozen Worker authority now includes a compact `runtime_identity_hash` over command, ACP transport argv, role prompt, and project runtime backend/session/socket identity without persisting raw invocation values, so later runtime configuration drift cannot silently change the confirmed Worker invocation. A single ACP attempt may now bind multiple sequential permission/tool-call requests, each with independent exact retry, recovery validation, consume-once authorization, conflict rejection, and crash zero-write. ACP transport close persists `disconnected`, so return-control cannot accept a closed session as ready evidence. daemon stop/force-stop now signal exit from the durable commit boundary rather than successful acknowledgement delivery, and service close cancels cooperative Worker tasks without retaining completion work.

The scoped permission handle now owns controller cleanup as a durable terminal obligation. Expiry/capacity purge, confirmation completion/failure, and daemon close must retire the exact private controller generation before discarding the registry record; cleanup failure retains the bounded record and reports an explicit credential-free failure. Production releases an exact active controller, expires an exact elapsed controller, and treats terminal or replacement generations as already inactive, so a 300-second confirmation can no longer leave its 3,600-second controller hidden and a stale handle can never release a newer controller. If the permission decision is already durable but retirement fails, the RPC says `permission state committed; controller cleanup incomplete` rather than returning false success.

The M2 daemon now owns frozen tmux Worker startup as an explicit scheduler transition. It persists one compact start claim before creating the session/pane, treats a lost receipt as ambiguous instead of replaying the spawn, and records the exact binding or sanitized blocker. Scheduler fact loading remains read-only: it derives missing/claimed/started/blocked startup state and performs only a provider-aware tmux readiness probe. Claude/Codex first-run setup is never answered automatically, and dispatch revalidates both `runtime_identity_hash` and the frozen step `task_hash`.

Fresh verification for this final review closure is 22 doctor/contract/reconciliation focused tests passing, 65 managed-tmux/daemon/atomic-save focused tests passing, 414 fault-injection/atomic-state compatibility tests passing, 1,225 broad test-name-selected daemon/ACP/protocol tests passing with one skip, and 2,675 full-suite tests passing with one skip. Compileall and `git diff --check` pass. The detached-daemon governance acceptance verifies endpoint/reaper cleanup, the production serve cleanup regression proves close failure still records `stopped` and removes socket/PID metadata, the daemon-loop runner regression proves cancellation-resistant teardown exits within its bound with only a compact diagnostic, the blocking-tmux OS-process acceptance proves every managed child is reaped, and the in-process real RPC acceptance proves post-commit force-stop cleanup failure still returns accepted/stopping before the endpoint exits.

Task 11 production truth: `mission.admit` persists exact-digest daemon admission or `confirmed_not_admitted`; ProjectView exposes compact `daemon_admission`; `_daemon serve` loads real SchedulerFacts and applies controlled transitions; every RPC mutation and Worker completion returns through one service-owned queue. The exact controller lease is retained and revalidated inside that queue immediately before mutation. Both tmux and ACP persist an exact standalone submitted receipt before completion begins. An ACP crash after session admission is therefore a submitted unknown external effect, never a replayable admission; validated result plus compact reply still commit atomically. Prompt/update/parse/finish/cleanup failure transitions the submitted attempt to a bounded stage-specific ambiguity without persisting exception, command, path, or payload text. ACP and tmux use explicit configured adapters without fallback. The real disconnect acceptance starts `_daemon serve`, admits through `DaemonClient`, closes the client, runs two official-SDK fake ACP Workers, and observes two succeeded attempts, validated replies, recorded handoffs, and a completed Mission in the real StateStore.

The final compact-handoff closure makes that acceptance prove the actual A→B data path rather than only terminal states. Every validated structured transport result, including blocked/failed output, is reduced through the same bounded `CanonicalHandoff` allowlist and stored on the durable reply; only `completed` can advance. ACP and tmux each commit their already-submitted result plus canonical reply in one lock/save. Exact structured non-success retries return the same persisted reply and conflicting content is zero-write. Handoff recording copies and revalidates exact content, audit events bind its canonical hash, and recovery rejects audit/content drift. Production prompt construction resolves only the immediately preceding step in the frozen same-Mission order and requires one exact `succeeded` attempt, one validated reply, and one recorded handoff with the same dispatch token/content. It also revalidates the frozen Worker runtime-identity hash and raw task hash before constructing either transport. It never guesses the latest reply or crosses Mission lineage. The real subprocess test runs twice consecutively, records the prompt each fake ACP Worker actually received, proves reviewer observed planner's recorded handoff before starting, proves planner summary/verification reach reviewer, and excludes private reasoning/full transcript/secret markers. Its cleanup guard begins before startup/admission, discovers delayed PID metadata, and covers every setup/assertion path; graceful stop is followed by bounded TERM/KILL fallback, then production `reconcile_endpoint()` verifies stale ownership/PID before unlinking and the test waits for daemon PID/socket/metadata/reaper cleanup. A deliberate post-admission failure test exercises forced cleanup.

ProjectView admission projection is now independently fail-closed at its read boundary: it reconstructs only the exact five `daemon_admission` fields and accepts only coherent `not_confirmed`, `confirmed_not_admitted`, or `admitted` type/state combinations. Malformed, missing, or extra-field records become a deterministic safe `not_confirmed` sentinel plus a fixed blocker; rejected values and credentials never reach ProjectView, status, or workbench. This projection performs no state/event repair writes.

Task 11 review closure also makes admission response loss converge on durable truth: if the exact Mission was admitted before the response disappeared, the caller receives `accepted=true/state=admitted`, not a contradictory `confirmed_not_admitted` wrapper. The service wakes immediately for queued work and alternates one queued callback with one scheduler opportunity, preventing a self-replenishing RPC/completion queue from starving Mission advancement. Explicit release is recognized from validated durable journal/outbox evidence for the exact lease generation, so daemon restart cannot append a false expiry after release. Focused regressions cover queue-backlogged stale lease rejection with zero writes, prompt-in-flight ACP shutdown retaining `submitted`, ACP completion success/ambiguity/idempotency/conflict behavior, release/stop restart audit ordering, bounded scheduler fairness, and the real disconnect acceptance.

Fresh Task 11 verification after the final compact ProjectView hardening is 887 project-view/contracts/daemon CLI tests, 711 daemon regressions, and 2,571 full-suite tests passing with one skip. The known idle-grace timing test failed once in an earlier full run, then passed both its exact rerun and the final full suite; no unrelated idle-loop code changed. Compileall and `git diff --check` pass.

Task 6's stop path is a complete production flow rather than a test-state shortcut: `daemon stop --confirm` acquires a temporary controller only when no active controller exists, durably flushes lease grant/release audit events, releases before acknowledgement, and sets the server-owned stop event only after response drain. A rejected automatic stop now invokes lease-gated `controller.release`; release must be confirmed or cleanup becomes an explicit blocker, while user-supplied credentials are never auto-released. `controller.renew` and `controller.release` both require the current lease; automatic takeover and background outbox flushing are not implemented. Lease credentials remain RPC-internal and are not added to ProjectView/workbench cards.

The Task 6 quality closure also makes the hidden daemon's idle loop reload the full persisted keepalive view every poll. Client-only activity is `ready`; Mission/Worker/pending approval, permission, reply, recovery/decision/ambiguity, outbox, recovery, safe-shutdown, or atomic-write work is `busy`; only an empty reason set enters `idle_grace`, and a new connection cancels that timer. Live status derives `controller_present` from the current unexpired lease. The idle poll commits and synchronously flushes one terminal expiry transition, so ProjectView cannot retain an active controller indefinitely and repeated polls do not duplicate expiry audit events. `agentdeck daemon status` itself remains zero-write. Strict daemon controls now require `enabled=true` with `blocker=null`, or `enabled=false` with a non-empty blocker.

The final Task 6 spec closure makes offline ProjectView use the same pure time-aware lease predicate as live status: only a strictly parsed active `lse_` lease whose aware expiry is later than current UTC reports `controller_present=true`; expired, terminal, naive, and malformed facts report false without repairing or writing state. DaemonServer also owns a monotonic process-local `activity_generation`: accept and each successfully decoded protocol-valid request increment once, while close never increments. The idle loop remembers the last generation and resets `idle_since` before evaluating keepalive, so a sub-100ms client that connects and closes entirely between polls still grants a new full idle window. This counter is runtime-only, is not added to ProjectView/contracts, and is not execution authority.

Historical routing note: before the Codex probe was made zero-write, the active instruction was to investigate the Task 11 `probe_wrote_files` blocker and rerun preflight before any live attempt. That instruction was completed and is no longer the active route. Frozen historical live results remain evidence only. The approved semantic-authority and Leader Preview observability work at `9db5b476f885cfcf68a55cbf59673a2d908d3fce` used its one explicit-model preflight and one separately authorized live attempt, which stopped at `leader_schema_before_preview` / `semantic_effect_conflict`; neither may be rerun in place. The target-exclusivity and pytest-redaction slice frozen at `75f0366d4d5619b29c77f10949365f43d46185b1` also used exactly one preflight and one live attempt, which stopped at `native_schema_provenance_missing`; neither may be rerun. That blocker was corrected and verified at new frozen SHA `7a76ada81938be3ba0720a7c2f5a540b4beebb3e`, but its own exact `gpt-5.5` cycle was exhausted at preflight/live `1/1`; live stopped at fixed `preflight_blocked`. At that historical checkpoint, the prescribed follow-up was a design/TDD cycle for same-authority binding and closed internal diagnostics. That prescription has been superseded by the P0-P5 architecture-reset program and carries no current execution or retry authority. At the time, M2c was classified **BLOCKED**, M3 had not started, and A2A Client/Server, remote daemon, global roaming, Workspace Client, system notifications, complete transcript persistence, automatic install/auth, Windows IPC, and terminal-emulator work were out of scope.

The completed natural-language Mission and G-series work below is historical context only. It must not be treated as an active continuation request or redone.

## Canonical Handoff Inputs

When switching from Codex to Claude Code CLI or another local agent, read these files first:

1. `CLAUDE.md`
2. `AGENT.md`
3. Top of `HISTORY.md`
4. `docs/roadmap/product-north-star.md`
5. `docs/roadmap/ultimate-goal-roadmap.md`
6. `docs/superpowers/plans/2026-07-17-agentdeck-v1-architecture-reset-program.md`
7. `docs/superpowers/plans/2026-07-17-agentdeck-p0-product-reset.md`
8. This file

Then inspect current state with:

```bash
git status --short
git log --oneline -5
git diff --name-only f3968720..HEAD -- src/agentdeck tests .agentdeck
```

## Current Phase

The approved [AgentDeck V1 architecture-reset program](../superpowers/plans/2026-07-17-agentdeck-v1-architecture-reset-program.md)
is the current development route and must run in strict P0-P5 order. P0 Tasks
1–9 are frozen. The current action is Task 10: a read-only human P0 exit
review of the seven durable documents, deterministic baseline, scope evidence,
and repository status. Task 10 authorizes no document or product-source change,
provider call, ACP/tmux session, daemon, preflight, live Mission, merge, or
push.

P1 remains locked. Only after explicit human approval of the P0 exit gate may
a separate P1 Durable Mission Kernel task-level TDD plan be created with
`writing-plans`; that approval does not itself authorize P1 implementation.
Old M2c evidence is historical only: it is not a release veto, a current
scheduling gate, or authority to retry any preflight or live node.

Earlier implementation and validation facts remain available in
[`HISTORY.md`](../../HISTORY.md),
[Phase 3 M1 validation](../validation/2026-07-13-phase3-m1-foreground-conversation.md),
[Phase 3 M2 validation](../validation/2026-07-13-phase3-m2-project-daemon.md),
and [historical M2c validation](../validation/2026-07-17-phase3-m2c-four-stage-live.md).
They are evidence, not continuation instructions.

## Cross-Agent Goal Continuity

Codex App `/goal` is session-local state. It does not automatically transfer into Claude Code CLI.

Claude can still continue the same work by treating this repository as the source of truth:

- `HISTORY.md` is the development timeline.
- `CLAUDE.md` and `AGENT.md` are the behavioral constraints.
- `docs/roadmap/product-north-star.md` is the active product north star.
- `docs/roadmap/ultimate-goal-roadmap.md` is the historical capability roadmap.
- The architecture-reset program and P0 plan define the current task order.
- This handoff file carries the current active goal and next slice.
- Git commits are the durable recovery points.

Suggested prompt for Claude Code CLI:

```text
Please perform the read-only AgentDeck P0 exit review from this repository. Do not continue implementation.
Read CLAUDE.md, AGENT.md, the top of HISTORY.md, docs/roadmap/product-north-star.md, docs/roadmap/ultimate-goal-roadmap.md, docs/handoff/current-development-state.md, docs/superpowers/plans/2026-07-17-agentdeck-v1-architecture-reset-program.md, and docs/superpowers/plans/2026-07-17-agentdeck-p0-product-reset.md first.
Use conda activate agentdeck or conda run -n agentdeck for commands.
P0 Tasks 1-9 are frozen. Execute only Task 10's read-only repository-state and evidence review, including the final f3968720..HEAD source/test/runtime scope audit. Report whether the human P0 exit gate can be approved, but do not mark it approved yourself.
Do not modify documents, product source, tests, or runtime state. Do not call a provider, start ACP/tmux sessions or daemons, run preflight/live Missions, merge, push, install tools, or change authentication/global settings.
P1 task-level planning may begin only after explicit human P0 exit approval and must use writing-plans. P1 implementation remains locked until separately authorized.
```

## Historical development log — not active

Everything below this heading is retained only as historical implementation context. Any wording about a direction, work in progress, a next step, a chosen lane, or verification reflected the state at that earlier time; it is not a current instruction and must not override the active goal above. Use `HISTORY.md` for the durable timeline. Do not resume or redo any item below unless a human explicitly approves it.

The explicit release command slice is already committed:

```bash
agentdeck release --confirm
```

Expected behavior:

- Refuses without `--confirm` and writes nothing.
- Validates ProjectView, then reuses the same `review_gate_card` facts.
- Refuses when the gate is blocked, appending `round_release_rejected` with the same gate `reason`.
- Refuses when the same code-review / round-review reply pair was already released (`round already released`).
- On success appends a release record to `releases[]` plus a `round_released` audit event, and returns a GUI-ready payload with `safety=explicit_user`, trace commands for both review replies, and a disabled `agentdeck leader plan --task <goal>` next-round template.
- Does not merge, ack inbox items, dispatch follow-up work, create plan/action/approval/message/job/inbox, call a provider, or read/write tmux.

The release-preview wiring slice is already committed:

```bash
agentdeck workbench
```

New behavior:

- When the review gate is ready, `release_preview_card.release_command` / `next_command` point at the explicit `agentdeck release --confirm` command and the `release_preview` control becomes an enabled `explicit_user` control with the same command.
- `next_round_command` exposes the disabled `agentdeck leader plan --task <goal>` template with blocker `requires goal text`.
- While the gate is blocked, all three command fields stay `null` and the explicit controls stay disabled with the gate reason.
- The workbench validator rejects an enabled release control without `can_release=true` or with a command that drifts from `release_command`.
- Rendering the card still never releases; only a human running `agentdeck release --confirm` records the round release.

The release history slice is already committed:

```bash
agentdeck status
agentdeck workbench
```

New behavior:

- ProjectView exposes a top-level `releases` summary (`count`, `items[]`); each item carries the release id, round number, review-gate snapshot, both reviewer/reply ids, and a `trace_command` pointing at the round-review reply lineage.
- `release_preview_card` gains `already_released`, `release_count`, and `latest_release_id` derived from the same summary.
- When the review gate is ready but the current code-review / round-review reply pair was already released, the card reports `status=released` with reason `round already released`, withdraws `release_command` / `next_command`, and keeps only the disabled next-round plan template.
- Validators reject a released card that still exposes executable release commands and require a ready review gate behind any released card.

The release contract discovery slice is already committed (Phase G5 complete):

- Read-only `agentdeck contract release` / `--example` discovery, and `agentdeck release --confirm` now self-validates via `validate_release_contract()`.

## Historical G6 context

Phase G6 Role Topology GUI was completed before the Phase 0/1 protocol-native work. The following entries are retained only as implementation history, not as an active phase or continuation instruction.

The first G6 slice is already committed:

```bash
agentdeck workbench
```

- Adds `role_topology_card`, a read-only unified role topology (logical roles + worker roles, each with kind/provider/lifecycle/status/blocker/next_command and an inspect-only control).

The second G6 slice is already committed:

```bash
agentdeck workbench
```

New behavior:

- `role_topology_card` now overlays the `review_gate_card` stage status onto the matching reviewer worker role: a `ready` stage → `status=reviewed`, a `waiting_for_review` stage → `status=reviewing` (no blocker), and any other stage (`waiting_for_artifacts` / `blocked`) → `status=blocked` with the stage's blocker.
- Non-reviewer worker roles keep their base `lifecycle_stage` status with a `null` blocker.
- Still read-only: the overlay never advances the gate, spawns, dispatches, captures, acks, releases, or writes state.

The first G6 surface details:

- Adds `role_topology_card`, a read-only unified role topology.
- Projects the three logical Leader coordination roles (`frontdesk`, `planner`, `orchestrator`) from `leader.coordination_roles[]` plus the configured worker roles from the same `worker_lifecycle_card` items.
- Each role carries `kind` (`logical_role` | `worker`), `provider`, `lifecycle`, `runtime_kind`, `pane_backed`, `pane_id`, a derived `status`, `blocker`, `next_command`, and a single inspect-only control.
- Logical roles keep `runtime_kind=logical_role` / `pane_backed=false` / `pane_id=null` / `agent_id=null`; their inspect control points at their own read-only state source (`frontdesk` → `agentdeck leader chat-history`, `planner` → `agentdeck plan list`, `orchestrator` → `agentdeck leader actions`). Worker roles use `runtime_kind=worker_pane`, reuse the worker `lifecycle_stage` as `status`, and inspect via `agentdeck inbox --agent <id>`.
- All controls appear in `control_registry[]` / `agentdeck controls` under `scope=role_topology`.
- Does not spawn, dispatch, capture, ack, release, or write state; every control is inspect-only.

The third G6 slice is already committed:

```bash
agentdeck leader chat --message "查看角色拓扑"
```

Expected behavior:

- Returns read-only `mode=role_topology`.
- Embeds the same `role_topology_card` as `agentdeck workbench`.
- Attaches a `control_registry_card` filtered to `scope=role_topology` / `card=role_topology_card`, selecting the card-level `agentdeck workbench` inspect control.
- Records only the chat turn and its audit event; does not call a provider, create plan/action/approval/message/job/inbox, spawn, dispatch, capture, ack, release, or read/write tmux.

The fourth G6 slice is already committed:

```bash
agentdeck workbench
```

New behavior:

- The `role_topology_card` logical-role overlay now marks `orchestrator` as `waiting_for_approval` (blocker `waiting for human approval`) when any approval is pending, `coordinating` when a pending Leader action exists, `released` when at least one round has been released, and `idle` otherwise.
- Only `orchestrator` carries a blocker; `frontdesk`/`planner` keep `null`.
- Still read-only: the overlay only projects ProjectView facts and writes no state.

The fifth G6 slice is already committed:

```bash
agentdeck workbench
```

New surface:

- `role_topology_card` now carries `by_status` (per-status counts) and `blocked_count` (roles with a non-null blocker); the validator requires `blocked_count` to match the roles carrying a blocker.

The sixth G6 slice is already committed (test-only coverage):

- A project configuring agents with roles `code_reviewer` / `round_reviewer` surfaces them as distinct worker roles; with an artifact but no review replies the code reviewer shows `reviewing` and the round reviewer shows `blocked` (`code review is not ready`). Worker order follows configured agent order.

Phase G6 (Role Topology GUI) is now functionally complete: workbench `role_topology_card` (logical + worker roles, review-gate overlay, orchestrator approval/release overlay, status summary) plus the read-only natural-language `role_topology` chat discovery.

The seventh G6 slice is already committed:

- The natural-language `role_topology` chat `leader_explanation.summary` now reports role count and blocked count (e.g. "...role topology with 6 roles (1 blocked)...").

Phase G6 (Role Topology GUI) is complete across workbench + natural-language surfaces.

The layered-role walkthrough is already committed:

- `docs/walkthroughs/layered-role-round.md` walks a full round (frontdesk intake → coordination topology → plan → approval → dispatch + worker lifecycle → review gate → release → role topology → recovery/loop) against the read-only contract surfaces and explicit human commands, cross-linking each phase's contract. Linked from the README top.

Phases G1–G6 are complete and now documented end-to-end.

## Historical direction: TUI reference client

The user chose to build a read-only TUI/CLI reference client that consumes the workbench + control_registry contracts, proving the contracts are sufficient to drive a GUI (no new backend behavior).

The first slice is already committed:

```bash
agentdeck dashboard
```

- Adds `src/agentdeck/dashboard.py` with the pure function `render_workbench_dashboard(payload)` and the `agentdeck dashboard` command.
- Renders header / recovery / role topology / review gate / queue as human-readable text, deriving every value and echoed command from the workbench contract payload alone.
- Reuses the same `_workbench_snapshot_payload` + `validate_workbench_contract()` as `agentdeck workbench`; read-only, no state writes.

The second and third slices are already committed:

- Slice 2: a "Command palette" section from `control_registry[]` grouped by scope (total / enabled / blocked per scope) with a `agentdeck controls --scope <scope>` drill-down pointer.
- Slice 3: "Release" and "Ledger" sections derived from `release_preview_card` (shows `agentdeck release --confirm` when ready) and `ledger_card` counts.

The dashboard now renders: header, recovery, role topology, review gate, release, ledger, queue, command palette — all from the workbench contract payload alone.

The fourth slice is already committed:

- `docs/walkthroughs/tui-reference-client.md` documents the reference client (section→card mapping, real sample output, the sufficiency argument), linked from the README `dashboard` paragraph.

The TUI reference-client direction is complete: `agentdeck dashboard` renders header / recovery / role topology / worker activity / review gate / release / ledger / queue / command palette purely from the `agentdeck workbench` contract, with tests (`tests/test_dashboard.py`) and a doc. A worker-activity section (per-worker lifecycle stage + active task ids + inbox/artifact counts) was added as polish.

## Historical autonomous run (completed directions 1 → 2 → 3)

The user approved doing all three directions in order, autonomously, overnight. Progress:

- Direction 1 (assisted run flow): first slice committed — a read-only "Run progress" section in `agentdeck dashboard`, derived from the existing `run_progress_card`, showing plan/step/approval status and the single explicit next command. It guides the human step-by-step but never executes (approval discipline preserved).
- Direction 2 (learning-layer GUI, Phase F): three slices committed — (a) a read-only "Learning layer" section in `agentdeck dashboard`; (b) `agentdeck learn review` defaults `--plan-id` to the latest plan; (c) a workbench `learning_review_card` (the earlier-deferred item, now done at the user's request): mirrors `leader_summary_card` — `null` until the latest plan review is `next_action=summarize`, then reuses the `agentdeck learn review` shape and enters `control_registry[]` under `scope=learning_review`. Read-only; the explicit `skills suggest` / `memory suggest` commands remain the only write path.
- Direction 3 (dashboard `--watch` polish): committed — `agentdeck dashboard --watch [--interval N] [--iterations N]` re-renders the text dashboard, mirroring `workbench --watch`, still read-only.

All three approved directions (1 → 2 → 3) have landed committed slices; the whole run kept the suite green (621 passing after the workbench `learning_review_card`).

## Historical direction: interactive curses TUI

`agentdeck tui` is a read-only interactive curses viewer over the workbench contract. First slices committed:

- `src/agentdeck/tui.py` with the pure, unit-tested `TuiModel` (navigation/selection/scroll/refresh) and `render_frame(model, height, width)` (screen layout); the curses I/O in `run_tui` is a thin shell.
- `agentdeck tui` command: builds+validates the workbench snapshot, launches curses; declines cleanly when not a TTY.
- Overview (scrollable dashboard) + palette (browsable `control_registry[]`); footer shows the selected control's safety/enabled/blocker and the exact `run: <command>`. Strictly read-only — it never executes.

A palette filter is also committed: `/` in the palette opens a filter prompt; `TuiModel.set_filter(text)` narrows controls by substring across scope/kind/label/command, re-clamping selection. Read-only.

All three optional TUI polish items are now committed: (1) the palette focuses the recovery `next_command` on open; (2) `?`/`h` opens a key-legend help overlay; (3) palette rows are colorized (selected reverse, disabled dim). All read-only; the styling decision is a pure, unit-tested `palette_row_style` / `palette_row_styles`.

## Historical next-step note

**The whole autonomous-mode goal (all three sub-projects) is done.** All three preserve human approval and keep every read-only surface read-only.

- **Sub-project 1 of 3 — audit / HISTORY gate (done)**: `agentdeck history` renders the `events.jsonl` ledger into a read-only, newest-first, date-grouped Markdown timeline (`src/agentdeck/history.py`, `StateStore.all_events()`, `tests/test_history.py`), with `--write` materializing `.agentdeck/HISTORY.md` and `--limit N` to cap. Design + plan: `docs/superpowers/specs/2026-07-08-agentdeck-history-timeline-design.md` and `docs/superpowers/plans/2026-07-08-agentdeck-history-timeline.md`.
- **Sub-project 2 of 3 — bounded autonomous mode (done)**: `AutonomousPolicy` + `[autonomous]` config (`models.py`/`config.py`), the pure `select_auto_approvals` decision (`src/agentdeck/autonomy.py`), `agentdeck policy set-mode --mode autonomous --confirm --allow-agent <id> --max-approvals <N>` (validated allowlist/budget writer), and `agentdeck approval auto --confirm` (auto-approve allowlisted, budget-bounded pending approvals and dispatch them to already-running panes — no force-spawn, stops at dispatch, fully audited). `control_mode_card` autonomous is enabled with a disabled `set_mode` template. Design + plan: `docs/superpowers/specs/2026-07-08-autonomous-mode-design.md` and `docs/superpowers/plans/2026-07-08-autonomous-mode.md`.
- **Sub-project 3 of 3 — executing round loop (done)**: `agentdeck run-loop --plan-id <id> --confirm` is the write counterpart to the read-only `agentdeck loop`. It performs one sanctioned autonomous wave for a plan (auto-approve allowlisted pending within budget via `select_auto_approvals`, dispatch approved-and-ready to running panes via the existing dispatch internals), then reuses `leader review` + the pure `run_loop_gate` (`src/agentdeck/autonomy.py`) to diagnose the resulting human gate and stops there with an explicit `next_command` (`stopped_reason` ∈ error/blocked/needs_human_approval/waiting_for_reply/complete/idle). Requires `--confirm` + autonomous mode; never force-spawns; never captures replies or infers completion; fully audited (`run_loop_advanced` → `agentdeck history`). Contract: `agentdeck contract run-loop` + `docs/contracts/run-loop-schema.md`. Design + plan: `docs/superpowers/specs/2026-07-08-run-loop-engine-design.md` and `docs/superpowers/plans/2026-07-08-run-loop-engine.md`.

The interactive TUI is feature-complete (overview/palette/help, filter, refresh, focus, colors) and fully tested — `run_tui` is covered end-to-end via a fake stdscr (`tests/test_tui.py`). The TUI/dashboard reference-client line is done.

An end-to-end integration test now locks the whole autonomous chain across invocations: `tests/test_agent_cli.py::test_run_loop_drives_plan_to_completion_across_invocations` (policy set-mode autonomous → run-loop auto-approve+dispatch → `waiting_for_reply` gate → capture-reply → run-loop → `complete`, with two `run_loop_advanced` ledger events).

The autonomous commands are now **surfaced into the read-only command palette** (done): `control_mode_card.autonomous_actions[]` carries `kind=approval_auto` (`agentdeck approval auto --confirm`, `safety=delegated`, enabled only in autonomous mode, else blocker `autonomous mode is not enabled`) and a disabled `kind=run_loop` template (`agentdeck run-loop --plan-id <id> --confirm`, blocker `requires --plan-id`); both flow into `control_registry[]` / `agentdeck controls --scope autonomous` under `scope=autonomous`. Both the cli `_workbench_control_registry` and the mirror `contracts.workbench_control_registry` (used by `validate_workbench_contract`'s cross-check) append the group; the `workbench_example()` fixture was updated to match. Rendering is not authorization — the commands still require explicit human `--confirm`. Design + plan: `docs/superpowers/specs/2026-07-08-autonomous-controls-lighting-design.md` and `docs/superpowers/plans/2026-07-08-autonomous-controls-lighting.md`.

The final GUI-mainline follow-up is now **done**: `agentdeck leader chat --message "推进计划 pln_xxx"` (and `往前推`/`驱动计划`/`run-loop` variants) enters read-only `mode=run_loop_preview`, embeds `run_loop_preview_card`, hands back the explicit `agentdeck run-loop --plan-id <id> --confirm` as top-level `next_command`, and attaches a `scope=autonomous` `control_registry_card` whose selection points at the disabled `run_loop` template. It requires a plan id (no guessing), the next control is `safety=explicit_runtime` (disabled with `autonomous mode is not enabled` when autonomous is off), and the chat records only the chat turn + `leader_chat_turn` audit event — never a provider call, tmux read/write, auto-approve, dispatch, or approval/runtime/plan mutation. Detectors + card builder: `_chat_wants_run_loop_preview` / `_chat_run_loop_preview_plan_id` / `_run_loop_preview_card` (cli.py); contract: `run_loop_preview_card_fields` + the `run_loop_preview` mode check in `validate_leader_chat_contract` (contracts.py). Design + plan: `docs/superpowers/specs/2026-07-08-run-loop-chat-intent-design.md` and `docs/superpowers/plans/2026-07-08-run-loop-chat-intent.md`.

**Historical next-step note:** The autonomous-mode goal and its full GUI-mainline surfacing (command palette `scope=autonomous` + natural-language `mode=run_loop_preview`) were complete. At that time the human delegated the next direction ("你帮我决定"), and the historical selection was **"make the contracts visible — grow the human-facing dashboard/TUI cockpit"** (local, deterministic-testable via pure renderers + fake stdscr, directly monetizes the large read-only-contract investment).

Two slices of that lane are **done** (both in `render_workbench_dashboard`, shared by `agentdeck dashboard` and the TUI overview via `tui.py`):
1. **Control mode** section (`_render_control_mode`, `src/agentdeck/dashboard.py`) — the ask/approve/autonomous gradient + `approval auto` / `run-loop` command hints with enabled/blocked state. Test: `tests/test_dashboard.py::test_render_workbench_dashboard_shows_control_mode_and_autonomous_commands`.
2. **Runtime** section (`_render_runtime`) — the visible tmux binding: `<running>/<total> running` + each agent's `agent_id · role · status · pane:<pane_id>` from `runtime_card.agents[]` (distinct from logical `role_topology` and `worker_activity`). Test: `tests/test_dashboard.py::test_render_workbench_dashboard_shows_runtime_pane_binding`.
3. **Recent activity** section (`_render_recent_activity`) — the audit-ledger tail: `<event_count> events (agentdeck events --limit 20)` + up to 5 recent events (`created_at · event_type · event_id`) from `audit_card`, complementing the full `agentdeck history` timeline. Test: `tests/test_dashboard.py::test_render_workbench_dashboard_shows_recent_activity_ledger_tail`.

The dashboard/TUI overview now lays out: Header → Recovery → Run progress → Runtime → Role topology → Worker activity → Review gate → Release preview → Ledger → Queue → Control mode → Learning layer → Recent activity → Command palette.

The interactive TUI (`src/agentdeck/tui.py`) also gained two navigable read-only modes alongside overview/palette/help, each with select + status-aware command footer (commands straight from contract fields; the TUI never executes):
- **`approvals`** (`[a]`) over `approval_card.approvals[]` — footer command is pending→approve, approved→dispatch, else preview. Tests: `test_tui_model_approvals_view_navigates_and_shows_status_aware_command` / `test_tui_render_frame_approvals_lists_items`.
- **`runtime`** (`[g]`) over `runtime_card.agents[]` — rows show status·agent·role·pane; footer command is running→capture, else spawn. Tests: `test_tui_model_runtime_view_navigates_and_shows_status_aware_command` / `test_tui_render_frame_runtime_lists_agents`.

The TUI is now a view→run bridge: on quit it returns/prints the currently-focused command (`TuiModel.focused_command()` — palette control / status-aware approval / status-aware agent command; `run_tui` returns it; `tui_command` prints it after curses teardown). Still read-only — it prints, never executes. Tests: `tests/test_tui.py::test_tui_model_focused_command_reflects_active_view` / `::test_run_tui_returns_focused_command_on_quit`.

The "make the contracts visible" lane is now substantial (dashboard: Control mode + Runtime + Recent activity sections; TUI: approvals + runtime interactive views + print-selected-command-on-quit).

Already done, do NOT redo: `agentdeck dashboard --watch [--interval N] [--iterations N]` exists (`dashboard_command`, cli.py); `learning_review_card` is already a read-only workbench card (`_workbench_learning_review_card`, cli.py:1480).

## Historical direction: multi-plan lane ("多个计划同屏可见、分别推进")

The human picked the multi-plan-parallel lane: see all active plans at once and drive any of them separately. The state layer is already per-plan (`list_plans`, `plan_by_id`, `plan_status`, `leader_review`); the gap was purely visibility — nearly every read-only surface defaults to the single latest plan (`plans[-1]`).

**Slice 1 of the multi-plan lane is done:** read-only `agentdeck plan board` — a multi-plan overview that lists every plan with its derived `gate` and explicit per-plan `next_command`, plus `plan_count` / `active_count`. It reuses only the read-only `store.leader_review(plan_id)` + the pure `run_loop_gate(review, False, plan_id)` (`src/agentdeck/autonomy.py`); it calls no provider, reads no tmux, writes no state, appends no event. Contract: `agentdeck contract plans` + `docs/contracts/plans-schema.md` (`plan_board_*` helpers + `validate_plan_board_contract` in `contracts.py`, registered in `CONTRACT_INDEX_SPECS`). Design + plan: `docs/superpowers/specs/2026-07-09-plan-board-design.md` and `docs/superpowers/plans/2026-07-09-plan-board.md`.

**Slice 2 of the multi-plan lane is done:** the board is now embedded in the one-screen `agentdeck workbench` snapshot as `plan_board_card` (always present, never `null`). A shared helper `_plan_board_payload(store)` (`src/agentdeck/cli.py`) builds the same payload for both `agentdeck plan board` and `_workbench_snapshot_payload`; `WORKBENCH_SNAPSHOT_FIELDS` carries `"plan_board_card"`, `validate_workbench_contract` runs `validate_plan_board_contract` on the embedded card (prefix `plan_board_card: `), `workbench_example()` embeds `plan_board_example()`, and the workbench contract discovery payload exposes `plan_board_card_fields`. Doc: `docs/contracts/workbench-schema.md`. Read-only.

**Slice 3 of the multi-plan lane is done:** a read-only **Plans** section in `render_workbench_dashboard` (`_render_plans`, `src/agentdeck/dashboard.py`), derived from the `plan_board_card` — `<active>/<total> active` + one row per plan (`plan_id · active/done · gate · task`) with an indented `→ <next_command>`; shared by `agentdeck dashboard` and the TUI overview. Test: `tests/test_dashboard.py::test_render_workbench_dashboard_shows_plans_board` (a position-brittle TUI viewport assertion was repointed from "Role topology" to "Run progress"). Read-only.

**Slice 4 of the multi-plan lane is done:** a navigable read-only **`plans`** mode in the TUI (`src/agentdeck/tui.py`, key `[b]` for board), mirroring the approvals/runtime views — rows = `active/done · gate · plan_id · task`, footer = the selected plan's `next_command`, and `focused_command()` returns it on quit. Consumes `plan_board_card.plans[]`; never executes. Tests: `tests/test_tui.py::test_tui_model_plans_view_navigates_and_shows_next_command` / `::test_tui_render_frame_plans_lists_items`.

**Slice 5 of the multi-plan lane is done:** a natural-language read-only **`mode=plan_board`** chat intent (`agentdeck leader chat --message "查看所有计划" / "计划看板" / "所有计划" / "查看计划列表" / "计划总览" / "plan board"`). `_chat_wants_plan_board` (`src/agentdeck/cli.py`) routes without a plan id and without colliding with `run_progress`/`run_loop_preview` (those require `进度`/`推进`); the route embeds the same `_plan_board_payload(store)` card, sets `next_command="agentdeck plan board"`, `leader_explanation.action_kind=plan_board` (`safety=inspect`, `requires_explicit_user=false`), `intent_card.embedded_card=plan_board_card`, and `control_registry_card=None` (no `scope=plan_board` group). Contract: `plan_board_card` added to `LEADER_CHAT_RESPONSE_FIELDS` + leader-chat example + `LEADER_CHAT_PLAN_BOARD_CARD_FIELDS` + a `mode=plan_board` branch in `validate_leader_chat_contract` (reuses `validate_plan_board_contract`). Docs: `docs/contracts/leader-chat-schema.md`, `CLAUDE.md`, `README.md`. Tests: `tests/test_agent_cli.py::test_leader_chat_plan_board_is_read_only_and_embeds_board` / `::test_leader_chat_plan_board_variants_route`. Read-only.

**Read-only multi-plan visibility is now fully delivered** by the plan board command (slice 1) + workbench `plan_board_card` (slice 2) + dashboard Plans section (slice 3) + TUI `plans` view (slice 4) + this NL `mode=plan_board` intent (slice 5). Nothing further is needed to *see* the multi-plan state.

**Slice 6 (final) of the multi-plan lane is done — the lane is COMPLETE:** the **parallel scheduler** `agentdeck run-loop --all --confirm` (`_run_loop_all` + `_busy_agents`, `src/agentdeck/cli.py`). One round-robin wave over active plans (creation order), reusing the run-loop wave primitives, with a **shared** `max_approvals` budget and **skip-on-contention** (busy = dispatched-unreplied; recorded in each plan's `skipped_contention[]`), then stops. The human resolved the fork: 轮转 / 跳过 / 一波 / 复用. Single-plan `run-loop --plan-id` is byte-for-byte unchanged (the scheduler is additive). Contract: `agentdeck contract run-loop-all` + `docs/contracts/run-loop-all-schema.md` (`run_loop_all_*` helpers + `validate_run_loop_all_contract`). Audited via `run_loop_all_advanced` (`agentdeck history` → "Parallel wave · N plans, M dispatched"). Design + plan: `docs/superpowers/specs/2026-07-09-parallel-scheduler-design.md` and `docs/superpowers/plans/2026-07-09-parallel-scheduler.md`.

Multi-plan **recovery arbitration** (making `recovery`/`agentdeck continue` recommend *across* plans, not just `plans[-1]`) remains deliberately deferred: the read-only multi-plan visibility is fully delivered (plan board + workbench card + dashboard Plans + TUI plans view + NL `mode=plan_board`), and cross-plan steering is a scheduler-policy concern — revisit only if a concrete need appears.

**Historical next-step note:** the whole multi-plan lane (read-only visibility + parallel scheduler) was complete. At that time the remaining product-fork options were a standalone **GUI client**, a **Skill Registry marketplace/allowlist**, or **remote access / MCP**. This record does not select or authorize any current lane.

(Not yet wired: a `control_registry[]` `scope=plan_board` entry — deferred until a plan-board control surface is actually needed. The NL intent deliberately carries `control_registry_card=None`, so this is still not required.)

Whatever is chosen next must preserve human approval and keep every read-only surface read-only.

## Historical direction: skill marketplace lane

The human opened the **Skill Registry marketplace/ecosystem** lane (one of the forks offered after the multi-plan lane closed). The north star: a browsable, importable, reviewable, auditable skill ecosystem — built-in + external sources — where nothing installs silently and every install stays preview-gated and audited.

**Slice 1 of the skill-marketplace lane is done:** read-only `agentdeck skills catalog --source <dir>` — a "shop window" over a local skill source directory of `<name>/SKILL.md`. New pure `browse_skill_source(dir)` (`src/agentdeck/skills.py`, reuses `_snapshot_from_content`); `skills_catalog_command` (`src/agentdeck/cli.py`) compares each source skill against `discover_skills(root)` **project-sourced** skills for a three-state `import_status` (`not_imported` / `imported_identical` / `imported_differs` by name + content_hash) and surfaces per-item `import-preview` / `import` commands + controls. Response fields `SKILLS_CATALOG_RESPONSE_FIELDS` / item fields `SKILLS_CATALOG_ITEM_FIELDS`, exposed via the existing `agentdeck contract skills` (`catalog_command` / `catalog_response_fields` / `catalog_item_fields` — no new contract-index entry). Read-only: copies no files, writes no state, appends no event, calls no provider, touches no tmux; browsing never installs (install still goes through the explicit, preview-gated, audited `skills import --path <SKILL.md>`). Design + plan: `docs/superpowers/specs/2026-07-09-skill-catalog-design.md` and `docs/superpowers/plans/2026-07-09-skill-catalog.md`. Tests: `tests/test_agent_cli.py -k skills_catalog`, `tests/test_contracts.py::test_skills_contract_exposes_catalog_fields`.

**Slice 2 of the skill-marketplace lane is done (read-only, NON-ENFORCING trusted-source allowlist):** `[skills] allowed_sources` is now a hand-edited list of trusted local skill source dirs in `.agentdeck/config.toml`, parsed into `config.skills["allowed_sources"]` (default empty) and round-tripped through `_dump_config` so other config writers (`update_leader_approval_mode`, `update_autonomous_policy`) no longer drop a hand-added `[skills]` section (a round-trip test locks this). Read-only `agentdeck skills sources` lists the configured sources (`mode=skills_sources`, `source_count`, `sources[]` = `{path, exists, catalog_command}`, inspect controls). `agentdeck skills catalog` gained a top-level `source_allowlisted` (bool: True when the resolved `--source` equals or sits under a configured allowed source). It is **NON-ENFORCING**: any dir is still fully browsable and the catalog still lists everything — the flag is just a marker. Contract: `SKILLS_SOURCES_RESPONSE_FIELDS` + `source_allowlisted` on `SKILLS_CATALOG_RESPONSE_FIELDS`, exposed via `agentdeck contract skills` (`sources_command` / `sources_response_fields`); docs in `docs/contracts/skills-schema.md`. **REORDER rationale:** the trusted-source allowlist was pulled ahead of the workbench/NL surfaces so the later `skills_catalog_card` / "浏览技能源" intent can browse the *configured* sources with **no argument** (they need the config list to exist first).

**Slice 3 of the skill-marketplace lane is done (read-only workbench embed):** `agentdeck workbench` now always embeds a `skills_catalog_card` — a no-argument overview of the configured skill sources (config `[skills] allowed_sources`). Fields: `mode="skills_catalog"`, `source_count`, `total_skill_count`, `imported_count`, `sources[]` = `{path, exists, skill_count, imported_count, catalog_command}` (`catalog_command = agentdeck skills catalog --source <path>`). Derived in `_skills_catalog_card(config)` (`src/agentdeck/cli.py`), reusing `browse_skill_source` + shared helpers `_project_skill_hashes` / `_catalog_import_status` (extracted from `skills_catalog_command`, which now reuses them). Contract: `WORKBENCH_SNAPSHOT_FIELDS` + `WORKBENCH_SKILLS_CATALOG_CARD_FIELDS` / `WORKBENCH_SKILLS_CATALOG_SOURCE_FIELDS`, validated in `validate_workbench_contract` (ALWAYS present, never null), embedded in `workbench_example()`, exposed via `agentdeck contract workbench` (`skills_catalog_card_fields` / `skills_catalog_source_fields`). Docs: `docs/contracts/workbench-schema.md`. Read-only: copies no files, imports/loads no skills, writes no state, appends no event, calls no provider, touches no tmux. Test: `tests/test_agent_cli.py::test_workbench_embeds_skills_catalog_card`.

**Slice 4 of the skill-marketplace lane is done (read-only NL intent):** a natural-language read-only **`mode=skills_catalog`** chat intent (`agentdeck leader chat --message "浏览技能源" / "查看技能源" / "技能源" / "技能市场" / "技能目录" / "skill sources" / "skill catalog"`). `_chat_wants_skills_catalog` (`src/agentdeck/cli.py`) routes on the specific 技能源/技能市场/技能目录/`skill sources|catalog|marketplace` phrases (guarded so it does not collide with plan_board / run_progress / run_loop_preview); the route embeds the same no-argument `_skills_catalog_card(config)` card, sets `next_command="agentdeck skills sources"`, `leader_explanation.action_kind=skills_catalog` (`safety=inspect`, `requires_explicit_user=false`), `intent_card.embedded_card=skills_catalog_card`, and `control_registry_card=None` (no `scope=skills_catalog` group — mirrors plan_board). Contract: `skills_catalog_card` added to `LEADER_CHAT_RESPONSE_FIELDS` + leader-chat example + `LEADER_CHAT_SKILLS_CATALOG_CARD_FIELDS = WORKBENCH_SKILLS_CATALOG_CARD_FIELDS` + a `mode=skills_catalog` branch in `validate_leader_chat_contract` + discovery `skills_catalog_card_fields`. Docs: `docs/contracts/leader-chat-schema.md`, `CLAUDE.md`, `README.md`. Tests: `tests/test_agent_cli.py::test_leader_chat_skills_catalog_is_read_only` / `::test_leader_chat_skills_catalog_variants_route`. Read-only.

**Read-only skill-source visibility is now fully delivered** by the catalog command (slice 1) + trusted-source allowlist + `agentdeck skills sources` (slice 2) + workbench `skills_catalog_card` (slice 3) + this NL `mode=skills_catalog` intent (slice 4). Nothing further is needed to *see* the configured skill sources.

**Skill dependencies (decision "B") slice 1 is done (read-only resolution):** `SKILL.md` frontmatter may declare a `depends_on` list (parsed onto `SkillSnapshot.depends_on` via `_metadata_list`; `summary()` unchanged). New pure `resolve_skill_dependencies(root, name)` (`src/agentdeck/skills.py`) does a DFS over `discover_skills(root)` yielding `depends_on` / sorted `resolved` / `missing` / `has_cycle` (+ `cycle` path) / topological `order`. Read-only `agentdeck skills deps --name <name>` (`skills_deps_command`, `src/agentdeck/cli.py`) wraps it as `mode=skills_deps` with inspect-only `agentdeck skills show --name <dep>` controls, self-validated via `validate_skills_deps_contract` (`SKILLS_DEPS_RESPONSE_FIELDS`), exposed via `agentdeck contract skills` (`deps_command` / `deps_response_fields`). Read-only: loads nothing, imports nothing, writes no state, appends no event, calls no provider, touches no tmux; `depends_on` is **parsed but NOT acted on** (no auto-load, no auto-import). Design + plan: `docs/superpowers/specs/2026-07-09-skill-dependencies-design.md` and `docs/superpowers/plans/2026-07-09-skill-dependencies.md`. Tests: `tests/test_agent_cli.py -k skills_deps` + `test_resolve_skill_dependencies_transitive_missing_and_cycle`, `tests/test_contracts.py -k skills`.

**Skill dependencies (decision "B") slice 2 is done (read-only unmet-deps note on load-preview):** `agentdeck skills load-preview --name <name>` now also returns `unmet_dependencies` (`list(resolution["missing"])`) and `has_dependency_cycle` (`bool(resolution["has_cycle"])`), computed by reusing `resolve_skill_dependencies(Path(config.root), args.name)` in `skills_load_preview_command` (`src/agentdeck/cli.py`) just before printing (only when the skill exists; the existing preview error path handles unknown skills). Added to `SKILLS_LOAD_PREVIEW_RESPONSE_FIELDS` (`src/agentdeck/contracts.py`) and to the skills-contract example fixture `load_preview` (both `[]` / `False`). READ-ONLY + NON-BLOCKING: the note is informational only — it does not block the preview, does not auto-load or auto-import any dependency, writes no state (`skill_loads[]` untouched), appends no event, calls no provider, touches no tmux; `skills load` behavior unchanged. Tests: `tests/test_agent_cli.py::test_skills_load_preview_surfaces_unmet_dependencies`, `tests/test_contracts.py -k skills`.

**Skill dependency auto-load (decision "B-auto") is done (preview + explicit confirm, never silent):** new pure `_skill_load_plan(config, store, name, agent)` (`src/agentdeck/cli.py`) reuses `resolve_skill_dependencies` + the agent's `skill_loads` to build a deps-first plan (`order` items `{name,status,source}`, `to_load` / `already_loaded` / `missing` / `has_cycle` / `cycle` / `blockers` / `can_load` / `confirm_command`). Read-only `agentdeck skills load-plan --name <name> --agent <id>` (`skills_load_plan_command`) wraps it as `mode=skill_load_plan` with inspect-only `skills show` controls, self-validated via `validate_skill_load_plan_contract` (`SKILL_LOAD_PLAN_RESPONSE_FIELDS`); it writes no state (a test asserts state unchanged). `agentdeck skills load --name <name> --agent <id> --with-deps --confirm` (`_skills_load_with_deps`, branched at the top of `skills_load_command`) loads each `to_load` skill deps-first via `store.record_skill_load` + a `skill_loaded` event each, then one `skill_deps_loaded` summary event (`mode=skill_deps_loaded`). GATED: `--with-deps` requires `--confirm` (else reject, no writes); a missing dep or cycle rejects writing nothing (never auto-imports — import stays the separate explicit allowlist-gated flow); single-skill `skills load` (no `--with-deps`) is unchanged. Exposed via `agentdeck contract skills` (`load_plan_command` / `skill_load_plan_response_fields`). Design + plan: `docs/superpowers/specs/2026-07-09-skill-dep-autoload-design.md`, `docs/superpowers/plans/2026-07-09-skill-dep-autoload.md`. Tests: `tests/test_agent_cli.py -k skills_load` + `tests/test_contracts.py::test_validate_skill_load_plan_contract`.

**Skill dependency version pinning (decision "B-ver") is done (content-hash pins, local + deterministic + no network):** a `depends_on` entry may pin a content hash — `name@sha256:<hex>` — while plain `name` still means "any version" (unchanged). New pure `_parse_dep(entry) -> (name, pin|None)` (`src/agentdeck/skills.py`) splits on the first `@` (empty suffix ignored); `SkillSnapshot.depends_on` keeps the raw entries. `resolve_skill_dependencies` gains `version_mismatch: list[{name, expected, actual}]` (deduped via `seen_vm`): when a pinned dep IS present but its `content_hash != pin` it is recorded and NOT recursed into (a blocker leaf, excluded from `resolved`/`order`); `resolved`/`missing`/cycle/`order` semantics are otherwise unchanged. `skills deps` surfaces `version_mismatch` (flows through the `**resolution` spread); `_skill_load_plan` adds `version_mismatch` to the payload and a `"version mismatch: <name> expected <pin>"` blocker, so `can_load` is false and `skills load --with-deps --confirm` rejects writing nothing (identical handling to `missing`/cycle). `version_mismatch` added to `SKILLS_DEPS_RESPONSE_FIELDS` / `SKILL_LOAD_PLAN_RESPONSE_FIELDS` + both validators. Design + plan: `docs/superpowers/specs/2026-07-09-skill-dep-version-pinning-design.md`, `docs/superpowers/plans/2026-07-09-skill-dep-version-pinning.md`. Tests: `tests/test_agent_cli.py::test_resolve_skill_dependencies_version_pinning`, `::test_skills_deps_and_load_plan_flag_version_mismatch`, `tests/test_contracts.py::test_validate_skill_load_plan_contract`.

**Read-only dependency VISIBILITY (B1+B2) + dependency LOAD (B-auto) + version PINNING (B-ver) are now complete.** The remaining dependency items are product forks needing the human.

**Remaining items are all product forks — STOP + ask the human first:**
1. ⚠️ **FORK:** allowlist **ENFORCEMENT** (blocking imports from non-allowlisted sources) — already delivered as opt-in decision "A"; further tightening (default-on, hard block) stays a product fork.
2. ⚠️ **FORK (post-B-ver):** semver **ranges / version intervals** (e.g. `name@>=1.2,<2`) and **lockfile generation / lock strategy** — content-hash pinning (B-ver) is done; these each need their own brainstorm→spec→plan. Do NOT start inside another slice.
3. ⚠️ **FORK (C):** remote / marketplace skill sources or remote dependency fetch (over the network) is a product fork — local trusted sources only until a human explicitly opts in. Do NOT build it unilaterally.

## Required Verification Before Handoff

At minimum, run:

```bash
conda run -n agentdeck pytest tests/test_agent_cli.py::test_leader_chat_frontdesk_routes_request_without_planning_or_provider_calls -q
conda run -n agentdeck pytest tests/test_agent_cli.py::test_status_surfaces_logical_coordination_roles_for_planner_orchestrator_split tests/test_agent_cli.py::test_leader_status_surfaces_provider_and_queue_snapshot_without_mutating_state tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state tests/test_contracts.py::test_leader_status_contract_payload_is_reusable_without_cli tests/test_contracts.py::test_leader_status_contract_response_includes_example_without_drift -q
conda run -n agentdeck pytest tests/test_agent_cli.py::test_loop_once_recommends_next_explicit_command_without_mutating_state tests/test_agent_cli.py::test_contract_loop_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_contract_loop_example_exports_gui_ready_card tests/test_contracts.py::test_loop_contract_payload_is_reusable_without_cli tests/test_contracts.py::test_loop_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_loop_once_contract_rejects_auto_execution_claim -q
conda run -n agentdeck pytest tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_workbench_contract_requires_worker_lifecycle_item_fields tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state -q
conda run -n agentdeck pytest tests/test_contracts.py::test_workbench_contract_response_includes_example_without_drift tests/test_contracts.py::test_validate_workbench_contract_requires_review_gate_stage_fields tests/test_agent_cli.py::test_workbench_embeds_operator_runtime_ledger_and_active_inbox_cards_without_mutating_state -q
conda run -n agentdeck pytest tests/test_agent_cli.py::test_leader_chat_review_gate_is_read_only_and_surfaces_control_palette tests/test_leader_cli.py::test_leader_chat_help_returns_capability_card_without_planning tests/test_agent_cli.py::test_contract_leader_chat_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_contract_leader_chat_example_exports_gui_ready_response -q
conda run -n agentdeck pytest tests/test_agent_cli.py::test_leader_chat_release_preview_is_read_only_and_surfaces_control_palette tests/test_leader_cli.py::test_leader_chat_help_returns_capability_card_without_planning tests/test_agent_cli.py::test_contract_leader_chat_discovers_schema_for_gui_clients tests/test_agent_cli.py::test_contract_leader_chat_example_exports_gui_ready_response -q
conda run -n agentdeck pytest -q
git diff --check
```
