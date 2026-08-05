# GUI 第一刀：stdlib 只读本地 web（已拍板，进入实施）

- 日期：2026-07-26；user 拍板方向 A（stdlib http.server 本地 web 只读面）
  + 数据通道走子进程 CLI JSON。
- 定位：GUI 是观察面/控制面，runtime 可见性仍归 tmux；40 个契约与
  workbench/controls/events 卡片体系是唯一数据源。

## 已定决策

1. **形态**：`agentdeck ui serve [--port N]` 用标准库
   `http.server.ThreadingHTTPServer` 起本地只读页面，**零新依赖**；
   绑定固定 `127.0.0.1`（不提供改绑参数）。
2. **数据通道**：UI 进程用子进程跑 `agentdeck workbench` /
   `agentdeck events --limit/--since` / `agentdeck controls` 拿 JSON 原样
   透传（`[sys.executable, -m, agentdeck, ...]`，无 shell），完全走已有
   契约面；CLI 侧 validator 天然守门；未来 daemon/remote 化只换通道不换
   渲染。
3. **只读边界**：API 端点白名单硬编码（workbench/events/controls 三条
   只读命令），server 绝不执行任何 mutating 命令。
   **2026-08-05 修订（user 拍板转 GUI 主线）**：本条的"绝不执行任何
   mutating 命令"**已被下面第 6 条突破**，改写为——白名单仍硬编码，但
   除三条只读命令外增加 `leader chat` 一条会写的命令；除它之外，server
   仍绝不执行任何 mutating 命令。不做无声放宽：突破写在这里，理由写在
   第 6 条。
4. **执行面 A 档（同日拍板落地）：仅 inspect 级可点执行**。
   `POST /api/inspect`（唯一允许的 POST 路径，其它 POST 仍 405）只接受
   `{"control_id": …}`——浏览器绝不发送命令文本；server 用
   `agentdeck controls --control-id <id>` 从 live registry 重解析，强制
   四重门：control 存在（否则 404）、`enabled=true`、`safety=inspect`、
   命令以 `agentdeck ` 开头且不含 `--confirm`（任一不满足 403 且零执行）；
   通过后以 shlex 解析 argv 执行并返回 JSON 结果。
   实施时 live 发现并顺带修复主线契约 bug：approve/autonomous 模式下
   ask set_mode 控件 enabled 且 safety=inspect，违反"enabled set_mode 必须
   explicit_user"的注册表契约（ask/approve 切换控件现统一 explicit_user）。
5. **执行面 B 档（同日拍板落地）：仅 explicit_\* 二步确认**。
   `POST /api/execute`（与 /api/inspect 同为仅有的两个 POST 路径，registry
   查找共享 `_lookup_control`）只接受 `{"control_id", "confirmed": true}`：
   门禁=存在（404）、`enabled`（403）、safety ∈ {explicit_user,
   explicit_runtime}（inspect 走 /api/inspect、**delegated 保持复制不可
   执行**，403）、命令以 `agentdeck ` 开头且不含 `<占位符>`（403）、
   `confirmed:true`（否则 428 零执行）；registry 命令合法携带的
   `--confirm` 不拒绝——人类已通过二步对话框确认该 sanctioned 命令。页面
   对可执行项渲染 "Execute…" 按钮，原生对话框展示完整命令原文，取消即
   零执行，成功后自动刷新 workbench。威胁模型=防误点（server 仅绑
   127.0.0.1，操作者即本人）；审计=被执行命令自身的事件链。
6. **对话面（2026-08-05 拍板）：`POST /api/chat`，第三条也是最后一条 POST
   路径。** 动机：GUI 转为主线后，对话是主界面而非附属面板；没有它，页面
   再完整也只是仪表盘。`leader chat` 的 `intent_card`（mode /
   embedded_card / controls[]）本就是为外壳内联渲染设计的，缺的只是通道。
   **它与前两条 POST 有一处本质差异，必须写明**：inspect/execute 的骄傲
   属性是"浏览器永远只发 control_id、命令文本只来自 live registry"，而
   chat **必须收自由文本**。缓解不是"限制文本"，而是文本从不进入命令位置
   ——它作为**单个 argv 元素**传给 `leader chat --message <text>`（与
   `events --since` 同一处理，无 shell），因此它是数据不是命令，注入面为零。
   **安全边界不来自 server，来自 chat 自己的契约**：`leader chat` 从不
   执行 runtime 动作——不 spawn、不发 tmux 输入、不 dispatch、不 capture、
   不 ack、不 approve；它只把命令递出来由人执行。**但它确实会写**：每轮
   写 `chat_turns[]` 与审计事件，且个别 mode 会创建 plan/approval
   （`run_start` 创建 plan+pending approvals、任务指派创建一条 pending
   approval）。这就是第 3 条被突破的确切范围——**不是"chat 是只读的"，
   而是"chat 写的东西都停在人类审批门之前"**。
   门禁：仅接受 `{"message": <非空字符串>}`（缺失/空/非字符串 400）；
   其它 POST 路径仍 405。不做 `confirmed` 二步确认——它执行不了任何东西，
   二步确认在这里只是噪音。
4. **页面**：单页内联 HTML/CSS/JS（无外部资源），前端轮询
   `/api/workbench`（5s）与 `/api/events?since=<cursor>`（events 游标由
   浏览器持有，符合 events contract 的 cursor 语义）。

## 安全边界

- 仅 GET；非 GET 返回 405；未知路径 404。
- `since` 参数作为单个 argv 元素传递（无 shell 拼接，注入面为零）。
- server 不写 state、不碰 tmux、不调 provider——它只是三条只读 CLI 的
  HTTP 外壳。

## 切片

1. `src/agentdeck/ui.py`：白名单命令表 + `run_cli_json` + HTTP handler
   （/、/api/workbench、/api/events、/api/controls）+ `ui serve` CLI。
2. 二期（另拍板）：执行面（inspect 直跑、explicit_* 点击确认、delegated
   输入确认）、workbench --watch 推流替代轮询、daemon 通道。
