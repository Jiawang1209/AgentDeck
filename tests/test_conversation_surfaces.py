from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from agentdeck import cli
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
