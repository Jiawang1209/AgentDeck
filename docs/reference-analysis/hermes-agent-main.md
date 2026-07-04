# Hermes Agent 参考仓库深度研究报告

> 审查对象：`References/hermes-agent-main`  
> 审查方式：subagent 只读源码分析，主线程整理落盘。  
> 重点：Hermes 的 agent 内核、技能、记忆、工具、学习闭环，以及对本项目 Leader/Worker Agent 的借鉴价值。

## 1. 项目一句话定位

Hermes 是一个以 Python 为 agent 内核、兼容 CLI/TUI/桌面端/多消息平台的“自学习个人智能体运行时”：它把对话循环、工具调用、记忆、技能、子代理、定时任务、多 provider、网关消息和后台学习整合成同一套可复用 agent 核心。

## 2. 技术栈与运行形态

Hermes 的核心是 Python 项目，`pyproject.toml` 声明包名为 `hermes-agent`，Python 版本要求为 `>=3.11,<3.14`。核心依赖包括 `openai`、`httpx`、`rich`、`prompt_toolkit`、`fastapi`、`uvicorn`、`croniter`、`pydantic`、`Pillow`、`psutil`、`websockets` 等。Node 侧主要服务于 UI/桌面/Web 辅助形态，`package.json` 要求 Node `>=20`，包含 `apps/*`、`ui-tui`、`web` 等 workspace。

运行入口主要有三类：

- CLI：`hermes = hermes_cli.main:main`，并保留 `hermes-agent = run_agent:main`。
- 交互终端/TUI：`cli.py` 承担大型交互式 REPL/TUI 逻辑，支持模型选择、命令补全、后台任务、审批、附件、语音/图片输入等。
- Gateway：`gateway/run.py` 启动多平台消息网关，把 Telegram、Discord、Slack、WhatsApp、Signal、Email、Webhook、API server 等平台路由到同一个 agent 核心。

Provider 形态很宽：OpenAI-compatible、Anthropic、Gemini/Vertex、Bedrock、OpenRouter、Nous Portal、自定义 endpoint、Kimi/Moonshot、GLM、MiniMax、Hugging Face 等。Provider 适配由 `providers/base.py`、`agent/transports/*` 和 `agent/auxiliary_client.py` 分层完成。

Tools 体系由 `tools/registry.py` 注册，`toolsets.py` 聚合为能力组。核心工具包括 web、terminal/process、file、vision/image、browser、tts、todo、memory、session_search、clarify、execute_code、delegate_task、cronjob、kanban、computer_use 等。

Skills 是 Markdown/目录式技能机制，既可通过 slash command 手动加载，也可由后台 review/curator 维护。Memory 则分为本地文件记忆和外部 provider 记忆，支持 prefetch、sync、工具 schema 注入和输出 scrub。Cron 提供内置定时任务系统，任务结果可保存并投递到消息平台。

测试覆盖面很广，从 agent、memory、compression、MoA、cron、gateway、ACP、CLI 到工具 guardrails 都有专门测试文件。这个项目不是“小工具”，而是一个完整 agent OS。

## 3. 源码结构地图

