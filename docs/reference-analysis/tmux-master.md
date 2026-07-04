# tmux-master 深度研究报告

> 审查对象：`References/tmux-master`  
> 审查方式：subagent 只读源码分析，主线程整理落盘。  
> 重点：tmux 如何支撑长期运行、可见、可恢复、可输入/读取的多 Agent 终端 runtime。

## 1. 项目一句话定位

tmux 是一个本地优先、server/client 架构的终端多路复用器：它把多个长期运行的 PTY 进程组织成 session/window/pane，并通过 UNIX socket、命令队列和 control mode 让这些终端可以被创建、拆分、附着、脱离、输入、读取、恢复和外部控制。

## 2. 技术栈与运行形态

- 语言与构建：C，Autotools，`configure.ac` + `Makefile.am`，主产物是单个 `tmux` 可执行文件。
- 事件模型：libevent 2.x。server、client、PTY、control mode、job、socket 通信都围绕 event/bufferevent。
- 终端能力：ncurses/terminfo，负责终端能力探测、颜色、键盘序列、TTY 输出适配。
- 运行形态：一个 tmux server 管理所有 session/window/pane；多个 tmux client 通过 UNIX socket 连接同一个 server。
- PTY 模型：每个 pane 对应一个伪终端和一个子进程；tmux server 读 PTY 输出、解析终端控制序列、维护 screen/grid，并把输入写回 PTY。
- 外部控制：control mode 是文本协议，外部程序可以发 tmux 命令、接收 `%output`、`%layout-change`、`%sessions-changed` 等事件。
- 平台：README 明确支持 OpenBSD、FreeBSD、NetBSD、Linux、macOS、Solaris；源码还有 AIX、Cygwin、Haiku、HP-UX 等 osdep/compat 适配。
- socket：默认 socket 放在用户私有目录，`-L` 选择 socket name，`-S` 指定 socket path；因此同一机器可跑多个隔离 server。
- 安全/权限：socket 目录检查 owner/permission；server/client 之间使用 imsg 协议和 fd passing；OpenBSD 上使用 pledge。
- 可选能力：utempter、systemd socket activation、systemd cgroup 隔离、ASAN/debug/fuzz 构建等。

## 3. 源码结构地图

tmux 源码是“扁平核心 + 少量子目录”：

- 根目录命令文件：`cmd-new-session.c`、`cmd-split-window.c`、`cmd-send-keys.c`、`cmd-capture-pane.c`、`cmd-respawn-pane.c` 等，每个文件实现一个或一组用户命令。
- 对象模型：`tmux.h` 定义 `struct session`、`struct window`、`struct window_pane`、`struct client`、`struct spawn_context`、`struct tty`、`struct grid` 等核心结构。
- runtime 生命周期：`tmux.c` 处理入口、socket path、全局配置；`client.c` 连接 server；`server.c` 启动 server、事件循环、退出策略；`server-client.c` 处理 client/pane 交互。
- PTY 与进程：`spawn.c` 创建/重启 pane 进程；`window.c` 维护 pane、PTY bufferevent、输入输出；`proc.c` 封装进程间 imsg 通信；`job.c` 管理后台 job。
- 屏幕与终端解析：`input.c` 解析 pane 输出；`screen.c`、`screen-write.c`、`grid.c`、`grid-view.c` 保存屏幕和历史；`tty.c`、`tty-keys.c`、`tty-term.c` 输出到真实终端。
- 布局：`layout.c` 是树状布局核心，`layout-set.c` 是 preset layout，`layout-custom.c` 是布局字符串解析/序列化。
- 控制协议：`control.c` 管理 control mode 队列、背压、pane offset、订阅；`control-notify.c` 发结构性事件。
- 格式与查询：`format.c` 提供 `#{pane_id}`、`#{pane_current_command}`、`#{session_name}` 等模板变量；`cmd-list-panes.c` 等命令依赖它输出机器可读状态。
- 回归测试：`regress/` 是 shell 脚本测试，覆盖 session/window/pane/control/capture/input/target 语义。
- 兼容层：`compat/` 提供 OpenBSD 风格函数、forkpty、imsg 等跨平台实现。
- fuzz：`fuzz/` 覆盖 command parse、format、input/style 等高风险解析器。

