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
4. **执行面 A 档（同日拍板落地）：仅 inspect 级可点执行**。
   `POST /api/inspect`（唯一允许的 POST 路径，其它 POST 仍 405）只接受
   `{"control_id": …}`——浏览器绝不发送命令文本；server 用
   `agentdeck controls --control-id <id>` 从 live registry 重解析，强制
   四重门：control 存在（否则 404）、`enabled=true`、`safety=inspect`、
   命令以 `agentdeck ` 开头且不含 `--confirm`（任一不满足 403 且零执行）；
   通过后以 shlex 解析 argv 执行并返回 JSON 结果。explicit_*/delegated
   控件仍只展示与复制；B 档（安全分级确认按钮）需另行拍板。
   实施时 live 发现并顺带修复主线契约 bug：approve/autonomous 模式下
   ask set_mode 控件 enabled 且 safety=inspect，违反"enabled set_mode 必须
   explicit_user"的注册表契约（ask/approve 切换控件现统一 explicit_user）。
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