- `README.md` / `README.zh-CN.md`：产品能力总览，说明 Hermes 的自学习、跨平台、多 provider、skills、memory、delegation、cron、gateway 等目标。
- `AGENTS.md`：开发约束和架构原则，尤其强调 per-conversation prompt cache 不可随意破坏，以及“core is narrow waist; capability at edges”。
- `pyproject.toml`：Python 包、依赖、optional extras、console scripts 和 package 组织。
- `package.json`：Node workspace、桌面/TUI/Web 安装脚本和前端依赖。
- `run_agent.py`：`AIAgent` 主类外壳，负责初始化、状态、委派 dispatch，并把对话循环转发到 `agent/conversation_loop.py`。
- `agent/conversation_loop.py`：主 agent loop，处理用户输入、LLM 调用、工具执行、循环终止、子代理结果回流、steer/interrupt、MoA marker 等。
- `agent/turn_context.py`：每一轮对话前置上下文构建，包括 prompt 恢复、session 持久化、压缩预检、memory prefetch、plugin hook 等。
- `agent/system_prompt.py`：系统 prompt 构建与缓存，分 stable/context/volatile 三层。
- `agent/prompt_builder.py`：上下文文件扫描、prompt injection 检测、memory/skills 使用说明生成。
- `model_tools.py`：工具调用总 dispatcher，处理工具参数、Tool Search 桥、plugin middleware、approval、工具注册表调用。
- `agent/tool_executor.py`：顺序/并行工具执行器，带 timeout、heartbeat、interrupt、session flush 和 thread context 传播。
- `toolsets.py`：工具集声明和核心工具集合边界。
- `tools/registry.py`：工具自注册表、schema、check_fn、动态启用状态和 TTL 缓存。
- `tools/delegate_tool.py`：子代理委派、批处理、角色、权限、summary budget、进度事件、interrupt/pause。
- `tools/code_execution_tool.py`：让模型写 Python 脚本，并通过本地/远端 RPC 调父进程工具。
- `tools/memory_tool.py`：本地 `MEMORY.md`/`USER.md` 文件记忆工具。
- `agent/memory_manager.py` / `agent/memory_provider.py`：记忆 provider 抽象、prefetch/sync、外部记忆工具 schema 注入、memory context scrub。
- `agent/skill_commands.py` / `agent/skill_preprocessing.py` / `agent/skill_bundles.py`：技能加载、模板变量、inline shell、技能 bundle。
- `agent/background_review.py` / `agent/curator.py`：后台学习、技能/记忆 review、技能整理与归档。
- `agent/context_compressor.py` / `agent/conversation_compression.py`：长上下文压缩、session split、压缩锁、memory/plugin 通知。
- `agent/agent_init.py` / `agent/auxiliary_client.py`：agent 初始化、provider 解析、辅助模型路由。
- `agent/transports/*`：不同 provider 的消息、工具、response 适配层。
- `providers/base.py` / `providers/README.md`：provider profile 插件化定义。
- `agent/moa_loop.py` / `hermes_cli/moa_config.py`：Mixture-of-Agents 参考模型 fanout 与聚合配置。
- `gateway/*`：多平台消息网关、session identity、stream dispatch、platform adapters。
- `cron/*`：定时任务、job 存储、调度器、suggestions、blueprint。
- `agent/tool_guardrails.py` / `agent/verification_stop.py` / `agent/verify_hooks.py`：工具循环保护和代码修改后的验证提醒。
- `tests/*`：围绕 agent、gateway、cron、tools、compression、MoA、ACP 的行为测试。

## 4. Hermes 如何做智能体

Hermes 的 agent 流程核心是 `AIAgent` + `run_conversation`。`run_agent.py` 中的 `AIAgent.chat()` 最终调用 `agent/conversation_loop.py`，返回 `final_response`。这说明 Hermes 的智能体不是单次 LLM 调用，而是一个有状态 turn loop。

一轮对话开始时，`agent/turn_context.py` 会先做 prologue：恢复或构建系统 prompt，写入 session DB，估算上下文长度，必要时触发 compression，执行 plugin `pre_llm_call`，启动 memory manager 的 turn-start/prefetch，并重置 streaming scrubber、tool guardrails、memory consolidation 状态。这个设计把“模型调用前的运行时整理”集中起来，值得自研系统借鉴。

Prompt/context 分层非常清楚。`agent/system_prompt.py` 把系统提示分为 stable、context、volatile 三层：stable 包括身份、工具使用、技能索引、平台提示、模型指导；context 包括调用者 system message 和 AGENTS/HERMES 等上下文文件；volatile 包括 memory snapshot、用户信息、时间戳等。它还坚持“系统 prompt 每个 session 构建后缓存，除 compression 外不随便重建”，这是为了保护 prompt cache 和对话稳定性。