## 4. tmux 如何开启和管理多 Agent 终端

tmux 对多 Agent 终端最重要的启发是：Agent 不应该只是一个子进程列表，而应是“PTY-backed pane + 稳定 ID + 可见 screen/grid + 可控制输入 + 可恢复绑定”的组合对象。

### 4.1 session/window/pane 模型

- `session` 是长期工作空间：有稳定 `session_id`、名字、cwd、环境、窗口集合、当前窗口和 last-window stack。
- `window` 是一个可被多个 session link 的 terminal surface：包含 pane 列表、活动 pane、布局树、尺寸和 alert 状态。
- `window_pane` 是实际运行单元：包含 pane id、cwd、argv、shell、pid、tty、fd、bufferevent、screen/base grid、history、pipe、exit 状态、prompt/mode 状态。
- 对多 Agent 项目而言，最自然映射是：
  - project/run = session
  - role/task group = window
  - individual agent terminal = pane
  - control UI = tmux client 或 control mode client

### 4.2 new-session

`cmd-new-session.c` 负责创建 session，关键动作是：

- 解析 `-s` session 名、`-n` 初始 window 名、`-c` cwd、`-d` detached、`-A` attach-or-create、`-t` session group。
- 没有 client 时自动 detached，这让后台创建长期 Agent terminal 成为一等路径。
- 保存 client termios，打开 terminal，计算默认尺寸。
- 通过 `spawn_context` 创建初始 window/pane。
- `-A` 如果 session 已存在则走 attach，而不是创建重复 session。

对 Agent runtime 的启发：启动任务应该是幂等的，`agent runtime new --attach-or-create project` 比盲目新开进程安全。

### 4.3 split-window / new-pane

`cmd-split-window.c` 通过 layout 选择目标 cell，再构造 `spawn_context` 调 `spawn_pane()`：

- 支持横向/纵向 split、百分比/固定尺寸、before、full-size、detached、zoom、floating。
- 支持 `-I`/`-E` 创建空 pane 或把 stdin 转发到 pane。
- 支持 pane 级环境变量、cwd、样式、remain-on-exit 等。
- 新 pane 默认变成 active，除非 detached。

对 Agent runtime 的启发：Agent pane 的创建不只是 fork，还必须绑定布局、身份、环境、cwd、激活策略和生命周期策略。

### 4.4 spawn_pane 是核心执行路径

`spawn.c` 中 `spawn_pane()` 是 tmux 运行子进程的核心：

- 选择 cwd：新建时来自 client/session，respawn 时可复用 pane 存储 cwd。
- 新建 pane：`window_add_pane()` + `layout_assign_pane()`。
- respawn pane：清旧 bufferevent/fd、重置 mode/screen/input，然后复用原 pane 对象。
- 构造子进程环境：合并 session/global 环境，注入 `TMUX_PANE=%id`，设置 PATH/SHELL/PWD。
- 通过 `fdforkpty()` 创建 PTY 和 child process。
- child 中设置 termios、恢复 signal、关闭多余 fd、`execvp()` 或 `$SHELL -c`。
- parent 中 `window_pane_set_event()`，把 pane fd 接入 bufferevent，并初始化 input parser。

这说明 tmux 的“可恢复”不是保存进程内存，而是保存 pane 元数据、screen/history 和启动命令；进程死后可用 respawn 在同一个 pane identity 上重新启动。

### 4.5 attach / detach

`cmd-attach-session.c`：

- 通过 target 解析 session/window/pane。
- 支持 attach 到具体 pane，设置 active pane/current window。
- 可 detach/kill 其他 client。
- 可设置 read-only client 和 client flags。
- 打开真实终端后 `server_client_set_session()` 绑定 client 和 session。

