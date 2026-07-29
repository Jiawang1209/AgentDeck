# MCP Tool Delegation Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the delegation registry so a human can pre-sanction MCP tool
authorization boxes ("Allow the chrome-devtools MCP server to run tool hover")
per (agent, server, tool), with the sentinel releasing only exact matches.

**Architecture:** One `delegations[]` list with a `kind` discriminator
(`command_prefix` default for legacy records, `mcp_tool` new). A fail-closed
whitespace-collapsed extractor parses the codex MCP box body; matching is
exact (server, tool) equality. `agent boxes` / `release-box` /
`_scan_release_delegated_boxes` (shared by `boxes watch` and
`run-loop --release-boxes`) all gain the second arm. Release invariants
unchanged: bare Enter on the pre-selected option only, every release audited.

**Tech Stack:** Python 3.12, stdlib only, pytest, conda env `agentdeck`.

**Spec:** `docs/superpowers/specs/2026-07-29-mcp-tool-delegation-scope-design.md`

**Verification ladder (run at each commit):** targeted tests →
`pytest tests/test_delegation_cli.py tests/test_contracts.py -q` →
`pytest tests/test_agent_cli.py tests/test_leader_cli.py -q` →
`python -m compileall src tests` → `git diff --check` → full `pytest tests/ -q`
before final commit. All commands inside `conda activate agentdeck`. No
`git push`, no Claude co-author trailer.

---

## Commit A — registry dual shape (Tasks 1–3)

### Task 1: `grant_delegation` writer accepts an MCP pair

**Files:**
- Modify: `src/agentdeck/state.py` (`grant_delegation`, near line 9083)
- Test: `tests/test_delegation_cli.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_delegation_cli.py`)

```python
def test_grant_delegation_writer_mcp_pair_and_mutual_exclusion(tmp_path, monkeypatch) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    store = StateStore(root)

    record = store.grant_delegation("coder", mcp_server="chrome-devtools", mcp_tool="hover")
    assert record["kind"] == "mcp_tool"
    assert record["mcp_server"] == "chrome-devtools"
    assert record["mcp_tool"] == "hover"
    assert record["prefix"] is None
    assert record["revoked_at"] is None

    # prefix 记录带显式 kind，且 mcp 字段为 null
    prefix_record = store.grant_delegation("coder", "node tests/")
    assert prefix_record["kind"] == "command_prefix"
    assert prefix_record["mcp_server"] is None
    assert prefix_record["mcp_tool"] is None

    # 重复活跃 (agent, server, tool) 拒绝零写
    try:
        store.grant_delegation("coder", mcp_server="chrome-devtools", mcp_tool="hover")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    assert len(store.load()["delegations"]) == 2

    # 同 server 异 tool 是新委托，不算重复
    second = store.grant_delegation("coder", mcp_server="chrome-devtools", mcp_tool="press_key")
    assert second["mcp_tool"] == "press_key"

    # 二选一：都给或都不给都拒绝
    for kwargs in (
        {"prefix": "node tests/x", "mcp_server": "s", "mcp_tool": "t"},
        {},
        {"mcp_server": "chrome-devtools"},
        {"mcp_tool": "hover"},
    ):
        try:
            store.grant_delegation("coder", **kwargs)
            raise AssertionError(f"expected ValueError for {kwargs}")
        except ValueError:
            pass
    assert len(store.load()["delegations"]) == 3
```

