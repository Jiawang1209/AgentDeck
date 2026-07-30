# Delegation Match Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Shell-wrapped, already-sanctioned read-only commands (env prefix,
loop wrappers, multi-command chains) match `command_prefix` delegations via
a fail-closed split-and-cover normalizer, removing round 12's manual
release points.

**Architecture:** New pure module `src/agentdeck/delegation_match.py`
(hard-reject scan → quote-aware top-level splitter → per-segment
control/redirect/env stripping → fixed glue allowlist → every-segment-covered
matching). `cli.py::_match_active_delegation` stays byte-identical; a new
wrapper `_match_delegation_with_provenance` adds the composite third arm and
threads `match_kind`/`matched_segments` provenance into the three box
surfaces and the `auth_box_released` audit event.

**Tech Stack:** Python 3.12 stdlib, pytest, conda env `agentdeck`.

**Spec:** `docs/superpowers/specs/2026-07-30-delegation-match-normalization-design.md`

**Discipline:** All commands via `conda run -n agentdeck …`. Strict TDD.
No `git push`, no co-author trailer. HISTORY.md entry in the same commit as
each slice. Two commits total (Task 1 = commit A; Tasks 2–3 = commit B).

---

## Task 1: pure normalizer module (commit A)

**Files:**
- Create: `src/agentdeck/delegation_match.py`
- Create: `tests/test_delegation_match.py`
- Modify: `HISTORY.md` (top entry)

- [ ] **Step 1: Write the failing tests** — create `tests/test_delegation_match.py`:

```python
from __future__ import annotations

from agentdeck.delegation_match import CompositeMatch, MatchedSegment, normalize_match

PREFIXES = ["node tests/", "git add", "git commit", "git diff", "git status"]

# round 12 逐字样本 ①:env 前缀赋值
ENV_SAMPLE = "REPRODUCE_UNCONTROLLED_BOOTSTRAP=1 node tests/focus-carousel-tab-order.mjs"

# round 12 逐字样本 ②:for 循环包装(重定向落 /tmp、条件 tail、exit)
LOOP_SAMPLE = (
    "for run_id in 1 2 3 4 5; "
    "do node tests/focus-carousel-tab-order.mjs > /tmp/msg-target-${run_id}.log 2>&1; "
    "run_code=$?; "
    'echo "target_run_${run_id}_exit=${run_code}"; '
    "if [ ${run_code} -ne 0 ]; "
    "then tail -80 /tmp/msg-target-${run_id}.log; "
    "exit ${run_code}; "
    "fi; done"
)

# round 12 逐字样本 ③:多命令链(node --check 段无对应委托)
CHAIN_SAMPLE = (
    "node tests/focus-carousel-tab-order.mjs > /tmp/final-focus.log 2>&1; "
    "focus_code=$?; "
    'echo "final_focus_exit=${focus_code}"; '
    "node tests/back-to-top.mjs > /tmp/final-b2t.log 2>&1; "
    "node --check tests/focus-carousel-tab-order.mjs; "
    "git diff --check"
)


def test_env_prefix_sample_matches() -> None:
    result = normalize_match(ENV_SAMPLE, PREFIXES)
    assert isinstance(result, CompositeMatch)
    assert len(result.segments) == 1
    assert result.segments[0].via == "node tests/"
    assert result.segments[0].segment == ENV_SAMPLE


def test_loop_sample_matches_with_glue_provenance() -> None:
    result = normalize_match(LOOP_SAMPLE, PREFIXES)
    assert result is not None
    vias = [s.via for s in result.segments]
    # 9 段:for 头/do node…/赋值/echo/if [ ]/then tail/exit/fi/done
    assert len(vias) == 9
    assert vias.count("node tests/") == 1
    assert all(v in ("node tests/", "glue") for v in vias)


def test_chain_sample_rejected_without_node_check_prefix() -> None:
    # node --check 段不命中任何委托 → 整体 None(绝不部分放行)
    assert normalize_match(CHAIN_SAMPLE, PREFIXES) is None
    # 人类显式补 grant node --check tests/ 前缀后整链命中
    widened = PREFIXES + ["node --check tests/"]
    result = normalize_match(CHAIN_SAMPLE, widened)
    assert result is not None
    vias = [s.via for s in result.segments]
    assert "node --check tests/" in vias
    assert "git diff" in vias


def test_dangerous_chain_rejected() -> None:
    assert normalize_match("node tests/x.mjs; rm -rf /", PREFIXES) is None


def test_command_substitution_and_eval_rejected() -> None:
    assert normalize_match("node tests/$(whoami).mjs", PREFIXES) is None
    assert normalize_match("node tests/`id`.mjs", PREFIXES) is None
    assert normalize_match("eval node tests/x.mjs", PREFIXES) is None
    assert normalize_match("node tests/x.mjs; source /tmp/env.sh", PREFIXES) is None
    assert normalize_match("node tests/x.mjs <(cat /etc/passwd)", PREFIXES) is None
    assert normalize_match("node tests/x.mjs << EOF", PREFIXES) is None


def test_input_redirect_and_background_rejected() -> None:
    assert normalize_match("node tests/x.mjs < /etc/passwd", PREFIXES) is None
    assert normalize_match("node tests/x.mjs & node tests/y.mjs", PREFIXES) is None


def test_redirect_targets_must_be_tmp_confined() -> None:
    assert normalize_match("node tests/x.mjs > /etc/evil", PREFIXES) is None
    assert normalize_match("node tests/x.mjs > /tmp/../etc/evil", PREFIXES) is None
    assert normalize_match("node tests/x.mjs > /tmp/ok.log", PREFIXES) is not None
    assert normalize_match("node tests/x.mjs >> /tmp/ok.log 2>&1", PREFIXES) is not None


def test_tail_glue_is_tmp_confined() -> None:
    assert normalize_match("node tests/x.mjs; tail -5 /tmp/x.log", PREFIXES) is not None
    assert normalize_match("node tests/x.mjs; tail -5 /tmp/../etc/passwd", PREFIXES) is None
    assert normalize_match("node tests/x.mjs; tail -5 /etc/passwd", PREFIXES) is None


def test_glue_alone_never_matches() -> None:
    assert normalize_match('echo "hi"; exit 0', PREFIXES) is None
    assert normalize_match("x=1", PREFIXES) is None


def test_quoted_separator_does_not_split() -> None:
    result = normalize_match('node tests/x.mjs; echo "a; rm -rf /"', PREFIXES)
    assert result is not None
    assert len(result.segments) == 2


def test_unbalanced_quote_rejected() -> None:
    assert normalize_match('node tests/x.mjs; echo "broken', PREFIXES) is None


def test_pipe_segments_require_coverage() -> None:
    # 管道两侧都是独立段:ps/rg 均不在委托或胶水内 → 拒
    assert normalize_match("ps -axo pid= | rg agentdeck", PREFIXES) is None


def test_empty_and_no_prefixes_rejected() -> None:
    assert normalize_match("", PREFIXES) is None
    assert normalize_match("   ", PREFIXES) is None
    assert normalize_match("node tests/x.mjs", []) is None


def test_matched_segment_shape() -> None:
    result = normalize_match("node tests/x.mjs", PREFIXES)
    assert result == CompositeMatch((MatchedSegment("node tests/x.mjs", "node tests/"),))
```

- [ ] **Step 2: Run to verify failure**

Run: `conda run -n agentdeck pytest tests/test_delegation_match.py -q`
Expected: FAIL at import (`ModuleNotFoundError: No module named 'agentdeck.delegation_match'`)

- [ ] **Step 3: Implement** — create `src/agentdeck/delegation_match.py`:

```python
"""Conservative shell normalization for command-prefix delegation matching.

round 12 live 发现 #3:env 前缀赋值、for 循环包装、多命令链会逃出
`command_prefix` 委托的 startswith 匹配。本模块把复合命令保守拆段,
要求每一段要么命中该 agent 的某条活跃委托前缀,要么属于内置固定胶水
白名单,且至少一段命中真实委托。任何解析不了的形态一律返回 None
(fail-closed:调用方回落到现行人工路径)。纯函数,不读 state、不碰
runtime。
"""
from __future__ import annotations

import re
from typing import NamedTuple, Sequence


class MatchedSegment(NamedTuple):
    segment: str
    via: str  # 命中的委托前缀原文,或字面量 "glue"


class CompositeMatch(NamedTuple):
    segments: tuple[MatchedSegment, ...]


# 命令替换/进程替换/heredoc:出现即整体拒绝(原文扫描,先于拆段)
_HARD_REJECT_SUBSTRINGS = ("$(", "`", "<(", ">(", "<<")
_CONTROL_PREFIX_WORDS = ("do", "then", "else")
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S+$")
_GLUE_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S*$")
_SIMPLE_WORD = re.compile(r"^(?:[A-Za-z0-9._\-]+|\$\{[A-Za-z_][A-Za-z0-9_]*\})$")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_REDIRECT_TOKEN = re.compile(r"^(?:2?>{1,2})(.*)$")
_TMP_TARGET = re.compile(r"^/tmp/\S+$")


def _split_top_level(command: str) -> list[str] | None:
    """引号感知的顶层拆段:在 ;、&&、||、|、换行处切分。

    不配对引号、单 &(后台执行)、顶层 <(输入重定向)→ None。
    引号内的分隔符不拆;反斜杠转义原样吞并下一字符。
    """
    segments: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i, n = 0, len(command)
    while i < n:
        ch = command[i]
        if ch == "\\":
            buf.append(command[i : i + 2])
            i += 2
            continue
        if quote is not None:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "\n":
            segments.append("".join(buf))
            buf = []
            i += 1
            continue
        if command.startswith("&&", i) or command.startswith("||", i):
            segments.append("".join(buf))
            buf = []
            i += 2
            continue
        if ch == "&":
            if i > 0 and command[i - 1] == ">":
                buf.append(ch)  # 2>&1 的 >& 形态
                i += 1
                continue
            return None
        if ch == "<":
            return None
        if ch in (";", "|"):
            segments.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    if quote is not None:
        return None
    segments.append("".join(buf))
    return [seg.strip() for seg in segments if seg.strip()]


def _strip_control_prefix(segment: str) -> str:
    tokens = segment.split()
    while tokens and tokens[0] in _CONTROL_PREFIX_WORDS:
        tokens = tokens[1:]
    return " ".join(tokens)


def _strip_redirects(tokens: list[str]) -> list[str] | None:
    """剥离并校验重定向:仅允许 2>&1 与目标为 /tmp/ 下(无 ..)的 >/>>。"""
    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "2>&1":
            i += 1
            continue
        m = _REDIRECT_TOKEN.match(tok)
        if m is not None:
            target = m.group(1)
            if not target:
                if i + 1 >= len(tokens):
                    return None
                target = tokens[i + 1]
                i += 2
            else:
                i += 1
            if _TMP_TARGET.match(target) is None or ".." in target:
                return None
            continue
        out.append(tok)
        i += 1
    return out


def _strip_env_assignments(tokens: list[str]) -> list[str] | None:
    i = 0
    while i < len(tokens) and _ENV_ASSIGNMENT.match(tokens[i]):
        value = tokens[i].split("=", 1)[1]
        if value[0] in "'\"":
            return None  # 带引号 value 无法安全按空白切 token:整体拒
        i += 1
    return tokens[i:]


def _is_glue(tokens: list[str]) -> bool:
    """内置固定胶水白名单 v1(不可配置;扩名单=显式改码+过测试)。"""
    if not tokens:
        return True
    head = tokens[0]
    if len(tokens) == 1 and head in ("done", "fi"):
        return True
    if len(tokens) == 1 and _GLUE_ASSIGNMENT.match(head):
        return True
    if head in ("echo", "true", "test"):
        return True
    if head == "exit":
        return len(tokens) <= 2
    if head == "[":
        return tokens[-1] == "]"
    if head == "if":
        return len(tokens) >= 3 and tokens[1] == "[" and tokens[-1] == "]"
    if head == "for":
        return (
            len(tokens) >= 3
            and _NAME.match(tokens[1]) is not None
            and tokens[2] == "in"
            and all(_SIMPLE_WORD.match(tok) for tok in tokens[3:])
        )
    if head in ("tail", "head"):
        for tok in tokens[1:]:
            if tok.startswith("-"):
                continue
            if not tok.startswith("/tmp/") or ".." in tok:
                return False
        return True
    return False


def normalize_match(
    command: str, prefixes: Sequence[str]
) -> CompositeMatch | None:
    """拆段+逐段覆盖匹配;任何解析失败返回 None(fail-closed)。"""
    if not command or not command.strip():
        return None
    active = [prefix for prefix in prefixes if prefix]
    if not active:
        return None
    for marker in _HARD_REJECT_SUBSTRINGS:
        if marker in command:
            return None
    raw_segments = _split_top_level(command)
    if not raw_segments:
        return None
    matched: list[MatchedSegment] = []
    covered_any = False
    for raw in raw_segments:
        stripped = _strip_control_prefix(raw)
        if not stripped:
            matched.append(MatchedSegment(raw, "glue"))
            continue
        tokens = stripped.split()
        if tokens[0] in ("eval", "source"):
            return None
        after_redirects = _strip_redirects(tokens)
        if after_redirects is None:
            return None
        after_env = _strip_env_assignments(after_redirects)
        if after_env is None:
            return None
        if _is_glue(after_env):
            matched.append(MatchedSegment(raw, "glue"))
            continue
        rest = " ".join(after_env)
        via = next((prefix for prefix in active if rest.startswith(prefix)), None)
        if via is None:
            return None
        covered_any = True
        matched.append(MatchedSegment(raw, via))
    if not covered_any:
        return None
    return CompositeMatch(tuple(matched))
```

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n agentdeck pytest tests/test_delegation_match.py -q`
Expected: all pass (14 tests). If `test_loop_sample_matches_with_glue_provenance`
fails on segment count, print the actual segments and verify against the
LOOP_SAMPLE split by hand before touching the module — the test locks the
spec's walkthrough, not the other way around.

- [ ] **Step 5: HISTORY + commit**

Add a top entry to `HISTORY.md` under `## 2026-07-30` (Type: feat, title
"Add conservative shell normalization module for delegation matching",
following the neighbors' template; Verification = the pytest command and
count). Then:

```bash
git add src/agentdeck/delegation_match.py tests/test_delegation_match.py HISTORY.md
git commit -m "feat: add conservative shell normalization for delegation matching"
```

---

## Task 2: matcher third arm + provenance threading

**Files:**
- Modify: `src/agentdeck/cli.py` (add `_match_delegation_with_provenance`
  right after `_match_active_delegation` ~line 9940; rewire
  `agent_boxes_command`, `agent_release_box_command`,
  `_scan_release_delegated_boxes`)
- Test: `tests/test_delegation_cli.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_delegation_cli.py`;
  reuse the file's existing `prepare_project`/`bind_coder`/`FakeTmuxBackend`
  idioms):

```python
CODEX_AUTH_BOX_LOOP = (
    "  Would you like to run the following command?\n"
    "  Environment: local\n"
    "  $ for run_id in 1 2 3; do node tests/focus-carousel-tab-order.mjs > "
    '/tmp/r12-${run_id}.log 2>&1; run_code=$?; echo "exit=${run_code}"; '
    "if [ ${run_code} -ne 0 ]; then tail -80 /tmp/r12-${run_id}.log; "
    "exit ${run_code}; fi; done\n"
    "› 1. Yes, proceed (y)\n"
    "  Press enter to confirm or esc to cancel\n"
)

CODEX_AUTH_BOX_ENV = (
    "  Would you like to run the following command?\n"
    "  $ REPRODUCE_UNCONTROLLED_BOOTSTRAP=1 node tests/focus-carousel-tab-order.mjs\n"
    "› 1. Yes, proceed (y)\n"
    "  Press enter to confirm or esc to cancel\n"
)

CODEX_AUTH_BOX_MIXED_DANGER = (
    "  Would you like to run the following command?\n"
    "  $ node tests/focus-carousel-tab-order.mjs; rm -rf /tmp/../etc\n"
    "› 1. Yes, proceed (y)\n"
    "  Press enter to confirm or esc to cancel\n"
)


def test_agent_boxes_reports_composite_match(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_coder(root)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    cli.main(["delegation", "grant", "--agent", "coder", "--prefix", "node tests/", "--confirm"])
    capsys.readouterr()

    fake.output = CODEX_AUTH_BOX_LOOP
    assert cli.main(["agent", "boxes", "--agent", "coder"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["delegated"] is True
    assert payload["match_kind"] == "composite"
    segs = payload["matched_segments"]
    assert isinstance(segs, list) and len(segs) == 9
    assert any(s["via"] == "node tests/" for s in segs)
    assert all(s["via"] in ("node tests/", "glue") for s in segs)

    # 平前缀命中:match_kind=prefix,matched_segments=None
    fake.output = CODEX_AUTH_BOX
    assert cli.main(["agent", "boxes", "--agent", "coder"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["match_kind"] == "prefix"
    assert payload["matched_segments"] is None

    # 危险混合链:整体不匹配,零输入
    fake.output = CODEX_AUTH_BOX_MIXED_DANGER
    assert cli.main(["agent", "boxes", "--agent", "coder"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["delegated"] is False
    assert payload["match_kind"] is None
    assert fake.sent == []


def test_release_box_composite_release_with_audit(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_coder(root)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    fake.output = CODEX_AUTH_BOX_ENV

    # 未 grant:env 包装框拒绝
    assert cli.main(["agent", "release-box", "--agent", "coder", "--confirm"]) == 1
    assert fake.sent == []
    capsys.readouterr()

    cli.main(["delegation", "grant", "--agent", "coder", "--prefix", "node tests/", "--confirm"])
    capsys.readouterr()
    assert cli.main(["agent", "release-box", "--agent", "coder", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["match_kind"] == "composite"
    assert payload["matched_segments"][0]["via"] == "node tests/"
    assert fake.sent == [("%50", "")]
    events = _events_text(root)
    assert '"match_kind": "composite"' in events
    assert '"matched_segments"' in events


def test_mcp_release_reports_mcp_tool_match_kind(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    bind_coder(root)
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)
    fake.output = CODEX_MCP_TOOL_BOX
    cli.main([
        "delegation", "grant", "--agent", "coder",
        "--mcp-server", "chrome-devtools", "--mcp-tool", "hover", "--confirm",
    ])
    capsys.readouterr()
    assert cli.main(["agent", "boxes", "--agent", "coder"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["match_kind"] == "mcp_tool"
    assert payload["matched_segments"] is None
```

Also mirror the existing `test_boxes_watch_releases_delegated_box_and_stops_at_bound`
setup to add one watch-path assertion: with `fake.output = CODEX_AUTH_BOX_LOOP`
and a `node tests/` grant, `boxes watch --confirm --iterations 1 --interval 0`
releases 1 box and the released item carries `match_kind == "composite"`.

- [ ] **Step 2: Run to verify failure**

Run: `conda run -n agentdeck pytest tests/test_delegation_cli.py -q -k "composite or mcp_tool_match_kind"`
Expected: FAIL on missing `match_kind` key / composite refusal (loop box
`delegated is False` today)

- [ ] **Step 3: Implement**

Import at the top of `cli.py` (next to other `agentdeck.` imports):

```python
from agentdeck.delegation_match import normalize_match
```

Add after `_match_active_delegation` (leave that function byte-identical —
tests call it directly):

```python
def _match_delegation_with_provenance(
    state: dict[str, object],
    agent_id: str,
    command: str | None,
    mcp_box: _McpToolTarget | None = None,
) -> tuple[dict[str, object] | None, str | None, list[dict[str, str]] | None]:
    """三臂匹配 + GUI/审计 provenance:平前缀/折叠比较(现行为)→ MCP →
    复合归一化(round 12 发现 #3)。返回 (delegation, match_kind,
    matched_segments);未命中为 (None, None, None)。"""
    match = _match_active_delegation(state, agent_id, command, mcp_box)
    if match is not None:
        kind = (
            "mcp_tool"
            if (match.get("kind") or "command_prefix") == "mcp_tool"
            else "prefix"
        )
        return match, kind, None
    if not command:
        return None, None, None
    prefix_items = [
        item
        for item in state.get("delegations", [])
        if isinstance(item, dict)
        and not item.get("revoked_at")
        and item.get("agent_id") == agent_id
        and (item.get("kind") or "command_prefix") == "command_prefix"
        and item.get("prefix")
    ]
    composite = normalize_match(command, [str(item["prefix"]) for item in prefix_items])
    if composite is None:
        return None, None, None
    first_prefix = next(
        segment.via for segment in composite.segments if segment.via != "glue"
    )
    item = next(
        item for item in prefix_items if item.get("prefix") == first_prefix
    )
    segments = [
        {"segment": segment.segment, "via": segment.via}
        for segment in composite.segments
    ]
    return item, "composite", segments
```

Rewire the three call sites. In each, replace

```python
    match = _match_active_delegation(store.load(), <agent_id>, command, mcp_box)
```

with

```python
    match, match_kind, matched_segments = _match_delegation_with_provenance(
        store.load(), <agent_id>, command, mcp_box
    )
```

and add to each payload dict and each `auth_box_released` event payload
(alongside the existing `_box_fields(...)` spread):

```python
            "match_kind": match_kind,
            "matched_segments": matched_segments,
```

Specifically: `agent_boxes_command` JSON payload; `agent_release_box_command`
success JSON + event payload; `_scan_release_delegated_boxes` released items
+ event payload (skipped items get `"match_kind": None` only if the field is
asserted there — keep skipped items unchanged unless a test requires it).

- [ ] **Step 4: Run to verify pass**

Run: `conda run -n agentdeck pytest tests/test_delegation_cli.py tests/test_delegation_match.py -q`
Expected: all pass (existing 33 + new)

## Task 3: contract + docs sync, ladder, commit B

**Files:**
- Modify: `src/agentdeck/contracts.py` (`DELEGATION_BOXES_RESPONSE_FIELDS`,
  `delegation_boxes_example`)
- Modify: `docs/contracts/delegation-schema.md`, `CLAUDE.md` (delegation
  bullet), `HISTORY.md`
- Test: `tests/test_delegation_cli.py`

- [ ] **Step 1: Write the failing test**

```python
def test_delegation_contract_exposes_match_provenance_fields(capsys) -> None:
    assert cli.main(["contract", "delegation", "--example"]) == 0
    payload = json.loads(capsys.readouterr().out)
    for field in ("match_kind", "matched_segments"):
        assert field in payload["boxes_response_fields"]
```

Run: `conda run -n agentdeck pytest tests/test_delegation_cli.py::test_delegation_contract_exposes_match_provenance_fields -q`
Expected: FAIL

- [ ] **Step 2: Implement**

`DELEGATION_BOXES_RESPONSE_FIELDS` gains `"match_kind", "matched_segments"`
(after `"mcp_tool"`); `delegation_boxes_example()` gains
`"match_kind": "prefix", "matched_segments": None`.

`docs/contracts/delegation-schema.md`: new "Composite matching" bullet under
Box Shapes documenting the split-and-cover semantic, the hard-reject set,
the fixed glue allowlist v1 (verbatim list), /tmp-confined redirects/tail,
the at-least-one-covered rule, `match_kind`/`matched_segments` provenance
(and `match_kind="mcp_tool"` for MCP matches), and that unparseable input
falls back to the manual path. Boundaries: normalization never widens a
prefix's meaning; glue alone never releases.

`CLAUDE.md` delegation bullet: extend with one sentence covering composite
matching (拆段+逐段覆盖、固定胶水白名单、任一段不命中即整体拒、
match_kind/matched_segments 审计 provenance)。

`HISTORY.md`: top entry (Type: feat, title "Match shell-wrapped delegated
commands via split-and-cover normalization (round 12 发现 #3)"), citing the
spec and the three live samples.

- [ ] **Step 3: Full verification ladder** (all green, report exact counts)

1. `conda run -n agentdeck pytest tests/test_delegation_match.py tests/test_delegation_cli.py -q`
2. `conda run -n agentdeck pytest tests/test_contracts.py tests/test_agent_cli.py tests/test_leader_cli.py -q`
3. `conda run -n agentdeck python -m compileall src tests`
4. `git diff --check`
5. `conda run -n agentdeck pytest tests/ -q` (expect ~4750+ passed, 3 skipped)

- [ ] **Step 4: Commit**

```bash
git add src/agentdeck/cli.py src/agentdeck/contracts.py tests/test_delegation_cli.py \
  docs/contracts/delegation-schema.md CLAUDE.md HISTORY.md
git commit -m "feat: match shell-wrapped delegated commands in box release paths"
```

(No co-author trailer. No push. Nothing under `.omc/`.)

---

## Post-plan notes

- Update `docs/handoff/current-development-state.md` (move 委托匹配归一化
  from 拍板项 to landed; live validation of composite release rides the
  next Line 1 round) — trailing docs commit at the session's judgment.
- Non-goals (future forks): configurable glue, pipes with read-only tools
  (rg/grep) as glue, normalization of the collapsed-fallback extraction
  (fold artifacts stay manual by design).