detach 的本质不是停止 Agent，而是断开 client 与 session 的 UI 连接；server 和 pane 子进程继续运行。这是多 Agent 项目的核心能力：UI 可断，runtime 不断。

### 4.6 send-keys

`cmd-send-keys.c`：

- 普通模式下把 key 或 UTF-8 字符转为 `key_code`，调用 `window_pane_key()` 写入目标 pane。
- `-l` literal 输入原始字符串，`-H` 输入十六进制字节。
- `-K` 走 client key handling，可触发绑定。
- `-X` 操作 copy-mode 等 pane mode 命令。
- 支持重复次数 `-N`。

对 Agent runtime 的启发：对 Agent 发指令不应只做 stdin write；要区分 literal 文本、特殊键、模式命令、鼠标/控制事件和 client 级快捷键。

### 4.7 capture-pane

`cmd-capture-pane.c`：

- 可从 grid/history/current screen/pending buffer 读取内容。
- `-p` 打印到 client；control client 下走 `control_write()`。
- 否则写入 paste buffer。
- 支持样式/颜色/hyperlink 等输出模式。
- `clear-history` 共享同一执行文件，能清 screen history。

对多 Agent 项目而言，`capture-pane -p -S - -E -` 是低成本读取 Agent 当前可见输出和 scrollback 的接口，但不是结构化日志；长期应配合 pipe/control/event log。

### 4.8 pipe-pane

`cmd-pipe-pane.c`：

- 为 pane 创建 `socketpair()`。
- fork 一个 shell command，把 pane 输出写到该 command stdin，或把 command stdout 写回 pane。
- 使用 bufferevent 管理 pipe read/write/error。
- `-o` 可实现 toggle logging：已有 pipe 时不重复创建。

这对 Agent runtime 很有用：可以把每个 Agent pane 的输出流实时 tee 到日志、索引器、状态抽取器，或把外部生成输入注入 pane。

### 4.9 respawn-pane

`cmd-respawn-pane.c` + `spawn.c`：

- 默认拒绝 respawn 仍活跃 pane，除非 `-k`。
- `remain-on-exit` 可让死 pane 保留，便于读取最后输出。
- `respawn-pane -E` 可保存命令/cwd 但暂不启动，之后再 respawn。
- respawn 复用同一个 pane 对象和 id 语义，重置 screen/input/fd。

对 Agent runtime 的启发：Agent restart 应该尽量保持逻辑 ID 和 UI 位置不变，而不是销毁旧视图再创建新视图。

### 4.10 layout 与可见性

`layout.c` 用树状 `layout_cell` 管理 split：

- pane 是 leaf，横向/纵向 split 是 internal node。
- resize 会受最小尺寸约束。
- layout 可 dump/parse，control notify 会发 `%layout-change`。
- `targets-panes.sh` 测试 `{top-left}`、`{right-of}`、`{marked}` 等 pane 定位。

对 Agent UI 来说，layout 是 runtime 状态，不只是前端样式。pane 的可见位置、大小、相邻关系、active/marked 都可成为控制逻辑的一部分。

### 4.11 socket/server 生命周期

- `tmux.c` 生成安全 socket path，`-L/-S` 支持多 server。
- `client.c` 先连 socket；若失败且允许，则启动 server。
- `server.c` daemonize，初始化全局 RB tree/TAILQ，创建 socket，进入 libevent loop。
- server loop 先跑全局和 client command queue，再跑 client/pane loop。
- `exit-empty` / `exit-unattached` 决定无 session/client 时是否退出。
- kill 最后一个 session 后 server 可退出，`regress/session-ops.sh` 有覆盖。

多 Agent 项目如果把 tmux 当底座，最小运行单元应是命名 socket + 命名 session，避免不同项目混用默认 socket。

## 5. control mode 与外部控制

control mode 是 tmux 给 GUI/控制面最值得复用的接口。