Tool calling 由 `model_tools.py` 统一调度。它先标准化工具名和参数，再经过 middleware、plugin hook、approval guard、registry 查找，最后调用具体 handler。`_AGENT_LOOP_TOOLS` 中的 `todo`、`memory`、`session_search`、`delegate_task` 被视作 agent loop 特殊工具。这个分层避免每个工具自己处理权限、审计和运行时上下文。

并行工具执行在 `agent/tool_executor.py`。Hermes 不是无脑并发，而是通过 `_should_parallelize_tool_batch` 判断工具是否独立：只读工具可以并发，文件操作必须路径不重叠。并发执行用 daemon `ThreadPoolExecutor`，默认最多 8 个 worker，并带 per-tool timeout、heartbeat、interrupt 传播、context propagation 和 session DB 增量 flush。对 Worker Agent 来说，这是一个很好的“安全并行”模型。

Memory 体系分两层：本地文件 memory tool 和 memory provider manager。`tools/memory_tool.py` 把 `MEMORY.md`/`USER.md` 作为 durable store，但加载到系统 prompt 的是 frozen snapshot；运行中写 memory 不会立刻重建 system prompt，避免破坏 prompt cache。`agent/memory_manager.py` 则负责外部 provider prefetch/sync、工具 schema 注入和 `<memory-context>` 输出 scrub。它还明确把任务进度和过程性知识排除出 memory，建议用 session_search 或 skills。

Skills 是 Hermes 的“可学习操作经验”载体。`agent/skill_commands.py` 支持 `/skill-name` 把技能内容注入对话，`agent/skill_preprocessing.py` 支持模板变量和受控 inline shell，`agent/skill_bundles.py` 支持多个技能打包加载。更关键的是 `agent/background_review.py` 会在对话后 fork 一个后台 agent，用 memory/skill tools 复盘本轮对话，决定是否写入记忆、更新已有技能或创建新技能。`agent/curator.py` 进一步定期整理 agent-created skills，支持归档、合并、保护 pinned/cron referenced skills。

Learning loop 的设计不是“模型自己随意改自己”，而是通过受限工具白名单和后台 review agent 完成。后台 review fork 继承父 agent runtime/cache，但只给 memory 和 skill_manage 权限；如果使用不同辅助模型，会用 compact digest 降低冷 cache 成本。这个模式对用户项目很有启发：学习可以异步、低权限、可审计，而不是塞进主对话路径。

Context compression 由 `agent/context_compressor.py` 和 `agent/conversation_compression.py` 完成。它会保护 head/tail，把中间对话总结成带有“仅供参考、最新用户消息优先、memory 更权威”语义的摘要，并在压缩后 split SQLite session、轮换 session_id、通知 memory provider/plugin/context engine。这个做法适合长会话，但实现复杂度高，MVP 可以先做手动摘要或简单 session summary。

Provider switching 通过 profile + transport + auxiliary client 三层完成。`providers/base.py` 定义 ProviderProfile，`agent/transports/base.py` 定义消息/工具/response 适配接口，`agent/auxiliary_client.py` 负责 compression、session_search、web extraction、vision 等辅助任务的 provider 自动选择和 fallback。主模型、辅助模型、MoA 参考模型可以走不同 provider。

Gateway 把 agent 变成多平台消息服务。`gateway/run.py` 维护 agent cache、平台 adapter 生命周期和消息调度；`gateway/session.py` 把 platform/chat/user/thread/profile 统一成 session identity；`gateway/stream_dispatch.py` 把 typed stream events 交给平台 adapter 渲染。Hermes 的关键点是：平台只是 transport，agent core 不绑死在某个平台。