Note: `grant_delegation("coder", prefix="node tests/x", ...)` — the existing
positional `prefix` becomes keyword-compatible, so `**kwargs` with `prefix`
works.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_delegation_cli.py::test_grant_delegation_writer_mcp_pair_and_mutual_exclusion -q`
Expected: FAIL with `TypeError: grant_delegation() got an unexpected keyword argument 'mcp_server'`

- [ ] **Step 3: Implement** — replace the body of `StateStore.grant_delegation`:

```python
    def grant_delegation(
        self,
        agent_id: str,
        prefix: str | None = None,
        *,
        mcp_server: str | None = None,
        mcp_tool: str | None = None,
    ) -> dict[str, Any]:
        wants_prefix = prefix is not None
        wants_mcp = mcp_server is not None or mcp_tool is not None
        if wants_prefix == wants_mcp:
            raise ValueError(
                "delegation requires exactly one of prefix or (mcp_server, mcp_tool)"
            )
        if wants_mcp and (not mcp_server or not mcp_tool):
            raise ValueError("mcp delegation requires both mcp_server and mcp_tool")
        state = self.load()
        delegations = state.setdefault("delegations", [])
        for item in delegations:
            if item.get("revoked_at") or item.get("agent_id") != agent_id:
                continue
            if wants_prefix and item.get("prefix") == prefix:
                raise ValueError(f"delegation already active for {agent_id}: {prefix}")
            if wants_mcp and (
                item.get("mcp_server") == mcp_server
                and item.get("mcp_tool") == mcp_tool
            ):
                raise ValueError(
                    f"delegation already active for {agent_id}: {mcp_server}/{mcp_tool}"
                )
        record = {
            "delegation_id": new_id("dlg"),
            "agent_id": agent_id,
            "kind": "command_prefix" if wants_prefix else "mcp_tool",
            "prefix": prefix,
            "mcp_server": mcp_server,
            "mcp_tool": mcp_tool,
            "created_at": utc_now(),
            "revoked_at": None,
        }
        delegations.append(record)
        self.save(state)
        return dict(record)
```

Behavior note: the duplicate-active check for prefix keeps its exact prior
semantics (`agent_id` + `prefix` + not revoked); legacy records without
`kind` are never mistaken for MCP records because their `mcp_server` is
absent (`.get` → None ≠ non-empty server).

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_delegation_cli.py -q`
Expected: all pass (existing 15 + 1 new)

### Task 2: `delegation grant` CLI dual form + `delegation list` projection

**Files:**
- Modify: `src/agentdeck/cli.py` (`delegation_grant_command` ~10406,
  `delegation_list_command` ~10440, argparse wiring ~21170)
- Test: `tests/test_delegation_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_delegation_grant_mcp_form_and_mutual_exclusion(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)

    # 互斥：两种形态同时给 / 都不给 / MCP 对不完整 → 拒绝零写
    assert cli.main([
        "delegation", "grant", "--agent", "coder", "--prefix", "node tests/",
        "--mcp-server", "chrome-devtools", "--mcp-tool", "hover", "--confirm",
    ]) == 1
    assert "exactly one" in capsys.readouterr().err
    assert cli.main(["delegation", "grant", "--agent", "coder", "--confirm"]) == 1
    capsys.readouterr()
    assert cli.main([
        "delegation", "grant", "--agent", "coder", "--mcp-server", "chrome-devtools", "--confirm",
    ]) == 1
    capsys.readouterr()
    assert cli.main([
        "delegation", "grant", "--agent", "coder",
        "--mcp-server", "  ", "--mcp-tool", "hover", "--confirm",
    ]) == 1
    capsys.readouterr()
    assert StateStore(root).load().get("delegations", []) == []

    # MCP 形态 happy path：入账 + 审计
    assert cli.main([
        "delegation", "grant", "--agent", "coder",
        "--mcp-server", "chrome-devtools", "--mcp-tool", "hover", "--confirm",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "delegation_granted"
    assert payload["kind"] == "mcp_tool"
    assert payload["mcp_server"] == "chrome-devtools"
    assert payload["mcp_tool"] == "hover"
    assert payload["prefix"] is None
    assert '"event_type": "delegation_granted"' in _events_text(root)
    assert '"mcp_server": "chrome-devtools"' in _events_text(root)


def test_delegation_list_projects_kind_and_legacy_records(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    # 直接写一条旧形态记录（无 kind/mcp 字段）模拟既有数据
    store = StateStore(root)
    state = store.load()
    state.setdefault("delegations", []).append(
        {
            "delegation_id": "dlg_legacy",
            "agent_id": "coder",
            "prefix": "node tests/",
            "created_at": "2026-07-26T00:00:00+00:00",
            "revoked_at": None,
        }
    )
    store.save(state)
    cli.main([
        "delegation", "grant", "--agent", "coder",
        "--mcp-server", "chrome-devtools", "--mcp-tool", "press_key", "--confirm",
    ])
    capsys.readouterr()

    assert cli.main(["delegation", "list"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 2
    legacy = payload["items"][0]
    assert legacy["kind"] == "command_prefix"
    assert legacy["mcp_server"] is None
    assert legacy["mcp_tool"] is None
    mcp = payload["items"][1]
    assert mcp["kind"] == "mcp_tool"
    assert mcp["prefix"] is None
    assert mcp["active"] is True
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_delegation_cli.py::test_delegation_grant_mcp_form_and_mutual_exclusion tests/test_delegation_cli.py::test_delegation_list_projects_kind_and_legacy_records -q`
Expected: FAIL (`unrecognized arguments: --mcp-server` from argparse; list
missing `kind`)

