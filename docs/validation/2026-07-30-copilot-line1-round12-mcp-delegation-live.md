# 2026-07-30 Copilot Line 1 Round 12:MCP tool 委托 scope live 首验(PASS)+ F2 竞态窗口修复整环

## 目的

MCP tool 委托 scope(af7023ed→5bef5457)的 live 验证是本轮首要目标;
载体任务=缺陷池 F2(移除 `--force-prefers-reduced-motion` 后页面启动
至测试 stub 安装间的自动播放竞态窗口)。同场执行每轮收尾三连检。

## 主环结果(F2 走开环)

- **G2 双 backend 复验 PASS**:DeepSeek v4-pro brief(4 条
  acceptance_criteria)→ claude-fable-5 拆 3 步(planner 调查→coder
  浏览器复现+修复→reviewer 按标准复核),plan `pln_b3b9515b5de6`
  三 provenance 入账。
- **走开环**:approve-plan 一次批准;8 段 follow 到 complete;文件
  通道 3/3(planner/coder/reviewer 全部 `captured_from=file`);摄入
  前移同 wave 解锁下一 step 派发 2 次;step 顺序守卫全程正确持留。
- **G5 三连验 PASS**:reviewer verdict `overall=pass, score=92,
  4/4 criteria pass, unverified/extra 空`;verdict gate 放行
  merge-on-complete,coder 分支自动合并(commit `4da4f73`,+87 行:
  测试引导竞态关闭 + 回归测试);reviewer 零新 commit 分支正确
  skip(already merged)。
- **F2 独立复验**:合并后 main 上 `node tests/focus-carousel-tab-order.mjs`
  与 `node tests/back-to-top.mjs` 均 exit 0。
- **收尾**:6+2 条 inbox 逐 head ack;release 3/3(reviewer 一个
  dirty worktree 被正确保护);prune 只回收 settle 项;
  **shadow-diff in_sync + events-diff in_sync**(5c 切权威后导出
  镜像一致);主仓库全量 4735 passed, 3 skipped。

## MCP 委托针对性验证(本轮核心)

预授 4 条 mcp_tool 委托(planner/coder × chrome-devtools
hover/press_key)。主环中 worker 天性全选 shell 无头 Chrome,未
自然触发 MCP 框;收官后针对性小验证:显式要求 planner 用
chrome-devtools MCP 的 hover 与 press_key 做只读观察
(msg_46e332932a6a)。

- **fail-closed 先行验证**:发布版提取器(按 round 11 转写措辞
  构造)对真实框 36 轮扫描全部 skip、零误按——降级方向完全正确。
- **真实框逐字捕获**(本轮最有价值证据):
  `Allow the chrome-devtools MCP server to run tool "hover"?` +
  参数行(`includeSnapshot: false` / `uid: 1_20`)+ 选项
  `› 1. Allow / 2. Allow for this session / 3. Always allow /
  4. Cancel` + footer `enter to submit | esc to cancel`。与转写
  措辞差两处:tool 名带引号、句子与选择器之间隔参数行。
- **TDD 修复(b14f56ed)后当场复验 PASS**:`agent boxes` 立即识别
  (`box_kind=mcp_tool, delegated=true`);`boxes watch` 累计放行
  **4 框**——hover ×3(dlg_6485fbba22b1,逐框放行)+ press_key ×1
  (dlg_3901081a99d8),**两条委托均真实命中**;每次
  `auth_box_released` 审计携带 box_kind/mcp_server/mcp_tool/
  waiting_hint/source 全字段;未命中框 0、误按 0。
- **观察结论回收**:planner 高质量回复经文件通道入账
  (`captured_from=file`):7 秒连续采样(超 6 秒自动播放周期)确认
  悬停暂停,Tab 一次焦点落 skip-link——独立复证 F3/F2 修复后行为。

## Live 发现

1. **`box_kind` 审计字段与旧数据兼容 live 生效**:16 条既有前缀
   委托正确投影 `kind=command_prefix`;命令框放行审计带
   `box_kind=command`。
2. **真实 MCP 框措辞与转写不符**(引号+参数行)→ 修复 b14f56ed;
   教训:框式 parser 必须以逐字 live 捕获为 fixture,转写不可信。
3. **shell 包装逃逸前缀委托**:`REPRODUCE_...=1 node tests/...`
   (env 前缀)、`for run_id in ...; do node tests/...`(循环)均
   不命中 `node tests/` 委托,哨兵正确跳过但走开体验退化——委托
   匹配归一化(env 剥离/包装识别)= 新拍板项候选。
4. **boxes watch pane 丢失裸崩** → 修复 b14f56ed(capture 失败记
   `skipped[] reason=pane capture failed` 继续扫描)。
5. **长选项 2 框溢出 10 行尾窗**(段 7 实测:提交前四门验证框,
   选项 2 逐字引用整条命令,`$ ` 行与 round-9 回退 marker 双路
   失明)→ 修复 b14f56ed(提取窗改全捕获上的 pending-box region)。
6. **worker 天性与环境行为**:planner/coder 未被指定工具时偏好
   shell 无头 Chrome 而非 MCP;`--virtual-time-budget` 无头 Chrome
   实测会挂住不退出(coder 自主 kill 清理,其框逐 PID 核实后人工
   放行);codex spawn 后立即 dispatch 存在启动竞态(pane 秒死,
   等待+capture 验活后重派成功)。
7. **操作者侧教训**:人工代按前的内容核验门必须以非零退出硬拦
   (`&&` 链中 python 打印不匹配但 exit 0 → 门禁失效一次;所按框
   与此前完整核验一致,无实害)。

## 结论

MCP tool 委托 scope 的注册表、grant、检测、匹配、放行、审计全链在
真实 codex MCP 框上 live PASS(经一次 live 驱动的措辞修复);
fail-closed 设计在未知措辞下 36 轮零误按,是本轮最强的安全证据。
走开环第五连 PASS;F2 闭环;影子/导出双 in_sync。

剩余拍板项不变:SQLite 5d、round_reviewer 独立角色、G1 frontdesk
增强、daemon 背景续跑;新增候选:委托匹配归一化(发现 #3)。
