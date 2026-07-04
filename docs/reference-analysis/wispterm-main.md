# WispTerm 深度研究报告

> 审查对象：`References/wispterm-main`  
> 审查方式：subagent 只读源码分析，主线程整理落盘。  
> 重点：技术栈、AI/Agent/Skill 机制、终端/GUI/控制 API，以及对本项目的可借鉴点。

## 1. 项目一句话定位

WispTerm 是一个用 Zig 构建的跨平台 AI 原生终端工作区：以 Ghostty 的终端模拟能力为底座，在桌面终端、分屏/标签页、远程 SSH/PTY、AI Copilot/Agent、工具调用、技能沉淀、MCP 扩展、本地控制 API 和远程 Web 控制台之间做一体化整合。

## 2. 技术栈与运行形态

- 语言：桌面主程序为 Zig；remote Web 控制台为 TypeScript；macOS 桥接有 Objective-C；Windows/PDF/WebView 等有少量 C/平台桥接。
- 构建：`build.zig` + `build.zig.zon`，要求 Zig `0.15.2`，桌面版本 `1.31.0`。默认开发目标偏 Windows `x86_64-windows-gnu`，macOS 有 `.app` bundle，Linux AppImage 属实验。
- 核心依赖：`ghostty-vt` 负责 VT parser / terminal state；`z2d`、FreeType、HarfBuzz、libpng、zlib、OpenGL、SDL、fontconfig、Apple SDK、libxev 等负责渲染、字体、平台能力。
- UI/渲染：自研桌面窗口与渲染管线。Windows/Linux 走 OpenGL，macOS 走 Metal/AppKit 方向；字体发现走 DirectWrite/CoreText/fontconfig；终端渲染支持 Ghostty 主题、sprite、背景图、shader、Kitty Graphics。
- 终端/PTY：`Surface.zig` 是每个终端 surface 的核心，拥有 PTY、terminal、选择区、OSC 状态、IO reader/writer 线程；Windows 用 ConPTY，POSIX 用 pty，tmux 镜像用 virtual PTY。
- AI provider：支持 OpenAI-compatible Chat Completions、OpenAI Responses API、Anthropic Messages API；profile 存在平台 config 下 `ai_profiles/`，字段 hex 编码。
- 技能/MCP/工具：内置 first-party tools、动态二进制工具、MCP stdio host、local skills、plugin skills、Skill Center、skill distillation。MCP 使用 `mcp.json` 配置，延迟激活，工具 catalog 缓存到 `mcp_catalog.json`。
- 测试：Zig fast tests、full tests、posix tests、ctl socket loopback tests、macOS UI E2E、remote TypeScript tests、skill fixture eval。仓库对架构债务还有 source guard ratchet。

## 3. 源码结构地图

- 根文档：`README.md` / `README.zh-CN.md` 给出产品能力；`AGENTS.md` 明确 Windows 优先、core/host 分层、source guard 规则；`docs/architecture.md` 是平台边界合同。
- `src/App.zig`：应用级状态、配置重载、remote client 生命周期。
- `src/AppWindow.zig`：窗口级集成层，协调 tabs、splits、render、input、overlay、agent/remote/tmux bridge，但按文档要求不应继续膨胀为 feature state 所有者。
- `src/Surface.zig`：单个 terminal surface，持有 PTY、Ghostty terminal、IO 线程、OSC、cwd、SSH 连接、remote snapshot、virtual tmux pane 支撑。
- `src/appwindow/`：对 AppWindow 的拆分，包含 `tab.zig`、`split_layout.zig`、`control_api.zig`、`tmux_bridge.zig`、`tmux_controller*.zig`、`ui_effect.zig`、`remote_sync.zig`。
- `src/assistant/conversation/`：AI Chat/Copilot 会话、协议 JSON、request worker、composer、model switch、markdown export、skill preload、distill。
- `src/assistant/profile/`：AI profile 持久化。
- `src/assistant/loop/`：`/loop`、`/watch`、`continue_later` 的调度引擎和持久化 store。
- `src/agent/`：agent 配置、权限、访问规则、文件编辑、memory、history store。
- `src/agent_tools/`：模型 tool-call runtime，负责 terminal、exec、file、memory、MCP、research、schedule、sessions、screenshot、weixin 等工具分发。
- `src/tools/`：first-party tool catalog、MCP registry/catalog、外部 tool import。
- `src/skill/`：Skill Center、registry、scan、install、transfer、diff。
- `src/terminal_agents/`：外部 CLI agent 的检测、prompt answer、OSC integration prompt、Codex/Claude/Reasonix 历史扫描与 resume。
- `src/tmux/`：tmux control-mode parser、layout parser、session model、pane IO bridge。
- `src/ctl/`：`wisptermctl` 本地控制 API 的 protocol/server/client/discovery/transport/socket test。
- `src/platform/`：平台 facade，包含 PTY、process、window_backend、clipboard、font、webview、notifications、http、dirs、file dialog 等。
- `src/renderer/` / `src/font/` / `src/input/`：渲染、字体、输入管线与 UI effect 边界。
- `remote/`：WispTerm Remote Web 控制台与 relay，Cloudflare Worker / Durable Object 与 Node server 双运行形态。
- `tests/` 与 `remote/test/`：macOS GUI E2E、skill fixture eval、remote client/server unit tests。

