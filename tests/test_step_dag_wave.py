"""Run-loop single-wave behaviour under the derived step DAG.

The load-bearing test here is the **zero behaviour change differential**:
`tests/fixtures/run_loop_linear_wave_golden.json` was captured from the engine
*before* the DAG guard landed, and a linear (no `[review]`) project must still
reproduce it payload for payload.

See docs/superpowers/specs/2026-08-03-dag-step-dependencies-design.md.
"""
from __future__ import annotations

import contextlib
import io
import json
import re
from pathlib import Path

import pytest

from agentdeck import cli
from agentdeck.config import write_default_config
from agentdeck.state import StateStore

GOLDEN = Path(__file__).parent / "fixtures" / "run_loop_linear_wave_golden.json"
_ID_RE = re.compile(r"\b(pln|apv|msg|rep|job|att|inb|art)_[0-9a-zA-Z]+")

_VERDICT_FAIL = {
    "schema_version": "review-verdict/v1",
    "criteria": [{"criterion": "测试全绿", "verdict": "fail"}],
    "overall": "fail",
    "score": 10,
}


class _Fake:
    """Minimal tmux stand-in; identical to the one used to capture the golden."""

    def create_session(self, _config) -> None: ...

    def spawn_agent(self, _config, agent, cwd: str) -> str:
        return "%42"

    def apply_visible_layout(self, _config, panes) -> None: ...

    def capture_output(self, _config, pane_id: str, lines: int = 200) -> str:
        return "planner output\n"

    def send_input(self, _config, pane_id: str, text: str) -> None: ...

    def kill_pane(self, _config, pane_id: str) -> None: ...

    def pane_exists(self, _config, pane_id: str) -> bool:
        return True

    def list_panes(self, _config):
        return []


def _prepare(tmp_path: Path, monkeypatch, review_section: str | None = None) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    if review_section:
        path = root / ".agentdeck" / "config.toml"
        path.write_text(path.read_text(encoding="utf-8") + review_section, encoding="utf-8")
    monkeypatch.chdir(root)
    monkeypatch.setattr(cli, "TmuxBackend", _Fake)
    return root


def _bind(root: Path, agent_id: str, pane_id: str) -> None:
    store = StateStore(root)
    state = store.load()
    state["agents"][agent_id] = {
        "agent_id": agent_id, "pane_id": pane_id, "session_name": "agentdeck",
        "cwd": str(root), "status": "running",
    }
    store.save(state)


def _run(argv: list[str]):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = cli.main(argv)
    text = buffer.getvalue().strip()
    return code, (json.loads(text) if text else None)


def _normalize(payload, seen: dict[str, str]):
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def repl(match: re.Match[str]) -> str:
        token = match.group(0)
        if token not in seen:
            prefix = match.group(1)
            seen[token] = f"{prefix}_{len([k for k in seen if k.startswith(prefix)]) + 1}"
        return seen[token]

    return json.loads(_ID_RE.sub(repl, text))


def _autonomous(root: Path) -> None:
    _run([
        "policy", "set-mode", "--mode", "autonomous", "--confirm",
        "--allow-agent", "planner", "--allow-agent", "coder",
        "--allow-agent", "reviewer", "--max-approvals", "9",
    ])


def _reply_file(root: Path, message_id: str, extra: str = "") -> None:
    path = root / ".agentdeck" / "replies" / f"{message_id}.reply.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("status: completed\nsummary: done\n" + extra, encoding="utf-8")


def _seed_plan(root: Path, task: str) -> str:
    _, plan = _run(["leader", "plan", "--provider", "fake", "--model", "fake-plan", "--task", task])
    plan_id = plan["plan_id"]
    _run(["approval", "create-from-plan", "--plan-id", plan_id])
    _autonomous(root)
    return plan_id


# --------------------------------------------------------------------------
# The pin: a linear plan must behave exactly as it did before the DAG guard.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", ["linear_full_drive", "blocked_middle_step"])
def test_linear_waves_are_byte_identical_to_the_pre_dag_engine(
    tmp_path: Path, monkeypatch, scenario: str
) -> None:
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))[scenario]
    root = _prepare(tmp_path, monkeypatch)
    _bind(root, "planner", "%41")
    if scenario == "linear_full_drive":
        _bind(root, "coder", "%42")
    _bind(root, "reviewer", "%43")
    plan_id = _seed_plan(root, "linear differential")

    observed = []
    seen: dict[str, str] = {}
    for _ in range(5):
        code, payload = _run(["run-loop", "--plan-id", plan_id, "--confirm"])
        observed.append({"exit": code, "payload": _normalize(payload, seen)})
        if payload is None:
            break
        for item in payload.get("dispatched", []):
            _reply_file(root, item["message_id"])
        if payload.get("stopped_reason") == "complete":
            break

    assert observed == expected


# --------------------------------------------------------------------------
# The relaxation: a review group fans out once its shared input is finished.
# --------------------------------------------------------------------------


def _group_project(tmp_path: Path, monkeypatch) -> tuple[Path, str]:
    root = _prepare(
        tmp_path, monkeypatch, '\n[review]\nreviewers = ["reviewer", "planner"]\n'
    )
    _bind(root, "planner", "%41")
    _bind(root, "coder", "%42")
    _bind(root, "reviewer", "%43")
    plan_id = _seed_plan(root, "review group fan-out")
    return root, plan_id


def _steps(root: Path, plan_id: str) -> list[dict[str, object]]:
    return StateStore(root).plan_status(plan_id)["steps"]


def test_review_group_expands_into_two_parallel_members(tmp_path: Path, monkeypatch) -> None:
    root, plan_id = _group_project(tmp_path, monkeypatch)
    steps = _steps(root, plan_id)
    assert [step["agent_id"] for step in steps] == ["planner", "coder", "reviewer", "planner"]
    assert [step["review_group"] for step in steps] == [None, None, 1, 1]