### 5.1 文本协议

`tmux -C` 后，外部程序通过 stdin 发 tmux 命令。每条命令输出一个 `%begin ...` 到 `%end/%error ...` 的 block。异步事件不会插入 block 内部，保证命令响应和通知可区分。

### 5.2 事件输出

`tmux.1` 和 `control-notify.c` 说明了事件类型：

- `%output %pane-id value`：pane 有输出。
- `%extended-output %pane-id age : value`：启用 pause-after 后附带缓冲年龄。
- `%layout-change @window layout visible-layout flags`：布局变化。
- `%window-add/%window-close/%window-renamed`：窗口生命周期。
- `%window-pane-changed`：活动 pane 变化。
- `%session-changed/%sessions-changed/%session-window-changed`：session 变化。
- `%pane-mode-changed`：copy-mode 等 mode 变化。
- `%pause/%continue`：控制客户端对 pane 输出背压后的状态。

### 5.3 订阅

`refresh-client -B name:what:format` 可以订阅格式变化：

- `what` 可为 session、`%pane`、`%*`、`@window`、`@*`。
- `control.c` 周期性展开 format，只有值变化才发 `%subscription-changed`。
- 可用于 GUI 订阅 `#{pane_current_command}`、`#{pane_dead}`、`#{pane_title}`、`#{history_size}`、`#{window_layout}` 等。

这比轮询 `list-panes` 更轻，适合 Agent 控制台实时更新状态。

### 5.4 队列与背压

`control.c` 的实现非常具体：

- 每个 control client 有一个全局 block queue 和每个 pane 的 output queue。
- `%output` 同时进入全局队列和 pane 队列，保证输出块与后续通知的顺序。
- 使用 `CONTROL_BUFFER_LOW=512`、`CONTROL_BUFFER_HIGH=8192` 控制写缓冲水位。
- 每个 pane 有独立 offset/queued offset，慢 client 不影响 pane 的真实 screen 解析。
- 若 client 太慢，默认超过 `CONTROL_MAXIMUM_AGE` 会退出；启用 `pause-after` 则发送 `%pause`，由 client 决定何时 `continue`。
- `refresh-client -A %pane:off` 可以关闭某 pane 输出；如果所有控制 client 都关了该 pane，server 可停止读取 pane，避免无意义堆积。

这对多 Agent runtime 的启发很硬：一定要设计背压。Agent 输出可能爆量，GUI/日志/LLM 观察器不能无限缓存。

## 6. 技术优势

1. 长期进程与 UI 解耦：detach 只断 client，server/pane 继续运行，天然适合夜间 Agent 任务。
2. PTY 真实终端语义：Agent CLI 以为自己在真实终端里，交互式 prompt、颜色、全屏 TUI 都能跑。
3. 稳定身份模型：session/window/pane 都有唯一 ID，pane id 生命周期内稳定，便于控制面引用。
4. 可见状态持久化：screen/grid/history 由 server 保存，断线重连后仍能看到上下文。
5. 输入通道成熟：`send-keys` 支持特殊键、literal、UTF-8、mode command，不局限于 stdin 字符串。
6. 输出读取多层次：可用 `capture-pane` 读当前屏幕/history，可用 control mode 收实时 `%output`，可用 `pipe-pane` 接外部日志。
7. 布局是 runtime 一等对象：split/resize/zoom/floating 都进入 layout tree，可序列化、通知和恢复。
8. 可恢复重启：respawn 保留 pane 位置/身份，重置进程，适合 Agent 崩溃后人工或自动重启。
9. 命令系统统一：用户命令、配置文件、control mode、key binding 最终进入同一 command queue，行为一致。
10. 机器可读 format 系统：`list-panes -F`、`display-message -p` 能输出 pane pid/cwd/command/dead/history/layout 等。
11. 多 client 并发观察：多个控制面或终端可 attach 同一 session，支持 read-only/ignore-size/active-pane 等 client flags。
12. 背压实现成熟：control mode 对慢消费者有 pause、off、高低水位、age 检测。
13. 跨平台工程扎实：大量 compat/osdep 处理 PTY、socket、event、terminfo 差异。
14. 回归测试覆盖行为语义：session/window/pane/control/capture/input/target 均有 shell 测试，便于学习命令契约。
15. 插件式外围容易：不需要嵌入 tmux 源码，用 CLI/control mode/socket/session 即可做上层 Agent runtime。

