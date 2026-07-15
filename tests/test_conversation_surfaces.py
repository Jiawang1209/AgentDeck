from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from agentdeck import cli
from agentdeck import contracts as contracts_module
from agentdeck.config import load_config, write_default_config
from agentdeck.contracts import (
    validate_conversation_runtime_contract,
    validate_leader_backend_contract,
    validate_worker_transport_contract,
    validate_workbench_contract,
)
from agentdeck.conversation.session import ConversationSession
from agentdeck.state import StateStore


def _project(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    write_default_config(tmp_path)
    return load_config(tmp_path), StateStore(tmp_path)


def _tree(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_workbench_conversation_surfaces_share_project_view_truth(tmp_path: Path) -> None:
    config, store = _project(tmp_path)
    session = ConversationSession(root=tmp_path, config=config, store=store)
    session.handle("/status")
    project_view = asdict(store.project_view(config))
    before = _tree(tmp_path)

    workbench = cli._workbench_snapshot_payload(project_view, store)

    conversation = workbench["conversation_runtime_card"]
    assert conversation["conversation_id"] == project_view["conversation"]["latest_conversation_id"]
    assert conversation["state"] == project_view["conversation"]["latest_conversation_state"]
    assert conversation["pending_preview"] == project_view["conversation"]["pending_preview"]
    assert validate_conversation_runtime_contract(conversation) == {"ok": True, "errors": []}

    mission_recovery = workbench["mission_recovery_card"]
    assert mission_recovery == project_view["mission_recovery"]
    assert contracts_module.validate_mission_recovery_contract(mission_recovery) == {
        "ok": True,
        "errors": [],
    }

    leader = workbench["leader_backend_card"]
    assert leader["identity"]["provider"] == project_view["leader"]["provider"]
    assert leader["identity"]["model"] == project_view["leader"]["model"]
    assert validate_leader_backend_contract(leader) == {"ok": True, "errors": []}

    workers = workbench["worker_transport_card"]
    assert workers["count"] == len(config.agents)
    assert [item["agent_id"] for item in workers["items"]] == [
        agent.agent_id for agent in config.agents
    ]
    assert all(validate_worker_transport_contract(item)["ok"] for item in workers["items"])

    assert workbench["contracts_card"]["conversation_runtime_contract"] == (
        "agentdeck contract conversation-runtime"
    )
    assert workbench["contracts_card"]["leader_backend_contract"] == (
        "agentdeck contract leader-backend"
    )
    assert workbench["contracts_card"]["worker_transport_contract"] == (
        "agentdeck contract worker-transport"
    )
    assert validate_workbench_contract(workbench) == {"ok": True, "errors": []}
    assert _tree(tmp_path) == before


def test_semantic_preview_surfaces_share_one_compact_non_leaking_authority(
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()
    write_default_config(tmp_path)
    config_path = tmp_path / ".agentdeck" / "config.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        .replace('provider = "deepseek"', 'provider = "fake"', 1)
        .replace('model = "deepseek-chat"', 'model = "fake-plan"', 1),
        encoding="utf-8",
    )
    config = load_config(tmp_path)
    store = StateStore(tmp_path)
    session = ConversationSession(root=tmp_path, config=config, store=store)
    response = session.handle(
        "Have planner and reviewer complete 4 steps strictly sequentially. "
        "The phases must be exactly implementation, review, revision, acceptance: "
        "First, planner creates artifact.txt with content exactly draft-v1 newline; "
        "Second, reviewer performs a read-only review and requires accepted-v2; "
        "Third, planner updates artifact.txt to exactly accepted-v2 newline; "
        "Fourth, reviewer performs read-only verification of the exact bytes. "
        "There are 4 steps."
    )
    assert response.kind == "mission_preview"
    before = _tree(tmp_path)

    project_view = asdict(store.project_view(config))
    workbench = cli._workbench_snapshot_payload(project_view, store)
    plan_card = project_view["plans"]["items"][-1]["semantic_authority"]
    mission_card = project_view["missions"]["items"][-1]["semantic_authority"]

    assert plan_card == mission_card == workbench["mission_card"][
        "semantic_authority"
    ]
    assert set(mission_card) == {
        "schema_version",
        "state",
        "authority_hash",
        "requirement_count",
        "proposed_effect_count",
        "unresolved_count",
        "compiled_step_count",
        "blockers",
    }
    assert mission_card["state"] == "preview"
    assert mission_card["requirement_count"] == 4
    assert mission_card["compiled_step_count"] == 4
    assert validate_workbench_contract(workbench) == {"ok": True, "errors": []}
    rendered = repr(
        (plan_card, mission_card, workbench["mission_card"]["semantic_authority"])
    )
    for forbidden in (
        "artifact.txt",
        "draft-v1",
        "accepted-v2",
        "semantic_steps",
        "prompt",
        "transcript",
        "secret_ref",
    ):
        assert forbidden not in rendered
    assert _tree(tmp_path) == before


def test_natural_language_migration_preview_is_read_only_and_never_calls_provider(
    tmp_path: Path,
) -> None:
    config, store = _project(tmp_path)

    class FailingGateway:
        def describe(self, _leader):
            return type("Description", (), {"readiness": "ready"})()

        def generate_mission(self, *_args, **_kwargs):
            raise AssertionError("migration preview must not call provider")

    session = ConversationSession(
        root=tmp_path,
        config=config,
        store=store,
        leader_gateway=FailingGateway(),
    )
    before = _tree(tmp_path)

    response = session.handle("预览项目迁移")

    assert response.kind == "migration_preview"
    assert response.payload["mode"] == "migration_preview"
    assert _tree(tmp_path) == before
