from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentdeck.conversation.bindings import execution_digest
from agentdeck.conversation.models import build_preview_binding_record
from agentdeck.conversation.router import (
    ConversationRouter,
    RoutingContext,
    build_project_setup_preview,
    confirm_project_setup,
    execute_bound_preview,
)


NOW = datetime(2026, 7, 13, tzinfo=timezone.utc)


def _context(**overrides: object) -> RoutingContext:
    values = {
        "initialized": True,
        "leader_ready": True,
        "pending_preview": None,
    }
    values.update(overrides)
    return RoutingContext(**values)


@pytest.mark.parametrize("text", ["/quit", "quit", "exit", "退出"])
def test_exit_words_route_before_every_other_intent(text: str) -> None:
    assert ConversationRouter().classify(text, _context()).kind == "exit"


@pytest.mark.parametrize(
    ("text", "kind", "command"),
    [
        ("/help", "deterministic", "help"),
        ("/leader", "deterministic", "leader"),
        ("/model", "deterministic", "model"),
        ("/team", "deterministic", "team"),
        ("/role", "deterministic", "role"),
        ("/status", "deterministic", "status"),
        ("/approvals", "deterministic", "approvals"),
        ("/trace msg_abc", "deterministic", "trace"),
        ("/takeover reviewer", "deterministic", "takeover"),
        ("/return-control", "deterministic", "return_control"),
    ],
)
def test_minimal_slash_registry_is_deterministic(
    text: str, kind: str, command: str
) -> None:
    decision = ConversationRouter().classify(text, _context())

    assert decision.kind == kind
    assert decision.command == command
    assert decision.may_call_leader is False


def test_deterministic_intents_work_without_configured_leader() -> None:
    router = ConversationRouter()
    context = _context(leader_ready=False)

    assert router.classify("查看当前状态", context).command == "status"
    assert router.classify("查看审批", context).command == "approvals"
    blocked = router.classify("请设计一个多智能体功能", context)
    assert blocked.kind == "leader_setup_preview"
    assert blocked.may_call_leader is False


def test_uninitialized_project_returns_setup_preview_without_writing(tmp_path: Path) -> None:
    before = list(tmp_path.iterdir())

    decision = ConversationRouter().classify(
        "请初始化并开始工作", _context(initialized=False, leader_ready=False)
    )

    assert decision.kind == "project_setup_preview"
    assert decision.requires_confirmation is True
    assert list(tmp_path.iterdir()) == before


def test_natural_confirmation_targets_only_current_pending_preview() -> None:
    pending = {"preview_id": "p1", "preview_kind": "mission_confirm", "state": "pending"}

    decision = ConversationRouter().classify(
        "确认执行当前预览", _context(pending_preview=pending)
    )

    assert decision.kind == "confirm_preview"
    assert decision.preview_id == "p1"
    assert decision.may_call_leader is False


def test_confirmation_without_pending_preview_is_not_execution() -> None:
    decision = ConversationRouter().classify("确认执行", _context())

    assert decision.kind == "blocked"
    assert decision.blocker == "no pending preview"


def test_open_request_with_ready_leader_routes_to_one_leader_call() -> None:
    decision = ConversationRouter().classify("请设计多智能体协作功能", _context())

    assert decision.kind == "leader_request"
    assert decision.may_call_leader is True


def test_bound_preview_executes_only_after_exact_revalidation() -> None:
    facts = {"control_kind": "mission_confirm", "action_id": "mis_example"}
    binding = build_preview_binding_record(
        "p1",
        conversation_id="c1",
        turn_id="t1",
        preview_kind="mission_confirm",
        execution_digest=execution_digest(facts),
        expires_at="2026-07-13T01:00:00+00:00",
        created_at="2026-07-13T00:00:00+00:00",
    )
    binding["state"] = "pending"
    calls: list[str] = []

    result = execute_bound_preview(
        binding,
        facts,
        now=NOW,
        execute=lambda consumed: calls.append(str(consumed["preview_id"])) or "done",
    )

    assert result == "done"
    assert calls == ["p1"]
    assert binding["state"] == "pending"


def test_bound_preview_drift_is_zero_execution_and_zero_mutation() -> None:
    facts = {"control_kind": "mission_confirm", "action_id": "mis_example"}
    binding = build_preview_binding_record(
        "p1",
        conversation_id="c1",
        turn_id=None,
        preview_kind="mission_confirm",
        execution_digest=execution_digest(facts),
        expires_at="2026-07-13T01:00:00+00:00",
        created_at="2026-07-13T00:00:00+00:00",
    )
    binding["state"] = "pending"
    before = deepcopy(binding)

    with pytest.raises(ValueError, match="preview state drift"):
        execute_bound_preview(
            binding,
            {**facts, "action_id": "mis_changed"},
            now=NOW,
            execute=lambda _binding: pytest.fail("must not execute"),
        )

    assert binding == before


def test_project_setup_preview_is_memory_only_and_exact_bound(tmp_path: Path) -> None:
    before = list(tmp_path.iterdir())

    preview = build_project_setup_preview(tmp_path, now=NOW)

    assert preview["mode"] == "project_setup_preview"
    assert preview["binding"]["state"] == "pending"
    assert preview["project_root"] == str(tmp_path.resolve())
    assert preview["confirm_command"] == "agentdeck project init"
    assert list(tmp_path.iterdir()) == before


def test_project_setup_confirmation_rechecks_marker_before_first_write(tmp_path: Path) -> None:
    preview = build_project_setup_preview(tmp_path, now=NOW)
    (tmp_path / ".agentdeck").mkdir()
    (tmp_path / ".agentdeck" / "config.toml").write_text("drift", encoding="utf-8")
    calls: list[str] = []

    with pytest.raises(ValueError, match="preview state drift"):
        confirm_project_setup(
            preview["binding"],
            tmp_path,
            now=NOW,
            initialize=lambda _root: calls.append("init") or {},
            append_audit=lambda _result: calls.append("audit"),
        )

    assert calls == []


def test_project_setup_audit_failure_never_rolls_back_successful_init(tmp_path: Path) -> None:
    preview = build_project_setup_preview(tmp_path, now=NOW)

    def initialize(root: Path) -> dict[str, object]:
        (root / ".agentdeck").mkdir()
        (root / ".agentdeck" / "config.toml").write_text("initialized", encoding="utf-8")
        return {"config_path": ".agentdeck/config.toml"}

    result = confirm_project_setup(
        preview["binding"],
        tmp_path,
        now=NOW,
        initialize=initialize,
        append_audit=lambda _result: (_ for _ in ()).throw(OSError("audit disk")),
    )

    assert result["status"] == "initialized_with_audit_blocker"
    assert (tmp_path / ".agentdeck" / "config.toml").read_text() == "initialized"
    assert result["blocker"] == "project initialized but conversation audit is pending"
