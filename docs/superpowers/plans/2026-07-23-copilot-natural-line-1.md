# Co-pilot Natural Line 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用一个确定性集成测试把 Desktop 已有的"自然结对循环"(fake Leader 文本拆计划 → approval → dispatch 进终端 → capture 结构化回复 → review 推进)端到端锁死不回退,再写一份人工授权的 live runbook,让同一条循环能用**真实 API Leader + 真实 coding-agent worker** 跑通。

**Architecture:** Line 1 不造新子系统 —— 复用现有 CLI(`leader plan/review` · `approval create-from-plan/approve/dispatch` · `capture-reply`)。确定性侧用 `FakeLeaderProvider`(config `provider=fake`)+ 内存 `FakeTmuxBackend`(`monkeypatch.setattr(cli, "TmuxBackend", ...)`)驱动整条循环并用测试锁定契约;真实侧(真实 API key 调用、真实 coding agent 派进 tmux)是**人工授权门**,只写 runbook + 采证文档,绝不在自动执行里跑。

**Tech Stack:** Python 3.12,conda 环境 `agentdeck`,标准库 CLI(`agentdeck.cli.main`),pytest,`capsys` 读 JSON,`monkeypatch` 打桩 provider/runtime。

**测试运行命令(全程用它):**
```bash
source "$(conda info --base)/etc/profile.d/conda.sh" && conda activate agentdeck
cd /Users/liuyue/Desktop/Github_repos/multi-agent-explore
python -m pytest tests/test_copilot_line1.py -q
```

**全局约束(每个 commit 都遵守):** 每次 commit 同步更新 `HISTORY.md` 并放进同一次提交;commit message **不带** `Co-Authored-By` trailer;**绝不 push**(由 human 自己推);真实/live 步骤(Task 3)是人工授权门,遇到就**停下报参数等授权**。

---

### Task 1: 确定性锁定测试 —— 结对循环端到端(fake Leader + fake tmux)

这是 Line 1 的确定性核心。它是一个**特征化/锁定测试**:所依赖的 CLI 行为**已经存在**,所以测试首次运行**预期 PASS**(把现状锁死);若某一跳 FAIL,说明那里有真实缺口,**最小修复**后再继续。

**Files:**
- Create: `tests/test_copilot_line1.py`
- Modify: `HISTORY.md`(在提交步骤追加条目)

- [ ] **Step 1: 写测试文件的 helper(项目准备 + 运行 + 绑定 running pane)**

创建 `tests/test_copilot_line1.py`,写入(照抄 `tests/test_dispatch_cli.py` 的既有打桩模式):

```python
from __future__ import annotations

import json
from pathlib import Path

from agentdeck import cli
from agentdeck.config import write_default_config
from agentdeck.state import StateStore


class FakeTmuxBackend:
    """内存 runtime 后端:记录发送,返回一段脚本化的 pane 捕获输出。"""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.output = ""

    def send_input(self, _config, pane_id: str, text: str) -> None:
        self.sent.append((pane_id, text))

    def capture_output(self, _config, pane_id: str, lines: int = 200) -> str:
        return self.output


def _prepare_fake_leader_project(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    config_path = root / ".agentdeck" / "config.toml"
    text = config_path.read_text(encoding="utf-8")
    text = text.replace('provider = "deepseek"', 'provider = "fake"', 1)
    text = text.replace('model = "deepseek-chat"', 'model = "fake-plan"', 1)
    config_path.write_text(text, encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def _bind_running(root: Path, agent_id: str, pane_id: str) -> None:
    store = StateStore(root)
    state = store.load()
    state["agents"][agent_id] = {
        "agent_id": agent_id,
        "pane_id": pane_id,
        "session_name": "agentdeck",
        "cwd": str(root),
        "status": "running",
    }
    store.save(state)


def _run(argv: list[str], capsys) -> dict:
    exit_code = cli.main(argv)
    out = capsys.readouterr().out
    assert exit_code == 0, out
    return json.loads(out)
```