## 4. AI/Agent/Skill 实现机制

- Profile：`src/assistant/profile/store.zig` 将 profile 字段按 tab 分隔、hex 编码保存，字段包括 name、base_url、api_key、model、system_prompt、thinking、reasoning_effort、stream、agent、protocol、max_tokens、vision。
- 协议层：`src/assistant/conversation/protocol.zig` 定义 `ApiProtocol = chat_completions | responses | anthropic`，同一套 `RequestMessage`、`ToolCall`、`ApiResult` 能构建不同 provider 的 request JSON。
- 请求执行：`src/assistant/conversation/request.zig` 把普通聊天、streaming、agent tool loop、标题生成、model switch summary、skill distillation、websearch/webread/pubmed local commands 都放在 worker thread 入口中。
- Tool dispatch：`src/agent_tools/mod.zig` 是工具总分发器，通过 `ToolContext` 接收 allocator、terminal host callbacks、snapshot、权限、working_dir、memory 开关、dynamic tools、MCP tools、取消/审批/ask_user/schedule hooks。
- 终端工具：`src/agent_tools/terminal.zig` 能列出 surface、读 snapshot、选择/聚焦 surface，并对 targeted surface 读取 live snapshot，避免 request-start snapshot 过期。
- 执行工具：`src/agent_tools/exec.zig` 支持本地 shell、SSH session、WSL session、REPL 执行、approval prompt answer；本地命令走 `create_no_window = true`，stdout/stderr 有输出限制和超时/取消。
- 文件工具：`src/agent_tools/files.zig` 支持 local/WSL/SSH target resolution，`read_file` 给 numbered lines，`write_file` atomic write，`edit_file` 精确替换；远程 SSH 可用 `wispterm-filetool` server-side 检查/应用。
- 权限模型：`src/agent_tools/access.zig` 区分 `confirm/auto/full`。本地写入如果 confined 到 working_dir 可少提示；危险命令、黑名单路径、远程写入会 force approval。
- Memory：`src/agent/memory.zig` 是 global + project 两级 Markdown memory store，项目 tier 由 working directory 生成 filesystem-safe key；`memory_save/recall/delete` 由 `src/agent_tools/memory.zig` 适配。
- Skills：`src/assistant/conversation/skills.zig` 在 config、cwd、exe dir、bundle Resources 下查找 `skills/<name>/SKILL.md` 和 `plugins/skills/<name>/SKILL.md`；`src/skill/registry.zig` 读取 frontmatter、去重、限制大小、生成包含 source/hash 的 replayable snapshot。
- Skill 可回放性：显式 `$skill` 加载后保存为 tool result，历史不会因为磁盘上 SKILL.md 后续变化而失真。
- Skill Distillation：`src/assistant/conversation/distill.zig` 根据 tool-heavy transcript 提示沉淀 skill，写入前做 secret redaction/sensitive content guard，只保存到用户 config skills。
- MCP：`src/tools/mcp_registry.zig` 读取标准 `mcpServers` 配置；`src/tools/mcp_catalog.zig` 缓存发现结果；`src/agent_tools/mcp_activate.zig` 按需启动 server 做 initialize/tools/list；`src/agent_tools/mcp.zig` 每次 tool call 通过 stdio JSON-RPC 调用 `tools/call`，并复用 approval/output truncation。
- History/resume：WispTerm 自己的 Copilot/AI Chat 历史在 `src/agent/history.zig`；外部 Codex/Claude/Reasonix 历史由 `src/terminal_agents/sessions/provider_*.zig` 扫描解析；resume 要求回到原 project_dir，缺失则停止，不退回 `$HOME`。
- Terminal control for agent：内置 Copilot 不需要 `wisptermctl`，但同一底层能力通过 local loopback API 暴露给外部 Codex/Claude/scripts。