Approval/guardrails 分布在多处。`model_tools.py` 有 ACP edit approval；`agent/tool_guardrails.py` 检测重复失败、同工具空转、幂等无进展；`agent/verification_stop.py` 在代码改动后提示验证；`prompt_builder.py` 和 `tools/memory_tool.py` 都有 prompt injection/threat pattern 扫描；gateway 对外部平台会过滤 provider error/secret。Hermes 的防线不是一个总开关，而是多层“小闸门”。

## 5. 子代理、委派与并行能力

Hermes 的子代理能力主要在 `tools/delegate_tool.py` 和 `run_agent.py` 的 delegation dispatch。

`delegate_task` 会创建 child `AIAgent`，拥有隔离上下文、独立 session、受限 toolsets 和自己的终端会话。父 agent 的上下文里只看到委派调用和最终 summary，不会塞入子代理中间推理或所有工具结果。这一点非常适合 Leader/Worker：Leader 保持上下文干净，Worker 负责执行脏活。

权限边界很明确。子代理默认禁用 `delegate_task`、`clarify`、`memory`、`send_message`、`execute_code`、`cronjob` 等高风险能力。只有 role 为 orchestrator 且深度限制允许时，子代理才可继续委派。`max_spawn_depth` 默认偏保守，`max_concurrent_children` 默认约 3，避免无限递归和成本失控。

Hermes 区分顶层异步委派和 orchestrator 同步委派。顶层模型发起 delegation 时，会倾向后台运行，并把子代理结果作为新消息回流到主对话；而 orchestrator 子代理内部委派通常同步等待，便于汇总 worker 结果。这给用户项目一个清晰模式：Leader 面向用户时可以异步收结果，Worker-Orchestrator 面向任务树时应同步聚合。

子代理 summary budget 是一大亮点。`_apply_summary_budget` 会按父上下文剩余空间、batch size 和静态上限裁剪每个 worker summary；超长完整结果会 spill 到 `cache/delegation/subagent-summary-*.txt`，父上下文只保留头尾和 `read_file` 指针。对多智能体系统来说，这是防止 Leader 被 Worker 输出淹没的关键机制。

Hermes 还会记录 child 的 `files_read`、`files_written`、tool trace、tokens、cost、duration、model、output_tail，并在 child 写了 parent 之前读过的文件时提醒父 agent 重新读取。这是一个很具体的协作安全点：多 agent 不是只合并文字，还要处理文件状态陈旧问题。

MoA 是另一类并行能力。`agent/moa_loop.py` 把 `/moa` 作为 slash-turn 模式：每轮主模型调用前，先并行调用多个 reference advisors。参考模型没有工具权限，只给建议；最终执行和工具调用仍由主 agent loop 负责。`hermes_cli/moa_config.py` 定义参考模型、聚合模型、fanout 策略和 token 限制。这个适合高价值推理，不适合 MVP 默认启用。

批处理还体现在 `delegate_task` 支持 batch parallel delegation，以及 `agent/tool_executor.py` 对工具调用批次的并行执行。Hermes 的并行不是单一机制，而是三层：工具级并行、worker 级并行、模型参考级并行。

工具 RPC 则由 `tools/code_execution_tool.py` 提供：模型可以生成 Python 脚本，脚本通过父进程生成的 `hermes_tools.py` stub 调用 web/search/file/terminal 等工具。Local 模式用 Unix domain socket，远端 backend 用文件轮询 RPC。它把多步程序化操作压缩成一次工具调用，减少上下文污染，但安全和复杂度都很高。

## 6. 技术优势

