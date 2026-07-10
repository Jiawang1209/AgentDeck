# Golden Demo Rehearsal Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one deterministic end-to-end pytest rehearsal proving the existing golden demo path reaches release while recommending the correct command at every checkpoint.

**Architecture:** Keep all new behavior in `tests/test_agent_cli.py`. The test uses a temporary project, the existing fake Leader, `FakeTmuxBackend`, existing CLI commands, and `StateStore` test data; no production module, CLI command, runtime backend, or contract changes.

**Tech Stack:** Python 3.12, pytest, AgentDeck stdlib CLI, existing fake Leader/runtime test doubles, JSON state store.

---

## File Structure

- Modify `tests/test_agent_cli.py`: add the single contiguous golden-demo rehearsal test beside the existing focused `demo golden` tests.
- Modify `HISTORY.md`: record the new regression coverage as a test-only slice.
- Modify `docs/handoff/current-development-state.md`: state that the golden-demo lane now has a contiguous deterministic rehearsal in addition to focused state tests.
- Do not modify any file under `src/agentdeck/`.

### Task 1: Add the contiguous golden-demo rehearsal

**Files:**
- Modify: `tests/test_agent_cli.py` after `test_demo_golden_marks_release_done_after_release`

- [ ] **Step 1: Add the complete rehearsal test**

Add this test without introducing a new helper:

```python
def test_golden_demo_rehearsal_drives_one_round_to_release(
    tmp_path, monkeypatch, capsys
) -> None:
    root = prepare_project(tmp_path, monkeypatch)
    config_path = root / ".agentdeck" / "config.toml"
    config_text = config_path.read_text(encoding="utf-8")
    config_text = config_text.replace('provider = "deepseek"', 'provider = "fake"', 1)
    config_text = config_text.replace('model = "deepseek-chat"', 'model = "fake-plan"', 1)
    config_path.write_text(config_text, encoding="utf-8")
    assert cli.main(
        [
            "agent",
            "assign-role",
            "--agent",
            "reviewer",
            "--role",
            "code_reviewer",
            "--role-prompt",
            "Review the implementation evidence.",
        ]
    ) == 0
    assert cli.main(
        [
            "agent",
            "assign-role",
            "--agent",
            "coder",
            "--role",
            "round_reviewer",
            "--role-prompt",
            "Accept or reject the completed round.",
        ]
    ) == 0
    capsys.readouterr()
    bind_agent(root, "planner", "%42")
    fake = FakeTmuxBackend()
    monkeypatch.setattr(cli, "TmuxBackend", lambda: fake)

    assert cli.main(["demo", "golden"]) == 0
    ready_to_plan = json.loads(capsys.readouterr().out)
    assert ready_to_plan["current_status"] == "ready_to_plan"
    assert ready_to_plan["next_command"] == "agentdeck leader plan --task <task>"

    assert cli.main(["leader", "plan", "--task", "Rehearse one golden demo round"]) == 0
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    assert cli.main(["demo", "golden"]) == 0
    plan_ready = json.loads(capsys.readouterr().out)
    assert plan_ready["current_status"] == "plan_ready"
    assert plan_ready["next_command"] == (
        f"agentdeck approval create-from-plan --plan-id {plan_id}"
    )

    assert cli.main(["approval", "create-from-plan", "--plan-id", plan_id]) == 0
    approvals = json.loads(capsys.readouterr().out)["approvals"]
    planner_approval = next(item for item in approvals if item["agent_id"] == "planner")
    approval_id = planner_approval["approval_id"]
    assert cli.main(["demo", "golden"]) == 0
    waiting_for_approval = json.loads(capsys.readouterr().out)
    assert waiting_for_approval["current_status"] == "waiting_for_approval"
    assert waiting_for_approval["next_command"] == (
        f"agentdeck approval approve --approval-id {approval_id}"
    )
    dispatch_step = next(
        step for step in waiting_for_approval["steps"] if step["step_id"] == "dispatch"
    )
    assert dispatch_step["enabled"] is False
    assert dispatch_step["blocker"] == "approval is not approved"

    assert cli.main(["approval", "approve", "--approval-id", approval_id]) == 0
    capsys.readouterr()
    assert cli.main(["demo", "golden"]) == 0
    ready_to_dispatch = json.loads(capsys.readouterr().out)
    assert ready_to_dispatch["current_status"] == "ready_to_dispatch"
    assert ready_to_dispatch["next_command"] == (
        f"agentdeck approval dispatch --approval-id {approval_id}"
    )

    assert cli.main(["approval", "dispatch", "--approval-id", approval_id]) == 0
    dispatch = json.loads(capsys.readouterr().out)
    message_id = dispatch["message_id"]
    assert fake.sent and fake.sent[0][0] == "%42"
    assert cli.main(["demo", "golden"]) == 0
    waiting_for_reply = json.loads(capsys.readouterr().out)
    assert waiting_for_reply["current_status"] == "waiting_for_reply"
    assert waiting_for_reply["next_command"] == (
        f"agentdeck capture-reply --agent planner --message-id {message_id}"
    )

    fake.capture_output = lambda _config, _pane_id, lines=200: (
            "status: completed\n"
            "summary: golden demo implementation complete\n"
            "full_output_path: docs/golden-demo-result.md"
    )
    assert cli.main(
        ["capture-reply", "--agent", "planner", "--message-id", message_id]
    ) == 0
    captured_reply = json.loads(capsys.readouterr().out)
    assert captured_reply["artifacts"]["items"][0]["path"] == "docs/golden-demo-result.md"
    assert cli.main(["demo", "golden"]) == 0
    waiting_for_reviews = json.loads(capsys.readouterr().out)
    assert waiting_for_reviews["current_status"] == "ready_for_review_gate"
    release_step = next(
        step for step in waiting_for_reviews["steps"] if step["step_id"] == "release"
    )
    assert release_step["enabled"] is False

    store = StateStore(root)
    code_review = store.create_dispatch_records(
        "leader", "reviewer", "Review golden demo evidence", "review", "%review"
    )
    store.record_reply(
        "reviewer", code_review["message"]["message_id"], "code review: pass"
    )
    round_review = store.create_dispatch_records(
        "leader", "coder", "Accept the golden demo round", "round review", "%round"
    )
    store.record_reply(
        "coder", round_review["message"]["message_id"], "round review: accepted"
    )

    assert cli.main(["demo", "golden"]) == 0
    ready_to_release = json.loads(capsys.readouterr().out)
    release_step = next(
        step for step in ready_to_release["steps"] if step["step_id"] == "release"
    )
    assert release_step["status"] == "ready"
    assert release_step["command"] == "agentdeck release --confirm"
    assert release_step["enabled"] is True

    assert cli.main(["release", "--confirm"]) == 0
    release = json.loads(capsys.readouterr().out)["release"]
    assert release["status"] == "released"
    assert cli.main(["demo", "golden"]) == 0
    released = json.loads(capsys.readouterr().out)
    assert released["current_status"] == "released"
    assert released["next_command"] == "agentdeck workbench"
    release_step = next(
        step for step in released["steps"] if step["step_id"] == "release"
    )
    assert release_step["status"] == "done"
    assert release_step["enabled"] is False
    events = StateStore(root).all_events()
    assert events[-1]["event_type"] == "round_released"
    assert events[-1]["payload"]["release_id"] == release["release_id"]
```

- [ ] **Step 2: Run the focused test and inspect the first result**

Run:

```bash
conda run -n agentdeck pytest tests/test_agent_cli.py::test_golden_demo_rehearsal_drives_one_round_to_release -q
```

Expected: either PASS because the existing commands already compose, or FAIL at the first genuinely inconsistent checkpoint. Do not manufacture a failure and do not change production code unless the failure proves an existing behavior bug.