## 5. 终端/GUI/远程控制机制

- Tabs/Splits：`src/appwindow/tab.zig` 每个 tab 持有 `SplitTree`；`src/split_tree.zig` 是 Ghostty split tree 的专用化移植，leaf 可为 terminal 或 preview。`src/appwindow/split_layout.zig` 计算 pane rectangle、hit test、divider resize、surface resize。
- Tab 类型：`TabState.Kind` 包含 `terminal`、`ai_chat`、`ai_history`、`skill_center`、`port_forwarding`。终端 tab 还能挂 per-tab Copilot sidebar。
- PTY：`Surface.init` 打开真实 PTY 并启动 child process；`Surface.initVirtual` 用 virtual PTY 支撑 tmux pane，仍复用正常 terminal read/render 路径。
- tmux：`src/tmux/control.zig` 解析 `tmux -CC` control-mode 通知，如 `%output`、`%layout-change`、`%window-add`、`%window-renamed`；`src/tmux/layout.zig` 解析 tmux layout string；`src/tmux/pane.zig` 把 tmux pane output 写入 virtual PTY controller，并把 surface keystrokes 转为 `send-keys`。
- tmux UI bridge：`src/appwindow/tmux_bridge.zig` 把 tmux window materialize 为 WispTerm tab，把 pane materialize 为 virtual PTY Surface，layout 变化时 reconcile SplitTree。
- wisptermctl：`src/wisptermctl.zig` 是独立 CLI，不链接 GUI/SDL；命令包括 `panes`、`ui-state`、`get-text`、`send-text`、`wait-for`、`spawn`。
- 本地 loopback API：`src/ctl/server.zig` 绑定 `127.0.0.1`，每连接一条 JSON line，请求带 token；token 从 `<config>/agent-control.json` 读取，`src/ctl/discovery.zig` 写入 0600 文件。
- 控制 API 实现：`src/appwindow/control_api.zig` 在 UI render tick 发布 panes/ui-state JSON cache；get-text/send-text 通过 `surface_registry.acquireById` pin surface，避免 UAF；spawn 从 server thread 入队，由 UI thread drain。
- Remote Web：`remote/src/worker.ts` 用 Cloudflare Durable Object 保存 WispTerm socket、browser sockets、lastLayout；Node 版本 `remote/src/server/session.ts` 用内存 Map；browser 通过 `remote/src/client/transport.ts` 接收 layout/output-bytes 并回传 input-bytes。
- Direct messaging：WeChat/Feishu 能把手机/企业 IM 消息路由到 AI Chat 或 remote session；这部分是很强的“终端之外控制面”探索，但复杂度也高。

## 6. 技术优势

1. AI 与终端不是松散拼接：Copilot sidebar 自动注入绑定终端的 cwd 和 recent output，terminal tools 也能读 live snapshot、选择写入 context、聚焦 surface。
2. ToolContext 抽象清晰：工具层不直接依赖 Session，而是通过 host callbacks、snapshot、approval、cancel、working_dir、schedule hooks 接入 UI 和安全策略，利于测试和扩展。
3. Provider 抽象务实：同时支持 Chat Completions、Responses、Anthropic Messages，profile 可在运行中 `/model` 切换，并通过 summary handoff 压缩旧上下文。
4. 权限模型有可用边界：`confirm/auto/full`、危险命令检测、黑名单路径、working_dir confinement、远程写入强提示，比简单“全自动 shell”稳。
5. Skill 机制强调可回放：加载 skill 时生成 source/hash snapshot 作为 tool result，历史不会依赖未来文件状态。
6. MCP 延迟激活设计好：启动时不 spawn 全部 MCP server，只在 prompt 中暴露可用 server，模型调用 `mcp_activate` 后再发现/缓存工具，降低启动成本和故障面。
7. 本地控制 API 适合多智能体：`wisptermctl panes/get-text/send-text/wait-for/spawn` 给外部 agent 一个可脚本化控制面，可以让一个 agent 观察/驱动另一个 pane。
8. tmux control-mode 映射到 GUI pane：通过 virtual PTY 复用现有终端渲染与输入路径，避免给 tmux pane 写第二套渲染器。
9. 架构债务有机械 guard：`source_guards/` 冻结 global state、import hub、side effect、file size 等指标，要求债务只降不升。
10. 测试面覆盖关键风险：MCP discovery E2E、ctl socket round-trip、skill fixture eval、tmux parser/pane IO、remote client/server tests，很多能力边界有测试表达。
11. 远程控制设计完整：Cloudflare Worker + Node relay 两种部署，同协议，同 client，支持 layout snapshot、surface-level output/input、heartbeat/reconnect。
12. 平台边界明确：`src/platform/` facade + `window_backend` host interface 让 terminal core 不直接 import Win32/AppKit/SDL runtime。