- [ ] **Step 3: Implement**

Argparse wiring (~line 21170) — `--prefix` becomes optional, add the pair:

```python
    delegation_grant.add_argument("--prefix", help="Command prefix the delegation covers")
    delegation_grant.add_argument("--mcp-server", dest="mcp_server", help="MCP server name the delegation covers")
    delegation_grant.add_argument("--mcp-tool", dest="mcp_tool", help="MCP tool name the delegation covers")
```

(remove `required=True` from `--prefix`).

`delegation_grant_command` — replace the prefix validation block with:

```python
    prefix = args.prefix.strip() if args.prefix is not None else None
    mcp_server = args.mcp_server.strip() if args.mcp_server is not None else None
    mcp_tool = args.mcp_tool.strip() if args.mcp_tool is not None else None
    wants_prefix = args.prefix is not None
    wants_mcp = args.mcp_server is not None or args.mcp_tool is not None
    if wants_prefix == wants_mcp:
        print(
            "delegation grant requires exactly one of --prefix or --mcp-server/--mcp-tool",
            file=sys.stderr,
        )
        return 1
    if wants_prefix and not prefix:
        print("delegation prefix must not be empty", file=sys.stderr)
        return 1
    if wants_mcp and (not mcp_server or not mcp_tool):
        print(
            "mcp delegation requires non-empty --mcp-server and --mcp-tool",
            file=sys.stderr,
        )
        return 1
```

then call `store.grant_delegation(args.agent, prefix, mcp_server=mcp_server,
mcp_tool=mcp_tool)` and extend the `delegation_granted` event payload with
`"kind": record["kind"], "mcp_server": record["mcp_server"], "mcp_tool":
record["mcp_tool"]` (keep existing keys).

`delegation_list_command` — replace the items comprehension:

```python
    items = [
        {
            **item,
            "kind": item.get("kind") or "command_prefix",
            "mcp_server": item.get("mcp_server"),
            "mcp_tool": item.get("mcp_tool"),
            "active": not item.get("revoked_at"),
        }
        for item in state.get("delegations", [])
        if isinstance(item, dict)
    ]
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_delegation_cli.py -q`
Expected: all pass

### Task 3: contract fields, examples, payload template, schema doc, HISTORY

**Files:**
- Modify: `src/agentdeck/contracts.py` (~903–1040)
- Modify: `docs/contracts/delegation-schema.md` (Registry Shapes section)
- Modify: `HISTORY.md` (new top entry)
- Test: `tests/test_delegation_cli.py`

- [ ] **Step 1: Write the failing test**

```python
def test_delegation_contract_exposes_mcp_fields(capsys) -> None:
    assert cli.main(["contract", "delegation", "--example"]) == 0
    payload = json.loads(capsys.readouterr().out)
    for field in ("kind", "mcp_server", "mcp_tool"):
        assert field in payload["delegation_item_fields"]
    assert "mcp_grant_command_template" in payload
    assert "--mcp-server <server>" in payload["mcp_grant_command_template"]
    kinds = {item["kind"] for item in payload["example"]["list"]["items"]}
    assert kinds == {"command_prefix", "mcp_tool"}
```

