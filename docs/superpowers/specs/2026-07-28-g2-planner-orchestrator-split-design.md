# G2 Planner / Orchestrator 拆分设计(2026-07-28 冻结)

对应 `docs/roadmap/ultimate-goal-roadmap.md` Phase G2 与
`docs/roadmap/2026-07-24-north-star-gap-review.md` 核心差距 #2。
目标:把当前单一逻辑 Leader 拆成两个可配置语义子角色——`planner`
(高推理宏观计划 + 验收标准)与 `orchestrator`(任务分解、审批创建、
worker 选择、结果聚合)——同时保持既有行为在未配置时逐字节不变。

## 冻结决策

1. **仍是 `agent_id=leader` 体系下的逻辑子角色。** planner/orchestrator
   不是新 agent、不占 tmux pane、不进 worker 注册表;二者只是 Leader
   推理的两个阶段,各自可绑定不同 provider/model。`runtime_kind=
   logical_leader`、`pane_backed=false` 语义不变。
2. **配置是可选叠加,缺省回落。** `.agentdeck/config.toml` 新增可选
   `[leader.planner]` / `[leader.orchestrator]` 子段,字段仅
   `provider` / `model`;任一子段缺失时该子角色回落到 `[leader]` 的
   provider/model。**两个子段都缺省时,全链路行为与今天逐字节相同**
   (单次 provider 调用、plan 记录形状不变、无新事件)。这是本切片的
   硬兼容承诺,复用 run-loop/worktree 旋钮的 opt-in 先例。
3. **两段推理,一条 plan 主线。** 拆分启用时(至少一个子段显式配置):
   - **Planner 段**:调用 planner backend,产出宏观 brief——`goal
     重述、acceptance_criteria[](非空字符串列表)、risks[]、
     macro_steps[](粗粒度意图,不含 agent 指派)`。
   - **Orchestrator 段**:把 planner brief 作为输入调用 orchestrator
     backend,产出现有 plan schema 的 steps[](逐 step agent_id/role/
     task/risk/requires_approval),复用既有 plan schema validator 全部
     规则(连续编号、已配置 worker、role 一致、强制 requires_approval)。
   - 最终落一条 plan 记录(不是两条),`steps` 语义与今天完全一致,
     下游 approval/dispatch/run-loop 零改动。
4. **Provenance 三份都存。** 拆分启用时 plan 记录新增:
   - `planner_backend` / `orchestrator_backend`:复用
     `leader_backend_identity()` 同源形状(provider/model/backend/
     transport/reasoning_backend 等),只表来源,不表授权;
   - `planner_brief`:compact 冻结快照(goal/acceptance_criteria/
     risks/macro_steps + content_hash),作为 orchestrator 输入的可
     审计证据;
   - 既有顶层 `leader_backend` 保留,拆分启用时等于 orchestrator
     backend(最终产出 steps 的那个 backend),保证旧消费方不破。
   未启用拆分时这些新字段一律缺省(不写 null 占位,校验按可选处理)。
5. **失败语义不合并。** planner 段失败记 `leader_provider_failed`
   (阶段标注 `stage=planner`)且不进入 orchestrator 段、不落半写
   plan;orchestrator 段失败同理(`stage=orchestrator`),已产出的
   planner brief 只进 `leader_errors[]` 上下文,不落 plan 记录。
6. **acceptance_criteria 本切片只存不判。** 量化验收、review gate 对
   照 criteria 打分是 G5 范围;G2 只保证 criteria 进 plan 记录、
   ProjectView、`plan status` 与 leader summary 的只读展示,为 G5
   铺数据面。
7. **安全边界不动。** planner/orchestrator 都不 dispatch、不创建
   approval 之外的调度对象、不读 tmux、不发送输入;approval gate、
   `--confirm` 门、autonomous allowlist/预算全部原样。prompt 注入
   规则沿用现行:只进 compact skill context 与 worker 元数据,不进
   完整 content_snapshot。

## 需要 human 拍板的 fork(STOP,不在本 spec 内开工)

- orchestrator 是否引入工具调用(如让它查询 ProjectView)——本 spec
  冻结为纯 prompt→JSON,一次调用。
- planner brief 是否独立成第一类 state 集合(`briefs[]`)供跨 plan
  复用——本 spec 冻结为 plan 内嵌快照。
- frontdesk 接待层改造(G1 增强)不在 G2 范围。

## 切片顺序(每片独立 TDD + commit)

- **S1(本文档)**:spec 冻结。
- **S2 config + 数据模型**:`LeaderConfig` 增可选 planner/orchestrator
  子配置解析(含非法值 fail-closed 测试);`resolved_planner_backend()`
  / `resolved_orchestrator_backend()` 纯 helper;缺省回落逐字节不变
  回归测试。
- **S3 planner 段**:planner prompt 模板 + brief JSON schema validator
  (goal/acceptance_criteria/risks/macro_steps,fail-closed)+
  `planner_brief` 快照与 hash;fake provider 先行,真实 provider 复用
  现有 client 抽象。
- **S4 orchestrator 段**:brief→steps prompt 模板 + 复用既有 plan
  schema validator;两段串联落单条 plan;失败分段审计。
- **S5 provenance 与契约面**:plan 记录 / ProjectView `plans.items[]` /
  `plan status` / workbench / leader summary 暴露 planner_backend /
  orchestrator_backend / acceptance_criteria 只读字段;同步
  project-view / leader-summary / run 相关 contract 文档、contract
  index、README、HISTORY、测试。
- **S6(G5 前置)**:`leader review` / `run_progress_card` 只读展示
  该 plan 的 acceptance_criteria(不打分、不 gate),为 G5 量化验收
  留数据入口。

## 验收标准(对照 roadmap G2)

- state 中能看到 planner brief 与 orchestrator 产出的来源
  (planner_backend/orchestrator_backend/planner_brief)。
- 两者可绑定不同 provider/model(config 子段 + live 验证留待 user
  在场的 Line 1 round)。
- planner 输出不直接 dispatch;orchestrator 产出仍全部经 approval
  gate;未配置拆分时行为逐字节不变。
