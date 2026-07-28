# 2026-07-28 Copilot Line 1 Round 11:G2 双 backend + G5 verdict live 首验(PASS,影子零 diff #3)

## 目的

按 `2026-07-28-copilot-line1-round11-runbook.md` 执行:G2 planner/
orchestrator 拆分真实双 backend 首验、G5 量化验收全链首验、SQLite
三连检证据 #3。任务=缺陷池 F3(轮播 focusout 无条件重启自动播放,
悬停中焦点离开不应恢复播放)+ 顺带补 F4(5887b83 缺失的
DEVELOPMENT_HISTORY 条目)。

## 整环结果

- **G2 双段规划 PASS**:planner=DeepSeek v4-pro(api/http)出 brief,
  orchestrator=claude-fable-5(cli/subprocess)展开 3 步
  (planner 复现→coder worktree 修复→reviewer 复核)。plan
  `pln_fe49bd561450` 三份 provenance 全部入账,顶层 leader_backend=
  orchestrator 侧(spec 决策 4 live 验证)。**DeepSeek brief 一次
  通过 fail-closed validator**:5 条 acceptance_criteria 全部具体
  可检,macro_steps 被 orchestrator 合理归并;step 3 task 自发要求
  "核对是否满足验收标准"(brief 真实影响 steps)。
- **走开环**:approve-plan 一次批准;6 段 follow(40+20+20+20+20+1
  wave)到 complete;文件通道 3/3;摄入前移两次同 wave 解锁下一
  step(planner 回收→coder 派发、coder 回收→reviewer 派发);
  merge-on-complete 自动合并 coder(088c93a fix)+ reviewer
  (5981483 audit)两分支;release 3/3 零 dirty、prune 全清、六条
  inbox 逐 head ack。
- **G5 全链 PASS**:
  - G5-1 review prompt 注入:reviewer message prompt 含系统注入段
    (5 条标准原文 + review-verdict/v1 格式),且在 coder 分支尖的
    隔离 worktree 检出(base 入账);首 step prompt 无注入。
  - G5-2 verdict 自然产出:claude reviewer 首次接触该格式即正确
    输出 `verdict:` 单行 JSON——overall=pass、score=92、5 条
    criteria 全 pass 且使用标准原文;`review_verdict_recorded`
    事件载荷精确,零 invalid。
  - G5-3 三面摘要:leader summary `verdict_summary` =
    {5/5 pass, unverified=[], extra=[], score 92},与 review/run
    面同源一致。
  - gate 零变化:verdict 只作证据,complete gate 按既有语义达成。
- **F3 独立复验**:合并后 main 上 `node
  tests/focus-carousel-tab-order.mjs` exit 0(coder 的 TDD RED→
  GREEN 流程含修复前基线复现)。
- **三连检**:`shadow-diff` in_sync(46 集合)= **零 diff 证据
  #3**;`events-diff` in_sync(163 条);回归见主仓库全量套件。

## Live 发现

1. **分段审计 live 生效**(好消息):首次尝试 orchestrator 段失败时
   `leader_provider_failed` 准确标注 stage=orchestrator,planner 段
   成功不受污染。
2. **跨 provider model 回落陷阱(产品发现,候选修复)**:
   `[leader.orchestrator]` 只写 provider 不写 model 时,S2 逐字段
   回落把 `[leader].model`(deepseek-v4-pro)喂给 claude CLI →
   nonzero。本轮以显式 `model = "claude-fable-5"` 绕过。候选方向:
   子段 provider 与 `[leader].provider` 不同时,model 缺省应回落到
   目标 provider 默认而非 leader.model,或 fail-closed 要求显式
   model(待拍板)。
3. **第五类授权框:MCP tool 授权**:planner(codex)用
   chrome-devtools MCP 复现 hover/Tab 行为,两次弹 "Allow the
   chrome-devtools MCP server to run tool ..."(hover、press_key)。
   命令前缀委托注册表天然不覆盖 MCP 工具授权——哨兵不代按=行为
   正确,操作者目视放行(选 2 会话级)。委托 scope 若要覆盖 MCP,
   需要 server/tool 粒度的新注册表形态(待拍板)。
4. **段间隙框错过扫描**:coder 的 `node tests/...` 已委托前缀框在
   两段 follow 之间弹出,`--release-boxes` 段内扫描错过;人工
   `release-box --confirm` 命中委托 dlg_ada1ea0c67e1 放行。候选:
   follow 段首也扫一次框(小改进,待排期)。
5. worker 天性观察:codex planner 主动用浏览器工具复现缺陷;claude
   reviewer 在 auto mode 下零授权框、自主 commit 审查文档并整理
   HISTORY 条目顺序。

## 结论

G2 与 G5 的全部开发面在真实 provider、真实多 agent、真实 worktree
生命周期上一次通过;走开链路第四连 PASS;影子零 diff 3/3。
