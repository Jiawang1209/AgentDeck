# AGENT.md

## AgentDeck Agent Contract

本项目中的 agent 是有名字、有角色、有 runtime 绑定、有消息队列、有权限边界的工作单元。

## Agent 类型

### Leader Agent

职责：

- 理解用户目标。
- 读取项目上下文。
- 拆解任务。
- 分配 Worker。
- 请求人类审批。
- 汇总 Worker 结果。
- 触发验证。
- 输出最终结论。

默认 provider：DeepSeek/OpenAI-compatible。

Leader 不应该：

- 把 Worker 的完整长输出全部塞进上下文。
- 在没有审批的情况下执行破坏性命令。
- 在 Worker 修改文件后不重新读取就直接汇总。

### Worker Agent

职责：

- 接收明确任务。
- 在有限工具集内执行。
- 返回结构化结果。
- 记录读写文件和验证结果。

Worker 输出建议格式：

```markdown
status: completed | blocked | failed
summary: 简短结果
files_read:
  - path
files_written:
  - path
verification:
  - command: ...
    result: passed | failed | not_run
risks:
  - ...
full_output_path: .agentdeck/artifacts/...
```

Worker 不应该：

- 默认写长期 memory。
- 直接询问用户。
- 私自派生更多子代理。
- 绕过 Leader 执行 git push、merge、reset、kill pane 等动作。

## Runtime 绑定

业务 ID 和 runtime handle 分离：

- `agent_id`: 本项目业务 ID，例如 `coder`。
- `role`: 任务角色，例如 `implementation`。
- `provider`: agent 使用的模型或 CLI 类型。
- `pane_id`: tmux runtime handle。
- `session_name`: tmux session。
- `cwd`: 工作目录。

## 消息规则

- 面向用户展示 agent name，不展示 provider 细节。
- task request 和 task reply 都进入 mailbox。
- 每个 agent 同时只消费一个 active task。
- 所有 job/reply/event 都要可 trace。

## 审批规则

以下动作必须进入审批：

- 写文件。
- 删除或移动文件。
- 执行 destructive shell command。
- 向 agent pane 发送可执行输入。
- kill 或 respawn pane。
- git commit/push/merge/reset。
- 暴露远程访问或写入 credential。
