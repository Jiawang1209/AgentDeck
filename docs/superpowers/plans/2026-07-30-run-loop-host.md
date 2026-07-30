# Run-Loop Background Host Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `agentdeck run-loop-host start|status|stop` runs the existing
single-wave run-loop engine in a detached background process that survives
client disconnect, under one start-time gate and a mandatory wave budget.

**Architecture:** A new near-pure module `src/agentdeck/run_loop_host.py` owns
the single-instance record (`.agentdeck/run-loop-host/host.json`), the
append-only JSONL log, pid liveness, and the closed `stopped_reason` enum.
Four CLI commands live in `cli.py`: `start` (four gates + injected spawn),
`serve` (the child loop — calls the **unchanged** `_run_loop_single_wave`,
re-reads config each wave for the policy brake, honors SIGTERM after the
current wave), `status` (read-only), `stop` (bounded SIGTERM, never SIGKILL).
The M2 Mission daemon under `src/agentdeck/daemon/` is not touched.

**Tech Stack:** Python 3.12 stdlib (`subprocess`, `signal`, `os.kill`),
pytest, conda env `agentdeck`.

**Spec:** `docs/superpowers/specs/2026-07-30-run-loop-host-design.md`

**Discipline:** All commands via `conda run -n agentdeck …`. Strict TDD.
No `git push`, no co-author trailer, nothing under `.omc/` staged. Each task
is one commit and carries its own `HISTORY.md` top entry under `## 2026-07-30`
(Type/Motivation/What/Impact/Verification, matching neighbours).

**Reused, unchanged (do not edit these):**
- `_run_loop_single_wave(config, store, plan_id) -> dict | None` (cli.py:20295)
- `_scan_release_delegated_boxes(config, store, backend, agent_ids, iteration, source=…)`
- `_verdict_merge_blocker(store, plan_id)`, `_merge_plan_worktrees(config, store, plan_id)`

---

## Task 1: host record module

**Files:**
- Create: `src/agentdeck/run_loop_host.py`
- Create: `tests/test_run_loop_host.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write the failing tests** — create `tests/test_run_loop_host.py`:

```python
from __future__ import annotations

import json
import os
from pathlib import Path

from agentdeck.run_loop_host import (
    RUN_LOOP_HOST_STOPPED_REASONS,
    append_host_log,
    host_dir,
    host_liveness,
    host_log_path,
    host_record_path,
    pid_alive,
    read_host_record,
    write_host_record,
)


def test_paths_are_under_project_agentdeck(tmp_path: Path) -> None:
    assert host_dir(tmp_path) == tmp_path / ".agentdeck" / "run-loop-host"
    assert host_record_path(tmp_path).name == "host.json"
    assert host_log_path(tmp_path).name == "host.log"


def test_stopped_reasons_are_closed_enum() -> None:
    assert RUN_LOOP_HOST_STOPPED_REASONS == (
        "gate_reached",
        "budget_exhausted",
        "policy_revoked",
        "signalled",
        "engine_error",
    )


def test_record_round_trip_and_missing_is_none(tmp_path: Path) -> None:
    assert read_host_record(tmp_path) is None
    record = {"pid": 4242, "plan_id": "pln_x", "wave_count": 0}
    write_host_record(tmp_path, record)
    assert read_host_record(tmp_path) == record
    # 目录自动创建,内容是可读 JSON
    assert json.loads(host_record_path(tmp_path).read_text(encoding="utf-8")) == record


def test_corrupt_record_reads_as_none(tmp_path: Path) -> None:
    host_dir(tmp_path).mkdir(parents=True)
    host_record_path(tmp_path).write_text("{not json", encoding="utf-8")
    assert read_host_record(tmp_path) is None
    host_record_path(tmp_path).write_text('["list"]', encoding="utf-8")
    assert read_host_record(tmp_path) is None


def test_pid_alive_probe() -> None:
    assert pid_alive(os.getpid()) is True
    assert pid_alive(0) is False
    assert pid_alive(-1) is False
    # 极大 pid 几乎不可能存在
    assert pid_alive(4_000_000) is False


def test_host_liveness_three_states(tmp_path: Path) -> None:
    # 无记录
    assert host_liveness(tmp_path) == (None, False, False)
    # 活 pid
    write_host_record(tmp_path, {"pid": os.getpid(), "plan_id": "pln_x"})
    record, running, stale = host_liveness(tmp_path)
    assert record is not None and running is True and stale is False
    # 死 pid → stale
    write_host_record(tmp_path, {"pid": 4_000_000, "plan_id": "pln_x"})
    _record, running, stale = host_liveness(tmp_path)
    assert running is False and stale is True
    # 干净停止(pid 已清)不是 stale
    write_host_record(tmp_path, {"pid": None, "plan_id": "pln_x", "stopped_reason": "gate_reached"})
    _record, running, stale = host_liveness(tmp_path)
    assert running is False and stale is False


def test_append_host_log_is_jsonl_and_appends(tmp_path: Path) -> None:
    append_host_log(tmp_path, {"plan_id": "pln_x", "wave": 1})
    append_host_log(tmp_path, {"plan_id": "pln_x", "wave": 2})
    lines = host_log_path(tmp_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["wave"] for line in lines] == [1, 2]