## 7. 复杂度/风险/不适合直接照搬的点

1. Zig 桌面终端完整栈成本极高：PTY、VT、GPU、字体、IME、窗口、平台差异、打包发布都会吞掉大量工程资源。
2. `AppWindow.zig`、`input.zig`、overlay 等历史集成层仍有全局状态和耦合，仓库自己也用 source guards 承认这是待收敛债务。
3. Windows/macOS/Linux 平台矩阵复杂：Windows 是主目标，macOS 活跃稳定中，Linux 实验；跨平台 GUI 行为和测试成本不适合早期项目照搬。
4. MCP/skills/tools/IM/remote/tmux/AI history 同时做，产品面很宽；如果用户项目还没稳定核心 loop，直接全吸收会失焦。
5. 安全模型仍是本地工具型安全，不是强沙箱：`full` 权限、远程写入、MCP stdio server、literal API key in args 都需要用户信任本机环境。
6. tmux GUI 镜像很高级但难维护：control-mode parser、layout reconcile、virtual PTY、pane metadata、SSH endpoint 绑定都需要长期调试。
7. Remote relay 仍有信任边界：Worker 代码里 WispTerm route 注释提到未来需要 device challenge/response；公开远程控制不能直接当生产安全模型照抄。
8. Agent detector 当前保守：`src/terminal_agents/detector.zig` 对旧式输出启发式基本禁用，转向 OSC 7748 authoritative marker；说明靠屏幕文本猜 agent 状态不可靠。
9. Profile/API 支持多 provider 但流式能力不均：Anthropic protocol 文档明确 streaming 暂未支持。
10. E2E 测试也暴露 GUI 自动化限制：macOS 合成输入在隔离实例中不完全可用，目前主要靠 control channel 写入文本。

## 8. 对本项目的可学习内容

应优先借鉴：

- “终端 surface 是第一等对象”：给每个 pane/surface 稳定 ID、cwd、recent output、focus、agent state、snapshot。
- “本地控制 API 先行”：先做 `panes/get-text/send-text/wait-for/spawn`，让外部 agent 和脚本能控制工作区。
- “ToolContext seam”：工具执行通过统一上下文接入权限、取消、审批、surface snapshot、working_dir。
- “历史可恢复”：每个 agent session 记录 provider、session_id、project_dir、resume command，不允许 project_dir 丢失时静默回家目录。
- “skills 作为可回放上下文”：加载时保存 source/hash/content snapshot，而不是只保存 `$skill-name`。
- “MCP 延迟激活”：配置和发现分离，启动不拉起所有外部 server。
- “权限分级”：ask/auto/full + dangerous/protected/outside-workdir gate。
- “source guards / 架构 ratchet”：对全局状态、跨层 import、直接 dirty write 建机械护栏。
- “手机/远程控制作为后期扩展”：Remote/WeChat/Feishu 的思路可学，但不要先做。

应推迟：

- 自研终端模拟/字体/GPU 渲染。
- tmux control-mode GUI 镜像。
- Cloudflare/Node 双 relay。
- 多 IM 平台直连。
- 完整 Skill Center GUI 和 GitHub skill installer。
- 多平台原生窗口后端。

## 9. 建议本项目如何吸收 WispTerm

### MVP