- [ ] **Step 3: Stop on an unexpected failure**

If Step 2 fails, do not edit production code from this plan. Record the first failing checkpoint and switch to `superpowers:systematic-debugging` before deciding whether the test data or an existing behavior is wrong.

- [ ] **Step 4: Re-run the focused golden-demo tests**

Run:

```bash
conda run -n agentdeck pytest tests/test_agent_cli.py -k "demo_golden or golden_demo_rehearsal" -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit the rehearsal test**

```bash
git add tests/test_agent_cli.py
git commit -m "Test golden demo end-to-end rehearsal"
```

### Task 2: Record the regression guarantee

**Files:**
- Modify: `HISTORY.md`
- Modify: `docs/handoff/current-development-state.md`

- [ ] **Step 1: Add the HISTORY entry**

Insert this entry at the top of the 2026-07-10 section, creating the date section above 2026-07-09 if necessary:

```markdown
## 2026-07-10

### Current - Golden demo deterministic end-to-end rehearsal

- **类型**: test
- **动机**: `agentdeck demo golden` 已有逐状态测试，但缺少一条在同一临时项目中连续覆盖 plan、approval、dispatch、reply/artifact、review gate 和 release 的回归演练。
- **What**: 新增单条 pytest 端到端演练，使用 fake Leader、FakeTmuxBackend、现有 CLI 命令和 StateStore 测试数据，在每个检查点断言 golden guide 的 `current_status`、`next_command` 和关键 step；不修改任何 `src/agentdeck/` 生产代码、CLI 或 contract。
- **影响**: golden demo 现在既有各状态 focused coverage，也有一条确定性、无网络、无真实 tmux 的完整 round-to-release 回归链。测试运行在 pytest 临时项目内，不触碰开发者当前 `.agentdeck/` 状态。
- **验证**: focused rehearsal、全部 golden-demo tests、全量 pytest、`python -m compileall src tests -q` 与 `git diff --check` 均通过。
```

- [ ] **Step 2: Add the handoff note**

Below `## Golden demo guide slice — implemented`, add:

```markdown
### Deterministic golden-demo rehearsal — covered

The golden path now has one contiguous pytest rehearsal in addition to focused state tests. It drives a single temporary project through fake-Leader planning, explicit approval, fake-runtime dispatch, captured reply/artifact, code review, round review, and explicit release while checking `agentdeck demo golden` at every checkpoint. This is test-only coverage: no production command, function, runtime backend, or contract was added.
```

- [ ] **Step 3: Verify documentation formatting**

Run:

```bash
git diff --check -- HISTORY.md docs/handoff/current-development-state.md
```

Expected: exit 0 with no output.

- [ ] **Step 4: Commit the development record**

```bash
git add HISTORY.md docs/handoff/current-development-state.md
git commit -m "Document golden demo rehearsal coverage"
```

### Task 3: Full verification and scope audit

**Files:**
- Verify only; no planned edits.

- [ ] **Step 1: Run the focused rehearsal**

```bash
conda run -n agentdeck pytest tests/test_agent_cli.py::test_golden_demo_rehearsal_drives_one_round_to_release -q
```

Expected: pass.

- [ ] **Step 2: Run all golden-demo tests**

```bash
conda run -n agentdeck pytest tests/test_agent_cli.py -k "demo_golden or golden_demo_rehearsal" -q
```

Expected: pass.

- [ ] **Step 3: Run the full suite**

```bash
conda run -n agentdeck pytest -q
```

Expected: pass with zero failures.

- [ ] **Step 4: Compile Python sources and tests**

```bash
conda run -n agentdeck python -m compileall src tests -q
```

Expected: exit 0 with no errors.

- [ ] **Step 5: Check whitespace and scope**

```bash
git diff --check
git status --short
git log -5 --oneline
```

Expected: `git diff --check` exits 0; no file under `src/agentdeck/` is modified; pre-existing `.omc/*` changes and untracked `AGENTS.md` remain untouched.
