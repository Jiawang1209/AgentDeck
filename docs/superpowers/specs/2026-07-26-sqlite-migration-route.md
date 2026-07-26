# SQLite 迁移路线（已拍板，阶段 0+1 进行中）

- 日期：2026-07-26；user 批准"以 P1 为底座 + rewrite 挑零件"路线并从
  阶段 0+1 开始。调研由只读探索完成（两个 donor worktree + 主线 state.py）。

## 核心事实（调研结论）

1. `codex/p1-durable-mission-kernel`（91413fe1）与
   `codex/product-kernel-rewrite`（6790e29f）都是 main 历史祖先，其
   storage/adapters/projections 代码已被主线整体移除——本路线是第三次
   尝试。前两次并非 SQLite 技术失败，而是 2026-07-23 产品方向 pivot
   （转向 copilot-line-1）；但结构教训成立：**两次都在 state.py 旁边另建
   平行内核。本次核心纪律：在 state.py 内部换后端，绝不另起炉灶。**
2. 两个 donor 都没有完成 JSON→SQLite 的真实记录映射（rewrite 只迁
   project 一行；P1 的 `legacy_records` 表无生产写入），该部分从零写。
3. 主线 `StateStore`：11,775 行、36 个顶层集合、**78 个 authoritative
   writer** 全部经单一装饰器 `_locked_state_mutation` 收敛，且
   `tests/test_daemon_migration.py` 的 AST 闭包断言机器验证完备性。
   换后端 = 换装饰器实现，无需逐个改签名。该测试全程必须保持绿。
4. events.jsonl 的 append 实现为全文重写（O(n²)），是迁移收益最直接点。

## 底座与零件选型（已定）

- **底座（P1）**：`authority_state` 三态机
  （`legacy_active→sqlite_installed_quarantined→sqlite_active` +
  `cutover_watermark`/`authority_generation`，即双写/影子读验证期的持久化
  状态模型）；`ProjectWriterLease` flock+inode 写者租约；revision CAS
  （与主线 save() 的 hash 漂移检测同构）；有界投影层工具。
- **零件（rewrite）**：`sqlite_secrets.py` 秘密防火墙（133 行纯函数）；
  版本化迁移模板（pin 常量 + BEGIN IMMEDIATE 内迁移 + foreign_key_check
  + 产物指纹再验）；文件安全 helper（O_NOFOLLOW/hardlink/inode 校验）；
  `ports/store.py` Protocol 契约写法。
- **两边都要改的坑**：donor 假设 `.agentdeck/state.json`，主线实际是
  `.agentdeck/state/state.json`（错一层）；锁文件必须复用主线
  `protocol-mutation.lock` 不得新开；P1 的 `LOCK_NB` 立即失败语义需加
  重试以保留主线"排队等待"行为；WAL 副产物（-wal/-shm）需 gitignore 与
  目录复制一致性处理。

## 阶段 0 调查结果（2026-07-26）

- 读侧耦合面：`StateStore.load()` 外部调用点 **131 个**
  （cli.py 112、daemon/service.py 9、mission_orchestration.py 6、
  conversation/session.py 4）。结论：SQLite 后端在验证期必须能物化完整
  state dict（保持 load() 契约），读路径瘦身推迟到阶段 6+。
- events.jsonl 外部消费者：仅 `history.py`（只读渲染，经 StateStore API，
  docstring 提及文件名），无外部 tail 依赖已知；`approvals.jsonl` 仅
  config.py 布局创建。事件切权威（阶段 5）风险低。
- 前两次尝试移出主线的原因：产品方向 pivot（见记忆/handoff），非技术
  否决；无需进一步考古。

## 分阶段切片（每阶段独立 commit + 可回滚）

0. ✅ 前置调查（本节）。
1. ✅ 搬无风险零件入新 `src/agentdeck/storage/` 包（secrets 防火墙、
   schema 指纹、文件安全 helper），零行为变更（63b708fb）。
2. ✅ **改定义（2026-07-26 实施前核实，user 批准）**：原计划"锁替换为
   P1 `ProjectWriterLease`"作废——核实发现主线 `_protocol_mutation_lock`
   在关键维度上**强于** P1 租约（dir_fd 逐级锚定 vs 仅末级 O_NOFOLLOW；
   flock 取得前后双重 inode 校验；阻塞排队语义；重入 depth + 跨项目嵌套
   禁止），替换会是倒退。阶段 2 新定义为硬规则：**SQLite 连接必须在现有
   `_protocol_mutation_lock` 内打开，单写者由同一把 flock 继承，永不新开
   第二把锁文件**（同时消除本 spec 前文警告的"两把锁双写者"风险）。
   P1 租约仅存的增量（uid/mode 检查、fork 检测、长寿命 validate_for）
   留待 daemon 长连接场景（阶段 5+）在现有锁内部按需补充。零行为变更。
3. ✅ 影子写骨架已落地（同日）：schema 采用 **generic mirror v1**
   （`meta`/`records(collection,record_id,position,record_json)`/
   `singletons`/`events` 四表 + 指纹 pin `745af5fb…`）而非 36 张规范化表
   ——quarantine 阶段镜像只需忠实复刻 JSON 以供影子比对，规范化表推迟到
   需要查询的阶段。`agentdeck storage shadow-enable --confirm` 建库
   （0600+O_EXCL+指纹校验+`storage_shadow_enabled` 事件）置
   `authority_state=quarantined`；`StateStore.save()` 在同一把 mutation
   lock 内、JSON 原子落盘后调用 `mirror_if_enabled` 全量镜像（DELETE
   journal 无 -wal/-shm；失败只写 `logs/shadow-errors.jsonl` 绝不进入
   JSON 路径；`rm state.db` 即完全回滚）；`storage shadow-status` 只读。
   注：词法 secrets 防火墙**不**应用于镜像（state 键如 `handoff_token`
   会被误杀；防火墙留给后续规范化 canonical facts）。
4. 影子读比对（差异写 shadow-diff.jsonl 不抛错）——质量闸门，零 diff
   N 天才推进。
5. events.jsonl + approvals.jsonl + 3 个 event_outbox 先切 SQLite 权威
   （append-only 耦合最低收益最高）。
6. 分集合切 78 writer 权威（叶子→中间→核心），每批递增
   `authority_generation`。
7. `sqlite_active` + JSON 降级导出，一个发布周期后删 JSON 写路径。