1. Agent loop 职责集中：`conversation_loop.py` 统一管理 LLM 调用、工具执行、子代理结果、interrupt、steer 和循环终止，行为可追踪。
2. Turn prologue 明确：`turn_context.py` 把 prompt、memory、session、compression、plugin hook 等前置动作收束到一处，减少散落状态。
3. Prompt cache 纪律强：`AGENTS.md` 和 `system_prompt.py` 都强调 session 内系统 prompt 稳定，避免随意变更工具集或上下文造成 cache 失效。
4. Core narrow waist：`toolsets.py` 和 `AGENTS.md` 明确能力优先放在 skills、CLI、service-gated tools、plugins、MCP，而不是不断膨胀核心工具。
5. 工具注册表成熟：`tools/registry.py` 支持 schema、check_fn、toolset、动态 schema、TTL 状态缓存和模块自注册，扩展性好。
6. 并行工具执行有安全判断：只读工具可并发，文件操作要求路径不重叠，避免简单并发带来的写冲突。
7. 子代理隔离做得具体：child agent 有独立 session、受限工具、独立 task id、可追踪 token/cost/files/tool trace，并且默认禁止 memory/cron/clarify 等高风险能力。
8. 子代理 summary budget 保护 Leader 上下文：超长输出落盘，只给父 agent 摘要和读取路径，适合大规模 Worker 并发。
9. Memory 不混淆任务进度：本地 memory guidance 明确 durable facts、用户偏好、环境知识进 memory，临时任务状态用 session_search，流程经验用 skills。
10. 学习闭环低权限异步化：`background_review.py` 用 forked review agent 写 memory/skills，不阻塞主对话，也不赋予全工具权限。
11. Skills 可被运行时调用和维护：技能不是静态文档，支持 slash 加载、bundle、模板变量、后台更新和 curator 归档。
12. Context compression 不是简单截断：它保护 head/tail，标注摘要权威性低于最新消息和 memory，并完成 session split。
13. Provider 适配层分明：ProviderProfile、Transport、AuxiliaryClient 分工清楚，避免每个功能直接写 provider if-else。
14. Gateway 复用同一 agent core：多平台消息只负责 session identity 和事件渲染，不重写 agent 行为。
15. Cron 是一等自动化系统：任务有 profile 隔离、文件锁、输出归档、平台投递、注入扫描、并发控制和 at-most-once 语义。
16. Guardrails 分层：工具循环检测、edit approval、memory/context injection 扫描、verification nudge、gateway secret filtering 共同构成防护。

## 7. 复杂度、风险与不适合直接照搬的点

1. 代码面很大：`cli.py`、`run_agent.py`、gateway、cron、provider、skills、memory、delegation 同时存在，MVP 照搬会迅速失控。
2. Prompt cache 不变量很难维护：一旦项目没有严格测试，动态工具、动态 memory、动态 system prompt 很容易破坏会话一致性。
3. Provider 支持过宽：多 provider profile、transport 适配、fallback、aux routing 会带来大量边缘行为，早期项目不宜一开始支持太多模型平台。
4. `execute_code` RPC 能力强但风险高：模型写脚本再调工具，虽然有限权和 token 保护，但对安全审计、沙箱、日志和权限要求很高。
5. 后台学习可能污染长期行为：自动写 memory/skills 如果没有 review、diff、撤销和 provenance，容易把一次性误判固化。
6. 多平台 gateway 权限复杂：不同平台的身份、线程、长度、附件、错误展示、安全过滤都不同，早期项目不应把消息平台作为核心复杂度。
7. Cron 自动化有自治风险：定时任务结合工具调用、平台投递、skills 后，若缺少审批和审计，可能产生不可预期动作。
8. 子代理成本和并发会膨胀：Worker、MoA、工具并行、execute_code 都可能叠加 token/cost，需要预算和硬限制。
9. Memory poisoning 是现实问题：Hermes 已做 threat pattern 扫描，但任何把外部内容放进长期 prompt 的系统都有注入风险。
10. Context compression 可能制造“摘要幻觉”：摘要若被模型当成事实，会导致历史状态错误；Hermes 用提示降低权威性，但仍需谨慎。
11. Skills/Plugins/MCP 增加供应链面：技能支持模板和 shell 片段，插件/provider 可扩展，安全边界要比普通 CLI 更严格。
12. 测试成本高：Hermes 的行为依赖缓存、session、provider、gateway、cron、并发和文件锁，单元测试之外还需要大量契约测试和 E2E。

