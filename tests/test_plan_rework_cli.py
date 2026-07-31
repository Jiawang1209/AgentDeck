from __future__ import annotations

import json
from pathlib import Path

from agentdeck import cli
from agentdeck.config import write_default_config
from agentdeck.state import StateStore

# pytest 默认 importmode=prepend 会把 tests/ 加进 sys.path,同目录直接导入
from test_review_iteration import _state


def prepare_seeded_project(tmp_path: Path, monkeypatch, overall: str = "fail") -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    write_default_config(root)
    store = StateStore(root)
    state = store.load()
    seed = _state(overall)
    for key in ("plans", "approvals", "messages", "replies"):
        state[key] = seed[key]
    store.save(state)
    monkeypatch.chdir(root)
    return root


def test_rework_gate_matrix_refuses_with_zero_writes(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_seeded_project(tmp_path, monkeypatch, "fail")
    store = StateStore(root)
    before = store.load()

    assert cli.main(["plan", "rework", "--plan-id", "pln_1"]) == 1
    assert "confirm" in capsys.readouterr().err
    assert cli.main(["plan", "rework", "--plan-id", "pln_ghost", "--confirm"]) == 1
    assert "no_plan" in capsys.readouterr().err
    assert store.load() == before


def test_rework_refuses_on_pass_verdict(tmp_path, monkeypatch, capsys) -> None:
    prepare_seeded_project(tmp_path, monkeypatch, "pass")
    assert cli.main(["plan", "rework", "--plan-id", "pln_1", "--confirm"]) == 1
    assert "verdict_pass" in capsys.readouterr().err


def test_rework_appends_and_reports(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_seeded_project(tmp_path, monkeypatch, "fail")
    assert cli.main(["plan", "rework", "--plan-id", "pln_1", "--confirm"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "plan_rework"
    assert payload["ok"] is True
    assert payload["plan_id"] == "pln_1"
    assert payload["round"] == 1
    assert payload["steps"] == [3, 4]
    assert len(payload["approval_ids"]) == 2
    assert payload["next_command"] == "agentdeck approval list"
    assert payload["requires_explicit_user"] is True
    assert payload["safety"] == "explicit_user"
    steps = StateStore(root).load()["plans"][0]["plan"]["steps"]
    assert len(steps) == 4
    # 同一 reply 第二次拒绝
    assert cli.main(["plan", "rework", "--plan-id", "pln_1", "--confirm"]) == 1
    assert "already_triggered" in capsys.readouterr().err


def test_rework_respects_budget(tmp_path, monkeypatch, capsys) -> None:
    root = prepare_seeded_project(tmp_path, monkeypatch, "fail")
    config_path = root / ".agentdeck" / "config.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + "\n[autonomous]\nmax_review_rounds = 0\n",
        encoding="utf-8",
    )
    assert cli.main(["plan", "rework", "--plan-id", "pln_1", "--confirm"]) == 1
    assert "rounds_exhausted" in capsys.readouterr().err