```

- [ ] **Step 2: Run to verify failure**

Run: `conda run -n agentdeck pytest tests/test_run_loop_host.py -q`
Expected: FAIL at import (`ModuleNotFoundError: No module named 'agentdeck.run_loop_host'`)

- [ ] **Step 3: Implement** — create `src/agentdeck/run_loop_host.py`:

```python
"""Single-instance record, JSONL log and pid probe for the run-loop host.

背景宿主让已验证的单 wave 引擎在脱离客户端的进程里继续跑(round 12
八次手动重启 follow 段的痛点)。本模块只管进程记录/日志/存活探测这一层
(user 拍板:只复用 pidfile+日志+单例互斥,不引入 socket/lease),不含调度
逻辑、不 import cli,也绝不触碰 M2 Mission daemon。
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

HOST_DIR_NAME = "run-loop-host"
HOST_RECORD_NAME = "host.json"
HOST_LOG_NAME = "host.log"

# 闭合枚举:每个值在 status 里都对应一条显式后续命令。
RUN_LOOP_HOST_STOPPED_REASONS = (
    "gate_reached",  # wave gate 不再是 waiting_for_reply
    "budget_exhausted",  # 达 --max-waves 上限而仍在等回复
    "policy_revoked",  # approval_mode 不再是 autonomous(远程刹车)
    "signalled",  # run-loop-host stop 的 SIGTERM 在本 wave 结束后被接受
    "engine_error",  # wave 引擎抛异常(只记异常类型)
)


def host_dir(root: Path) -> Path:
    return Path(root) / ".agentdeck" / HOST_DIR_NAME


def host_record_path(root: Path) -> Path:
    return host_dir(root) / HOST_RECORD_NAME


def host_log_path(root: Path) -> Path:
    return host_dir(root) / HOST_LOG_NAME


def read_host_record(root: Path) -> dict[str, Any] | None:
    """读单例记录;缺失、不可读或损坏一律 None(调用方按"无宿主"处理)。"""
    try:
        text = host_record_path(root).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        record = json.loads(text)
    except json.JSONDecodeError:
        return None
    return record if isinstance(record, dict) else None


def write_host_record(root: Path, record: dict[str, Any]) -> None:
    """原子替换写入(读者永不看到半个 JSON)。"""
    directory = host_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = host_record_path(root)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def pid_alive(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 存在但不属于本用户
    except OSError:
        return False
    return True


def host_liveness(
    root: Path, probe: Callable[[int], bool] = pid_alive
) -> tuple[dict[str, Any] | None, bool, bool]:
    """返回 (record, running, stale)。

    running=pid 存活;stale=记录声称有 pid 但进程已死(需 stop 清理)。
    pid 已被清空的干净停止记录既不 running 也不 stale。
    `probe` 可注入,使 CLI 层与测试共用同一份判定逻辑(单一来源)。
    """
    record = read_host_record(root)
    if record is None:
        return None, False, False
    pid = record.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return record, False, False
    alive = probe(pid)
    return record, alive, not alive


def append_host_log(root: Path, entry: dict[str, Any]) -> None:
    """追加一行 JSONL;跨宿主共享同一文件,历史永不被截断或重写。"""
    directory = host_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    with host_log_path(root).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
```

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n agentdeck pytest tests/test_run_loop_host.py -q`
Expected: 7 passed

- [ ] **Step 5: HISTORY + commit**

`HISTORY.md` top entry under `## 2026-07-30`, Type: feat, title "Add
run-loop host record module (pidfile, JSONL log, liveness)". Motivation cites
round 12's eight manual `--follow` relaunches and the spec path. Then:

```bash
git add src/agentdeck/run_loop_host.py tests/test_run_loop_host.py HISTORY.md
git commit -m "feat: add run-loop host record module"
```

---

## Task 2: run-loop-host contract

**Files:**
- Modify: `src/agentdeck/contracts.py`
- Create: `docs/contracts/run-loop-host-schema.md`
- Modify: `HISTORY.md`
- Test: `tests/test_run_loop_host.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_run_loop_host.py`):

```python
def test_contract_field_tuples_and_examples() -> None:
    from agentdeck.contracts import (
        RUN_LOOP_HOST_START_RESPONSE_FIELDS,
        RUN_LOOP_HOST_STATUS_RESPONSE_FIELDS,
        RUN_LOOP_HOST_STOP_RESPONSE_FIELDS,
        run_loop_host_start_example,
        run_loop_host_status_example,
        run_loop_host_stop_example,
        validate_run_loop_host_start_contract,
        validate_run_loop_host_status_contract,
        validate_run_loop_host_stop_contract,
    )

    for field in ("ok", "mode", "plan_id", "pid", "max_waves", "log_path", "status_command", "stop_command"):
        assert field in RUN_LOOP_HOST_START_RESPONSE_FIELDS
    for field in ("running", "stale", "wave_count", "last_gate", "stopped_reason", "start_command_template"):
        assert field in RUN_LOOP_HOST_STATUS_RESPONSE_FIELDS
    for field in ("ok", "mode", "plan_id", "wave_count", "stopped_reason", "next_command"):
        assert field in RUN_LOOP_HOST_STOP_RESPONSE_FIELDS

    assert validate_run_loop_host_start_contract(run_loop_host_start_example())["ok"] is True
    assert validate_run_loop_host_status_contract(run_loop_host_status_example())["ok"] is True
    assert validate_run_loop_host_stop_contract(run_loop_host_stop_example())["ok"] is True

    # 缺字段 / 错 mode / 非法 stopped_reason 必须被拒
    broken = dict(run_loop_host_status_example())
    broken.pop("running")
    assert validate_run_loop_host_status_contract(broken)["ok"] is False
    wrong_mode = {**run_loop_host_status_example(), "mode": "nope"}
    assert validate_run_loop_host_status_contract(wrong_mode)["ok"] is False
    bad_reason = {**run_loop_host_status_example(), "stopped_reason": "made_up"}
    assert validate_run_loop_host_status_contract(bad_reason)["ok"] is False


def test_contract_is_discoverable(capsys) -> None:
    from agentdeck import cli

    assert cli.main(["contract", "run-loop-host", "--example"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["start_response_fields"]
    assert payload["status_response_fields"]
    assert payload["stop_response_fields"]
    assert payload["stopped_reasons"] == list(RUN_LOOP_HOST_STOPPED_REASONS)
    assert payload["contract_exists"] is True
    assert cli.main(["contract", "list"]) == 0
    index = json.loads(capsys.readouterr().out)
    assert any(item.get("name") == "run-loop-host" for item in index["contracts"])
```

If `contract list`'s item key is not `name`/`contracts`, read
`CONTRACT_INDEX_SPECS` and `contract_index_*` in `src/agentdeck/contracts.py`
and match the real shape before locking the assertion.

- [ ] **Step 2: Run to verify failure**

Run: `conda run -n agentdeck pytest tests/test_run_loop_host.py -q -k contract`
Expected: FAIL with ImportError on the new contract symbols

- [ ] **Step 3: Implement** — in `src/agentdeck/contracts.py`, next to the
existing run-loop contract code:

```python
RUN_LOOP_HOST_START_RESPONSE_FIELDS = (
    "ok",
    "mode",
    "plan_id",
    "pid",
    "max_waves",
    "interval",
    "release_boxes",
    "merge_on_complete",
    "log_path",
    "status_command",
    "stop_command",
    "requires_explicit_user",
    "safety",
)

RUN_LOOP_HOST_STATUS_RESPONSE_FIELDS = (
    "ok",
    "mode",
    "running",
    "stale",
    "pid",
    "plan_id",
    "wave_count",
    "max_waves",
    "interval",
    "last_gate",
    "last_wave_at",
    "stopped_reason",
    "log_path",
    "start_command_template",
    "stop_command",
)

RUN_LOOP_HOST_STOP_RESPONSE_FIELDS = (
    "ok",
    "mode",
    "plan_id",
    "pid",
    "wave_count",
    "stopped_reason",
    "next_command",
)

RUN_LOOP_HOST_STOP_MODES = (
    "run_loop_host_stopped",
    "run_loop_host_stop_timed_out",
    "run_loop_host_stale_cleared",
)


def _validate_fields(
    payload: dict[str, object], fields: tuple[str, ...], label: str
) -> list[str]:
    return [f"missing {label} field: {field}" for field in fields if field not in payload]


def validate_run_loop_host_start_contract(payload: dict[str, object]) -> dict[str, object]:
    errors = _validate_fields(payload, RUN_LOOP_HOST_START_RESPONSE_FIELDS, "run_loop_host_start")
    if payload.get("mode") != "run_loop_host_started":
        errors.append(
            f"run_loop_host_start.mode must be run_loop_host_started, got {payload.get('mode')}"
        )
    max_waves = payload.get("max_waves")
    if not isinstance(max_waves, int) or max_waves < 1:
        errors.append("run_loop_host_start.max_waves must be an int >= 1")
    if payload.get("safety") != "delegated":
        errors.append("run_loop_host_start.safety must be delegated")
    if payload.get("requires_explicit_user") is not True:
        errors.append("run_loop_host_start.requires_explicit_user must be true")
    return {"ok": not errors, "errors": errors}


def validate_run_loop_host_status_contract(payload: dict[str, object]) -> dict[str, object]:
    errors = _validate_fields(payload, RUN_LOOP_HOST_STATUS_RESPONSE_FIELDS, "run_loop_host_status")
    if payload.get("mode") != "run_loop_host_status":
        errors.append(
            f"run_loop_host_status.mode must be run_loop_host_status, got {payload.get('mode')}"
        )
    for flag in ("running", "stale"):
        if not isinstance(payload.get(flag), bool):
            errors.append(f"run_loop_host_status.{flag} must be a bool")
    reason = payload.get("stopped_reason")
    if reason is not None and reason not in RUN_LOOP_HOST_STOPPED_REASONS:
        errors.append(f"run_loop_host_status.stopped_reason must be null or one of {list(RUN_LOOP_HOST_STOPPED_REASONS)}")
    if payload.get("running") is True and payload.get("stale") is True:
        errors.append("run_loop_host_status cannot be both running and stale")
    return {"ok": not errors, "errors": errors}


def validate_run_loop_host_stop_contract(payload: dict[str, object]) -> dict[str, object]:
    errors = _validate_fields(payload, RUN_LOOP_HOST_STOP_RESPONSE_FIELDS, "run_loop_host_stop")
    if payload.get("mode") not in RUN_LOOP_HOST_STOP_MODES:
        errors.append(f"run_loop_host_stop.mode must be one of {list(RUN_LOOP_HOST_STOP_MODES)}")
    reason = payload.get("stopped_reason")
    if reason is not None and reason not in RUN_LOOP_HOST_STOPPED_REASONS:
        errors.append(f"run_loop_host_stop.stopped_reason must be null or one of {list(RUN_LOOP_HOST_STOPPED_REASONS)}")
    return {"ok": not errors, "errors": errors}


def run_loop_host_start_example() -> dict[str, object]:
    return {
        "ok": True,
        "mode": "run_loop_host_started",
        "plan_id": "pln_example",
        "pid": 43121,
        "max_waves": 40,
        "interval": 10.0,
        "release_boxes": True,
        "merge_on_complete": True,
        "log_path": ".agentdeck/run-loop-host/host.log",
        "status_command": "agentdeck run-loop-host status",
        "stop_command": "agentdeck run-loop-host stop --confirm",
        "requires_explicit_user": True,
        "safety": "delegated",
    }


def run_loop_host_status_example() -> dict[str, object]:
    return {
        "ok": True,
        "mode": "run_loop_host_status",
        "running": True,
        "stale": False,
        "pid": 43121,
        "plan_id": "pln_example",
        "wave_count": 7,
        "max_waves": 40,
        "interval": 10.0,
        "last_gate": "waiting_for_reply",
        "last_wave_at": "2026-07-30T02:00:00+00:00",
        "stopped_reason": None,
        "log_path": ".agentdeck/run-loop-host/host.log",
        "start_command_template": (
            "agentdeck run-loop-host start --plan-id <plan_id> --confirm --max-waves <n>"
        ),
        "stop_command": "agentdeck run-loop-host stop --confirm",
    }


def run_loop_host_stop_example() -> dict[str, object]:
    return {
        "ok": True,
        "mode": "run_loop_host_stopped",
        "plan_id": "pln_example",
        "pid": 43121,
        "wave_count": 7,
        "stopped_reason": "signalled",
        "next_command": "agentdeck run-loop-host status",
    }


def run_loop_host_contract_payload(contract_path: Path) -> dict[str, object]:
    return {
        "schema_version": PROJECT_VIEW_SCHEMA_VERSION,
        "start_command_template": (
            "agentdeck run-loop-host start --plan-id <plan_id> --confirm --max-waves <n>"
            " [--interval <seconds>] [--release-boxes] [--merge-on-complete]"
        ),
        "status_command": "agentdeck run-loop-host status",
        "stop_command_template": "agentdeck run-loop-host stop --confirm",
        "contract_path": str(contract_path),
        "contract_exists": contract_path.exists(),
        "start_response_fields": list(RUN_LOOP_HOST_START_RESPONSE_FIELDS),
        "status_response_fields": list(RUN_LOOP_HOST_STATUS_RESPONSE_FIELDS),
        "stop_response_fields": list(RUN_LOOP_HOST_STOP_RESPONSE_FIELDS),
        "stop_modes": list(RUN_LOOP_HOST_STOP_MODES),
        "stopped_reasons": list(RUN_LOOP_HOST_STOPPED_REASONS),
        "run_loop_contract": "agentdeck contract run-loop",
        "project_view_schema_version": PROJECT_VIEW_SCHEMA_VERSION,
    }


def run_loop_host_contract_response(
    contract_path: Path, include_example: bool = False
) -> dict[str, object]:
    payload = run_loop_host_contract_payload(contract_path)
    if include_example:
        payload["example"] = {
            "start": run_loop_host_start_example(),
            "status": run_loop_host_status_example(),
            "stop": run_loop_host_stop_example(),
        }
    return payload
```

Import `RUN_LOOP_HOST_STOPPED_REASONS` from `.run_loop_host` at the top of
`contracts.py` (single source of truth for the enum — do not retype it).
If a `_validate_fields` helper with that name already exists, reuse it
instead of adding a second one.

Register the index entry in `CONTRACT_INDEX_SPECS` right after the
`("run-loop-all", …)` entry:

```python
    (
        "run-loop-host",
        "agentdeck contract run-loop-host",
        "agentdeck contract run-loop-host --example",
        "run-loop-host-schema.md",
    ),
```

In `cli.py`, add the contract command next to `contract_run_loop_command`:

```python
def contract_run_loop_host_command(args: argparse.Namespace) -> int:
    contract_path = (
        Path(__file__).resolve().parents[2] / "docs" / "contracts" / "run-loop-host-schema.md"
    )
    _print_json(run_loop_host_contract_response(contract_path, include_example=args.example))
    return 0
```

and add `("run-loop-host", "Show run-loop background host contract metadata",
contract_run_loop_host_command),` to the contract subparser table (the
3-tuple table near the `("run-loop", …)` entry), importing
`run_loop_host_contract_response` from `.contracts`.

Create `docs/contracts/run-loop-host-schema.md` documenting: the discovery
entrypoint, the three response shapes with every field, the closed
`stopped_reason` and stop-`mode` enums with their follow-up commands, the
four start gates (confirm, autonomous, `--max-waves >= 1`, known plan) plus
single-instance refusal, the per-wave policy brake, the SIGTERM-after-current-wave
stop with no SIGKILL escalation, the three status record states, the
append-only shared log, and a Boundaries section stating that hosting is not
authorization (every wave-engine invariant is inherited unchanged) and that
this host is deliberately separate from the M2 Mission daemon.

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n agentdeck pytest tests/test_run_loop_host.py tests/test_contracts.py -q`
Expected: all pass

- [ ] **Step 5: HISTORY + commit**

Top entry, Type: feat, "Add run-loop-host contract (three shapes, closed
stopped-reason enum)".

```bash
git add src/agentdeck/contracts.py src/agentdeck/cli.py tests/test_run_loop_host.py \
  docs/contracts/run-loop-host-schema.md HISTORY.md
git commit -m "feat: add run-loop-host contract and discovery entrypoint"
```

---

## Task 3: `start` (four gates + injected spawn) and `status`

**Files:**
- Modify: `src/agentdeck/cli.py`
- Create: `tests/test_run_loop_host_cli.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write the failing tests** — create `tests/test_run_loop_host_cli.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from agentdeck import cli
from agentdeck.config import write_default_config
from agentdeck.run_loop_host import host_record_path, read_host_record, write_host_record
from agentdeck.state import StateStore


def prepare_project(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    monkeypatch.chdir(root)
    return root


def enable_autonomous(capsys) -> None:
    cli.main([
        "policy", "set-mode", "--mode", "autonomous", "--confirm",
        "--allow-agent", "coder", "--max-approvals", "3",
    ])
    capsys.readouterr()


def seed_plan(root: Path, plan_id: str = "pln_host_1") -> str:
    store = StateStore(root)
    state = store.load()
    state.setdefault("plans", []).append({
        "plan_id": plan_id,
        "goal": "host test",
        "summary": "host test",
        "steps": [{"step": 1, "agent_id": "coder", "role": "implementation",
                   "task": "do work", "risk": "low", "requires_approval": True}],
    })
    store.save(state)
    return plan_id


class RecordingSpawn:
    def __init__(self, pid: int = 999_001) -> None:
        self.pid = pid
        self.calls: list[tuple[list[str], str]] = []

    def __call__(self, argv: list[str], cwd: Path) -> int:
        self.calls.append((list(argv), str(cwd)))
        return self.pid


def test_start_gate_matrix_refuses_with_zero_writes(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    plan_id = seed_plan(root)
    spawn = RecordingSpawn()
    monkeypatch.setattr(cli, "_spawn_host_process", spawn)

    # 缺 --confirm
    assert cli.main(["run-loop-host", "start", "--plan-id", plan_id, "--max-waves", "5"]) == 1
    assert "confirm" in capsys.readouterr().err
    # 非 autonomous 模式
    assert cli.main([
        "run-loop-host", "start", "--plan-id", plan_id, "--confirm", "--max-waves", "5",
    ]) == 1
    assert "autonomous" in capsys.readouterr().err

    enable_autonomous(capsys)
    # 缺 --max-waves
    assert cli.main(["run-loop-host", "start", "--plan-id", plan_id, "--confirm"]) == 1
    assert "max-waves" in capsys.readouterr().err
    # --max-waves < 1
    assert cli.main([
        "run-loop-host", "start", "--plan-id", plan_id, "--confirm", "--max-waves", "0",
    ]) == 1
    capsys.readouterr()
    # 未知 plan
    assert cli.main([
        "run-loop-host", "start", "--plan-id", "pln_ghost", "--confirm", "--max-waves", "5",
    ]) == 1
    assert "unknown plan" in capsys.readouterr().err

    # 全程零写、零 spawn
    assert read_host_record(root) is None
    assert spawn.calls == []


def test_start_spawns_records_and_refuses_second_instance(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    plan_id = seed_plan(root)
    enable_autonomous(capsys)
    spawn = RecordingSpawn(pid=999_002)
    monkeypatch.setattr(cli, "_spawn_host_process", spawn)
    # 记录里的 pid 探活必须为真,否则第二次 start 不会被判为"已在跑"
    monkeypatch.setattr(cli, "_host_pid_alive", lambda pid: pid == 999_002)

    assert cli.main([
        "run-loop-host", "start", "--plan-id", plan_id, "--confirm",
        "--max-waves", "40", "--interval", "3", "--release-boxes", "--merge-on-complete",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "run_loop_host_started"
    assert payload["pid"] == 999_002
    assert payload["plan_id"] == plan_id
    assert payload["max_waves"] == 40
    assert payload["release_boxes"] is True
    assert payload["merge_on_complete"] is True
    assert payload["status_command"] == "agentdeck run-loop-host status"
    assert payload["safety"] == "delegated"

    record = read_host_record(root)
    assert record["pid"] == 999_002
    assert record["plan_id"] == plan_id
    assert record["wave_count"] == 0
    assert record["stopped_reason"] is None

    # spawn argv 指向 serve 子命令并带全部参数
    argv, cwd = spawn.calls[0]
    assert argv[:3] == ["agentdeck", "run-loop-host", "serve"]
    assert "--plan-id" in argv and plan_id in argv
    assert "--max-waves" in argv and "40" in argv
    assert "--release-boxes" in argv and "--merge-on-complete" in argv
    assert cwd == str(root)
    assert '"event_type": "run_loop_host_started"' in (
        root / ".agentdeck" / "state" / "events.jsonl"
    ).read_text(encoding="utf-8")

    # 单例:第二次 start 拒绝,不再 spawn
    assert cli.main([
        "run-loop-host", "start", "--plan-id", plan_id, "--confirm", "--max-waves", "5",
    ]) == 1
    assert "already running" in capsys.readouterr().err
    assert len(spawn.calls) == 1


def test_status_is_read_only_across_three_record_states(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    # 无记录
    assert cli.main(["run-loop-host", "status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "run_loop_host_status"
    assert payload["running"] is False and payload["stale"] is False
    assert payload["plan_id"] is None
    assert "--max-waves <n>" in payload["start_command_template"]

    # 活 pid
    write_host_record(root, {
        "pid": 999_003, "plan_id": "pln_host_1", "wave_count": 4, "max_waves": 40,
        "interval": 10.0, "last_gate": "waiting_for_reply",
        "last_wave_at": "2026-07-30T02:00:00+00:00", "stopped_reason": None,
        "log_path": ".agentdeck/run-loop-host/host.log",
    })
    monkeypatch.setattr(cli, "_host_pid_alive", lambda pid: True)
    before = StateStore(root).load()
    assert cli.main(["run-loop-host", "status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["running"] is True and payload["stale"] is False
    assert payload["wave_count"] == 4
    assert payload["stop_command"] == "agentdeck run-loop-host stop --confirm"
    assert StateStore(root).load() == before  # 只读

    # 死 pid → stale
    monkeypatch.setattr(cli, "_host_pid_alive", lambda pid: False)
    assert cli.main(["run-loop-host", "status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["running"] is False and payload["stale"] is True

    # 干净停止(pid 已清)既不 running 也不 stale
    write_host_record(root, {
        "pid": None, "plan_id": "pln_host_1", "wave_count": 9, "max_waves": 40,
        "interval": 10.0, "last_gate": "complete",
        "last_wave_at": "2026-07-30T02:30:00+00:00", "stopped_reason": "gate_reached",
        "log_path": ".agentdeck/run-loop-host/host.log",
    })
    assert cli.main(["run-loop-host", "status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["running"] is False and payload["stale"] is False
    assert payload["stopped_reason"] == "gate_reached"
```

- [ ] **Step 2: Run to verify failure**

Run: `conda run -n agentdeck pytest tests/test_run_loop_host_cli.py -q`
Expected: FAIL — argparse rejects the unknown `run-loop-host` command
(SystemExit(2)) and `cli._spawn_host_process` does not exist

- [ ] **Step 3: Implement** — in `cli.py`, add near the run-loop commands:

```python
def _spawn_host_process(argv: list[str], cwd: Path) -> int:
    """Detached child; survives client disconnect. Tests inject a fake."""
    process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        argv,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return int(process.pid)


def _host_pid_alive(pid: int) -> bool:
    """Indirection so tests can control liveness without real processes."""
    return pid_alive(pid)


def _host_liveness_or_none(root: Path) -> tuple[dict[str, object] | None, bool, bool]:
    """模块逻辑单一来源:只注入可测试的存活探测器,不重复判定规则。"""
    return host_liveness(root, probe=_host_pid_alive)


def _run_loop_host_status_payload(root: Path) -> dict[str, object]:
    record, running, stale = _host_liveness_or_none(root)
    record = record or {}
    return {
        "ok": True,
        "mode": "run_loop_host_status",
        "running": running,
        "stale": stale,
        "pid": record.get("pid"),
        "plan_id": record.get("plan_id"),
        "wave_count": record.get("wave_count"),
        "max_waves": record.get("max_waves"),
        "interval": record.get("interval"),
        "last_gate": record.get("last_gate"),
        "last_wave_at": record.get("last_wave_at"),
        "stopped_reason": record.get("stopped_reason"),
        "log_path": record.get("log_path") or str(
            host_log_path(root).relative_to(root)
        ),
        "start_command_template": (
            "agentdeck run-loop-host start --plan-id <plan_id> --confirm --max-waves <n>"
        ),
        "stop_command": "agentdeck run-loop-host stop --confirm",
    }


def run_loop_host_start_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    if not args.confirm:
        print("run-loop-host start requires --confirm", file=sys.stderr)
        return 1
    if config.leader.approval_mode != "autonomous":
        print(
            "run-loop-host start requires autonomous mode "
            "(agentdeck policy set-mode --mode autonomous ...)",
            file=sys.stderr,
        )
        return 1
    if args.max_waves is None:
        print("run-loop-host start requires --max-waves (bounded budget)", file=sys.stderr)
        return 1
    if args.max_waves < 1:
        print("run-loop-host start requires --max-waves >= 1", file=sys.stderr)
        return 1
    root = Path(config.root)
    known = any(
        isinstance(plan, dict) and plan.get("plan_id") == args.plan_id
        for plan in store.load().get("plans", [])
    )
    if not known:
        print(f"unknown plan: {args.plan_id}", file=sys.stderr)
        return 1
    _record, running, _stale = _host_liveness_or_none(root)
    if running:
        print(
            "run-loop host already running; see agentdeck run-loop-host status",
            file=sys.stderr,
        )
        return 1
    argv = [
        "agentdeck", "run-loop-host", "serve",
        "--project", str(root),
        "--plan-id", str(args.plan_id),
        "--max-waves", str(args.max_waves),
        "--interval", str(args.interval),
    ]
    if args.release_boxes:
        argv.append("--release-boxes")
    if getattr(args, "merge_on_complete", False):
        argv.append("--merge-on-complete")
    pid = _spawn_host_process(argv, root)
    log_relative = str(host_log_path(root).relative_to(root))
    write_host_record(root, {
        "pid": pid,
        "plan_id": str(args.plan_id),
        "started_at": utc_now(),
        "max_waves": int(args.max_waves),
        "interval": float(args.interval),
        "release_boxes": bool(args.release_boxes),
        "merge_on_complete": bool(getattr(args, "merge_on_complete", False)),
        "log_path": log_relative,
        "wave_count": 0,
        "last_gate": None,
        "last_wave_at": None,
        "stopped_reason": None,
    })
    store.append_event(EventRecord.create("run_loop_host_started", {
        "plan_id": str(args.plan_id),
        "pid": pid,
        "max_waves": int(args.max_waves),
        "interval": float(args.interval),
        "release_boxes": bool(args.release_boxes),
        "merge_on_complete": bool(getattr(args, "merge_on_complete", False)),
    }))
    payload = {
        "ok": True,
        "mode": "run_loop_host_started",
        "plan_id": str(args.plan_id),
        "pid": pid,
        "max_waves": int(args.max_waves),
        "interval": float(args.interval),
        "release_boxes": bool(args.release_boxes),
        "merge_on_complete": bool(getattr(args, "merge_on_complete", False)),
        "log_path": log_relative,
        "status_command": "agentdeck run-loop-host status",
        "stop_command": "agentdeck run-loop-host stop --confirm",
        "requires_explicit_user": True,
        "safety": "delegated",
    }
    validation = validate_run_loop_host_start_contract(payload)
    if not validation["ok"]:
        print("run-loop-host start contract validation failed", file=sys.stderr)
        for error in validation["errors"]:
            print(f"- {error}", file=sys.stderr)
        return 1
    _print_json(payload)
    return 0


def run_loop_host_status_command(_args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    payload = _run_loop_host_status_payload(Path(config.root))
    validation = validate_run_loop_host_status_contract(payload)
    if not validation["ok"]:
        print("run-loop-host status contract validation failed", file=sys.stderr)
        for error in validation["errors"]:
            print(f"- {error}", file=sys.stderr)
        return 1
    _print_json(payload)
    return 0
```

Add the imports `subprocess` (if absent) and, from `.run_loop_host`:
`RUN_LOOP_HOST_STOPPED_REASONS, append_host_log, host_log_path,
pid_alive, read_host_record, write_host_record`; from `.contracts`:
`validate_run_loop_host_start_contract, validate_run_loop_host_status_contract,
validate_run_loop_host_stop_contract, run_loop_host_contract_response`.

Argparse wiring (place after the `run-loop` parser block):

```python
    run_loop_host = subparsers.add_parser(
        "run-loop-host",
        help="Run the bounded run-loop wave engine in a detached background host",
    )
    run_loop_host_subparsers = run_loop_host.add_subparsers(
        dest="run_loop_host_command", required=True
    )

    host_start = run_loop_host_subparsers.add_parser("start", help="Start the background host")
    host_start.add_argument("--plan-id", required=True, help="Plan to drive forward")
    host_start.add_argument("--confirm", action="store_true", help="Explicitly confirm the host")
    host_start.add_argument(
        "--max-waves", type=int, default=None, help="Required bounded number of waves"
    )
    host_start.add_argument("--interval", type=float, default=10.0, help="Seconds between waves")
    host_start.add_argument(
        "--release-boxes",
        action="store_true",
        help="Release delegation-covered authorization boxes between waves (audited)",
    )
    host_start.add_argument(
        "--merge-on-complete",
        dest="merge_on_complete",
        action="store_true",
        help="Merge the plan's task branches when the final gate is complete",
    )
    host_start.set_defaults(func=run_loop_host_start_command)

    host_status = run_loop_host_subparsers.add_parser(
        "status", help="Show background host status (read-only)"
    )
    host_status.set_defaults(func=run_loop_host_status_command)
```

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n agentdeck pytest tests/test_run_loop_host_cli.py tests/test_run_loop_host.py -q`
Expected: all pass

- [ ] **Step 5: HISTORY + commit**

Top entry, Type: feat, "Add run-loop-host start and status commands".

```bash
git add src/agentdeck/cli.py tests/test_run_loop_host_cli.py HISTORY.md
git commit -m "feat: add run-loop-host start and status commands"
```

---

## Task 4: `serve` (the child loop, policy brake, SIGTERM)

**Files:**
- Modify: `src/agentdeck/cli.py`
- Test: `tests/test_run_loop_host_cli.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_run_loop_host_cli.py`):

```python
def _serve_argv(root: Path, plan_id: str, max_waves: int, interval: str = "0") -> list[str]:
    return [
        "run-loop-host", "serve", "--project", str(root),
        "--plan-id", plan_id, "--max-waves", str(max_waves), "--interval", interval,
    ]


def _log_lines(root: Path) -> list[dict]:
    from agentdeck.run_loop_host import host_log_path

    text = host_log_path(root).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_serve_runs_waves_until_gate_and_records(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    plan_id = seed_plan(root)
    enable_autonomous(capsys)
    write_host_record(root, {
        "pid": 1, "plan_id": plan_id, "max_waves": 5, "interval": 0.0,
        "release_boxes": False, "merge_on_complete": False,
        "log_path": ".agentdeck/run-loop-host/host.log",
        "wave_count": 0, "last_gate": None, "last_wave_at": None, "stopped_reason": None,
    })
    gates = ["waiting_for_reply", "waiting_for_reply", "complete"]
    calls = {"n": 0}

    def fake_wave(_config, _store, wave_plan_id):
        assert wave_plan_id == plan_id
        gate = gates[calls["n"]]
        calls["n"] += 1
        return {"ok": True, "mode": "run_loop", "plan_id": wave_plan_id,
                "stopped_reason": gate, "next_command": "agentdeck leader summary"}

    monkeypatch.setattr(cli, "_run_loop_single_wave", fake_wave)
    assert cli.main(_serve_argv(root, plan_id, 5)) == 0

    record = read_host_record(root)
    assert record["wave_count"] == 3
    assert record["last_gate"] == "complete"
    assert record["stopped_reason"] == "gate_reached"
    assert record["pid"] is None  # 干净停止清 pid
    lines = _log_lines(root)
    assert [line["wave"] for line in lines] == [1, 2, 3]
    assert all(line["plan_id"] == plan_id for line in lines)
    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "run_loop_host_stopped"' in events
    assert '"stopped_reason": "gate_reached"' in events


def test_serve_stops_at_budget(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    plan_id = seed_plan(root)
    enable_autonomous(capsys)
    write_host_record(root, {
        "pid": 1, "plan_id": plan_id, "max_waves": 2, "interval": 0.0,
        "release_boxes": False, "merge_on_complete": False,
        "log_path": ".agentdeck/run-loop-host/host.log",
        "wave_count": 0, "last_gate": None, "last_wave_at": None, "stopped_reason": None,
    })
    monkeypatch.setattr(cli, "_run_loop_single_wave", lambda *_a: {
        "ok": True, "mode": "run_loop", "plan_id": plan_id,
        "stopped_reason": "waiting_for_reply", "next_command": "agentdeck capture-reply",
    })
    assert cli.main(_serve_argv(root, plan_id, 2)) == 0
    record = read_host_record(root)
    assert record["wave_count"] == 2
    assert record["stopped_reason"] == "budget_exhausted"


def test_serve_policy_brake_stops_when_mode_leaves_autonomous(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    plan_id = seed_plan(root)
    enable_autonomous(capsys)
    write_host_record(root, {
        "pid": 1, "plan_id": plan_id, "max_waves": 5, "interval": 0.0,
        "release_boxes": False, "merge_on_complete": False,
        "log_path": ".agentdeck/run-loop-host/host.log",
        "wave_count": 0, "last_gate": None, "last_wave_at": None, "stopped_reason": None,
    })
    waves = {"n": 0}

    def fake_wave(_config, _store, _plan_id):
        waves["n"] += 1
        if waves["n"] == 1:
            # 第一 wave 后人类把模式改回 ask(远程刹车)
            cli.main(["policy", "set-mode", "--mode", "ask"])
            capsys.readouterr()
        return {"ok": True, "mode": "run_loop", "plan_id": plan_id,
                "stopped_reason": "waiting_for_reply", "next_command": "agentdeck capture-reply"}

    monkeypatch.setattr(cli, "_run_loop_single_wave", fake_wave)
    assert cli.main(_serve_argv(root, plan_id, 5)) == 0
    record = read_host_record(root)
    assert record["stopped_reason"] == "policy_revoked"
    assert record["wave_count"] == 1


def test_serve_engine_error_is_recorded_not_crashed(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    plan_id = seed_plan(root)
    enable_autonomous(capsys)
    write_host_record(root, {
        "pid": 1, "plan_id": plan_id, "max_waves": 3, "interval": 0.0,
        "release_boxes": False, "merge_on_complete": False,
        "log_path": ".agentdeck/run-loop-host/host.log",
        "wave_count": 0, "last_gate": None, "last_wave_at": None, "stopped_reason": None,
    })

    def boom(*_args):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(cli, "_run_loop_single_wave", boom)
    assert cli.main(_serve_argv(root, plan_id, 3)) == 1
    record = read_host_record(root)
    assert record["stopped_reason"] == "engine_error"
    # 只记异常类型,不记消息(避免 provider 输出/密钥入日志)
    line = _log_lines(root)[-1]
    assert line["error_type"] == "RuntimeError"
    assert "exploded" not in json.dumps(line)


def test_serve_signal_finishes_current_wave_then_exits(tmp_path, monkeypatch, capsys) -> None:
    import signal as signal_module

    root = prepare_project(tmp_path, monkeypatch)
    plan_id = seed_plan(root)
    enable_autonomous(capsys)
    write_host_record(root, {
        "pid": 1, "plan_id": plan_id, "max_waves": 9, "interval": 0.0,
        "release_boxes": False, "merge_on_complete": False,
        "log_path": ".agentdeck/run-loop-host/host.log",
        "wave_count": 0, "last_gate": None, "last_wave_at": None, "stopped_reason": None,
    })
    handlers: dict[int, object] = {}
    monkeypatch.setattr(
        cli.signal, "signal", lambda number, handler: handlers.setdefault(number, handler)
    )

    def fake_wave(*_args):
        # wave 执行中收到 SIGTERM:必须完成本 wave 再退出
        handler = handlers.get(signal_module.SIGTERM)
        if handler is not None:
            handler(signal_module.SIGTERM, None)
        return {"ok": True, "mode": "run_loop", "plan_id": plan_id,
                "stopped_reason": "waiting_for_reply", "next_command": "agentdeck capture-reply"}

    monkeypatch.setattr(cli, "_run_loop_single_wave", fake_wave)
    assert cli.main(_serve_argv(root, plan_id, 9)) == 0
    record = read_host_record(root)
    assert record["wave_count"] == 1  # 当前 wave 完整跑完
    assert record["stopped_reason"] == "signalled"
```

- [ ] **Step 2: Run to verify failure**

Run: `conda run -n agentdeck pytest tests/test_run_loop_host_cli.py -q -k serve`
Expected: FAIL — argparse has no `serve` subcommand

- [ ] **Step 3: Implement** — add to `cli.py`:

```python
def _run_loop_host_finish(
    root: Path,
    store: StateStore,
    *,
    plan_id: str,
    wave_count: int,
    last_gate: str | None,
    stopped_reason: str,
) -> None:
    record = read_host_record(root) or {}
    record.update({
        "pid": None,
        "plan_id": plan_id,
        "wave_count": wave_count,
        "last_gate": last_gate,
        "stopped_reason": stopped_reason,
        "stopped_at": utc_now(),
    })
    write_host_record(root, record)
    append_host_log(root, {
        "plan_id": plan_id,
        "event": "host_stopped",
        "wave": wave_count,
        "stopped_reason": stopped_reason,
        "at": utc_now(),
    })
    store.append_event(EventRecord.create("run_loop_host_stopped", {
        "plan_id": plan_id,
        "wave_count": wave_count,
        "stopped_reason": stopped_reason,
        "source": "host",
    }))


def run_loop_host_serve_command(args: argparse.Namespace) -> int:
    """The detached child. stdout is DEVNULL in production; all output is the
    JSONL log plus audit events. The wave engine is reused unchanged."""
    root = Path(args.project).expanduser().resolve()
    try:
        config = load_config(root)
        store = StateStore(root)
    except Exception as exc:
        print(f"run-loop-host serve failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    plan_id = str(args.plan_id)
    stop_requested = {"value": False}

    def _handle_terminate(_number, _frame) -> None:
        # 只置旗标:当前 wave 必须跑完,worker 绝不被半途切断。
        stop_requested["value"] = True

    signal.signal(signal.SIGTERM, _handle_terminate)
    signal.signal(signal.SIGINT, _handle_terminate)

    backend = TmuxBackend() if args.release_boxes else None
    agent_ids = [agent.agent_id for agent in config.agents]
    if backend is not None:
        released, _skipped = _scan_release_delegated_boxes(
            config, store, backend, agent_ids, 0, source="run_loop_host"
        )
        for item in released:
            append_host_log(root, {
                "plan_id": plan_id, "event": "box_released", "wave": 0,
                "agent_id": item.get("agent_id"), "match_kind": item.get("match_kind"),
                "at": utc_now(),
            })

    wave_count = 0
    last_gate: str | None = None
    stopped_reason = "budget_exhausted"
    for wave_number in range(1, int(args.max_waves) + 1):
        # 每 wave 重读配置:approval_mode 被改回 ask 即自停(远程刹车)。
        try:
            config = load_config(root)
        except Exception:
            stopped_reason = "policy_revoked"
            break
        if config.leader.approval_mode != "autonomous":
            stopped_reason = "policy_revoked"
            break
        try:
            payload = _run_loop_single_wave(config, store, plan_id)
        except Exception as exc:  # 引擎异常:记类型,绝不记 provider 输出
            append_host_log(root, {
                "plan_id": plan_id, "event": "engine_error", "wave": wave_number,
                "error_type": type(exc).__name__, "at": utc_now(),
            })
            _run_loop_host_finish(
                root, store, plan_id=plan_id, wave_count=wave_count,
                last_gate=last_gate, stopped_reason="engine_error",
            )
            return 1
        if payload is None:
            append_host_log(root, {
                "plan_id": plan_id, "event": "engine_error", "wave": wave_number,
                "error_type": "ContractValidationFailed", "at": utc_now(),
            })
            _run_loop_host_finish(
                root, store, plan_id=plan_id, wave_count=wave_count,
                last_gate=last_gate, stopped_reason="engine_error",
            )
            return 1
        wave_count = wave_number
        last_gate = payload.get("stopped_reason")
        append_host_log(root, {**payload, "wave": wave_number, "plan_id": plan_id, "at": utc_now()})
        record = read_host_record(root) or {}
        record.update({
            "wave_count": wave_count,
            "last_gate": last_gate,
            "last_wave_at": utc_now(),
        })
        write_host_record(root, record)
        if last_gate != "waiting_for_reply":
            stopped_reason = "gate_reached"
            break
        if stop_requested["value"]:
            stopped_reason = "signalled"
            break
        if wave_number >= int(args.max_waves):
            stopped_reason = "budget_exhausted"
            break
        if backend is not None:
            released, _skipped = _scan_release_delegated_boxes(
                config, store, backend, agent_ids, wave_number, source="run_loop_host"
            )
            for item in released:
                append_host_log(root, {
                    "plan_id": plan_id, "event": "box_released", "wave": wave_number,
                    "agent_id": item.get("agent_id"), "match_kind": item.get("match_kind"),
                    "at": utc_now(),
                })
        if args.interval > 0:
            time.sleep(float(args.interval))

    if (
        getattr(args, "merge_on_complete", False)
        and stopped_reason == "gate_reached"
        and last_gate == "complete"
    ):
        blocker = _verdict_merge_blocker(store, plan_id)
        if blocker:
            append_host_log(root, {
                "plan_id": plan_id, "event": "plan_merge", "mode": "verdict_blocked",
                "blocker": blocker, "wave": wave_count, "at": utc_now(),
            })
        else:
            append_host_log(root, {
                "plan_id": plan_id, "event": "plan_merge", "wave": wave_count,
                "result": _merge_plan_worktrees(config, store, plan_id), "at": utc_now(),
            })

    _run_loop_host_finish(
        root, store, plan_id=plan_id, wave_count=wave_count,
        last_gate=last_gate, stopped_reason=stopped_reason,
    )
    return 0
```

Add `import signal` at the top of `cli.py` if absent.

Argparse (inside the `run_loop_host_subparsers` block from Task 3):

```python
    host_serve = run_loop_host_subparsers.add_parser(
        "serve", help="Internal: run the host loop in the foreground of a detached child"
    )
    host_serve.add_argument("--project", required=True, help="Project root")
    host_serve.add_argument("--plan-id", required=True, help="Plan to drive forward")
    host_serve.add_argument("--max-waves", type=int, required=True, help="Bounded wave budget")
    host_serve.add_argument("--interval", type=float, default=10.0, help="Seconds between waves")
    host_serve.add_argument("--release-boxes", action="store_true", help="Release delegated boxes")
    host_serve.add_argument(
        "--merge-on-complete", dest="merge_on_complete", action="store_true",
        help="Merge task branches when the final gate is complete",
    )
    host_serve.set_defaults(func=run_loop_host_serve_command)
```

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n agentdeck pytest tests/test_run_loop_host_cli.py tests/test_run_loop_follow.py -q`
Expected: all pass (the follow tests prove the shared engine is untouched)

- [ ] **Step 5: HISTORY + commit**

Top entry, Type: feat, "Add run-loop-host serve loop with policy brake and
graceful SIGTERM".

```bash
git add src/agentdeck/cli.py tests/test_run_loop_host_cli.py HISTORY.md
git commit -m "feat: add run-loop-host serve loop"
```

---

## Task 5: `stop`, CLAUDE.md rule, README, full ladder

**Files:**
- Modify: `src/agentdeck/cli.py`, `CLAUDE.md`, `README.md`, `HISTORY.md`
- Test: `tests/test_run_loop_host_cli.py`

- [ ] **Step 1: Write the failing tests** (append):

```python
def test_stop_requires_confirm_and_refuses_without_record(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    assert cli.main(["run-loop-host", "stop"]) == 1
    assert "confirm" in capsys.readouterr().err
    assert cli.main(["run-loop-host", "stop", "--confirm"]) == 1
    assert "no run-loop host" in capsys.readouterr().err
    assert read_host_record(root) is None


def test_stop_signals_live_host_and_reports(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    write_host_record(root, {
        "pid": 999_010, "plan_id": "pln_host_1", "wave_count": 6, "max_waves": 40,
        "interval": 10.0, "last_gate": "waiting_for_reply", "last_wave_at": None,
        "stopped_reason": None, "log_path": ".agentdeck/run-loop-host/host.log",
    })
    signals: list[tuple[int, int]] = []
    liveness = {"alive": True}

    def fake_kill(pid: int, number: int) -> None:
        signals.append((pid, number))
        liveness["alive"] = False  # 子进程接受信号后退出

    monkeypatch.setattr(cli.os, "kill", fake_kill)
    monkeypatch.setattr(cli, "_host_pid_alive", lambda _pid: liveness["alive"])
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)

    assert cli.main(["run-loop-host", "stop", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "run_loop_host_stopped"
    assert payload["pid"] == 999_010
    assert payload["wave_count"] == 6
    import signal as signal_module
    assert signals == [(999_010, signal_module.SIGTERM)]
    assert read_host_record(root)["pid"] is None
    events = (root / ".agentdeck" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type": "run_loop_host_stopped"' in events
    assert '"source": "explicit"' in events


def test_stop_timeout_keeps_record_and_never_kills(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    write_host_record(root, {
        "pid": 999_011, "plan_id": "pln_host_1", "wave_count": 2, "max_waves": 40,
        "interval": 10.0, "last_gate": "waiting_for_reply", "last_wave_at": None,
        "stopped_reason": None, "log_path": ".agentdeck/run-loop-host/host.log",
    })
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(cli.os, "kill", lambda pid, number: signals.append((pid, number)))
    monkeypatch.setattr(cli, "_host_pid_alive", lambda _pid: True)  # 永不退出
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)

    assert cli.main(["run-loop-host", "stop", "--confirm"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "run_loop_host_stop_timed_out"
    import signal as signal_module
    assert {number for _pid, number in signals} == {signal_module.SIGTERM}  # 绝不 SIGKILL
    assert read_host_record(root)["pid"] == 999_011  # 记录保留给人工


def test_stop_clears_stale_record(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    write_host_record(root, {
        "pid": 999_012, "plan_id": "pln_host_1", "wave_count": 3, "max_waves": 40,
        "interval": 10.0, "last_gate": "waiting_for_reply", "last_wave_at": None,
        "stopped_reason": None, "log_path": ".agentdeck/run-loop-host/host.log",
    })
    monkeypatch.setattr(cli, "_host_pid_alive", lambda _pid: False)
    killed: list[int] = []
    monkeypatch.setattr(cli.os, "kill", lambda pid, _n: killed.append(pid))

    assert cli.main(["run-loop-host", "stop", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "run_loop_host_stale_cleared"
    assert killed == []  # 死进程不发信号
    assert read_host_record(root)["pid"] is None
```

- [ ] **Step 2: Run to verify failure**

Run: `conda run -n agentdeck pytest tests/test_run_loop_host_cli.py -q -k stop`
Expected: FAIL — no `stop` subcommand

- [ ] **Step 3: Implement** — add to `cli.py`:

```python
_HOST_STOP_TIMEOUT_SECONDS = 60.0
_HOST_STOP_POLL_SECONDS = 0.5


def run_loop_host_stop_command(args: argparse.Namespace) -> int:
    config, store, exit_code = _load_project_or_error()
    if config is None or store is None:
        return exit_code
    if not args.confirm:
        print("run-loop-host stop requires --confirm", file=sys.stderr)
        return 1
    root = Path(config.root)
    record, running, stale = _host_liveness_or_none(root)
    if record is None:
        print("no run-loop host record for this project", file=sys.stderr)
        return 1
    plan_id = record.get("plan_id")
    pid = record.get("pid")
    wave_count = record.get("wave_count")
    if not running:
        cleared = {**record, "pid": None}
        write_host_record(root, cleared)
        payload = {
            "ok": True,
            "mode": "run_loop_host_stale_cleared",
            "plan_id": plan_id,
            "pid": pid if stale else None,
            "wave_count": wave_count,
            "stopped_reason": record.get("stopped_reason"),
            "next_command": "agentdeck run-loop-host status",
        }
    else:
        os.kill(int(pid), signal.SIGTERM)
        deadline = time.monotonic() + _HOST_STOP_TIMEOUT_SECONDS
        exited = False
        while time.monotonic() < deadline:
            if not _host_pid_alive(int(pid)):
                exited = True
                break
            time.sleep(_HOST_STOP_POLL_SECONDS)
        if not exited:
            # 有界超时:绝不升级到 SIGKILL,保留记录交人工。
            payload = {
                "ok": False,
                "mode": "run_loop_host_stop_timed_out",
                "plan_id": plan_id,
                "pid": pid,
                "wave_count": wave_count,
                "stopped_reason": None,
                "next_command": "agentdeck run-loop-host status",
            }
            validation = validate_run_loop_host_stop_contract(payload)
            if not validation["ok"]:
                print("run-loop-host stop contract validation failed", file=sys.stderr)
                for error in validation["errors"]:
                    print(f"- {error}", file=sys.stderr)
                return 1
            _print_json(payload)
            return 1
        latest = read_host_record(root) or record
        write_host_record(root, {**latest, "pid": None})
        store.append_event(EventRecord.create("run_loop_host_stopped", {
            "plan_id": plan_id,
            "wave_count": (latest.get("wave_count") if latest else wave_count),
            "stopped_reason": latest.get("stopped_reason") or "signalled",
            "source": "explicit",
        }))
        payload = {
            "ok": True,
            "mode": "run_loop_host_stopped",
            "plan_id": plan_id,
            "pid": pid,
            "wave_count": latest.get("wave_count", wave_count),
            "stopped_reason": latest.get("stopped_reason") or "signalled",
            "next_command": "agentdeck run-loop-host status",
        }
    validation = validate_run_loop_host_stop_contract(payload)
    if not validation["ok"]:
        print("run-loop-host stop contract validation failed", file=sys.stderr)
        for error in validation["errors"]:
            print(f"- {error}", file=sys.stderr)
        return 1
    _print_json(payload)
    return 0
```

Add `import os` at the top of `cli.py` if absent. Argparse:

```python
    host_stop = run_loop_host_subparsers.add_parser("stop", help="Stop the background host")
    host_stop.add_argument("--confirm", action="store_true", help="Explicitly confirm the stop")
    host_stop.set_defaults(func=run_loop_host_stop_command)
```

`CLAUDE.md`: add a rule paragraph after the run-loop-all bullet stating that
`agentdeck run-loop-host start|status|stop` hosts the **unchanged** single-wave
engine in a detached process; that `start` requires `--confirm` **and**
`approval_mode=autonomous` **and** an explicit `--max-waves >= 1` **and** a
known plan, refusing a second instance; that the child re-reads config each
wave so flipping `approval_mode` stops it (`stopped_reason=policy_revoked`);
that `stop --confirm` sends SIGTERM, the child finishes the current wave, and
timeout never escalates to SIGKILL; that `status` is read-only; that every
wave-engine invariant is inherited unchanged (approval-gated, allowlist and
budget, running panes only, never force-spawn, never read a pane to infer
completion, file-channel replies only, step-ordering guard, delegated box
release); that hosting is not authorization; and that this host is separate
from the M2 Mission daemon (`agentdeck daemon …`), which is untouched.

`README.md`: add the three commands to the command list near `run-loop`.

- [ ] **Step 4: Full verification ladder** (report exact counts)

1. `conda run -n agentdeck pytest tests/test_run_loop_host.py tests/test_run_loop_host_cli.py -q`
2. `conda run -n agentdeck pytest tests/test_run_loop_follow.py tests/test_autonomy.py tests/test_contracts.py tests/test_agent_cli.py -q`
3. `conda run -n agentdeck python -m compileall src tests`
4. `git diff --check`
5. `conda run -n agentdeck pytest tests/ -q` — full suite (~5 min, wait for it;
   expect ~4790+ passed, 3 skipped)

Also confirm the Mission daemon was not touched:
`git diff <base>..HEAD -- src/agentdeck/daemon/` must be empty.

- [ ] **Step 5: HISTORY + commit**

Top entry, Type: feat, "Add run-loop-host stop with bounded SIGTERM and no
SIGKILL escalation".

```bash
git add src/agentdeck/cli.py tests/test_run_loop_host_cli.py CLAUDE.md README.md HISTORY.md
git commit -m "feat: add run-loop-host stop command"
```

---

## Post-plan notes

- Update `docs/handoff/current-development-state.md` (move daemon 背景续跑
  from 拍板项 to landed; note that live validation rides the next Line 1 round:
  start a host, disconnect the client, confirm waves continue and
  `status`/`stop` behave).
- Non-goals restated: no multi-plan host, no unbounded host, no
  restart-on-crash supervision, no remote host, no Mission-lane convergence.