## 8. 对本项目的可学习内容

对本项目的多智能体系统，最值得借鉴的是 Hermes 的“Leader 保持上下文干净，Worker 隔离执行并结构化回报”思想。Leader Agent 应该拥有用户上下文、计划、任务分解、权限决策、最终汇总和验证责任；Worker Agent 应该拿到明确任务、有限上下文、有限工具、独立 session，并返回结构化结果。

Worker 返回格式建议吸收 Hermes 的字段：`status`、`summary`、`files_read`、`files_written`、`tool_trace`、`risks`、`verification`、`tokens/cost`、`duration`、`full_output_path`。这比只返回一段自然语言更适合 Leader 做二次判断。

Memory 值得借鉴“冻结快照 + 活存储”的模式。Leader 可以在任务开始读取用户偏好和项目事实，但不要在同一轮中频繁重建系统 prompt。Worker 默认不写长期 memory，最多写任务局部 artifacts。真正的长期学习应由后台 reviewer 或用户确认完成。

Skills 值得作为“可复用工作流”而不是“提示词收藏”。例如代码审查、调试、发布、数据分析、PRD 拆解都可以是技能。MVP 只需要手动技能加载和少量内置技能，不需要一开始做自动 skill curator。

工具系统应学 Hermes 的 registry/toolset/check_fn，而不是把所有工具硬编码在 agent loop。Leader 可以按任务启用工具集；Worker 只继承必要子集。尤其是文件写入、终端、网络、浏览器、消息发送、定时任务应分级授权。

Context compression 可以先学理念，不必学完整实现。MVP 阶段可以做“任务摘要文件 + 最近 N 轮 + 关键 artifacts 路径”，Phase 2 再做自动压缩和 session split。

Guardrails 必须早做。至少应有：重复同一工具失败检测、无进展循环检测、文件修改后验证提醒、危险工具审批、Worker 禁止直接问用户、Worker 禁止长期记忆写入、Leader 重新读取 Worker 修改过的文件。

不建议进入 MVP 的能力包括：全平台 gateway、MoA、execute_code RPC、外部 memory provider、自动 skill curator、完整 provider catalog、cron 平台投递、桌面端/TUI、复杂 ACP、多云 terminal backend。这些是 Hermes 的成熟能力，但会拖慢本项目核心闭环。

## 9. 建议本项目如何吸收 Hermes

### MVP：先做可控 Leader/Worker 内核

MVP 应聚焦一个 repo-native CLI，多智能体只支持 Leader + 若干 Worker。

建议实现：

- `leader run <task>`：Leader 读取项目上下文、拆任务、派发 Worker、汇总。
- Worker 运行在独立 session，默认只读；需要写文件时由 Leader 授权。
- 工具 registry + toolsets：至少包括 `read_file`、`search_files`、`shell_readonly`、`write_patch`、`run_tests`。
- Delegation result schema：强制 Worker 返回结构化 JSON/Markdown。
- Summary budget：Worker 输出过长时落盘，只给 Leader 摘要和路径。
- 文件陈旧提醒：Worker 写过的文件，Leader 合并前必须重新读取。
- Memory 初版：只读项目事实和用户偏好，不自动写长期记忆。
- Skills 初版：Markdown 技能手动加载，例如 code-review、debugging、implementation-plan。
- Verification gate：有文件改动必须运行对应检查或说明未运行原因。

这个阶段不要做 MoA、自动学习、cron、多消息平台和 execute_code RPC。

### Phase 2：加入学习和更强上下文管理

第二阶段可以引入 Hermes 式后台 reviewer，但要先可审计。

建议实现：