- [ ] **Step 2: 写锁定测试 —— 一整轮结对循环**

在同文件追加:

```python
def test_copilot_line1_loop_locks_end_to_end(tmp_path, monkeypatch, capsys) -> None:
    root = _prepare_fake_leader_project(tmp_path, monkeypatch)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)

    # 1) Leader(fake)用文本拆出 per-agent 计划。
    plan = _run(["leader", "plan", "--task", "add a small util with tests"], capsys)
    plan_id = plan["plan_id"]
    assert plan_id.startswith("pln_")
    steps = plan["plan"]["steps"]
    assert [s["agent_id"] for s in steps] == ["planner", "coder", "reviewer"]
    assert all(s["requires_approval"] is True for s in steps)

    # 2) 人把计划变成 pending approvals(不自动派发)。
    created = _run(["approval", "create-from-plan", "--plan-id", plan_id], capsys)
    assert created["count"] == 3

    # 3) 人审批第一步(planner)对应的 approval。
    listing = _run(["approval", "list"], capsys)
    first = next(a for a in listing["approvals"] if a["agent_id"] == "planner")
    assert first["status"] == "pending"
    _run(["approval", "approve", "--approval-id", first["approval_id"]], capsys)

    # 4) dispatch 需要目标 agent 有 running pane(真实场景由 tmux spawn)。
    _bind_running(root, "planner", "%1")

    # 5) dispatch:worker prompt 进入它的终端。
    dispatched = _run(["approval", "dispatch", "--approval-id", first["approval_id"]], capsys)
    message_id = dispatched["message_id"]
    assert dispatched["agent_id"] == "planner"
    assert dispatched["trace_command"] == f"agentdeck trace --id {message_id}"
    assert len(fake.sent) == 1  # 恰好向 pane 发了一次任务

    # 6) worker 在 pane 里产出结构化回复;人捕获入账。
    fake.output = "status: completed\nsummary: util added with tests\n"
    reply = _run(["capture-reply", "--agent", "planner", "--message-id", message_id], capsys)
    assert reply["trace_command"].startswith("agentdeck trace --id ")

    # 7) leader review 推进循环,且**绝不自动派发**(pane 发送次数不变)。
    review = _run(["leader", "review", "--plan-id", plan_id], capsys)
    assert review["next_action"] in {
        "dispatch_approved",
        "wait_for_approval",
        "wait_for_reply",
        "summarize",
    }
    assert len(fake.sent) == 1  # review 是只读的,没有新派发
```

- [ ] **Step 3: 运行测试(锁定现状)**

Run: `python -m pytest tests/test_copilot_line1.py -q`
Expected: PASS(锁定既有结对循环行为)。

若某一跳 FAIL:那是真实缺口。读报错、定位到对应 CLI 函数(`leader_plan_command` / `approval_dispatch_command` / `_dispatch_approved_approval` / `capture_reply_command` / `leader_review_command`),做**最小**修复让该跳通过 —— 不要重构、不要加新子系统。修复与测试放进本 Task 的同一次 commit。

- [ ] **Step 4: 跑一次全量套件,确认没碰坏别的**

Run: `python -m pytest -q 2>&1 | tail -3`
Expected: 全绿(与 Task 前一致)。

- [ ] **Step 5: 更新 HISTORY 并提交**

在 `HISTORY.md` 顶部 `## 2026-07-23` 段落下追加一条:

```markdown
### Lock the co-pilot natural loop with a deterministic end-to-end test

- **Type**: test
- **Motivation**: Line 1 要把 Desktop 已有的自然结对循环端到端锁死,防止后续
  拧旋钮时回退。
- **What**: 新增 `tests/test_copilot_line1.py`,用 fake Leader + 内存
  FakeTmuxBackend 驱动 leader plan → approval create/approve/dispatch →
  capture-reply → leader review 一整轮,断言计划为 planner/coder/reviewer
  三步且全需审批、dispatch 恰好向 pane 发一次、review 只读不自动派发。
- **Impact**: 确定性锁定结对循环契约;真实 API/agent 验证走 Task 2/3 的人工
  授权 runbook。
- **Verification**: `pytest tests/test_copilot_line1.py -q` PASS;全量套件保持全绿。
```