Check the actual example-embedding key first: `grep -n '"example"' src/agentdeck/contracts.py`
around `delegation_contract_response` and mirror its real shape in the
assertion (adjust `payload["example"]["list"]` to the actual structure).

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_delegation_cli.py::test_delegation_contract_exposes_mcp_fields -q`
Expected: FAIL (missing fields)

- [ ] **Step 3: Implement** in `src/agentdeck/contracts.py`:

`DELEGATION_ITEM_FIELDS` gains `"kind", "mcp_server", "mcp_tool"` (after
`"agent_id"`). `delegation_list_example()` items: add
`"kind": "command_prefix", "mcp_server": None, "mcp_tool": None` to the
existing item and append a second item:

```python
            {
                "delegation_id": "dlg_mcp_example",
                "agent_id": "planner",
                "kind": "mcp_tool",
                "prefix": None,
                "mcp_server": "chrome-devtools",
                "mcp_tool": "hover",
                "created_at": "2026-07-29T00:00:00+00:00",
                "revoked_at": None,
                "active": True,
            }
```

(update `"count": 2`). `delegation_contract_payload()` adds:

```python
        "mcp_grant_command_template": "agentdeck delegation grant --agent <agent_id> --mcp-server <server> --mcp-tool <tool> --confirm",
```

`docs/contracts/delegation-schema.md` Registry Shapes section: document the
`kind` discriminator, the MCP grant form, legacy `kind`-less records reading
as `command_prefix`, and per-kind duplicate refusal.

`HISTORY.md`: add a top entry (Type: feat) titled "MCP tool delegation scope:
registry dual shape" following the existing template, covering Tasks 1–3 and
naming the spec.

- [ ] **Step 4: Verify and commit**

Run: `pytest tests/test_delegation_cli.py tests/test_contracts.py -q` then
`pytest tests/test_agent_cli.py tests/test_leader_cli.py -q` then
`python -m compileall src tests` then `git diff --check`.
Expected: all pass.

```bash
git add src/agentdeck/state.py src/agentdeck/cli.py src/agentdeck/contracts.py \
  tests/test_delegation_cli.py docs/contracts/delegation-schema.md HISTORY.md
git commit -m "feat: add mcp_tool kind to the delegation registry"
```

---

## Commit B — MCP box detection, matching, release surfaces (Tasks 4–6)

### Task 4: fail-closed MCP box extractor

**Files:**
- Modify: `src/agentdeck/cli.py` (new helper next to `_extract_auth_box_command` ~9820)
- Test: `tests/test_delegation_cli.py`

- [ ] **Step 1: Add fixtures + failing tests**

```python
CODEX_MCP_TOOL_BOX = (
    "  Allow the chrome-devtools MCP server to run tool hover?\n"
    "› 1. Yes, proceed (y)\n"
    "  2. Yes, and don't ask again this session\n"
    "  3. No, and tell Codex what to do differently (esc)\n"
    "  Press enter to confirm or esc to cancel\n"
)

CODEX_MCP_TOOL_BOX_FOLDED = (
    "  Allow the chrome-dev\n"
    "  tools MCP server to run tool\n"
    "  press_key?\n"
    "› 1. Yes, proceed (y)\n"
    "  Press enter to confirm or esc to cancel\n"
)


def test_extract_mcp_tool_box_fail_closed() -> None:
    assert cli._extract_mcp_tool_box(CODEX_MCP_TOOL_BOX) == ("chrome-devtools", "hover")
    # token 中间折行:全空白折叠还原
    assert cli._extract_mcp_tool_box(CODEX_MCP_TOOL_BOX_FOLDED) == (
        "chrome-devtools",
        "press_key",
    )
    # 非框文本 / 命令框 / 句尾缺 ? :一律 None(fail-closed)
    assert cli._extract_mcp_tool_box("worker is thinking...\n") is None
    assert cli._extract_mcp_tool_box(CODEX_AUTH_BOX) is None
    assert cli._extract_mcp_tool_box(
        "  Allow the chrome-devtools MCP server to run tool hover\n"
        "  Reason: replay\n"
    ) is None
    # 命令框提取器对 MCP 框返回 None(两类互不干扰)
    assert cli._extract_auth_box_command(CODEX_MCP_TOOL_BOX) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_delegation_cli.py::test_extract_mcp_tool_box_fail_closed -q`
Expected: FAIL with `AttributeError: ... has no attribute '_extract_mcp_tool_box'`

- [ ] **Step 3: Implement** — add after `_extract_auth_box_command`:

```python
_MCP_TOOL_BOX_PATTERN = re.compile(
    r"Allowthe(?P<server>[A-Za-z0-9_\-]+)MCPservertoruntool(?P<tool>[A-Za-z0-9_\-]+)\?"
)