- 后台 review agent 只拥有 `memory_suggest` 和 `skill_suggest` 权限，不直接覆盖文件。
- Memory 写入采用 pending review 队列，用户确认后落地。
- Skills 支持版本、来源、最近使用、适用场景和撤销。
- 自动压缩长任务上下文，但摘要必须明确“历史参考，不覆盖最新用户要求”。
- Worker 支持批量并发，但增加全局 token/cost/concurrency 预算。
- Provider adapter 抽象化，但只支持 2-3 个主 provider。
- 增加 session_search，用于找历史任务进度，避免把过程状态塞进 memory。
- 增加工具循环 guardrails 的测试。

这一阶段的目标是“多轮项目工作不丢上下文”，而不是扩展平台。

### Phase 3：平台化、自动化和高级并行

第三阶段再考虑 Hermes 的高级形态。

建议实现：

- Gateway：先接一个平台，例如本地 Web/API 或 Slack，再扩展其他消息端。
- Cron：只允许只读报告类任务，后续再开放写操作和投递。
- MoA：用于高价值设计评审或方案比较，不默认用于普通任务。
- execute_code RPC：仅在有沙箱、审计、工具白名单、token 预算后引入。
- 外部 memory provider：作为可替换 provider，而不是核心依赖。
- Skill curator：只能归档/建议合并，避免静默删除或重写关键技能。
- Worker-Orchestrator：允许二级 delegation，但必须有深度、并发、预算上限。
- 多平台 UI/TUI：当 CLI contract 稳定后再做。

最终形态可以接近 Hermes：同一个 agent core 支撑 CLI、Web、Gateway、定时任务和子代理。但路径上应先把 Leader/Worker 的任务契约、工具权限和验证闭环打牢。

## 10. 证据索引