## 7. 复杂度/风险/不适合直接照搬的点

1. C 代码复杂度高：RB tree、TAILQ、manual memory、event lifetime、fd lifetime 对新项目维护成本大。
2. 终端模拟细节深：input/grid/screen/tty 的边界非常多，重写容易出现 ANSI、Unicode、scrollback、alternate screen 错误。
3. control mode 是文本协议：虽然稳定，但不是 JSON/RPC；需要自己处理 quoting、octal escape、block 边界和异步事件。
4. capture-pane 不是结构化日志：它读取屏幕状态，不等同于完整 stdout/stderr 事件流，可能丢语义。
5. send-keys 易受 TUI 状态影响：同样的输入在 shell、vim、less、Agent prompt 中含义不同，上层必须知道 pane 当前状态。
6. 进程恢复有限：tmux 能 respawn，但不能恢复子进程内存、网络连接、LLM 上下文；Agent 应自行持久化任务状态。
7. 多平台行为不完全一致：PTY、kqueue/epoll、macOS Unicode、systemd/cgroup、socket 权限都有平台分支。
8. 默认 socket/global server 容易串项目：多 Agent 项目必须固定 `-L` 或 `-S`，否则会误操作用户已有 tmux。
9. pane 输出可能爆量：control client 若不处理背压，会被 pause、断开，甚至拖慢读取。
10. 安全边界要谨慎：send-keys/pipe-pane 本质可执行任意 shell 输入/命令，必须有人类确认和权限分层。
11. 窗口布局与业务状态分离：tmux 不知道哪个 pane 是 “reviewer” 或 “planner”，上层要维护 role/task metadata。
12. 不适合直接 fork 改内核：除非目标是做终端复用器，否则改 tmux C core 会把项目拖进终端模拟器维护泥潭。

## 8. 对本项目的可学习内容：把 tmux 当底座还是重写？

建议：MVP 和相当长一段 Phase 2 都把 tmux 当底座，不要重写 PTY/session/layout/control 基础设施。

原因：

- 本项目的核心价值应是多 Agent 编排、任务状态、人类审批、消息路由、工作树/上下文管理，而不是重新实现终端模拟器。
- tmux 已经解决“多个长期运行、可见、可恢复、可输入/读取”的底层问题。
- 上层可以用 `tmux -L <project> ...`、`list-panes -F`、`send-keys`、`capture-pane`、`pipe-pane`、control mode 封装出 Agent runtime API。
- 重写只适合后期需要浏览器/桌面原生 terminal renderer、远程协作、多租户权限、结构化协议强约束时，再替换底层 adapter。

推荐封装方式：

- 写 `RuntimeBackend` 接口：`create_session`、`spawn_agent`、`send_input`、`capture_output`、`stream_events`、`restart_agent`、`kill_agent`、`attach_ui`。
- 第一实现为 `TmuxBackend`，内部固定 `-L agentdeck-<project-id>` 或 `-S .agentdeck/tmux.sock`。
- 上层数据库维护 `agent_id -> tmux session/window/pane_id` 映射。
- 不把 tmux pane id 当唯一业务 ID；pane id 是 runtime handle，业务 ID 应由项目自己生成。
- 每个 Agent pane 启动命令包装成 supervisor shell：设置 cwd/env，打印启动 banner，写 heartbeat/status 文件，最后保留 exit code。
- 输出读取分三层：实时 control mode 事件、pipe-pane 日志、按需 capture-pane 快照。

