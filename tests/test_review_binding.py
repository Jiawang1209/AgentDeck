"""Review-step worktree bases and the digest bound to them.

Group members must all review the SAME finished implementation: basing member 2
on member 1's branch leaks member 1's review into member 2's tree (any-fail-blocks
aggregation assumes independent judgements) and would make member 1's own commit
look like the reviewed code drifting once a digest is bound to that base.

See docs/superpowers/specs/2026-08-03-review-digest-binding-design.md.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

from agentdeck import cli
from agentdeck.config import write_default_config
from agentdeck.state import StateStore


class _Fake:
    def create_session(self, _config) -> None: ...

    def spawn_agent(self, _config, agent, cwd: str) -> str:
        return "%42"

    def apply_visible_layout(self, _config, panes) -> None: ...

    def capture_output(self, _config, pane_id: str, lines: int = 200) -> str:
        return "output\n"

    def send_input(self, _config, pane_id: str, text: str) -> None: ...

    def kill_pane(self, _config, pane_id: str) -> None: ...

    def pane_exists(self, _config, pane_id: str) -> bool:
        return True

    def list_panes(self, _config):
        return []


def _run(argv: list[str]):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = cli.main(argv)
    text = buffer.getvalue().strip()
    return code, (json.loads(text) if text else None)


def _prepare(tmp_path: Path, monkeypatch, review_section: str | None = None) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    write_default_config(root)
    path = root / ".agentdeck" / "config.toml"
    # The default config makes only `coder` worktree-mode; a shared-mode agent
    # gets no branch at all, so the sibling-base bug cannot even arise. Real
    # review setups put reviewers in worktree mode (round 6 handoff note:
    # "scratch reviewer 需改 workspace_mode=worktree").
    text = path.read_text(encoding="utf-8").replace(
        'workspace_mode = "shared"', 'workspace_mode = "worktree"'
    )
    if review_section:
        text += review_section
    path.write_text(text, encoding="utf-8")
    monkeypatch.chdir(root)
    monkeypatch.setattr(cli, "TmuxBackend", _Fake)
    return root


def _git_repo(root: Path) -> None:
    cli.subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    cli.subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "base"],
        cwd=root, check=True,
    )


def _running(root: Path, *agent_ids: str) -> None:
    store = StateStore(root)
    state = store.load()
    for index, agent_id in enumerate(agent_ids):
        state["agents"][agent_id] = {
            "agent_id": agent_id, "pane_id": f"%4{index}", "session_name": "agentdeck",
            "cwd": str(root), "status": "running",
        }
    store.save(state)


def _seed(root: Path, task: str) -> str:
    _, plan = _run(["leader", "plan", "--provider", "fake", "--model", "fake-plan", "--task", task])
    plan_id = plan["plan_id"]
    _run(["approval", "create-from-plan", "--plan-id", plan_id])
    return plan_id


def _approve_all(root: Path, plan_id: str) -> None:
    store = StateStore(root)
    state = store.load()
    for approval in state["approvals"]:
        if approval.get("plan_id") == plan_id:
            approval["status"] = "approved"
    store.save(state)


def _dispatch_step(root: Path, plan_id: str, step: int) -> str:
    approval_id = next(
        a["approval_id"] for a in StateStore(root).load()["approvals"]
        if a.get("plan_id") == plan_id and a.get("step") == step
    )
    _run(["approval", "dispatch", "--approval-id", approval_id])
    return next(
        str(a["message_id"]) for a in StateStore(root).load()["approvals"]
        if a.get("approval_id") == approval_id
    )


def _message_for_step(root: Path, plan_id: str, step: int) -> dict:
    state = StateStore(root).load()
    approval = next(
        a for a in state["approvals"] if a.get("plan_id") == plan_id and a.get("step") == step
    )
    return next(
        m for m in state["messages"] if m.get("message_id") == approval.get("message_id")
    )


def _branch_for_step(root: Path, plan_id: str, step: int) -> str | None:
    return _message_for_step(root, plan_id, step).get("worktree_branch")


# --------------------------------------------------------------------------
# Task 1: a review group shares one base, and it is the implementation branch.
# --------------------------------------------------------------------------


def test_dispatched_group_members_share_the_implementation_base(
    tmp_path: Path, monkeypatch
) -> None:
    root = _prepare(tmp_path, monkeypatch, '\n[review]\nreviewers = ["reviewer", "planner"]\n')
    _git_repo(root)
    _running(root, "planner", "coder", "reviewer")
    plan_id = _seed(root, "group base dispatch")
    _approve_all(root, plan_id)
    for step in (1, 2):
        _dispatch_step(root, plan_id, step)

    implementation = _branch_for_step(root, plan_id, 2)
    assert implementation is not None

    store = StateStore(root)
    first = cli._plan_base_worktree_branch(store, plan_id, 3)
    assert first == implementation

    # Member 1 goes out FIRST -- which is what actually happens now that the
    # DAG guard fans the group out inside one wave. Only then does member 2's
    # base get computed, and the sibling's brand-new branch is sitting there
    # looking like the newest earlier step. Computing member 2's base before
    # member 1 is dispatched would pass under the old rule too, proving nothing.
    _dispatch_step(root, plan_id, 3)
    sibling = _branch_for_step(root, plan_id, 3)
    assert sibling is not None and sibling != implementation

    second = cli._plan_base_worktree_branch(StateStore(root), plan_id, 4)

    assert second == implementation, "member 2 must not be based on member 1's branch"


# --------------------------------------------------------------------------
# Task 2: the commit a review worktree was created from is recorded.
# --------------------------------------------------------------------------


def _head_commit(cwd: Path, ref: str = "HEAD") -> str:
    done = cli.subprocess.run(
        ["git", "rev-parse", ref], cwd=cwd, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def test_dispatch_records_the_commit_the_worktree_was_created_from(
    tmp_path: Path, monkeypatch
) -> None:
    root = _prepare(tmp_path, monkeypatch)
    _git_repo(root)
    _running(root, "planner", "coder", "reviewer")
    plan_id = _seed(root, "record base commit")
    _approve_all(root, plan_id)
    for step in (1, 2):
        _dispatch_step(root, plan_id, step)

    message = _message_for_step(root, plan_id, 2)
    base_branch = message["worktree_base_branch"]
    assert base_branch, "step 2 should be based on step 1's branch"
    # The recorded commit is exactly what that base branch pointed at.
    assert message["worktree_base_commit"] == _head_commit(root, base_branch)


def test_a_step_without_a_base_records_no_commit(tmp_path: Path, monkeypatch) -> None:
    root = _prepare(tmp_path, monkeypatch)
    _git_repo(root)
    _running(root, "planner")
    plan_id = _seed(root, "no base")
    _approve_all(root, plan_id)
    _dispatch_step(root, plan_id, 1)

    message = _message_for_step(root, plan_id, 1)

    assert message["worktree_base_branch"] is None
    assert message["worktree_base_commit"] is None


# --------------------------------------------------------------------------
# Task 4: drift withholds the AUTOMATIC merge; the human's command never is.
# --------------------------------------------------------------------------


_PASS_VERDICT = (
    'verdict: {"schema_version": "review-verdict/v1", '
    '"criteria": [{"criterion": "\\u6d4b\\u8bd5\\u5168\\u7eff", "verdict": "pass"}], '
    '"overall": "pass", "score": 90}\n'
)
_AGENT_FOR_STEP = {1: "planner", 2: "coder", 3: "reviewer"}


def _reviewed_plan(tmp_path: Path, monkeypatch) -> tuple[Path, str]:
    """Run planner -> coder -> reviewer to completion, reviewer passing."""
    root = _prepare(tmp_path, monkeypatch)
    _git_repo(root)
    _running(root, "planner", "coder", "reviewer")
    plan_id = _seed(root, "digest gate")
    _approve_all(root, plan_id)
    for step in (1, 2, 3):
        message_id = _dispatch_step(root, plan_id, step)
        text = "status: completed\nsummary: done\n" + (_PASS_VERDICT if step == 3 else "")
        _run(["reply", "--agent", _AGENT_FOR_STEP[step], "--message-id", message_id,
              "--text", text])
    return root, plan_id


def _commit_onto(worktree: Path, name: str) -> None:
    (worktree / name).write_text("changed after review\n", encoding="utf-8")
    git = ["-c", "user.email=t@t", "-c", "user.name=t"]
    cli.subprocess.run(["git", *git, "add", name], cwd=worktree, check=True)
    cli.subprocess.run(["git", *git, "commit", "-q", "-m", "after review"],
                       cwd=worktree, check=True)


def test_a_clean_review_does_not_withhold_the_merge(tmp_path: Path, monkeypatch) -> None:
    root, plan_id = _reviewed_plan(tmp_path, monkeypatch)

    bindings = cli._plan_review_bindings(cli.load_config(root), StateStore(root), plan_id)

    # Assert the binding EXISTS before asserting it is clean: "no blocker"
    # is also what an empty binding set produces, and an earlier version of
    # this test passed that way -- the verdict had failed to parse, so nothing
    # was ever bound and the silence meant nothing.
    assert bindings["count"] == 1
    assert bindings["match"] == 1
    assert bindings["bindings"][0]["state"] == "match"
    assert bindings["blocker"] is None
    assert cli._stale_review_merge_blocker(
        cli.load_config(root), StateStore(root), plan_id
    ) is None


def test_drift_withholds_the_automatic_merge(tmp_path: Path, monkeypatch) -> None:
    root, plan_id = _reviewed_plan(tmp_path, monkeypatch)
    reviewed_branch = _message_for_step(root, plan_id, 3)["worktree_base_branch"]
    assert reviewed_branch, "the review step must be based on the implementation branch"
    # the reviewed branch gains a commit AFTER the verdict was recorded
    _commit_onto(Path(_message_for_step(root, plan_id, 2)["worktree_path"]), "late.txt")

    blocker = cli._stale_review_merge_blocker(cli.load_config(root), StateStore(root), plan_id)

    assert blocker is not None
    assert reviewed_branch in blocker
    assert "auto-merge withheld" in blocker


def test_the_human_merge_command_is_never_gated(tmp_path: Path, monkeypatch) -> None:
    root, plan_id = _reviewed_plan(tmp_path, monkeypatch)
    _commit_onto(Path(_message_for_step(root, plan_id, 2)["worktree_path"]), "late.txt")

    _code, payload = _run(["worktree", "merge-plan", "--plan-id", plan_id, "--confirm"])

    # It may fail or skip for ordinary git/gate reasons -- what it must NEVER do
    # is refuse with the staleness blocker.
    assert "auto-merge withheld" not in json.dumps(payload or {})


# --------------------------------------------------------------------------
# Task 6: a human sees the drift before the merge refuses it.
# --------------------------------------------------------------------------


def test_plan_status_projects_the_recorded_binding_and_live_state(
    tmp_path: Path, monkeypatch
) -> None:
    root, plan_id = _reviewed_plan(tmp_path, monkeypatch)
    reviewed_branch = _message_for_step(root, plan_id, 3)["worktree_base_branch"]
    _commit_onto(Path(_message_for_step(root, plan_id, 2)["worktree_path"]), "late.txt")
    state_path = root / ".agentdeck" / "state" / "state.json"
    before = state_path.read_bytes()

    code, payload = _run(["plan", "status", "--plan-id", plan_id])

    assert code == 0
    review_step = next(s for s in payload["steps"] if s["step"] == 3)
    assert review_step["worktree_base_commit"]           # recorded fact
    binding = next(b for b in payload["review_bindings"]["bindings"] if b["step"] == 3)
    assert binding["state"] == "drift"                   # live comparison
    assert binding["base_branch"] == reviewed_branch
    assert payload["review_bindings"]["blocker"] is not None
    assert state_path.read_bytes() == before             # read-only


def test_plan_status_says_not_recorded_rather_than_verified(
    tmp_path: Path, monkeypatch
) -> None:
    root, plan_id = _reviewed_plan(tmp_path, monkeypatch)
    # simulate a plan created before this feature: drop the recorded commit
    store = StateStore(root)
    state = store.load()
    for message in state["messages"]:
        message["worktree_base_commit"] = None
    store.save(state)

    _, payload = _run(["plan", "status", "--plan-id", plan_id])

    binding = next(b for b in payload["review_bindings"]["bindings"] if b["step"] == 3)
    assert binding["state"] == "unverifiable"
    assert binding["reason"] == "not_recorded"
    # the one deliberate fail-open: not recorded does not block ...
    assert payload["review_bindings"]["blocker"] is None
    # ... but it must never read as verified
    assert binding["state"] != "match"