1. `README.md`：说明 Hermes 的自学习 agent、skills、memory、delegation、gateway、cron、provider 等产品定位。
2. `README.zh-CN.md`：中文说明同一能力面，便于确认项目面向用户的公开叙述。
3. `AGENTS.md`：记录核心架构原则，尤其是 prompt cache、narrow waist、能力扩展路径和测试要求。
4. `pyproject.toml`：定义 Python 版本、核心依赖、optional extras、console scripts 和包结构。
5. `package.json`：说明 Node workspace、桌面/TUI/Web 辅助运行形态。
6. `run_agent.py`：定义 `AIAgent` 主类、chat 入口和 delegation dispatch 的关键连接点。
7. `agent/conversation_loop.py`：实现主对话循环、LLM 调用、工具执行、子代理结果回流和循环终止。
8. `agent/turn_context.py`：集中处理每轮对话前的 prompt、session、compression、memory 和 plugin hook。
9. `agent/system_prompt.py`：实现系统 prompt 分层构建和 session 内缓存策略。
10. `agent/prompt_builder.py`：实现上下文文件加载、prompt injection 检测、memory/skills guidance。
11. `model_tools.py`：实现工具调用统一调度、middleware、approval、plugin hook 和 registry dispatch。
12. `agent/tool_executor.py`：实现顺序/并行工具执行、timeout、heartbeat、interrupt 和 session flush。
13. `toolsets.py`：定义核心工具集、能力分组和哪些工具属于 agent core。
14. `tools/registry.py`：实现工具注册表、schema、check_fn、动态启用和 TTL 缓存。
15. `tools/delegate_tool.py`：实现子代理创建、批量委派、角色、权限、summary budget 和进度事件。
16. `tools/code_execution_tool.py`：实现模型生成 Python 脚本并通过 RPC 调父进程工具的机制。
17. `tools/memory_tool.py`：实现本地 `MEMORY.md`/`USER.md` 记忆、冻结快照和注入扫描。
18. `agent/memory_manager.py`：实现 memory provider 编排、prefetch/sync、tool schema 注入和 context scrub。
19. `agent/memory_provider.py`：定义外部记忆 provider 的抽象生命周期和 hook。
20. `agent/skill_commands.py`：实现 slash skill 加载、技能查找和技能 scaffold 提取。
21. `agent/skill_preprocessing.py`：实现技能模板变量和 inline shell 预处理。
22. `agent/skill_bundles.py`：实现多个技能的 bundle 加载机制。
23. `agent/background_review.py`：实现后台 review agent，用于异步更新 memory 和 skills。
24. `agent/curator.py`：实现技能整理、归档、保护和可选 LLM consolidation。
25. `agent/context_compressor.py`：实现长上下文摘要、head/tail 保护、工具输出裁剪和摘要提示。
26. `agent/conversation_compression.py`：实现压缩触发、session split、压缩锁和 provider/plugin 通知。
27. `agent/agent_init.py`：实现 agent 初始化、provider/runtime 解析、MoA client 和运行配置。
28. `agent/auxiliary_client.py`：实现 compression/session_search/vision 等辅助模型路由和 fallback。
29. `providers/base.py`：定义 ProviderProfile、模型目录、鉴权、hook 和 provider 元数据。
30. `providers/README.md`：说明 provider profile 的扩展和发现方式。
31. `agent/transports/base.py`：定义 provider transport 的消息、工具和 response 适配接口。
32. `agent/transports/chat_completions.py`：实现 OpenAI-compatible chat completions 的消息和工具适配。
33. `agent/transports/anthropic.py`：实现 Anthropic 消息、thinking/tool_use block 和 response 规范化。
34. `agent/moa_loop.py`：实现 MoA 参考模型并行咨询和主 agent 聚合流程。
35. `hermes_cli/moa_config.py`：定义 MoA preset、参考模型、聚合模型和 fanout 配置。
36. `hermes_cli/moa_cmd.py`：实现 MoA CLI 配置命令。
37. `hermes_cli/main.py`：实现 `hermes` CLI 主入口、环境修复和子命令挂载。
38. `hermes_cli/_parser.py`：定义 CLI flags、oneshot、model/provider、toolsets、resume、TUI/CLI 模式。
39. `hermes_cli/commands.py`：集中定义 slash commands，包括 session、model、tools、skills、memory、cron、MoA 等。
40. `cli.py`：实现大型交互式 CLI/TUI REPL、审批、模型选择、附件和后台状态处理。
41. `gateway/run.py`：实现多平台 gateway runner、agent cache、平台生命周期和消息调度。
42. `gateway/platforms/base.py`：定义平台 adapter 接口、消息长度、媒体和网络能力边界。
43. `gateway/session.py`：实现 platform/chat/user/thread/profile 的 session identity 和安全 key 处理。
44. `gateway/stream_dispatch.py`：实现 typed stream event 到平台渲染的分发。
45. `cron/jobs.py`：实现 cron job 文件存储、profile 隔离、输出归档、锁和路径安全。
46. `cron/scheduler.py`：实现 cron 调度、注入扫描、工具集限制、并发池和投递。
47. `agent/tool_guardrails.py`：实现重复失败、无进展和幂等循环的 per-turn guardrail。
48. `agent/verification_stop.py`：实现代码编辑后未验证时的合成提醒。
49. `agent/verify_hooks.py`：实现验证 hook 和验证提醒配置。
50. `batch_runner.py`：提供研究/批量 trajectory 运行线索，说明 Hermes 也面向批处理实验。
51. `tests/agent/test_context_compressor.py`：表明上下文压缩有专门行为测试。
52. `tests/agent/test_moa.py`：表明 MoA 流程有专门测试。
53. `tests/cron/test_parallel_pool.py`：表明 cron 并发池行为有测试覆盖。
54. `tests/gateway/test_compression_indicator.py`：表明 gateway 与压缩状态展示有测试覆盖。
55. `tests/tools/test_registry.py`：表明工具注册表行为有测试覆盖。
