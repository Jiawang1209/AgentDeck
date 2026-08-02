# 从人类门一键决策:`delegation gate-preview` 设计

Status: frozen(user 拍板 2026-08-03,方向由 user 在四个候选中选定)
Baseline: human_gate(宿主与 `--follow` 双面已落地并 live 验证)+ scoped
授权委托(`docs/contracts/delegation-schema.md`)。本设计**不改动任何一方**,
只在两者之间架一座桥。

## 问题:停得诚实了,但停下之后还是死路

授权框是这个产品**被实测证明**的头号瓶颈,不是缺哪个功能:

| 轮次 | 证据 |
| --- | --- |
| Round 3 | 6 个同类授权框逐个人工放行 |
| Round 5 | 7 个框,全是 `node tests/*` |
| Round 13 | 委托生效后一路自动跑完 |
| **Round 14** | **一道 Playwright 框卡了两天**;846 wave 里 834 个空转在它上面 |

human_gate 解决了"停得诚实"——宿主现在 7 秒内停下并指名道姓。但**停下
之后**人还是得:自己把命令抄出来 → 自己想清楚该 grant 什么前缀 → 手敲一条
grant → 再手动放行那道框。而且**下次同一类命令还会再停一次**。

宿主已经把框的完整证据握在手里(`agent_id` / `command` / `box_kind` /
`mcp_server` / `mcp_tool`),`delegation grant` 也早就存在。**缺的只是桥。**

## 设计支点:宽度必须由人选,不能由程序选

这是整个切片的核心,也是它值得认真做的唯一理由。

同一道框,不同前缀的授权宽度差着数量级:

```
命令: /Users/x/.codex/skills/playwright/scripts/playwright_cli.sh open file:///…/index.html

--prefix ".../playwright_cli.sh open file:///…/index.html"  → 只授权这一个文件
--prefix ".../playwright_cli.sh open"                       → 授权打开任意文件
--prefix ".../playwright_cli.sh"                            → 连带授权 navigate / fill / evaluate 全家
```

所以本命令**给出一梯候选,每条标注它连带授权什么,让人自己挑**——
**绝不推荐其中任何一条**。

候选是确定性推导的:按空白切分命令,取逐级递减的 token 前缀(最长 = 整条
命令 = 最窄,最短 = 首个 token = 最宽),上限 5 条,首尾两条必在。每条附:

- `prefix` —— 可直接填进 grant 的原文;
- `unpinned_tail` —— 该前缀**没有**钉住的那段(即"这部分可以是任何东西");
- `is_widest` —— 是否是裸单 token(最大宽度)。

MCP 框没有梯子:一条 grant 恰好覆盖一个 `(server, tool)`,精确等值,
无宽度可调。

## 交付面

`agentdeck delegation gate-preview [--agent <id>]` —— **纯只读**。

**证据来源两条,都只读:**

- 缺省:读 `.agentdeck/run-loop-host/host.json` 的 `human_gate`——**零 pane
  读取**,正对应"走开段刚停下来告诉你"这个流程;
- `--agent <id>`:复用 `agentdeck agent boxes` 的只读扫描做一次实时检测。
  `--follow` 不写宿主记录,它的用户只能走这条路,所以这条必须有。

**输出摊开完整的两步闭环**(仅是文本,不执行):

```
planner 卡在一道未委托的授权框上:
  $ /Users/x/.codex/skills/playwright/scripts/playwright_cli.sh open file:///…/index.html

若你决定授权这一类,可选的前缀(越往下越宽):

  1) …/playwright_cli.sh open file:///…/index.html
     连带授权: (无——仅此一条命令)
  2) …/playwright_cli.sh open
     连带授权: 任意 <file:///…> 参数
  3) …/playwright_cli.sh
     连带授权: 任意子命令与参数            ⚠ 最宽
     agentdeck delegation grant --agent planner --prefix "<上面选定的一条>" --confirm

授权之后,这道框仍需显式放行一次:
  agentdeck agent release-box --agent planner --confirm

⚠ AgentDeck 无法核验一条命令的性质。打开本地页面看似只是观察,
   但同一个脚本也能导航、填表、执行脚本。前缀越短,授权越宽。
   本命令不推荐、不判断、不执行任何一条。
```

## 安全边界

- **纯只读**:不写 state、不追加事件、不 grant、不 release、不调 provider、
  不发送任何 tmux 输入。缺省路径连 pane 都不读。
- **绝不推荐**。不排序出"建议项",不高亮某一条,不因命令看起来无害就少说
  一句风险。人选哪条,风险由人承担——这与仓库既有指南一致:
  "AgentDeck 无法核验工具性质,该判断由人类在 grant 时负责"。
- **绝不代按**。`release-box` 只作为文本出现;它自身的既有门(必须命中
  活跃委托)一字不动。
- 输出里的 grant 命令**含占位符时对应 control 必须 disabled**,与仓库既有
  control 纪律一致。

## 非目标(以及一条刻意的拒绝)

- 自动 grant、自动选前缀、自动放行——本命令永不写任何东西。
- 修改 `delegation grant` / `agent release-box` / human_gate 任何一方的行为。
- 多框批量决策(一次一道框;批量决策会诱导不看内容就放行)。
- **按模式警告危险命令(刻意拒绝)。** 仓库指南列了"绝不对 push / 安装 /
  网络变更前缀 grant",听起来该做个检测器。**不做**,理由是:一个只认
  `push|install|curl` 的部分检测器,会让"没有警告"被读成"安全"——而这
  正是本 session 反复在修的那类**显示了不成立的事实**。宁可一句话说清
  "AgentDeck 无法核验命令性质,没有警告不代表安全",也不要一个覆盖不全
  却看起来在保护你的检测器。

## 契约

**不新增第 46 个契约**,扩展既有 delegation 契约——`gate-preview` 与
`grant`/`list`/`revoke`/`boxes`/`release-box`/`boxes watch` 同族。新增
`DELEGATION_GATE_PREVIEW_RESPONSE_FIELDS` 与候选项字段元组、validator、
example,并同步 `docs/contracts/delegation-schema.md`。

## 测试要点

- 前缀梯子:多 token 命令给出正确的递减序列、上限 5 条、首尾必在、
  `unpinned_tail` 与 `is_widest` 正确;单 token 命令只出一条且 `is_widest`。
- MCP 框:无梯子,给出精确 `(server, tool)` grant 命令。
- 两条来源:缺省读宿主记录且**零 pane 读取**(用记录监视器钉住);
  `--agent` 走实时只读扫描。
- 无门可预览时(无宿主记录 / 无框)给出清晰空态,退出码明确。
- 只读:执行前后 `state.json` 与 events 逐字节相同。
- **绝不推荐**的回归钉:输出中不得出现"建议/recommended/safe"字样,
  且没有任何候选项带"被选中"标记。
