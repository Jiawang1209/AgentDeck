# codex 精修返工任务 live 确认(2026-08-01)

Status: PASS
Scope: `CodexCliProvider.refine_rework_task()`(`src/agentdeck/providers/cli_subprocess.py`)
Trigger: `agentdeck plan rework --plan-id <id> --confirm --refine` 在
`[leader] provider = "codex-cli"` 下走的那条精修路径。

## 为什么需要这一验

Leader 精修返工任务落地时的终审指出:codex 的 stdout 是**交互记录**而不是
答案,把 stdout 当返工任务会把整段 transcript 写进 plan step。修复方式是
给 codex 单独一个 override,复用 planning 路径同源的 `--output-last-message`
把最终答案写进私有工作区文件再读,stdout/stderr 仍在 **OS 边界**丢弃。

该修复当时只有单元测试覆盖(mock subprocess),**没有真实 codex 确认**,
终审因此把它列为未验证项。本文件是那一项的收尾。

## 方法

直接构造 provider 调用被质疑的那一个方法,不经过 state、不写任何项目:

```python
from agentdeck.providers.cli_subprocess import CodexCliProvider
from agentdeck.review_iteration import validate_refined_task

p = CodexCliProvider()
out = p.refine_rework_task(task=<原实现任务>, feedback=<含 verdict 的评审回复>)
validate_refined_task(out)
```

输入是一段真实形态的评审反馈:`verdict: {"schema":"review-verdict/v1",
"overall":"needs_changes"}` 加三条具体意见(缺 aria-label、点击后焦点没
回到顶部、滚动监听未节流)。

## 结果

- 真实 `codex exec` 调用成功返回,长度 **316 字符**(远低于
  `MAX_REWORK_TASK_CHARS = 4000`)。
- 返回内容是**干净的返工任务正文**——一段说明加五条编号要求,逐条对应
  评审意见并补上"保持原有 300px 淡入行为"与验证要求;**没有任何 `•
  Ran ...`、`└ {...}`、token 统计等 codex 交互记录残留**。这正是
  `--output-last-message` 修复要保证的性质,live 成立。
- `validate_refined_task()` 接受该文本,并由**程序**追加固定尾句
  `修复后 commit 到任务分支。`(模型没有、也不需要自己写出这句)。

## 边界复核

- 该路径**只**改写返工 step 的 `task` 文本;step 的 agent/role/编号/
  provenance 仍与模板路径逐字节一致(单元测试覆盖,本次未改动)。
- stdout/stderr 仍为 `DEVNULL`——codex 诊断永不被消费的不变量保持不变,
  因此 codex 精修失败只记退出码,不做 `classify_cli_failure` 分类。
- `--refine` 仍是 explicit-only:`run-loop` / `--all` / `--follow` /
  `run-loop-host` 都够不到这个入口,"run-loop 永不调用 Leader provider"
  的 live 结论不受影响。

## 结论

Leader 精修返工的两个 CLI backend 现在都已确证:claude 走 envelope 且
坏包 fail-closed(单元覆盖),codex 走 `--output-last-message` 且真实调用
返回干净正文(本文件)。终审遗留的未验证项关闭。