def test_group_members_are_held_while_their_shared_input_is_unreplied(
    tmp_path: Path, monkeypatch
) -> None:
    root, plan_id = _group_project(tmp_path, monkeypatch)

    _, wave1 = _run(["run-loop", "--plan-id", plan_id, "--confirm"])
    assert [d["agent_id"] for d in wave1["dispatched"]] == ["planner"]
    _reply_file(root, wave1["dispatched"][0]["message_id"])

    _, wave2 = _run(["run-loop", "--plan-id", plan_id, "--confirm"])
    # the implementation step is out, both reviewers wait on it
    assert [d["agent_id"] for d in wave2["dispatched"]] == ["coder"]
    held = [s for s in wave2["skipped"] if s["reason"] == "awaiting earlier step completion"]
    assert sorted(s["agent_id"] for s in held) == ["planner", "reviewer"]


def test_review_group_dispatches_both_members_in_one_wave(tmp_path: Path, monkeypatch) -> None:
    root, plan_id = _group_project(tmp_path, monkeypatch)
    for _ in range(2):
        _, wave = _run(["run-loop", "--plan-id", plan_id, "--confirm"])
        _reply_file(root, wave["dispatched"][0]["message_id"])

    _, wave3 = _run(["run-loop", "--plan-id", plan_id, "--confirm"])

    assert sorted(d["agent_id"] for d in wave3["dispatched"]) == ["planner", "reviewer"]
    assert wave3["skipped"] == []
    assert wave3["blocked"] == []
    assert wave3["stopped_reason"] == "waiting_for_reply"


# --------------------------------------------------------------------------
# The one new risk: one pane never receives two tasks at once.
# --------------------------------------------------------------------------


def test_two_ready_steps_on_one_agent_dispatch_one_and_skip_the_other(
    tmp_path: Path, monkeypatch
) -> None:
    root, plan_id = _group_project(tmp_path, monkeypatch)
    # Point both members of the parallel group at the same worker.
    store = StateStore(root)
    state = store.load()
    for plan in state["plans"]:
        if plan["plan_id"] == plan_id:
            plan["plan"]["steps"][3]["agent_id"] = "reviewer"
    for approval in state["approvals"]:
        if approval["plan_id"] == plan_id and approval["step"] == 4:
            approval["agent_id"] = "reviewer"
    store.save(state)

    for _ in range(2):
        _, wave = _run(["run-loop", "--plan-id", plan_id, "--confirm"])
        _reply_file(root, wave["dispatched"][0]["message_id"])

    _, wave3 = _run(["run-loop", "--plan-id", plan_id, "--confirm"])

    assert [d["agent_id"] for d in wave3["dispatched"]] == ["reviewer"]
    contention = [s for s in wave3["skipped"] if s["agent_id"] == "reviewer"]
    assert len(contention) == 1
    # distinguishable from the ordering hold
    assert contention[0]["reason"] == "agent busy this wave"
    assert contention[0]["reason"] != "awaiting earlier step completion"
    # the held approval keeps its approved status, nothing was merged away
    state_after = StateStore(root).load()
    held = next(
        a for a in state_after["approvals"]
        if a["plan_id"] == plan_id and a["step"] == 4
    )
    assert held["status"] == "approved"


# --------------------------------------------------------------------------
# Unchanged: a group is judged only once the whole group has replied.
# --------------------------------------------------------------------------


def test_partial_group_reply_derives_no_verdict_and_no_iteration_round(
    tmp_path: Path, monkeypatch
) -> None:
    root, plan_id = _group_project(tmp_path, monkeypatch)
    for _ in range(2):
        _, wave = _run(["run-loop", "--plan-id", plan_id, "--confirm"])
        _reply_file(root, wave["dispatched"][0]["message_id"])
    _, wave3 = _run(["run-loop", "--plan-id", plan_id, "--confirm"])
    assert len(wave3["dispatched"]) == 2
    step_count_before = len(_steps(root, plan_id))

    # only ONE member of the parallel group reports, and it reports a fail
    first = wave3["dispatched"][0]
    _reply_file(
        root, first["message_id"],
        extra=f"verdict: {json.dumps(_VERDICT_FAIL, ensure_ascii=False)}\n",
    )

    _, wave4 = _run(["run-loop", "--plan-id", plan_id, "--confirm"])

    assert [c["message_id"] for c in wave4.get("captured_replies", [])] == [first["message_id"]]
    assert wave4.get("review_iterations") in (None, [])
    assert len(_steps(root, plan_id)) == step_count_before


# --------------------------------------------------------------------------
# Read-only projection: a human can see which steps may run together.
# --------------------------------------------------------------------------


def test_plan_status_projects_the_linear_chain(tmp_path: Path, monkeypatch) -> None:
    root = _prepare(tmp_path, monkeypatch)
    plan_id = _seed_plan(root, "projection")
    assert [step["depends_on"] for step in _steps(root, plan_id)] == [[], [1], [2]]


def test_plan_status_projects_the_group_fan_out(tmp_path: Path, monkeypatch) -> None:
    root, plan_id = _group_project(tmp_path, monkeypatch)
    steps = _steps(root, plan_id)
    assert [step["depends_on"] for step in steps] == [[], [1], [2], [2]]


def test_plan_status_projection_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    root, plan_id = _group_project(tmp_path, monkeypatch)
    state_path = root / ".agentdeck" / "state" / "state.json"
    before = state_path.read_bytes()

    code, payload = _run(["plan", "status", "--plan-id", plan_id])

    assert code == 0
    assert [step["depends_on"] for step in payload["steps"]] == [[], [1], [2], [2]]
    assert state_path.read_bytes() == before
