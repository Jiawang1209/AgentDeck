# 2026-07-26 Copilot Line 1 Round 9：GUI 实战 + 影子零 diff（PASS，双发现闭环）

## 目的

走开链路第二次整环（任务=round 7 标记的 M2 既有 a11y 隐患：mouseleave
无条件重启自动播放把键盘焦点藏进 aria-hidden 子树），同时完成三件验收：
①GUI 三刀首次真浏览器实战；②委托系统在长轮次下的表现；③round 收尾跑
`storage shadow-diff` 攒 SQLite 阶段 5 的第一份零 diff 证据。

## 整环结果

- 两段 follow：60 wave（到上限，有界语义正确）+ 25 wave（到 complete）；
  委托自动放框 **10 个**（coder 的 RED/GREEN/跨资产回归全周期），第二段
  0 框（reviewer=Claude auto-mode）。
- 文件通道 3/3；merge-on-complete 自动合并 `5887b83 fix: keep carousel
  paused while keyboard focused` + 审查文档 `9304de4`；release 3/3 零
  dirty；prune 全回收。
- reviewer 审查达到取证级：隔离副本回退验证 RED 真实性（autoplayRestart
  Count 2≠0 直接证明危害链存在）、进程采样 + CDP 探针定位 F1。

## 发现与当场闭环

1. **页面 JS 语法瘫痪（当场修复）**：`_PAGE` 里 `\n` 被 Python 先解释成
   真换行落进 JS 字符串 → 整个 `<script>` SyntaxError → 页面全部卡
   loading。修复=转义 `\\n`；新增 `node --check` 页面脚本语法守卫测试。
   GUI 三刀首次真渲染即暴露盲区（此前只有 curl 冒烟）。
2. **codex 折叠框盲区（当场修复）**：长框中段被折叠（"[… N lines] view
   all"），`$ 命令`行不在可见 pane → 提取 None → 委托无法匹配 →
   release-boxes 正确保守跳过（绝不盲按）→ 哨兵报警人工放行。修复=
   回退从选项 2 自身的 "commands that start with `…`" 反引号内容跨行
   提取（行内空格保留、跨行无空格拼接），匹配器加空白折叠比较兜住
   折行歧义；修复后同轮后续折叠框全部自动放行。
3. **F1（高，入池下一任务）**：coder 新增的 focusedMouseleave 测试用例
   带竞态缺陷——G2 末尾切页后未等 0.7s 交叉淡切即 focus+Tab，焦点偶发
   落错元素 → closest 为 null → 回调 TypeError → promise 永不 resolve →
   测试无限挂起（reviewer 实测 5 跑 2 挂；收尾在 main 上首跑即复现
   TIMEOUT）。生产修复本身经隔离验证正确；修复方案 reviewer 已给出
   （补 `await delay(750)` + 页面侧空值防御始终 resolve）。F2-F4 低级
   发现同入池。

## GUI 实战验收

浏览器打开 `ui serve` 页面（截图
`assets/agentdeck-ui-round9.png`）：Overview/Agents/Queues 实时正确；
Controls 面板 inspect 项 Run 按钮、explicit 项 Execute… 按钮、disabled
无按钮，分级渲染全对；Events 流实时滚动 round 9 实况（reply_captured、
worktree_created、auth_box_released 可见）——GUI 作为第二屏观察面成立。

## 影子镜像零 diff 证据 #1

整轮 live（数百次 state 写入：plan/approvals/dispatch/replies/事件/
worktree provenance/release）后 `agentdeck storage shadow-diff`：
**in_sync=true，46 集合零 mismatch**。SQLite 阶段 5（events 切权威）的
推进条件开始积累。

## 结论

走开链路二连 PASS；委托在真实长轮次消掉 10/11 框（1 个折叠框盲区当场
修复闭环）；GUI 从"能跑"进化到"实战可用且有语法守卫"；影子镜像第一份
零 diff 证据落袋。下一任务：F1 测试竞态修复（方案已备）。
