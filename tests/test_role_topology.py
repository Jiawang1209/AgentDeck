from __future__ import annotations

from agentdeck.role_topology import (
    IMPLEMENTATION_ROLE_HINTS,
    REVIEW_ROLE_HINTS,
    ROLE_BINDING_KINDS,
    ROLE_BINDING_STATUSES,
    ROLE_LIFECYCLES,
    ROLE_SPECS,
    ROLE_TOPOLOGY_LAYERS,
    resolve_worker_role,
)


def test_role_specs_cover_the_six_north_star_layers_in_order():
    assert tuple(spec["role"] for spec in ROLE_SPECS) == (
        "frontdesk",
        "planner",
        "orchestrator",
        "coder",
        "code_reviewer",
        "round_reviewer",
    )
    for spec in ROLE_SPECS:
        assert spec["layer"] in ROLE_TOPOLOGY_LAYERS
        assert spec["binding_kind"] in ROLE_BINDING_KINDS
        assert spec["lifecycle"] in ROLE_LIFECYCLES


def test_closed_enums_are_closed():
    assert ROLE_TOPOLOGY_LAYERS == ("intake", "orchestration", "work", "acceptance")
    assert ROLE_BINDING_KINDS == ("command", "logical_leader", "worker_agent")
    assert ROLE_BINDING_STATUSES == ("bound", "unbound", "ambiguous")
    assert ROLE_LIFECYCLES == ("persistent", "task_scoped", "on_demand")


def test_role_specs_bind_each_layer_to_exactly_one_binding_kind():
    """三种绑定方式的分配是设计支点:拍平成一张 agent 表就会撒谎。"""
    by_role = {spec["role"]: spec for spec in ROLE_SPECS}
    assert by_role["frontdesk"]["binding_kind"] == "command"
    assert by_role["planner"]["binding_kind"] == "logical_leader"
    assert by_role["orchestrator"]["binding_kind"] == "logical_leader"
    for role in ("coder", "code_reviewer", "round_reviewer"):
        assert by_role[role]["binding_kind"] == "worker_agent"
    assert by_role["round_reviewer"]["lifecycle"] == "on_demand"
    assert by_role["round_reviewer"]["layer"] == "acceptance"


def test_resolve_worker_role_binds_a_single_match():
    agents = [
        {"agent_id": "coder", "role": "implementation"},
        {"agent_id": "reviewer", "role": "review"},
    ]
    assert resolve_worker_role(agents, IMPLEMENTATION_ROLE_HINTS) == ("coder", "bound", [])


def test_resolve_worker_role_reports_unbound_with_no_match():
    agents = [{"agent_id": "reviewer", "role": "review"}]
    assert resolve_worker_role(agents, IMPLEMENTATION_ROLE_HINTS) == (None, "unbound", [])


def test_resolve_worker_role_reports_ambiguous_and_never_picks_one():
    """fail-closed:两个同为实现角色时绝不挑第一个。"""
    agents = [
        {"agent_id": "coder_a", "role": "implementation"},
        {"agent_id": "coder_b", "role": "coding"},
    ]
    agent_id, status, candidates = resolve_worker_role(agents, IMPLEMENTATION_ROLE_HINTS)
    assert agent_id is None
    assert status == "ambiguous"
    assert candidates == ["coder_a", "coder_b"]


def test_resolve_worker_role_matches_case_insensitively_and_ignores_bad_rows():
    agents = [
        {"agent_id": "c", "role": "IMPLEMENTATION"},
        "not a dict",
        {"agent_id": "x"},
    ]
    assert resolve_worker_role(agents, IMPLEMENTATION_ROLE_HINTS) == ("c", "bound", [])


def test_review_hints_do_not_match_implementation_roles():
    agents = [{"agent_id": "coder", "role": "implementation"}]
    assert resolve_worker_role(agents, REVIEW_ROLE_HINTS) == (None, "unbound", [])


def test_resolve_worker_role_keeps_candidate_order_and_handles_empty_input():
    agents = [
        {"agent_id": "z", "role": "code review"},
        {"agent_id": "a", "role": "审查"},
        {"agent_id": "m", "role": "planning"},
    ]
    assert resolve_worker_role(agents, REVIEW_ROLE_HINTS) == (None, "ambiguous", ["z", "a"])
    assert resolve_worker_role([], REVIEW_ROLE_HINTS) == (None, "unbound", [])
    assert resolve_worker_role(None, REVIEW_ROLE_HINTS) == (None, "unbound", [])


def test_role_topology_module_is_pure_and_imports_no_io_modules():
    """纯推导模块:不得 import cli/state/config 或任何写路径。"""
    import agentdeck.role_topology as module

    source = module.__file__
    assert source is not None
    text = open(source, encoding="utf-8").read()
    for forbidden in ("from .cli", "from .state", "import subprocess", "from .config"):
        assert forbidden not in text