Run:
```bash
git add tests/test_copilot_line1.py HISTORY.md
git commit -m "test: lock the co-pilot natural loop end-to-end (fake leader + fake tmux)"
```

---

### Task 2: Live Runbook —— 真实 API Leader + 真实 coding-agent worker

把 Task 1 锁定的同一条循环,写成一份**人工授权**的真实跑通手册。这是文档交付物,**不执行任何 live 命令**。

**Files:**
- Create: `docs/validation/2026-07-23-copilot-line1-runbook.md`
- Modify: `HISTORY.md`

- [ ] **Step 1: 写 runbook 文档**

创建 `docs/validation/2026-07-23-copilot-line1-runbook.md`,写入:

````markdown
# Co-pilot Line 1 Live Runbook(人工授权)

目标:在真实项目里用**真实 API Leader** + **两个真实 coding-agent worker**
(可见 tmux 终端)跑通一整轮结对,每步人工确认。

## 安全边界(必须遵守)
- 每个 live 步骤都要人类显式授权;agent 不得自行推断授权、不得自动 push。
- 不安装/认证任何东西(无 npx/npm/pip 自动下载);Leader API key 由人类预置。
- Worker 写文件后,Leader 汇总前必须重读它真写的文件。

## 前置
1. 激活环境并安装:
   ```bash
   conda activate agentdeck && python -m pip install -e .
   ```
2. 预置真实 Leader provider(二选一,由人类完成):
   - DeepSeek:`export DEEPSEEK_API_KEY=... DEEPSEEK_BASE_URL=... DEEPSEEK_MODEL=...`
   - openai-compatible:`export AGENTDECK_LEADER_API_KEY=... AGENTDECK_LEADER_BASE_URL=... AGENTDECK_LEADER_MODEL=...`
3. 诊断就绪(只读,不调用 provider):
   ```bash
   agentdeck doctor
   ```

## 一整轮结对(逐步确认)
1. 初始化项目并 spawn 两个 worker 的可见终端(planner/coder/reviewer 按需):
   ```bash
   agentdeck project init
   agentdeck agent spawn-ready --confirm
   agentdeck agent ready          # 确认 running
   ```
2. 真实 Leader 用文本拆计划:
   ```bash
   agentdeck leader plan --task "<你的真实小需求>" --provider deepseek
   ```
   人工检查返回的 plan(step 1..n、agent_id 只用已配置 worker、role 对应)。
3. 生成待确认项,逐条审批第一步:
   ```bash
   agentdeck approval create-from-plan --plan-id pln_xxx
   agentdeck approval list
   agentdeck approval approve --approval-id apv_xxx
   ```
4. 派发第一步给真实 worker 的终端,并在 tmux 里观察它自然执行:
   ```bash
   agentdeck approval dispatch --approval-id apv_xxx
   ```
5. worker 干完后,从 pane 回收结构化回复(约定 worker 输出以 `status:` 起始):
   ```bash
   agentdeck capture-reply --agent coder --message-id msg_xxx
   ```
6. Leader review 决定下一步,人工确认后再派下一个 worker:
   ```bash
   agentdeck leader review --plan-id pln_xxx
   ```
7. 全部回复入账后汇总(Leader 汇总前重读 worker 写的文件):
   ```bash
   agentdeck leader summary --plan-id pln_xxx
   ```

## 成功判据
一整轮(说 → 确认 → coder 执行 → 捕获 → 确认 → reviewer 执行 → 捕获 → 汇总)
端到端跑通,每步人工确认,全程 tmux 可见、账本/trace 可审计,**用的是真实 API
Leader,没有 fake、没有刚性协议**。
````