- 不做终端模拟器，复用 tmux/系统 terminal/现有 PTY 库。
- 建一个 local-first 多 agent workspace：pane/session registry、稳定 pane id、cwd、recent output、agent role、task id。
- 做最小控制 CLI/API：`list`、`read`、`send`、`wait`、`spawn`。
- 做一个 SQLite/JSON state：sessions、messages、tasks、approvals、artifacts、resume command。
- 做 approval gate：所有会改文件/执行命令/跨工作区操作先进入 human approval。
- 做 agent history/resume：Codex/Claude 等外部 CLI 先作为 provider adapter，不内置完整 LLM API。

### Phase 2

- 引入 ToolContext：terminal_snapshot、send_input、exec、read_file/edit_file、ask_user、continue_later。
- 引入 skill snapshot：本地 `skills/<name>/SKILL.md`，显式加载，存入历史。
- 引入 MCP 但只做 stdio + 延迟激活 + catalog cache。
- 加一个轻量 Web/GUI 控制台，用来显示 panes、任务、审批、日志，不做完整 terminal renderer。
- 引入 loop/watch/continue_later，实现长任务自动回访。

### Phase 3

- 做更强 GUI：分屏、tab、拖拽、可视化 agent 状态、artifact preview。
- 研究 tmux control-mode，把 tmux pane 映射成自己的 GUI surface。
- 做远程访问 relay，但先明确设备认证、session key、浏览器登录、审计日志。
- 做 Skill Center/工具市场/插件安装。
- 做手机/企业 IM 控制入口，但必须和 approval、人类监督、可追溯历史绑定。
- 如果确实要自研 terminal emulator，再评估 Ghostty/libghostty-vt 或更成熟组件，避免从零写 VT/GPU/font。

## 10. 证据索引

