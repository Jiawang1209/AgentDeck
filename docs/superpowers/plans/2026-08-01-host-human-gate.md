# Run-loop 宿主人类门诚实停止 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 run-loop 背景宿主在被等待的 worker 停在一道**未委托授权框**上时,
以新的 `stopped_reason=human_gate` 诚实停下并带出框证据,而不是把整个
`--max-waves` 预算烧在一个永远不会自己解决的等待上。

**Architecture:** 零新增能力面——`--release-boxes` 的扫描
(`_scan_release_delegated_boxes`)**已经**返回携带全部证据的 `skipped[]`,
宿主现在把它丢进了 `_`。本计划只是不再丢弃它,加上 awaiting 集限定与
连续两次 debounce,再把结果接进既有的 host 记录 / 日志 / 审计 / status /
契约五个面。单 wave 引擎 `_run_loop_single_wave` **一字不改**。

**Tech Stack:** Python 3.12 标准库;`src/agentdeck/run_loop_host.py`(闭合枚举
单一来源)、`src/agentdeck/cli.py`(扫描 helper、awaiting helper、serve 循环、
status payload)、`src/agentdeck/contracts.py`(字段元组 + validator)、pytest。

**Spec:** `docs/superpowers/specs/2026-08-01-host-human-gate-design.md`

---

## 全局约束(每个任务都适用)

- 所有命令走 `conda run --no-capture-output -n agentdeck …`。
- **不得 `git push`**;**不得**在 commit message 里加任何 `Co-Authored-By`
  trailer;**不得** `git add` 任何 `.omc/` 路径。
- 每个任务的 commit 必须同时更新 `HISTORY.md`(新条目加在**顶部**,
  日期 `## 2026-08-01` 那一节下面)。
- 不带 `--release-boxes` 的宿主**必须**逐字节保持既有行为(零 pane 读取)。
- 检测**绝不**发送任何 tmux 输入;放行路径一字不改。

---

### Task 1: 抽出 awaiting 集单一来源 helper

把内联在 `_ingest_plan_reply_files` 里的 awaiting 计算原样抽出,供人类门
判定复用,杜绝出现第二套 awaiting 定义。**纯重构,行为零变化。**

**Files:**
- Modify: `src/agentdeck/cli.py`(`_ingest_plan_reply_files`,约 20536 行)
- Test: `tests/test_run_loop_host.py`

- [ ] **Step 1: 写失败测试**

加到 `tests/test_run_loop_host.py`:

```python
def test_plan_awaiting_lists_dispatched_unreplied_approvals_for_this_plan_only():
    from agentdeck.cli import _plan_awaiting

    state = {
        "approvals": [
            {"plan_id": "pln_a", "status": "dispatched", "message_id": "msg_1", "agent_id": "coder"},
            {"plan_id": "pln_a", "status": "dispatched", "message_id": "msg_2", "agent_id": "reviewer"},
            {"plan_id": "pln_a", "status": "approved", "message_id": "msg_3", "agent_id": "planner"},
            {"plan_id": "pln_b", "status": "dispatched", "message_id": "msg_4", "agent_id": "coder"},
        ],
        "replies": [{"message_id": "msg_2"}],
    }

    assert _plan_awaiting(state, "pln_a") == [("msg_1", "coder")]
```

- [ ] **Step 2: 运行,确认失败**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_run_loop_host.py::test_plan_awaiting_lists_dispatched_unreplied_approvals_for_this_plan_only -q
```

预期:FAIL,`ImportError: cannot import name '_plan_awaiting'`。

- [ ] **Step 3: 抽出 helper**

在 `_ingest_plan_reply_files` **之前**插入:

```python
def _plan_awaiting(
    state: dict[str, object], plan_id: str
) -> list[tuple[str, str]]:
    """(message_id, agent_id) for this plan's dispatched-but-unreplied approvals.

    单一来源:文件通道摄入与宿主人类门判定共用同一份 awaiting 定义,
    绝不允许出现第二套。
    """
    replied_messages = {
        str(reply.get("message_id"))
        for reply in state.get("replies", [])
        if isinstance(reply, dict)
    }
    return [
        (str(approval.get("message_id")), str(approval.get("agent_id")))
        for approval in state.get("approvals", [])
        if isinstance(approval, dict)
        and approval.get("plan_id") == plan_id
        and approval.get("status") == "dispatched"
        and approval.get("message_id")
        and str(approval.get("message_id")) not in replied_messages
    ]