## 9. 建议本项目如何吸收 tmux

### MVP：tmux 作为 runtime，先做薄封装

目标：让用户能创建一个多 Agent 本地工作台，并可看、可输、可读、可恢复。

建议命令：

```bash
agentdeck runtime doctor
agentdeck project init
agentdeck session start --name <project>
agentdeck agent spawn --role planner --cmd "codex"
agentdeck agent spawn --role worker --cmd "claude"
agentdeck agent send --agent worker --text "继续执行任务"
agentdeck agent capture --agent worker --lines 200
agentdeck agent list
agentdeck ui attach
agentdeck session stop
```

tmux 映射：

```bash
tmux -L agentdeck-<project> new-session -d -s <session> -n control
tmux -L agentdeck-<project> split-window -d -t <session>:<window> -c <cwd> '<agent command>'
tmux -L agentdeck-<project> list-panes -a -F '#{session_name} #{window_id} #{pane_id} #{pane_pid} #{pane_current_command} #{pane_dead} #{pane_current_path}'
tmux -L agentdeck-<project> send-keys -t %pane -l '<text>'
tmux -L agentdeck-<project> send-keys -t %pane Enter
tmux -L agentdeck-<project> capture-pane -p -t %pane -S -200
```

MVP 要点：

- 固定 socket/name，避免误操作用户默认 tmux。
- 每个 Agent 有业务 ID、role、pane id、cwd、command、status。
- 支持 `remain-on-exit`，Agent 崩溃后保留最后输出。
- 使用 `capture-pane` 做人工审查上下文。
- 所有 destructive 命令，如 kill/respawn/send Enter，走确认或审批。

### Phase 2：control mode + 日志管道

目标：从“能操作”升级为“可观察、可订阅、可审计”。

建议增加：

```bash
agentdeck events watch
agentdeck agent logs --follow
agentdeck agent pause-output
agentdeck agent resume-output
agentdeck agent restart --keep-pane
agentdeck layout save
agentdeck layout restore
```

实现建议：

- 后台起一个 control mode client：`tmux -L ... -C attach -t <session>`。
- 解析 `%output`、`%layout-change`、`%sessions-changed`、`%window-pane-changed`。
- 用 `refresh-client -B` 订阅 pane/window/session format。
- 对每个 Agent 开 `pipe-pane -o` 把原始输出写入 `.agentdeck/logs/<agent-id>.log`。
- 实现背压策略：默认只订阅活跃 Agent 输出，低优先级 pane 可 `refresh-client -A %pane:off`。
- `respawn-pane -k` 前保存旧日志、capture 最后 N 行、记录 restart reason。

### Phase 3：抽象 runtime，tmux 仍为本地后端

目标：把 tmux 能力沉淀为通用 runtime contract，未来可换本地 PTY/远程容器/浏览器终端。

建议模块：

- `RuntimeBackend`: tmux/local-pty/ssh/container 后端接口。
- `TerminalSurface`: pane screen snapshot、history、cursor、size、title。
- `AgentProcess`: pid、command、cwd、env、exit status、restart policy。
- `EventBus`: output/layout/session/process/control event。
- `ApprovalGate`: send input、kill、restart、file mutation、merge/push 等危险动作审批。
- `StateStore`: SQLite 保存 project/session/window/agent/pane/log/checkpoint。

Phase 3 不建议直接改 tmux core；除非你要做自有终端模拟器。更好的路线是：tmux 作为 local backend，前端通过自己的 API 读状态、发命令、渲染日志和审批流。

## 10. 证据索引

