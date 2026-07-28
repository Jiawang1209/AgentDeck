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
4. ✅ 影子读比对面已落地（同日，实现偏差已记录）：`reconstruct_state`
   （镜像逆函数；空集合靠 meta `collections` 清单恢复，否则空 key 丢失
   ——实现时发现的真缺口）+ 只读 `agentdeck storage shadow-diff`
   （重建 vs `StateStore.load()` 深比对，逐集合点名 mismatch，漂移
   exit 1，零写）。**偏差**：不做"每 writer 后自动比对+写 shadow-diff.jsonl"
   ——全量重写镜像后立即比对是恒真式；真实漂移源（JSON 落盘与镜像间的
   crash 窗口、绕过 save 的写、镜像损坏）由显式 diff 捕获。质量闸门改为
   纪律：每轮 live 收尾必跑 `shadow-diff`，零 diff 记录随 validation 文档
   积累，N 轮零 diff 才推进阶段 5。scratch 首验：46 集合 in_sync。
5. events 切权威（设计冻结于 2026-07-27，实现分四子切片）。
   **前置事实**：events.jsonl 是独立 journal，不在 state dict 里——
   quarantine 镜像不含它，需要自己的双写→比对→切换周期。读写面已收敛：
   写单点 `append_event`→`_append_event_journal_at`（全文重写 O(n²)，
   64MB 上限）；读单点 `_event_journal_source()`（6 调用方：event ids、
   3 个 outbox flush、events 列表、history）；全部在 mutation lock 内。
   - ✅ **5a 双写已落地**（2026-07-27，零行为变更）：钩在真正的写单点
     `_append_event_bytes_locked` 成功路径末尾（覆盖 append_event 与
     daemon outbox 批量两条调用链），同一把 mutation lock 内向 events 表
     `INSERT OR IGNORE`（多行解析；失败落 shadow-errors.jsonl 绝不影响
     journal 路径）。
   - ✅ **5b 比对面已落地**（同日）：只读 `agentdeck storage events-diff`
     ——**后缀对齐**语义：启用前的历史 journal 事件不在表里是 5a 语义而非
     漂移，以表首行 event_id 定位 baseline，此后逐条按 event_id/type/
     payload/created_at 深比对，长度或内容不符即非 0（mismatches 上限
     10 条详情）；表空=平凡同步。scratch live 首验：baseline 506、新增
     2 条双写 in_sync。每轮 live 收尾与 shadow-diff 一起跑。
   - ✅ **5c 切权威已落地**（2026-07-28 user 拍板，显式
     `storage events-cutover --confirm`）：shadow db meta 新增
     `events_authority`（`journal`→`sqlite`）；cutover 在 mutation lock
     内**整表按 journal 顺序重建**（实施偏差：5a 双写使启用后事件先占
     低 cursor，直接回填历史会错序——同事务 DELETE+全量插入使表序=
     journal 序），随后**逐字节校验**表重建与 journal 相同才翻转权威,
     任何不一致拒绝且零改动；切换后 append 表写为权威（失败冒泡）、
     同锁同步导出到 events.jsonl（导出失败只落 shadow-errors,
     events-diff 可见）；`_event_journal_source()` 按 authority 从表
     重建 bytes（6 个调用方零改动）；`events --since` 对外契约不变。
     `storage events-rollback --confirm` 验证导出零漂移后切回 journal;
     `shadow-status` 暴露 `events_authority`。authority 读取任何异常
     fail-safe 降级 journal（同步导出保证无数据丢失）。
   - **5d 停同步导出**（观察期后另行拍板）：JSONL 改为按需导出，
     O(n²) 写彻底消失。
   approvals.jsonl 与 3 个 event_outbox 的迁移复用同一形态，在 5c 稳定
   后跟进。
6. 分集合切 78 writer 权威（叶子→中间→核心），每批递增
   `authority_generation`。
7. `sqlite_active` + JSON 降级导出，一个发布周期后删 JSON 写路径。