- [ ] **Step 2: 更新 HISTORY 并提交**

在 `HISTORY.md` 的 `## 2026-07-23` 段追加:

```markdown
### Write the co-pilot Line 1 live runbook

- **Type**: docs
- **Motivation**: 给 Line 1 的真实 API Leader + 真实 coding-agent worker 跑通
  提供人工授权手册。
- **What**: 新增 `docs/validation/2026-07-23-copilot-line1-runbook.md`,列出前置、
  逐步确认的一整轮结对命令序列和成功判据,并标注全部 live 步骤为人工授权门。
- **Impact**: 真实验证有据可依;不改代码、不执行 live。
- **Verification**: 文档自查;无占位符。
```

Run:
```bash
git add docs/validation/2026-07-23-copilot-line1-runbook.md HISTORY.md
git commit -m "docs: add co-pilot Line 1 live runbook (human-authorized)"
```

---

### Task 3: Live 验证(人工授权门 —— 不自动执行)

**这一步不由 agent 自动执行。** 它是 Line 1 的成功判据落地,必须由人类显式授权
并亲自(或明确授权后)运行 Task 2 runbook,过程中每个 live 命令都是授权门。

- [ ] **Step 1: 停下并请求授权**

向 human 报清将要真实执行的内容:哪个 provider、哪个 model、哪个真实项目、
哪几个 worker、要跑的真实小需求。**等 human 明确授权后**再进行。

- [ ] **Step 2: 按 runbook 跑一整轮,采集证据**

在授权下执行 `docs/validation/2026-07-23-copilot-line1-runbook.md` 的步骤,记录每步
的真实输出(plan、approval、dispatch、capture-reply、review、summary)与 tmux 截图/
pane 捕获。

- [ ] **Step 3: 写采证文档并提交**

创建 `docs/validation/2026-07-23-copilot-line1-acceptance.md`,粘贴真实证据链与
PASS/FAIL 结论;更新 `HISTORY.md`(Type: data);提交(**不 push**,由 human 推)。

若 live 过程中冒出真实缺口(如 capture 抓不到真实 agent 的输出格式、review 卡在
某状态),**停下**记录,回到确定性侧用 Task 1 的模式加锁定测试 + 最小修复,再重跑
live。

---

## Self-Review

**1. Spec coverage(对照 `2026-07-23-copilot-natural-line-1-design.md`):**
- 锁定档位(结对 / API Leader / 2+ 真实 worker / 每次派活前确认)→ Task 1(确定性锁定)+ Task 2 runbook(真实)。✅
- "真正要做/要验证的缺口"三条(接真实 API Leader、证明真实 worker 能跑、证明整链活着)→ Task 2/3 runbook + live 采证;确定性可锁的部分 → Task 1。✅
- "明确不做" → 计划未引入任何新模式/粒度/provider/SQLite/GUI。✅
- 成功标准 → Task 3 Step 2/3 的采证判据逐字对应 spec。✅
- 落地约定(新分支、不 push、HISTORY、无 Co-Authored-By、live 授权门)→ 已写入全局约束与每个 commit 步骤。✅

**2. Placeholder scan:** 无 TBD/TODO;所有测试代码、命令、HISTORY 条目均为完整内容。Task 3 的真实需求文本用 `<你的真实小需求>` 是**运行期人工输入**,非代码占位符。✅

**3. Type consistency:** `FakeTmuxBackend.send_input/capture_output` 签名与 `dispatch_command`/`capture_reply_command` 实际调用一致;`_run` 返回 JSON dict;approval 选择用 `a["agent_id"]`(dispatch 输出已确认含 `agent_id`);`leader plan` 输出键 `plan_id`/`plan`、dispatch 输出键 `message_id`/`agent_id`/`trace_command` 均与源码核对一致。✅
