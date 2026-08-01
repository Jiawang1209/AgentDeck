from __future__ import annotations

from agentdeck.role_topology import (
    IMPLEMENTATION_ROLE_HINTS,
    REVIEW_ROLE_HINTS,
    ROLE_BINDING_KINDS,
    ROLE_BINDING_STATUSES,
    ROLE_LIFECYCLES,
    ROLE_SPECS,
    ROLE_TOPOLOGY_LAYERS,
    WORKER_ROLE_HINTS,
    resolve_worker_role,
    resolve_worker_roles,
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


def test_worker_role_hints_declare_the_two_hint_matched_layers_in_order():
    assert WORKER_ROLE_HINTS == (
        ("coder", IMPLEMENTATION_ROLE_HINTS),
        ("code_reviewer", REVIEW_ROLE_HINTS),
    )


def test_resolve_worker_roles_binds_each_layer_when_roles_are_distinct():
    agents = [
        {"agent_id": "coder", "role": "implementation"},
        {"agent_id": "reviewer", "role": "code review"},
    ]

    resolved = resolve_worker_roles(agents)

    assert resolved["coder"] == {
        "agent_id": "coder",
        "binding_status": "bound",
        "candidates": [],
        "conflicts": [],
    }
    assert resolved["code_reviewer"] == {
        "agent_id": "reviewer",
        "binding_status": "bound",
        "candidates": [],
        "conflicts": [],
    }


def test_a_role_matching_two_layers_binds_neither_layer():
    """跨层碰撞 fail-closed:同时读作实现和审查的角色对两层都是模糊证据。"""
    agents = [
        {"agent_id": "planner", "role": "planning"},
        {"agent_id": "hybrid", "role": "implementation review"},
    ]

    resolved = resolve_worker_roles(agents)

    for layer in ("coder", "code_reviewer"):
        assert resolved[layer]["agent_id"] is None, layer
        assert resolved[layer]["binding_status"] == "ambiguous", layer
        assert resolved[layer]["candidates"] == ["hybrid"], layer
        assert resolved[layer]["conflicts"] == [
            {"agent_id": "hybrid", "layers": ["coder", "code_reviewer"]}
        ], layer


def test_a_reviewer_role_carrying_an_implementation_hint_never_steals_the_coder_layer():
    """真正的实现者不匹配任何提示时,绝不能把 coder 层绑到 reviewer 身上。"""
    agents = [
        {"agent_id": "coder", "role": "开发编码"},
        {"agent_id": "reviewer", "role": "代码审查与实现验证"},
    ]

    resolved = resolve_worker_roles(agents)

    assert resolved["coder"]["agent_id"] is None
    assert resolved["coder"]["binding_status"] == "ambiguous"
    assert resolved["coder"]["candidates"] == ["reviewer"]
    assert resolved["code_reviewer"]["agent_id"] is None
    assert resolved["code_reviewer"]["binding_status"] == "ambiguous"
    assert resolved["code_reviewer"]["conflicts"] == [
        {"agent_id": "reviewer", "layers": ["coder", "code_reviewer"]}
    ]


def test_resolve_worker_roles_keeps_intra_layer_ambiguity_intact():
    agents = [
        {"agent_id": "coder_a", "role": "implementation"},
        {"agent_id": "coder_b", "role": "coding"},
        {"agent_id": "reviewer", "role": "review"},
    ]

    resolved = resolve_worker_roles(agents)

    assert resolved["coder"]["binding_status"] == "ambiguous"
    assert resolved["coder"]["candidates"] == ["coder_a", "coder_b"]
    assert resolved["coder"]["conflicts"] == []
    assert resolved["code_reviewer"]["agent_id"] == "reviewer"


def test_resolve_worker_roles_reports_unbound_layers_without_conflicts():
    resolved = resolve_worker_roles([{"agent_id": "planner", "role": "planning"}])

    for layer in ("coder", "code_reviewer"):
        assert resolved[layer] == {
            "agent_id": None,
            "binding_status": "unbound",
            "candidates": [],
            "conflicts": [],
        }
    assert resolve_worker_roles(None)["coder"]["binding_status"] == "unbound"


def test_resolve_worker_role_shim_is_cross_layer_fail_closed_too():
    """单层入口也不得 fail-open —— 它就是本缺陷的入口。"""
    agents = [
        {"agent_id": "coder", "role": "开发编码"},
        {"agent_id": "reviewer", "role": "代码审查与实现验证"},
    ]

    assert resolve_worker_role(agents, IMPLEMENTATION_ROLE_HINTS) == (
        None,
        "ambiguous",
        ["reviewer"],
    )
    assert resolve_worker_role(agents, REVIEW_ROLE_HINTS) == (None, "ambiguous", ["reviewer"])


def test_role_topology_module_is_pure_and_imports_no_io_modules():
    """纯推导模块:不得 import cli/state/config 或任何写路径。"""
    import agentdeck.role_topology as module

    source = module.__file__
    assert source is not None
    text = open(source, encoding="utf-8").read()
    for forbidden in ("from .cli", "from .state", "import subprocess", "from .config"):
        assert forbidden not in text