1. `README.md`：英文主说明，定义 WispTerm 是 Zig + libghostty-vt 的远程开发与 AI agent 终端工作区。
2. `README.zh-CN.md`：中文功能列表，确认 AI Agent、历史浏览器、分屏、文件预览、remote access 等用户可见能力。
3. `AGENTS.md`：项目架构规则、Windows 优先、core/host/platform services 分层、source guard 与测试策略。
4. `docs/architecture.md`：core、host、platform services 的正式边界合同。
5. `docs/ai-agent.md`：AI provider、profile、model switch、Copilot sidebar、file tools、memory、skills、MCP、distill、slash commands。
6. `docs/agent-control.md`：`wisptermctl` 本地控制 API 的启用、安全与命令说明。
7. `docs/tabs-panels.md`：tabs/splits/panels 的用户模型和快捷键。
8. `docs/source-layout.md`：源码目录迁移规则和已完成 feature family 拆分。
9. `build.zig.zon`：Zig 版本、项目版本和 ghostty/z2d/freetype/opengl/harfbuzz/libxev 等依赖。
10. `build.zig`：构建桌面 app、`wisptermctl`、`wispterm-filetool`、fast/full/ctl/macOS tests、文档 embed、bundle 资源。
11. `src/Surface.zig`：每个终端 surface 的 PTY、Ghostty terminal、IO threads、virtual PTY、cwd/SSH/remote snapshot。
12. `src/split_tree.zig`：Ghostty split tree 移植，支持 terminal/preview pane leaf 和 immutable tree 操作。
13. `src/appwindow/tab.zig`：TabState、terminal/ai_chat/ai_history/skill_center/port_forwarding tab 类型、per-tab Copilot、tmux metadata。
14. `src/appwindow/split_layout.zig`：分屏布局计算、pane hit test、divider hit test、surface resize。
15. `src/appwindow/control_api.zig`：agent-control panes/ui-state cache、get-text/send-text、spawn queue、surface registry pinning。
16. `src/wisptermctl.zig`：外部 CLI 控制工具，自动发现 config token/port，经 127.0.0.1 JSON-lines 协议控制 panes。
17. `src/ctl/protocol.zig`：agent-control JSON-lines wire protocol、request/response 编解码。
18. `src/ctl/server.zig`：loopback TCP server、token 验证、one request per connection、read timeout、dispatch。
19. `src/ctl/discovery.zig`：`agent-control.json` 发现文件，0600 权限写入 port/token。
20. `src/ctl/socket_test.zig`：真实 loopback round-trip 测试，覆盖 panes、ui-state、spawn、大 get-text、bad token、shutdown。
21. `src/assistant/profile/store.zig`：AI profiles hex 编码持久化。
22. `src/assistant/conversation/protocol.zig`：OpenAI/Responses/Anthropic 协议抽象、tool/message/result schema。
23. `src/assistant/conversation/request.zig`：worker request、agent tool loop、model switch summary、distill/websearch/webread/pubmed threads。
24. `src/assistant/conversation/skills.zig`：skills/commands root discovery、skills digest、preload snapshot、distilled skill save。
25. `src/assistant/conversation/distill.zig`：skill distillation candidate、tool-heavy suggestion、secret redaction 相关逻辑。
26. `src/assistant/loop/schedule.zig`：`/loop`、`/watch` 纯调度引擎。
27. `src/assistant/loop/store.zig`：loop/watch/continuation 持久化 store 与 UI-frame tick 注入。
28. `src/agent_tools/mod.zig`：first-party tools 总分发，包括 terminal、exec、file、skill、MCP、memory、research、ask_user、schedule。
29. `src/agent_tools/terminal.zig`：terminal list/context/snapshot/select/focus，支持 live targeted snapshot。
30. `src/agent_tools/exec.zig`：本地命令、SSH/WSL session、REPL、approval prompt answer、超时/取消/输出限制。
31. `src/agent_tools/files.zig`：local/WSL/SSH 文件读写编辑复制 target resolution 和 remote filetool fallback。
32. `src/agent_tools/access.zig`：工具审批 gate，区分 confirm/auto/full、危险/黑名单/confined。
33. `src/agent/memory.zig`：global/project 两级 Markdown memory store、slug/project key/index。
34. `src/agent/history.zig`：WispTerm 自有 AI/Copilot 会话历史、index、replayable tool metadata。
35. `src/tools/first_party.zig`：内置工具 catalog 与 enable/disable 状态文件。
36. `src/tools/mcp_registry.zig`：MCP config 读取、发现、schema 投影、cache reload。
37. `src/tools/mcp_catalog.zig`：MCP tool discovery 的磁盘 catalog 与 config hash invalidation。
38. `src/agent_tools/mcp_client.zig`：stdio MCP JSON-RPC initialize/tools/list/tools/call minimal client。
39. `src/tmux/control.zig`：tmux control-mode parser。
40. `src/tmux/pane.zig`：tmux pane output 到 virtual PTY、surface keystrokes 到 tmux send-keys 的桥。
41. `src/appwindow/tmux_bridge.zig`：tmux Session 到 WispTerm tab/split/surface 的 UI bridge。
42. `src/terminal_agents/sessions/provider_codex.zig`：Codex JSONL 元数据与 transcript 解析。
43. `src/terminal_agents/sessions/provider_claude.zig`：Claude Code 历史解析，包含 tool_result 处理。
44. `src/terminal_agents/sessions/resume.zig`：Codex/Claude/Reasonix resume command 和 project_dir 存在性检查。
45. `src/terminal_agents/detector.zig`：agent 状态检测转向 OSC 7748 authoritative marker。
46. `src/terminal_agents/prompt_answer.zig`：Codex/Claude approval menu 解析与语义回答映射。
47. `src/terminal_agents/integration_prompt.zig`：引导外部 agent 注入 OSC 7748 状态上报 hook。
48. `src/skill/registry.zig`：SKILL.md frontmatter 解析、大小限制、snapshot source/hash。
49. `src/skill/center.zig`：Skill Center library、deploy/import、first-party tool entries、GitHub install picker 模型。
50. `src/skill/scan.zig`：远端技能目录扫描与 hash 聚合。
51. `remote/README.md`：Remote Console 部署、路由、relay message、Weixin bridge 说明。
52. `remote/src/worker.ts`：Cloudflare Durable Object relay，保存 WispTerm/browser sockets 和 lastLayout。
53. `remote/src/server/session.ts`：Node relay session、layout surface 查找、input forwarding、AI Agent open request。
54. `remote/src/client/transport.ts`：browser websocket、heartbeat/reconnect、layout normalize、output/input-bytes。
55. `tests/eval/README.md`：skill loading eval 说明，强调 snapshot 可回放稳定性。
56. `tests/macos_e2e/README.md`：真实 WispTerm.app E2E 测试策略和当前 GUI 输入限制。
57. `tests/macos_e2e/test_mcp_discovery.py`：真实 app 启动后对 fake MCP server 做 initialize/tools/list 的 E2E。
58. `src/source_guards/*.zig`：文件大小、global state、import hub、side effect、layer dependency 等架构 ratchet。