1. `References/tmux-master/README`：项目定位、依赖、平台、构建方式。
2. `References/tmux-master/tmux.1`：session/window/pane 概念、命令语义、control mode 协议。
3. `References/tmux-master/tmux.h`：核心结构体和 flags。
4. `References/tmux-master/tmux.c`：入口、socket path、安全目录、全局状态。
5. `References/tmux-master/client.c`：client 连接 socket、启动 server、处理 imsg。
6. `References/tmux-master/server.c`：server 启动、daemon、事件循环、退出策略。
7. `References/tmux-master/server-client.c`：client loop、pane 输出消费、control client 与 PTY 读取协调。
8. `References/tmux-master/proc.c`：imsg peer、socketpair daemon、event loop。
9. `References/tmux-master/session.c`：session 创建/销毁、window attach/detach、session group。
10. `References/tmux-master/window.c`：window/pane 管理、PTY bufferevent、pane read/error callback。
11. `References/tmux-master/spawn.c`：spawn/respawn pane、forkpty、环境、exec。
12. `References/tmux-master/layout.c`：split/resize/layout tree。
13. `References/tmux-master/layout-set.c`：预设布局。
14. `References/tmux-master/layout-custom.c`：layout dump/parse。
15. `References/tmux-master/input.c`：pane 输出解析为 screen/grid。
16. `References/tmux-master/grid.c`：屏幕网格与 history 存储。
17. `References/tmux-master/grid-view.c`：grid 视图操作。
18. `References/tmux-master/screen.c`：screen 状态。
19. `References/tmux-master/screen-write.c`：写 screen/grid 的抽象。
20. `References/tmux-master/tty.c`：真实终端输出、tty 写入、client redraw。
21. `References/tmux-master/tty-keys.c`：终端键盘输入解析。
22. `References/tmux-master/tty-term.c`：terminfo 能力。
23. `References/tmux-master/job.c`：后台 job、PTY job、bufferevent。
24. `References/tmux-master/control.c`：control mode 队列、背压、输出、订阅。
25. `References/tmux-master/control-notify.c`：control mode 结构性通知。
26. `References/tmux-master/cmd-new-session.c`：new-session/has-session 命令。
27. `References/tmux-master/cmd-split-window.c`：split-window/new-pane 命令。
28. `References/tmux-master/cmd-send-keys.c`：键盘/文本注入。
29. `References/tmux-master/cmd-capture-pane.c`：pane 内容读取。
30. `References/tmux-master/cmd-pipe-pane.c`：pane 输出/输入外接管道。
31. `References/tmux-master/cmd-respawn-pane.c`：pane 重启。
32. `References/tmux-master/cmd-list-panes.c`：机器可读 pane 枚举。
33. `References/tmux-master/cmd-attach-session.c`：client attach session/pane。
34. `References/tmux-master/cmd-kill-session.c`：session 销毁和过滤 kill。
35. `References/tmux-master/cmd-refresh-client.c`：control client size、pane off/pause/continue、subscription。
36. `References/tmux-master/format.c`：format 变量系统。
37. `References/tmux-master/environ.c`：session/pane 环境合并。
38. `References/tmux-master/options.c`：server/session/window/pane option。
39. `References/tmux-master/options-table.c`：option 定义表。
40. `References/tmux-master/Makefile.am`：源码清单和构建 flags。
41. `References/tmux-master/configure.ac`：依赖、平台、libevent/ncurses/PTY/socket 检测。
42. `References/tmux-master/compat/fdforkpty.c`：forkpty 兼容封装。
43. `References/tmux-master/regress/session-ops.sh`：session 命名、分组、kill 行为测试。
44. `References/tmux-master/regress/window-ops.sh`：window link/move/kill 语义测试。
45. `References/tmux-master/regress/pane-ops.sh`：split/join/respawn/resize/pane 行为测试。
46. `References/tmux-master/regress/control-client-sanity.sh`：control client 下 split/layout 操作测试。
47. `References/tmux-master/regress/control-client-size.sh`：control mode client size 测试。
48. `References/tmux-master/regress/targets-panes.sh`：pane target 解析测试。
49. `References/tmux-master/regress/capture-pane-sgr0.sh`：capture-pane 样式输出测试。
50. `References/tmux-master/regress/kill-session-process-exit.sh`：kill session 会终止其中进程的行为测试。