```

把 `_ingest_plan_reply_files` 里从 `replied_messages = {` 到 `awaiting = [ … ]`
整段替换为:

```python
    state_now = store.load()
    awaiting = _plan_awaiting(state_now, plan_id)
```

(注意保留原有的 `state_now = store.load()` 语义——它原本就在该段之前。)

- [ ] **Step 4: 运行,确认通过 + 零行为变化**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_run_loop_host.py tests/test_run_loop_cli.py tests/test_run_loop_all.py -q
```

预期:全绿。既有 run-loop 摄入测试是这次重构的行为钉。

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/cli.py tests/test_run_loop_host.py HISTORY.md
git commit -m "refactor: extract the plan awaiting set as a single source"
```

HISTORY 条目:Type refactor;Motivation=人类门判定需要与文件通道摄入共用
同一份 awaiting 定义;What=抽出 `_plan_awaiting`;Impact=纯重构零行为变化;
Verification=既有 run-loop 摄入测试全绿。

---

### Task 2: 扫描的 skipped 项补 `waiting_hint`

人类门证据需要屏上原文提示。`_scan_release_delegated_boxes` 已经算出
`waiting_hint`,但只放进了 released 项,skipped 项没带。**纯附加字段。**

**Files:**
- Modify: `src/agentdeck/cli.py`(`_scan_release_delegated_boxes`,约 10262 行)
- Test: `tests/test_delegation_cli.py`

- [ ] **Step 1: 写失败测试**

加到 `tests/test_delegation_cli.py`(紧跟既有的 `skipped[0]["reason"]` 断言
那个测试之后,复用它的 fixture 写法):

```python
def test_boxes_watch_skipped_box_carries_waiting_hint(tmp_path, monkeypatch, capsys):
    """人类门检测要靠 skipped 项的屏上证据;waiting_hint 必须带出来。"""
    payload = _run_boxes_watch_with_unmatched_box(tmp_path, monkeypatch, capsys)

    skipped = payload["skipped"][0]
    assert skipped["reason"] == "no active delegation"
    assert skipped["waiting_hint"]
    assert isinstance(skipped["waiting_hint"], str)
```

若该文件中没有 `_run_boxes_watch_with_unmatched_box` 这样的 helper,
就照第 335 行那个测试的结构内联复制一份 fixture(pane 输出里含
`› 1. Yes, proceed (y)` 与一条不被任何委托覆盖的 `$ ` 命令),
不要为了复用去改既有测试。

- [ ] **Step 2: 运行,确认失败**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_delegation_cli.py -k waiting_hint -q
```

预期:FAIL,`KeyError: 'waiting_hint'`。

- [ ] **Step 3: 加字段**

`_scan_release_delegated_boxes` 里 `if match is None:` 分支的 skipped dict
加一项(放在 `"reason"` 之前):

```python
            skipped.append(
                {
                    "agent_id": agent_id,
                    "command": command,
                    **_box_fields(command, mcp_box),
                    "waiting_hint": waiting_hint,
                    "reason": "no active delegation",
                    "iteration": iteration,
                }
            )
```

**只改这一处**;pane capture 失败那条 skipped 保持不变(它没有 hint,
且按 spec 不算人类门)。

- [ ] **Step 4: 运行,确认通过**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_delegation_cli.py -q
```

预期:全绿(既有断言只点名 `reason`,附加字段向后兼容)。

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/cli.py tests/test_delegation_cli.py HISTORY.md
git commit -m "feat: carry the waiting hint on undelegated box skips"
```

---

### Task 3: `human_gate` 进闭合枚举

**Files:**
- Modify: `src/agentdeck/run_loop_host.py:21-28`
- Test: `tests/test_run_loop_host.py`

- [ ] **Step 1: 写失败测试**

```python
def test_human_gate_is_a_closed_stopped_reason():
    from agentdeck.run_loop_host import RUN_LOOP_HOST_STOPPED_REASONS

    assert "human_gate" in RUN_LOOP_HOST_STOPPED_REASONS
    assert len(RUN_LOOP_HOST_STOPPED_REASONS) == 6
    assert len(set(RUN_LOOP_HOST_STOPPED_REASONS)) == 6
```

- [ ] **Step 2: 运行,确认失败**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_run_loop_host.py -k closed_stopped_reason -q
```

预期:FAIL,`assert 'human_gate' in (...)`。

- [ ] **Step 3: 加枚举值**

```python
RUN_LOOP_HOST_STOPPED_REASONS = (
    "gate_reached",  # wave gate 不再是 waiting_for_reply
    "budget_exhausted",  # 达 --max-waves 上限而仍在等回复
    "policy_revoked",  # approval_mode 不再是 autonomous(远程刹车)
    "signalled",  # run-loop-host stop 的 SIGTERM 在本 wave 结束后被接受
    "engine_error",  # wave 引擎抛异常(只记异常类型)
    "human_gate",  # 被等待的 worker 停在未委托授权框上(等待永不会自解)
)
```

- [ ] **Step 4: 运行,确认通过 + 契约面同步绿**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_run_loop_host.py tests/test_run_loop_host_cli.py tests/test_contracts.py -q
```

预期:全绿。若 `test_contracts.py` 有硬编码 stopped_reasons 数量/列表的
断言,**同步更新为 6 值**(这是契约扩张,不是测试放宽)。

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/run_loop_host.py tests/ HISTORY.md
git commit -m "feat: add human_gate to the host stopped reason enum"
```

---

### Task 4: 人类门判定纯函数

判定逻辑与 IO 分离,便于把矩阵测透。

**Files:**
- Modify: `src/agentdeck/run_loop_host.py`(追加纯函数,**不 import cli**)
- Test: `tests/test_run_loop_host.py`

- [ ] **Step 1: 写失败测试**

```python
def test_human_gate_candidate_matches_undelegated_box_on_awaited_agent():
    from agentdeck.run_loop_host import human_gate_candidate

    skipped = [{
        "agent_id": "planner", "command": "playwright_cli.sh open file:///x",
        "box_kind": "command", "mcp_server": None, "mcp_tool": None,
        "waiting_hint": "› 1. Yes, proceed (y)", "reason": "no active delegation",
    }]
    assert human_gate_candidate(skipped, {"planner"}) == {
        "agent_id": "planner",
        "box_kind": "command",
        "command": "playwright_cli.sh open file:///x",
        "mcp_server": None,
        "mcp_tool": None,
        "waiting_hint": "› 1. Yes, proceed (y)",
    }


def test_human_gate_candidate_ignores_agents_outside_the_awaiting_set():
    from agentdeck.run_loop_host import human_gate_candidate

    skipped = [{"agent_id": "idle_bot", "command": "x", "box_kind": "command",
                "mcp_server": None, "mcp_tool": None, "waiting_hint": "h",
                "reason": "no active delegation"}]
    assert human_gate_candidate(skipped, {"planner"}) is None


def test_human_gate_candidate_ignores_pane_capture_failures():
    from agentdeck.run_loop_host import human_gate_candidate

    skipped = [{"agent_id": "planner", "command": None,
                "reason": "pane capture failed", "iteration": 3}]
    assert human_gate_candidate(skipped, {"planner"}) is None


def test_human_gate_candidate_returns_none_for_empty_scan():
    from agentdeck.run_loop_host import human_gate_candidate

    assert human_gate_candidate([], {"planner"}) is None


def test_same_human_gate_compares_by_agent_and_box_identity():
    from agentdeck.run_loop_host import same_human_gate

    a = {"agent_id": "planner", "box_kind": "command", "command": "x",
         "mcp_server": None, "mcp_tool": None, "waiting_hint": "h1"}
    b = {"agent_id": "planner", "box_kind": "command", "command": "x",
         "mcp_server": None, "mcp_tool": None, "waiting_hint": "h2"}
    c = {"agent_id": "planner", "box_kind": "command", "command": "y",
         "mcp_server": None, "mcp_tool": None, "waiting_hint": "h1"}

    assert same_human_gate(a, b) is True   # hint 变化不影响身份
    assert same_human_gate(a, c) is False  # 命令不同 = 不同的框
    assert same_human_gate(None, a) is False
    assert same_human_gate(a, None) is False
```

- [ ] **Step 2: 运行,确认失败**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_run_loop_host.py -k human_gate -q
```

预期:FAIL,`ImportError: cannot import name 'human_gate_candidate'`。

- [ ] **Step 3: 写纯函数**

追加到 `src/agentdeck/run_loop_host.py`:

```python
# 人类门证据的身份键:同一道框判定用,waiting_hint 是展示文本不参与身份。
_HUMAN_GATE_IDENTITY = ("agent_id", "box_kind", "command", "mcp_server", "mcp_tool")
_HUMAN_GATE_FIELDS = (*_HUMAN_GATE_IDENTITY, "waiting_hint")


def human_gate_candidate(
    skipped: list[dict[str, Any]], awaiting_agents: set[str]
) -> dict[str, Any] | None:
    """从一次框扫描的 skipped 项里挑出人类门候选。

    只认「未委托」且「落在本 plan awaiting 集内」的框。pane capture 失败
    是 runtime 抖动而非人类门。解析不出任何候选一律 None——fail-open 到
    既有轮询行为,宁可多转也绝不误停一个正常的走开段。
    """
    for item in skipped:
        if not isinstance(item, dict):
            continue
        if item.get("reason") != "no active delegation":
            continue
        agent_id = item.get("agent_id")
        if agent_id not in awaiting_agents:
            continue
        return {field: item.get(field) for field in _HUMAN_GATE_FIELDS}
    return None


def same_human_gate(
    left: dict[str, Any] | None, right: dict[str, Any] | None
) -> bool:
    """两次扫描看到的是否是同一道框(debounce 用)。"""
    if not left or not right:
        return False
    return all(left.get(key) == right.get(key) for key in _HUMAN_GATE_IDENTITY)
```

- [ ] **Step 4: 运行,确认通过**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_run_loop_host.py -q
```

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/run_loop_host.py tests/test_run_loop_host.py HISTORY.md
git commit -m "feat: add pure human-gate candidate derivation"
```

---

### Task 5: serve 循环接线(debounce + 停止)

**Files:**
- Modify: `src/agentdeck/cli.py`(`run_loop_host_serve_command`,约 21030 行;
  `_run_loop_host_finish`,约 21000 行前后)
- Test: `tests/test_run_loop_host_cli.py`

- [ ] **Step 1: 写失败测试**

```python
def test_serve_stops_on_the_second_consecutive_sighting_of_the_same_human_gate(
    tmp_path, monkeypatch
):
    """第一次命中只记候选;第二次同一道框才停。"""
    root = _autonomous_project(tmp_path)          # 既有 fixture
    plan_id = _dispatched_plan(root, agent_id="planner")  # 既有 fixture

    box = {"agent_id": "planner", "command": "playwright open x",
           "box_kind": "command", "mcp_server": None, "mcp_tool": None,
           "waiting_hint": "› 1. Yes, proceed (y)", "reason": "no active delegation"}
    monkeypatch.setattr(cli, "_scan_release_delegated_boxes",
                        lambda *a, **k: ([], [box]))
    monkeypatch.setattr(cli, "_run_loop_single_wave",
                        lambda *a, **k: {"ok": True, "mode": "run_loop",
                                         "stopped_reason": "waiting_for_reply",
                                         "next_command": "x"})
    monkeypatch.setattr(cli, "TmuxBackend", lambda: object())

    args = _serve_args(root, plan_id, max_waves=10, interval=0, release_boxes=True)
    assert cli.run_loop_host_serve_command(args) == 0

    record = read_host_record(root)
    assert record["stopped_reason"] == "human_gate"
    assert record["human_gate"]["agent_id"] == "planner"
    assert record["human_gate"]["waiting_hint"] == "› 1. Yes, proceed (y)"
    # 段首扫描(wave 0)+ wave1 后扫描 = 两次同一道框 → 停在 wave 1
    assert record["wave_count"] <= 2


def test_serve_does_not_stop_when_the_box_changes_between_scans(tmp_path, monkeypatch):
    """两次命中的是不同的框 → 不判定,重新计数。"""
    ...  # 同上,但扫描交替返回 command="a" / command="b";
         # 断言 max_waves 跑满、stopped_reason == "budget_exhausted"


def test_serve_without_release_boxes_never_detects_a_human_gate(tmp_path, monkeypatch):
    """不开 --release-boxes:零 pane 读取,行为逐字节不变。"""
    scanned = []
    monkeypatch.setattr(cli, "_scan_release_delegated_boxes",
                        lambda *a, **k: scanned.append(1) or ([], []))
    ...  # release_boxes=False;断言 scanned == [] 且
         # stopped_reason == "budget_exhausted"
```

三个测试的 `...` 部分按第一个测试的结构补完;若 `_autonomous_project` /
`_dispatched_plan` / `_serve_args` 在既有文件中叫别的名字,用既有的。

- [ ] **Step 2: 运行,确认失败**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_run_loop_host_cli.py -k human_gate -q
```

- [ ] **Step 3: 接线**

3a. `_run_loop_host_finish` 增加可选证据参数:

```python
def _run_loop_host_finish(
    root: Path,
    store: StateStore,
    *,
    plan_id: str,
    wave_count: int,
    last_gate: str | None,
    stopped_reason: str,
    human_gate: dict[str, object] | None = None,
) -> None:
    record = read_host_record(root) or {}
    record.update({
        "pid": None,
        "plan_id": plan_id,
        "wave_count": wave_count,
        "last_gate": last_gate,
        "stopped_reason": stopped_reason,
        "stopped_at": utc_now(),
        "human_gate": human_gate,
    })
    write_host_record(root, record)
    append_host_log(root, {
        "plan_id": plan_id,
        "event": "host_stopped",
        "wave": wave_count,
        "stopped_reason": stopped_reason,
        **({"human_gate": human_gate} if human_gate else {}),
        "at": utc_now(),
    })
    store.append_event(EventRecord.create("run_loop_host_stopped", {
        "plan_id": plan_id,
        "wave_count": wave_count,
        "stopped_reason": stopped_reason,
        **({"human_gate": human_gate} if human_gate else {}),
        "source": "host",
    }))
```

3b. serve 里 import 纯函数并加状态:

```python
from .run_loop_host import human_gate_candidate, same_human_gate  # 顶部 import 区
```

在 `wave_count = 0` 附近加:

```python
    pending_human_gate: dict[str, object] | None = None
    confirmed_human_gate: dict[str, object] | None = None
```

3c. **段首扫描**(wave 0)之后立即判定候选:

```python
    if backend is not None:
        released, skipped = _scan_release_delegated_boxes(
            config, store, backend, agent_ids, 0, source="run_loop_host"
        )
        for item in released:
            append_host_log(root, {...})  # 保持既有
        pending_human_gate = human_gate_candidate(
            skipped, {agent for _msg, agent in _plan_awaiting(store.load(), plan_id)}
        )
```

3d. **wave 间隙扫描**之后同样判定,并在连续两次同一道框时停止:

```python
        if backend is not None:
            released, skipped = _scan_release_delegated_boxes(
                config, store, backend, agent_ids, wave_number, source="run_loop_host"
            )
            for item in released:
                append_host_log(root, {...})  # 保持既有
            candidate = human_gate_candidate(
                skipped, {agent for _msg, agent in _plan_awaiting(store.load(), plan_id)}
            )
            if same_human_gate(pending_human_gate, candidate):
                confirmed_human_gate = candidate
                append_host_log(root, {
                    "plan_id": plan_id, "event": "human_gate", "wave": wave_number,
                    **candidate, "at": utc_now(),
                })
                stopped_reason = "human_gate"
                break
            pending_human_gate = candidate
```

3e. 收尾把证据传下去:

```python
    _run_loop_host_finish(
        root, store, plan_id=plan_id, wave_count=wave_count,
        last_gate=last_gate, stopped_reason=stopped_reason,
        human_gate=confirmed_human_gate,
    )
```

**注意**:`merge_on_complete` 那段的条件是
`stopped_reason == "gate_reached" and last_gate == "complete"`,`human_gate`
不满足,因此人类门停止**绝不触发自动合并**——无需改动,但要在测试里钉住。

- [ ] **Step 4: 运行,确认通过**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_run_loop_host_cli.py tests/test_run_loop_host.py -q
```

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/cli.py tests/test_run_loop_host_cli.py HISTORY.md
git commit -m "feat: stop the run-loop host on a confirmed human gate"
```

---

### Task 6: status 上报 + 契约同步

**Files:**
- Modify: `src/agentdeck/contracts.py`(`RUN_LOOP_HOST_STATUS_RESPONSE_FIELDS`
  约 7520 行,`validate_run_loop_host_status_contract` 约 7578 行)
- Modify: `src/agentdeck/cli.py`(`_run_loop_host_status_payload`,约 20770 行)
- Modify: `docs/contracts/run-loop-host-schema.md`
- Test: `tests/test_run_loop_host_cli.py`, `tests/test_contracts.py`

- [ ] **Step 1: 写失败测试**

```python
def test_status_surfaces_the_human_gate_evidence(tmp_path, monkeypatch, capsys):
    root = _project_with_host_record(tmp_path, {
        "plan_id": "pln_x", "pid": None, "wave_count": 2,
        "stopped_reason": "human_gate",
        "human_gate": {"agent_id": "planner", "box_kind": "command",
                       "command": "playwright open x", "mcp_server": None,
                       "mcp_tool": None, "waiting_hint": "› 1. Yes, proceed (y)"},
    })
    assert cli.run_loop_host_status_command(_args(root)) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["stopped_reason"] == "human_gate"
    assert payload["human_gate"]["agent_id"] == "planner"
    assert payload["human_gate"]["command"] == "playwright open x"


def test_status_human_gate_is_null_without_one(tmp_path, monkeypatch, capsys):
    root = _project_with_host_record(tmp_path, {
        "plan_id": "pln_x", "pid": None, "wave_count": 2,
        "stopped_reason": "budget_exhausted",
    })
    assert cli.run_loop_host_status_command(_args(root)) == 0
    assert json.loads(capsys.readouterr().out)["human_gate"] is None
```

- [ ] **Step 2: 运行,确认失败**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_run_loop_host_cli.py -k status_human_gate -q
conda run --no-capture-output -n agentdeck pytest tests/test_run_loop_host_cli.py -k "surfaces_the_human_gate" -q
```

- [ ] **Step 3: 加字段**

契约元组末尾追加 `"human_gate"`:

```python
RUN_LOOP_HOST_STATUS_RESPONSE_FIELDS = (
    ...,
    "stop_command",
    "human_gate",
)
```

`_run_loop_host_status_payload` 返回值加:

```python
        "human_gate": record.get("human_gate"),
```

`validate_run_loop_host_status_contract` 在既有 stopped_reason 校验之后追加:

```python
    gate = payload.get("human_gate")
    if gate is not None:
        if not isinstance(gate, dict):
            errors.append("run_loop_host_status.human_gate must be null or an object")
        else:
            errors.extend(
                f"missing run_loop_host_status.human_gate field: {field}"
                for field in ("agent_id", "box_kind", "command", "mcp_server",
                              "mcp_tool", "waiting_hint")
                if field not in gate
            )
    if payload.get("stopped_reason") == "human_gate" and gate is None:
        errors.append(
            "run_loop_host_status.human_gate is required when stopped_reason is human_gate"
        )
```

同时更新 `run_loop_host` contract 的 example fixture,使其字段齐全。

- [ ] **Step 4: 运行,确认通过**

```bash
conda run --no-capture-output -n agentdeck pytest tests/test_run_loop_host_cli.py tests/test_contracts.py tests/test_agent_cli.py -q
```

- [ ] **Step 5: 更新契约文档**

`docs/contracts/run-loop-host-schema.md`:
- stopped_reason 表加 `human_gate` 一行(含"被等待的 worker 停在未委托
  授权框上;等待永远不会自解")。
- status 字段表加 `human_gate`(说明:仅 `stopped_reason=human_gate` 时非
  null;字段 agent_id/box_kind/command/mcp_server/mcp_tool/waiting_hint;
  **只是证据,不是授权**——AgentDeck 永不代按)。
- 安全边界节写明:检测只在 `--release-boxes` 开启时生效(零新增 pane
  读取面),连续两次同一道框才判定,检测失败一律 fail-open 到轮询。

- [ ] **Step 6: Commit**

```bash
git add src/agentdeck/contracts.py src/agentdeck/cli.py \
        docs/contracts/run-loop-host-schema.md tests/ HISTORY.md
git commit -m "feat: surface human-gate evidence in host status and contract"
```

---

### Task 7: 全量回归 + 文档同步

**Files:**
- Modify: `README.md`, `CLAUDE.md`, `docs/handoff/current-development-state.md`

- [ ] **Step 1: 全量测试**

```bash
conda run --no-capture-output -n agentdeck pytest -q
conda run --no-capture-output -n agentdeck python -m compileall src tests -q
```

预期:0 failed(基线 5005 passed / 3 skipped,本计划新增测试后应更多)。

- [ ] **Step 2: README**

在 run-loop background host 那一条的末尾补一句:开启 `--release-boxes`
时,若被等待的 worker 停在一道未委托的授权框上,宿主以
`stopped_reason=human_gate` 停下并带出框证据,而不是把 wave 预算耗尽;
AgentDeck 永不代按该框。

- [ ] **Step 3: CLAUDE.md**

在 run-loop-host 那条规则里补:`human_gate` 是第六个 stopped_reason;
检测复用 `--release-boxes` 已有的只读扫描(零新增 pane 读取面),只看本
plan awaiting 集内的 agent,同一道框连续两次才判定,证据进 host.json /
host.log / `run_loop_host_stopped` 审计与 status;它只让宿主**更早停下**,
不放宽任何授权,绝不代按。

- [ ] **Step 4: handoff**

`docs/handoff/current-development-state.md` 顶部记录本切片落地:spec/plan
路径、846-wave 实测动机、零新增能力面的做法、以及 live 验证待办
(下一轮 round 应能在 Playwright 框场景下看到 `human_gate` 而非烧满预算)。

- [ ] **Step 5: Commit**

```bash
git add README.md CLAUDE.md docs/handoff/current-development-state.md HISTORY.md
git commit -m "docs: document the host human-gate stop"
```

---

## 完成后

**不要 push。**向 user 报告:落地的 commit 列表、全量测试结果、以及
live 验证仍需 user 在 Round 14 的 Playwright 框上按回车(或重启一台
带 `--release-boxes` 的宿主来观察 `human_gate` 是否如期触发)。