def _extract_mcp_tool_box(output: str) -> tuple[str, str] | None:
    # 第五类框（round 11 live）：codex MCP tool 授权框正文
    # "Allow the <server> MCP server to run tool <tool>?"。TUI 折行可发生在
    # 任意位置（含 token 中间），折行点空格信息已丢失——与折叠命令框同策略：
    # 尾窗全空白折叠后按框自身句式匹配，句尾 `?` 是硬边界。
    # 任何解析失败返回 None（fail-closed：未命中绝不代按）。
    tail = "\n".join(output.splitlines()[-_WAITING_FOR_INPUT_TAIL_LINES:])
    collapsed = "".join(tail.split())
    match = _MCP_TOOL_BOX_PATTERN.search(collapsed)
    if match is None:
        return None
    return match.group("server"), match.group("tool")
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_delegation_cli.py -q`
Expected: all pass

### Task 5: `_match_active_delegation` MCP arm

**Files:**
- Modify: `src/agentdeck/cli.py` (`_match_active_delegation` ~9860)
- Test: `tests/test_delegation_cli.py`

- [ ] **Step 1: Write the failing test**

```python
def test_match_active_delegation_mcp_arm() -> None:
    state = {
        "delegations": [
            {
                "delegation_id": "dlg_prefix",
                "agent_id": "coder",
                "prefix": "node tests/",
                "revoked_at": None,
            },
            {
                "delegation_id": "dlg_mcp",
                "agent_id": "planner",
                "kind": "mcp_tool",
                "prefix": None,
                "mcp_server": "chrome-devtools",
                "mcp_tool": "hover",
                "revoked_at": None,
            },
        ]
    }
    hit = cli._match_active_delegation(state, "planner", None, ("chrome-devtools", "hover"))
    assert hit["delegation_id"] == "dlg_mcp"
    # 同 server 异 tool / 同 tool 异 server / 异 agent / prefix 记录:都不命中
    assert cli._match_active_delegation(state, "planner", None, ("chrome-devtools", "press_key")) is None
    assert cli._match_active_delegation(state, "planner", None, ("other-server", "hover")) is None
    assert cli._match_active_delegation(state, "coder", None, ("chrome-devtools", "hover")) is None
    # revoked 不命中
    state["delegations"][1]["revoked_at"] = "2026-07-29T00:00:00+00:00"
    assert cli._match_active_delegation(state, "planner", None, ("chrome-devtools", "hover")) is None
    # 旧签名（无 mcp_box）prefix 路径不变
    assert cli._match_active_delegation(state, "coder", "node tests/x.mjs")["delegation_id"] == "dlg_prefix"
    # MCP 框绝不落入 prefix 匹配
    assert cli._match_active_delegation(state, "coder", None, None) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_delegation_cli.py::test_match_active_delegation_mcp_arm -q`
Expected: FAIL with `TypeError: _match_active_delegation() takes 3 positional arguments but 4 were given`

- [ ] **Step 3: Implement** — replace `_match_active_delegation`:

```python
def _match_active_delegation(
    state: dict[str, object],
    agent_id: str,
    command: str | None,
    mcp_box: tuple[str, str] | None = None,
) -> dict[str, object] | None:
    if not command and mcp_box is None:
        return None
    for item in state.get("delegations", []):
        if not isinstance(item, dict) or item.get("revoked_at"):
            continue
        if item.get("agent_id") != agent_id:
            continue
        kind = item.get("kind") or "command_prefix"
        if kind == "mcp_tool":
            if mcp_box is None:
                continue
            server, tool = mcp_box
            if (
                "".join(str(item.get("mcp_server") or "").split()) == server
                and "".join(str(item.get("mcp_tool") or "").split()) == tool
            ):
                return item
            continue
        if not command:
            continue
        prefix = str(item.get("prefix") or "")
        if not prefix:
            continue
        if command.startswith(prefix):
            return item
        # 折叠框回退提取会丢失折行点空格：空白折叠后再比一次，
        # 使 "git add" 这类含空格前缀在任一断行形态下都能命中。
        collapsed_command = "".join(command.split())
        collapsed_prefix = "".join(prefix.split())
        if collapsed_prefix and collapsed_command.startswith(collapsed_prefix):
            return item
    return None
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_delegation_cli.py -q`
Expected: all pass

### Task 6: boxes/release/watch surfaces + boxes contract fields + docs + HISTORY

**Files:**
- Modify: `src/agentdeck/cli.py` (`agent_boxes_command` ~9884,
  `agent_release_box_command` ~9913, `_scan_release_delegated_boxes` ~9965)
- Modify: `src/agentdeck/contracts.py` (`DELEGATION_BOXES_RESPONSE_FIELDS`,
  `delegation_boxes_example`, `boxes_watch_example`)
- Modify: `docs/contracts/delegation-schema.md` (Box Shapes + Boundaries)
- Modify: `CLAUDE.md` (delegation contract bullet)
- Modify: `HISTORY.md` (new top entry)
- Test: `tests/test_delegation_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_agent_boxes_reports_mcp_tool_box(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_coder(root)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    fake.output = CODEX_MCP_TOOL_BOX

    # 未委托:检测到 MCP 框但 delegated=False,零写零输入
    before = StateStore(root).load()
    assert cli.main(["agent", "boxes", "--agent", "coder"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["box_present"] is True
    assert payload["box_kind"] == "mcp_tool"
    assert payload["command"] is None
    assert payload["mcp_server"] == "chrome-devtools"
    assert payload["mcp_tool"] == "hover"
    assert payload["delegated"] is False
    assert StateStore(root).load() == before
    assert fake.sent == []

    # grant 后命中;命令框路径 box_kind=command 回归
    cli.main([
        "delegation", "grant", "--agent", "coder",
        "--mcp-server", "chrome-devtools", "--mcp-tool", "hover", "--confirm",
    ])
    granted = json.loads(capsys.readouterr().out)
    assert cli.main(["agent", "boxes", "--agent", "coder"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["delegated"] is True
    assert payload["delegation_id"] == granted["delegation_id"]
    fake.output = CODEX_AUTH_BOX
    assert cli.main(["agent", "boxes", "--agent", "coder"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["box_kind"] == "command"
    assert payload["mcp_server"] is None


def test_release_box_releases_delegated_mcp_box_with_audit(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_coder(root)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    fake.output = CODEX_MCP_TOOL_BOX_FOLDED

    # 未委托拒绝,零输入
    assert cli.main(["agent", "release-box", "--agent", "coder", "--confirm"]) == 1
    assert "no active delegation" in capsys.readouterr().err
    assert fake.sent == []

    cli.main([
        "delegation", "grant", "--agent", "coder",
        "--mcp-server", "chrome-devtools", "--mcp-tool", "press_key", "--confirm",
    ])
    capsys.readouterr()
    assert cli.main(["agent", "release-box", "--agent", "coder", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "auth_box_released"
    assert payload["box_kind"] == "mcp_tool"
    assert payload["mcp_server"] == "chrome-devtools"
    assert payload["mcp_tool"] == "press_key"
    assert fake.sent == [("%50", "")]
    events = _events_text(root)
    assert '"event_type": "auth_box_released"' in events
    assert '"mcp_tool": "press_key"' in events


def test_boxes_watch_releases_delegated_mcp_box(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_coder(root)
    fake = DismissingTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    fake.output = CODEX_MCP_TOOL_BOX
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)
    root_config = root / ".agentdeck" / "config.toml"
    text = root_config.read_text(encoding="utf-8").replace(
        'approval_mode = "ask"', 'approval_mode = "autonomous"'
    )
    root_config.write_text(text, encoding="utf-8")
    cli.main([
        "delegation", "grant", "--agent", "coder",
        "--mcp-server", "chrome-devtools", "--mcp-tool", "hover", "--confirm",
    ])
    capsys.readouterr()

    assert cli.main(["boxes", "watch", "--confirm", "--iterations", "1", "--interval", "0"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["released_count"] == 1
    released = payload["released"][0]
    assert released["box_kind"] == "mcp_tool"
    assert released["mcp_server"] == "chrome-devtools"
    assert released["mcp_tool"] == "hover"
```

Before writing, mirror the setup of the existing
`test_boxes_watch_releases_delegated_box_and_stops_at_bound` (lines ~260) for
the autonomous-mode config edit and `DismissingTmuxBackend` — reuse its exact
idioms if they differ from the sketch above.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_delegation_cli.py -q -k "mcp"`
Expected: new tests FAIL on missing `box_kind` / MCP release refusal

- [ ] **Step 3: Implement**

In all three call sites, replace the extraction+match pair with:

```python
    command = _extract_auth_box_command(output)
    mcp_box = _extract_mcp_tool_box(output) if command is None else None
    match = _match_active_delegation(store.load(), <agent_id>, command, mcp_box)
    box_kind = "command" if command else ("mcp_tool" if mcp_box else None)
```

(`agent_boxes_command` keeps its `if waiting_hint else None` gating: compute
`command`/`mcp_box` only when `waiting_hint` is not None.)

`agent_boxes_command` payload adds:

```python
            "box_kind": box_kind,
            "mcp_server": mcp_box[0] if mcp_box else None,
            "mcp_tool": mcp_box[1] if mcp_box else None,
```

`agent_release_box_command`: refusal message becomes

```python
        described = command or (f"mcp:{mcp_box[0]}/{mcp_box[1]}" if mcp_box else None)
        print(
            f"no active delegation covers this box for {args.agent}: {described or '(command not detected)'}",
            file=sys.stderr,
        )
```

and both the `auth_box_released` event payload and the success JSON gain
`"box_kind": box_kind, "mcp_server": mcp_box[0] if mcp_box else None,
"mcp_tool": mcp_box[1] if mcp_box else None` (keep `prefix`/`command` keys).

`_scan_release_delegated_boxes`: `skipped` items gain `"box_kind": box_kind,
"mcp_server": ..., "mcp_tool": ...` (same expressions); `released` items and
the event payload gain the same three keys.

`src/agentdeck/contracts.py`: `DELEGATION_BOXES_RESPONSE_FIELDS` gains
`"box_kind", "mcp_server", "mcp_tool"` (after `"command"`);
`delegation_boxes_example()` adds `"box_kind": "command", "mcp_server": None,
"mcp_tool": None`; `boxes_watch_example()` released item adds the same three
keys with command-kind values.

Docs: `docs/contracts/delegation-schema.md` Box Shapes documents the MCP box
sentence, fail-closed extraction, exact (server, tool) matching, and the new
response/audit fields; Boundaries adds the guidance sentence: grant MCP
delegations only for read-only-natured tools (hover/press_key/screenshot
class), never for page-mutating tools (navigate/fill/evaluate_script class) —
AgentDeck cannot verify a tool's nature, the human owns that judgment at
grant time. `CLAUDE.md` delegation bullet: extend with the MCP grant form,
kind discriminator, fail-closed extractor, and unchanged release invariants.
`HISTORY.md`: top entry (Type: feat) "MCP tool delegation scope: box
detection and release" citing round 11 finding #3 and the spec.

- [ ] **Step 4: Full verification ladder**

Run in order, expect all green:
1. `pytest tests/test_delegation_cli.py -q`
2. `pytest tests/test_contracts.py tests/test_agent_cli.py tests/test_leader_cli.py -q`
3. `python -m compileall src tests`
4. `git diff --check`
5. `pytest tests/ -q` (full suite; expected ~4725 passed, 3 skipped — the
   three opt-in real nodes)

- [ ] **Step 5: Commit**

```bash
git add src/agentdeck/cli.py src/agentdeck/contracts.py tests/test_delegation_cli.py \
  docs/contracts/delegation-schema.md CLAUDE.md HISTORY.md
git commit -m "feat: release delegated MCP tool authorization boxes"
```

---

## Post-plan notes

- The handoff doc `docs/handoff/current-development-state.md` top section
  should be updated after Commit B (move "MCP tool 委托 scope" from 拍板项 to
  landed, note the live-validation follow-up) — fold into Commit B or a
  trailing docs commit per the session's judgment.
- Live validation: next Line 1 round should pre-grant
  (planner, chrome-devtools, hover/press_key) and observe automatic release —
  requires user present; not part of this plan.
- Explicit non-goals (future forks needing new decisions): whole-server
  wildcard delegation; mapping to codex-native session-level allow (option 2);
  any non-codex box wording.
