from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import re

import pytest

from agentdeck.models import AgentSpec, LeaderConfig, ProjectConfig, RuntimeConfig
from agentdeck.providers.base import LeaderPlanRequest, LeaderPlanResult
from agentdeck.providers.plan_schema import (
    LEADER_PLAN_SCHEMA_VERSION,
    SEMANTIC_LEADER_PLAN_SCHEMA_VERSION,
    ProviderPlanValidationError,
    build_leader_generation_provenance,
    build_leader_plan_schema,
    canonical_leader_plan_schema_hash,
    leader_plan_authority,
    validate_leader_generation_provenance,
    validate_provider_plan_schema,
)
from agentdeck.providers.semantic_plan_schema import (
    SemanticPlanSchemaAuthorityError,
    build_semantic_leader_plan_schema,
    resolve_semantic_leader_plan_context,
)
from agentdeck.semantic_planning import semantic_context_text_is_safe


def _config(root: str = "/private/project-a") -> ProjectConfig:
    return ProjectConfig(
        name="schema-test",
        root=root,
        leader=LeaderConfig(),
        agents=(
            AgentSpec("planner", "planning", "codex-cli", "codex"),
            AgentSpec("reviewer", "review", "claude-cli", "claude"),
            AgentSpec("builder", "implementation", "codex-cli", "codex"),
        ),
        runtime=RuntimeConfig(),
    )


def _request(**changes: object) -> LeaderPlanRequest:
    request = LeaderPlanRequest(
        task="Build a canonical plan",
        config=_config(),
        selected_agent_ids=("reviewer", "planner"),
        step_count=2,
    )
    return replace(request, **changes)


def _semantic_authority() -> dict[str, object]:
    return {
        "schema_version": "mission-semantic-authority/v1",
        "source_message_hash": f"sha256:{'a' * 64}",
        "requirements": [
            {
                "requirement_id": "req_111111111111",
                "kind": "create",
                "target": "artifact.txt",
                "operation": "create",
                "phase": "implementation",
                "agent_id": "reviewer",
                "sensitivity": "ordinary",
                "literal": "draft-v1\n",
            },
            {
                "requirement_id": "req_222222222222",
                "kind": "review",
                "target": "artifact.txt",
                "operation": "review",
                "phase": "review",
                "agent_id": "planner",
                "sensitivity": "ordinary",
                "literal": "accepted-v2\n",
            },
        ],
        "proposed_effects": [],
        "unresolved": [],
    }


def _semantic_request(**changes: object) -> LeaderPlanRequest:
    changes.setdefault("semantic_authority", _semantic_authority())
    return _request(**changes)


def _open_semantic_authority() -> dict[str, object]:
    authority = _semantic_authority()
    authority["requirements"] = []
    return authority


def _phased_semantic_authority(
    selected_agent_ids: tuple[str, ...], *, step_count: int
) -> dict[str, object]:
    requirements = []
    for index in range(step_count):
        requirements.append(
            {
                "requirement_id": f"req_{index:012x}",
                "kind": "read",
                "target": f"artifact_{index}.txt",
                "operation": "read",
                "phase": f"phase_{index:02d}",
                "agent_id": selected_agent_ids[index % len(selected_agent_ids)],
                "sensitivity": "ordinary",
                "literal": f"value_{index}",
            }
        )
    return {
        "schema_version": "mission-semantic-authority/v1",
        "source_message_hash": f"sha256:{'d' * 64}",
        "requirements": requirements,
        "proposed_effects": [],
        "unresolved": [],
    }


def _assert_closed_object_schemas(value: object) -> None:
    if type(value) is dict:
        if value.get("type") == "object":
            assert value.get("additionalProperties") is False
            assert set(value.get("required", [])) == set(value.get("properties", {}))
        for child in value.values():
            _assert_closed_object_schemas(child)
    elif type(value) is list:
        for child in value:
            _assert_closed_object_schemas(child)


def _resolve_schema_ref(
    schema: dict[str, object], reference: str
) -> dict[str, object]:
    assert reference.startswith("#/$defs/")
    return schema["$defs"][reference.removeprefix("#/$defs/")]


def _semantic_step_branch(
    schema: dict[str, object], *, agent_id: str, phase: str | None
) -> dict[str, object]:
    branches = schema["properties"]["steps"]["items"]["anyOf"]
    for branch_ref in branches:
        branch = _resolve_schema_ref(schema, branch_ref["$ref"])
        properties = branch["properties"]
        agent_schema = _resolve_schema_ref(
            schema,
            properties["agent_id"]["$ref"],
        )
        if agent_schema["const"] != agent_id:
            continue
        if phase is None or properties["phase"].get("const") == phase:
            return branch
    raise AssertionError("semantic step branch not found")


def _scoped_authority_ref_ids(
    schema: dict[str, object], *, agent_id: str, phase: str
) -> tuple[str, ...]:
    branch = _semantic_step_branch(schema, agent_id=agent_id, phase=phase)
    refs_schema = _resolve_schema_ref(
        schema,
        branch["properties"]["authority_refs"]["$ref"],
    )
    return tuple(refs_schema["items"]["enum"])


def _scoped_authority_refs_accept(
    schema: dict[str, object], *, agent_id: str, phase: str, refs: list[str]
) -> bool:
    allowed = set(
        _scoped_authority_ref_ids(
            schema,
            agent_id=agent_id,
            phase=phase,
        )
    )
    return all(ref in allowed for ref in refs)


def _string_schema_accepts(schema: dict[str, object], value: str) -> bool:
    if len(value) < schema.get("minLength", 0) or len(value) > schema.get(
        "maxLength", len(value)
    ):
        return False
    pattern = schema.get("pattern")
    if pattern is not None and re.search(pattern, value) is None:
        return False
    return True


_UNSUPPORTED_STRICT_SCHEMA_KEYS = frozenset(
    {
        "allOf",
        "not",
        "if",
        "then",
        "else",
        "dependentRequired",
        "dependentSchemas",
        "prefixItems",
        "uniqueItems",
    }
)
_SUPPORTED_STRICT_SCHEMA_KEYS = frozenset(
    {
        "$id",
        "$defs",
        "$ref",
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "anyOf",
        "enum",
        "const",
        "pattern",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "minItems",
        "maxItems",
    }
)


def _assert_portable_strict_schema(value: object) -> None:
    assert type(value) is dict
    assert not (_UNSUPPORTED_STRICT_SCHEMA_KEYS & set(value))
    assert set(value) <= _SUPPORTED_STRICT_SCHEMA_KEYS
    for map_key in ("properties", "$defs"):
        children = value.get(map_key)
        if children is not None:
            assert type(children) is dict
            for child in children.values():
                _assert_portable_strict_schema(child)
    items = value.get("items")
    if items is not None:
        assert type(items) is dict
        _assert_portable_strict_schema(items)
    alternatives = value.get("anyOf")
    if alternatives is not None:
        assert type(alternatives) is list
        for child in alternatives:
            _assert_portable_strict_schema(child)


def _strict_schema_budget(schema: dict[str, object]) -> dict[str, int]:
    property_count = 0
    string_budget = 0
    enum_total = 0
    enum_max = 0

    def count_raw(value: object) -> None:
        nonlocal property_count, string_budget, enum_total, enum_max
        if type(value) is not dict:
            if type(value) is list:
                for item in value:
                    count_raw(item)
            return
        properties = value.get("properties")
        if type(properties) is dict:
            property_count += len(properties)
            string_budget += sum(len(name) for name in properties)
        definitions = value.get("$defs")
        if type(definitions) is dict:
            string_budget += sum(len(name) for name in definitions)
        enum = value.get("enum")
        if type(enum) is list:
            enum_total += len(enum)
            enum_max = max(enum_max, len(enum))
            string_budget += sum(len(item) for item in enum if type(item) is str)
        const = value.get("const")
        if type(const) is str:
            string_budget += len(const)
        for child in value.values():
            count_raw(child)

    definitions = schema.get("$defs", {})

    def schema_depth(
        value: dict[str, object], depth: int, active_refs: frozenset[str]
    ) -> int:
        reference = value.get("$ref")
        if type(reference) is str and reference.startswith("#/$defs/"):
            name = reference.removeprefix("#/$defs/")
            if name in active_refs:
                return depth
            return schema_depth(
                definitions[name],
                depth,
                active_refs | {name},
            )
        depths = [depth]
        properties = value.get("properties")
        if type(properties) is dict:
            depths.extend(
                schema_depth(child, depth + 1, active_refs)
                for child in properties.values()
            )
        items = value.get("items")
        if type(items) is dict:
            depths.append(schema_depth(items, depth + 1, active_refs))
        alternatives = value.get("anyOf")
        if type(alternatives) is list:
            depths.extend(
                schema_depth(child, depth, active_refs) for child in alternatives
            )
        return max(depths)

    count_raw(schema)
    return {
        "property_count": property_count,
        "string_budget": string_budget,
        "enum_total": enum_total,
        "enum_max": enum_max,
        "max_depth": schema_depth(schema, 1, frozenset()),
    }


def test_schema_freezes_worker_order_step_count_and_approval() -> None:
    schema = build_leader_plan_schema(_request())

    assert schema["$id"] == LEADER_PLAN_SCHEMA_VERSION == "leader-plan/v1"
    assert canonical_leader_plan_schema_hash(schema) == (
        "sha256:5b43467ee5cbf4f407c1c58b628b90bf03f2c30eaa4fa59cf660cc1aaa7da0a4"
    )
    assert schema["required"] == ["goal", "summary", "steps"]
    assert schema["additionalProperties"] is False
    steps = schema["properties"]["steps"]
    assert steps["minItems"] == steps["maxItems"] == 2
    step_schema = steps["items"]
    assert step_schema["required"] == [
        "step",
        "agent_id",
        "role",
        "task",
        "risk",
        "requires_approval",
    ]
    assert step_schema["additionalProperties"] is False
    assert step_schema["properties"]["agent_id"]["enum"] == ["reviewer", "planner"]
    assert step_schema["properties"]["requires_approval"] == {"type": "boolean", "const": True}


def test_schema_hash_is_deterministic_and_project_path_free() -> None:
    request = _request()
    other_path_request = replace(request, config=replace(request.config, root="/secret/other-project"))

    schema = build_leader_plan_schema(request)
    other_schema = build_leader_plan_schema(other_path_request)
    digest = canonical_leader_plan_schema_hash(schema)
    encoded = json.dumps(
        schema,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    expected = f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    assert schema == other_schema
    assert "/private/project-a" not in repr(schema)
    assert "/secret/other-project" not in repr(schema)
    assert digest == expected
    assert digest == canonical_leader_plan_schema_hash(other_schema)
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", digest)


def test_semantic_schema_is_exact_closed_and_contains_only_safe_authority() -> None:
    schema = build_leader_plan_schema(_semantic_request())

    assert schema["$id"] == SEMANTIC_LEADER_PLAN_SCHEMA_VERSION == "leader-semantic-plan/v1"
    assert schema["required"] == ["goal", "summary", "steps"]
    steps = schema["properties"]["steps"]
    assert steps["minItems"] == steps["maxItems"] == 2
    assert set(steps["items"]) == {"anyOf"}
    assert len(steps["items"]["anyOf"]) == 2
    expected_agents = ("reviewer", "planner")
    expected_roles = ("review", "planning")
    expected_phases = ("implementation", "review")
    required_fields = [
        "step",
        "agent_id",
        "role",
        "phase",
        "authority_refs",
        "proposed_effects",
        "verification",
        "risk",
        "requires_approval",
    ]
    for index, (agent_id, role, phase) in enumerate(
        zip(expected_agents, expected_roles, expected_phases),
        start=1,
    ):
        step_schema = _semantic_step_branch(
            schema,
            agent_id=agent_id,
            phase=phase,
        )
        properties = step_schema["properties"]
        assert step_schema["required"] == required_fields
        assert properties["step"] == {
            "type": "integer",
            "minimum": 1,
            "maximum": 2,
        }
        assert set(properties["agent_id"]) == {"$ref"}
        assert _resolve_schema_ref(schema, properties["agent_id"]["$ref"]) == {
            "type": "string",
            "const": agent_id,
        }
        assert set(properties["role"]) == {"$ref"}
        assert _resolve_schema_ref(schema, properties["role"]["$ref"]) == {
            "type": "string",
            "const": role,
        }
        assert properties["phase"] == {
            "type": "string",
            "const": phase,
        }
        refs_schema = _resolve_schema_ref(
            schema,
            properties["authority_refs"]["$ref"],
        )
        assert refs_schema["maxItems"] == len(refs_schema["items"]["enum"])
        assert properties["risk"] == {"type": "string", "const": "low"}
        assert properties["requires_approval"] == {"type": "boolean", "const": True}
        proposal_array = _resolve_schema_ref(
            schema,
            properties["proposed_effects"]["$ref"],
        )
        proposal = proposal_array["items"]
        assert proposal["required"] == ["target", "operation", "sensitivity"]
        assert proposal["properties"]["operation"] == {
            "type": "string",
            "enum": ["create", "read", "review", "update", "verify"],
        }
        assert proposal["properties"]["sensitivity"] == {
            "type": "string",
            "const": "ordinary",
        }

    encoded = json.dumps(schema, ensure_ascii=False, sort_keys=True)
    assert '"task"' not in encoded
    for forbidden in ("artifact.txt", "draft-v1", "accepted-v2", "SECRET"):
        assert forbidden not in encoded
    assert encoded.count("req_111111111111") == 1
    assert encoded.count("req_222222222222") == 1
    assert _scoped_authority_ref_ids(
        schema, agent_id="reviewer", phase="implementation"
    ) == ("req_111111111111",)
    assert _scoped_authority_ref_ids(
        schema, agent_id="planner", phase="review"
    ) == (
        "req_222222222222",
    )
    assert _scoped_authority_refs_accept(
        schema,
        agent_id="reviewer",
        phase="implementation",
        refs=["req_111111111111"],
    )
    assert not _scoped_authority_refs_accept(
        schema,
        agent_id="reviewer",
        phase="implementation",
        refs=["req_222222222222"],
    )
    assert not _scoped_authority_refs_accept(
        schema,
        agent_id="planner",
        phase="review",
        refs=["req_111111111111"],
    )
    _assert_portable_strict_schema(schema)
    _assert_closed_object_schemas(schema)


def test_semantic_schema_builder_matches_request_projection() -> None:
    request = _semantic_request()

    assert build_leader_plan_schema(request) == build_semantic_leader_plan_schema(
        semantic_authority=request.semantic_authority,
        selected_agent_ids=("reviewer", "planner"),
        roles={"reviewer": "review", "planner": "planning"},
        step_count=2,
    )


def test_open_semantic_authority_rejects_sensitive_configured_agent_id() -> None:
    authority = _semantic_authority()
    authority["requirements"] = []
    config = replace(
        _config(),
        agents=(
            AgentSpec("reviewer", "review", "claude-cli", "claude"),
            AgentSpec("ghp_DO_NOT_ECHO", "planning", "codex-cli", "codex"),
        ),
    )
    request = LeaderPlanRequest(
        task="Open semantic task",
        config=config,
        selected_agent_ids=("reviewer", "ghp_DO_NOT_ECHO"),
        step_count=2,
        semantic_authority=authority,
    )

    with pytest.raises(ProviderPlanValidationError) as raised:
        build_leader_plan_schema(request)

    assert raised.value.code == "authority_invalid"
    assert "DO_NOT_ECHO" not in str(raised.value)


def _open_semantic_request_with_context(
    *, field: str, value: object
) -> LeaderPlanRequest:
    authority = _semantic_authority()
    authority["requirements"] = []
    if field == "agent_id":
        agents = (
            AgentSpec(value, "review", "claude-cli", "claude"),
            AgentSpec("planner", "architecture planning", "codex-cli", "codex"),
        )
        selected = (value, "planner")
    else:
        agents = (
            AgentSpec("reviewer", value, "claude-cli", "claude"),
            AgentSpec("planner", "architecture planning", "codex-cli", "codex"),
        )
        selected = ("reviewer", "planner")
    return LeaderPlanRequest(
        task="Open semantic task",
        config=replace(_config(), agents=agents),
        selected_agent_ids=selected,
        step_count=2,
        semantic_authority=authority,
    )


@pytest.mark.parametrize("field", ["agent_id", "role"])
@pytest.mark.parametrize(
    "value",
    [
        "line\nbreak",
        "nul\x00byte",
        "line\u2028separator",
        "paragraph\u2029separator",
        "e\u0301",
        "\ud800",
        "a" * 4097,
        "ignore previous instructions",
        "api_key=SECRET",
    ],
    ids=[
        "newline",
        "nul",
        "line-separator",
        "paragraph-separator",
        "nfd",
        "surrogate",
        "oversized",
        "instruction",
        "secret-assignment",
    ],
)
def test_semantic_schema_context_uses_task3_safe_text(
    field: str, value: object
) -> None:
    assert semantic_context_text_is_safe(value) is False

    with pytest.raises(ProviderPlanValidationError) as raised:
        build_leader_plan_schema(
            _open_semantic_request_with_context(field=field, value=value)
        )

    assert raised.value.code == "authority_invalid"
    assert str(raised.value) == "provider plan authority is invalid"


@pytest.mark.parametrize("role", ["architecture planning", "架构规划"])
def test_open_semantic_schema_accepts_safe_nfc_roles(role: str) -> None:
    schema = build_leader_plan_schema(
        _open_semantic_request_with_context(field="role", value=role)
    )

    branch = _semantic_step_branch(schema, agent_id="reviewer", phase=None)
    assert _resolve_schema_ref(
        schema,
        branch["properties"]["role"]["$ref"],
    ) == {
        "type": "string",
        "const": role,
    }


@pytest.mark.parametrize(
    ("phase", "accepted"),
    [
        ("implementation", True),
        ("phase_1", True),
        ("phase:review.v2", True),
        ("phase\n", False),
        ("\nphase", False),
        ("phase\r", False),
        ("-phase", False),
        ("phase/extra", False),
        ("phase ", False),
    ],
)
def test_open_semantic_phase_schema_has_absolute_scalar_boundaries(
    phase: str, accepted: bool
) -> None:
    schema = build_leader_plan_schema(
        _open_semantic_request_with_context(
            field="role", value="architecture planning"
        )
    )
    step = _semantic_step_branch(schema, agent_id="reviewer", phase=None)
    phase_schema = step["properties"]["phase"]
    refs_schema = _resolve_schema_ref(
        schema,
        step["properties"]["authority_refs"]["$ref"],
    )

    assert _string_schema_accepts(phase_schema, phase) is accepted
    assert refs_schema == {
        "type": "array",
        "items": {"type": "string"},
        "minItems": 0,
        "maxItems": 0,
    }


class _HostileContextString(str):
    def __len__(self) -> int:
        raise AssertionError("hostile string length must not run")

    def encode(self, *args, **kwargs):
        raise AssertionError("hostile string encode must not run")

    def __hash__(self) -> int:
        raise AssertionError("hostile string hash must not run")

    def __eq__(self, other: object) -> bool:
        raise AssertionError("hostile string equality must not run")


class _HostileAgentEntry:
    @property
    def agent_id(self) -> str:
        raise AssertionError("unselected agent_id property must not run")

    @property
    def role(self) -> str:
        raise AssertionError("unselected role property must not run")


@pytest.mark.parametrize("field", ["agent_id", "role"])
def test_semantic_schema_rejects_str_subclass_before_magic(field: str) -> None:
    hostile = _HostileContextString("safe-looking")
    assert semantic_context_text_is_safe(hostile) is False

    with pytest.raises(ProviderPlanValidationError) as raised:
        build_leader_plan_schema(
            _open_semantic_request_with_context(field=field, value=hostile)
        )

    assert raised.value.code == "authority_invalid"


@pytest.mark.parametrize("field", ["agent_id", "role"])
def test_semantic_provenance_rejects_str_subclass_before_magic(field: str) -> None:
    hostile = _HostileContextString("safe-looking")

    with pytest.raises(ValueError) as raised:
        build_leader_generation_provenance(
            request=_open_semantic_request_with_context(field=field, value=hostile),
            provider="fake",
            constraint_mode="local",
        )

    assert str(raised.value) == "leader generation request authority is invalid"


def test_unselected_invalid_config_context_does_not_change_semantic_identity() -> None:
    request = _semantic_request()
    hostile_id = _HostileContextString("hostile-unselected")
    hostile_role = _HostileContextString("hostile-role")
    extras = (
        AgentSpec("unused", "bad\nrole", "codex-cli", "codex"),
        AgentSpec("unused", "api_key=SECRET", "codex-cli", "codex"),
        AgentSpec("bad\nid", hostile_role, "codex-cli", "codex"),
        AgentSpec(hostile_id, hostile_role, "codex-cli", "codex"),
        AgentSpec("nfd-unused", "e\u0301", "codex-cli", "codex"),
    )
    augmented = replace(
        request,
        config=replace(request.config, agents=request.config.agents + extras),
    )
    baseline_schema = build_leader_plan_schema(request)

    assert build_leader_plan_schema(augmented) == baseline_schema
    baseline_provenance = build_leader_generation_provenance(
        request=request,
        provider="codex-cli",
        constraint_mode="native_json_schema",
        schema=baseline_schema,
    )
    assert build_leader_generation_provenance(
        request=augmented,
        provider="codex-cli",
        constraint_mode="native_json_schema",
        schema=baseline_schema,
    ) == baseline_provenance
    assert validate_leader_generation_provenance(
        request=augmented,
        provider="codex-cli",
        provenance=baseline_provenance,
    ) == baseline_provenance


@pytest.mark.parametrize(
    "entry",
    [
        (),
        [],
        "unselected-scalar",
        _HostileAgentEntry(),
    ],
)
def test_public_semantic_paths_ignore_malformed_unselected_config_entry(
    entry: object,
) -> None:
    request = _semantic_request()
    changed = replace(
        request,
        config=replace(request.config, agents=request.config.agents + (entry,)),
    )
    schema = build_leader_plan_schema(request)
    provenance = build_leader_generation_provenance(
        request=request,
        provider="codex-cli",
        constraint_mode="native_json_schema",
        schema=schema,
    )

    assert build_leader_plan_schema(changed) == schema
    assert build_leader_generation_provenance(
        request=changed,
        provider="codex-cli",
        constraint_mode="native_json_schema",
        schema=schema,
    ) == provenance
    assert validate_leader_generation_provenance(
        request=changed,
        provider="codex-cli",
        provenance=provenance,
    ) == provenance


@pytest.mark.parametrize(
    "entry",
    [
        ("reviewer",),
        ["reviewer", "review"],
        "reviewer",
        _HostileAgentEntry(),
    ],
)
def test_public_semantic_paths_reject_malformed_selected_config_entry(
    entry: object,
) -> None:
    request = _semantic_request()
    remaining = tuple(
        item for item in request.config.agents if item.agent_id != "reviewer"
    )
    changed = replace(
        request,
        config=replace(request.config, agents=remaining + (entry,)),
    )

    with pytest.raises(ProviderPlanValidationError) as schema_error:
        build_leader_plan_schema(changed)
    with pytest.raises(ValueError) as provenance_error:
        build_leader_generation_provenance(
            request=changed,
            provider="fake",
            constraint_mode="local",
        )

    assert schema_error.value.code == "authority_invalid"
    assert str(schema_error.value) == "provider plan authority is invalid"
    assert str(provenance_error.value) == "leader generation request authority is invalid"


def test_semantic_context_resolver_ignores_malformed_unselected_entries() -> None:
    configured_context = (
        ("reviewer", "review"),
        ("planner", "planning"),
        (),
        ("unused",),
        ("unused", "role", "extra"),
        "not-a-tuple",
        ["also", "not", "a", "tuple"],
    )

    assert resolve_semantic_leader_plan_context(
        selected_agent_ids=("reviewer", "planner"),
        step_count=2,
        configured_context=configured_context,
    ) == (
        ("reviewer", "planner"),
        {"reviewer": "review", "planner": "planning"},
        2,
    )


@pytest.mark.parametrize(
    "malformed",
    [
        ("reviewer",),
        ("reviewer", "review", "extra"),
        "reviewer",
        ["reviewer", "review"],
    ],
)
def test_semantic_context_resolver_rejects_malformed_selected_entry(
    malformed: object,
) -> None:
    with pytest.raises(SemanticPlanSchemaAuthorityError):
        resolve_semantic_leader_plan_context(
            selected_agent_ids=("reviewer", "planner"),
            step_count=2,
            configured_context=(
                ("reviewer", "review"),
                ("planner", "planning"),
                malformed,
            ),
        )


def test_duplicate_selected_config_context_fails_closed() -> None:
    request = _semantic_request()
    duplicate = next(
        item for item in request.config.agents if item.agent_id == "reviewer"
    )
    changed = replace(
        request,
        config=replace(
            request.config,
            agents=request.config.agents + (duplicate,),
        ),
    )

    with pytest.raises(ProviderPlanValidationError) as raised:
        build_leader_plan_schema(changed)

    assert raised.value.code == "authority_invalid"
    assert str(raised.value) == "provider plan authority is invalid"


def test_semantic_schema_is_deterministic_and_regeneration_diagnostic_free() -> None:
    request = _semantic_request()
    retry = replace(
        request,
        regeneration_diagnostic="semantic_candidate_missing_requirement",
    )

    assert build_leader_plan_schema(request) == build_leader_plan_schema(retry)
    assert canonical_leader_plan_schema_hash(
        build_leader_plan_schema(request)
    ) == canonical_leader_plan_schema_hash(build_leader_plan_schema(retry))


def test_semantic_schema_maximum_authority_is_compact_and_deterministic() -> None:
    selected = ("reviewer", "planner")
    requirements: list[dict[str, object]] = []
    requirement_ids: list[str] = []
    for phase_index in range(64):
        for item_index in range(4):
            number = phase_index * 4 + item_index
            requirement_id = f"req_{number:012x}"
            requirement_ids.append(requirement_id)
            requirements.append(
                {
                    "requirement_id": requirement_id,
                    "kind": "read",
                    "target": f"artifact_{number}.txt",
                    "operation": "read",
                    "phase": f"phase_{phase_index:02d}",
                    "agent_id": selected[phase_index % len(selected)],
                    "sensitivity": "ordinary",
                    "literal": f"value_{number}",
                }
            )
    authority = {
        "schema_version": "mission-semantic-authority/v1",
        "source_message_hash": f"sha256:{'c' * 64}",
        "requirements": requirements,
        "proposed_effects": [],
        "unresolved": [],
    }

    first = build_semantic_leader_plan_schema(
        semantic_authority=authority,
        selected_agent_ids=selected,
        roles={"reviewer": "review", "planner": "architecture planning"},
        step_count=64,
    )
    second = build_semantic_leader_plan_schema(
        semantic_authority=authority,
        selected_agent_ids=selected,
        roles={"reviewer": "review", "planner": "architecture planning"},
        step_count=64,
    )
    encoded = json.dumps(
        first,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    assert first == second
    assert canonical_leader_plan_schema_hash(first) == canonical_leader_plan_schema_hash(
        second
    )
    assert len(encoded.encode("utf-8")) < 150_000
    assert all(encoded.count(requirement_id) == 1 for requirement_id in requirement_ids)
    budget = _strict_schema_budget(first)
    assert budget["property_count"] < 5_000
    assert budget["max_depth"] <= 10
    assert budget["string_budget"] < 120_000
    assert budget["enum_total"] <= 1_000
    assert budget["enum_max"] <= 1_000
    _assert_portable_strict_schema(first)


def test_semantic_schema_deduplicates_long_role_constants_across_phases() -> None:
    selected = ("reviewer", "planner")
    roles = {
        "reviewer": "r" * 4096,
        "planner": "p" * 4096,
    }

    schema = build_semantic_leader_plan_schema(
        semantic_authority=_phased_semantic_authority(selected, step_count=64),
        selected_agent_ids=selected,
        roles=roles,
        step_count=64,
    )
    encoded = json.dumps(schema, ensure_ascii=False, sort_keys=True)

    assert encoded.count(roles["reviewer"]) == 1
    assert encoded.count(roles["planner"]) == 1
    assert _strict_schema_budget(schema)["string_budget"] < 120_000


def test_semantic_schema_budget_guard_fails_closed_without_echo() -> None:
    selected = tuple(f"worker_{index:02d}" for index in range(64))
    roles = {
        agent_id: f"{index:02d}" + "r" * 4094
        for index, agent_id in enumerate(selected)
    }

    with pytest.raises(SemanticPlanSchemaAuthorityError) as raised:
        build_semantic_leader_plan_schema(
            semantic_authority=_open_semantic_authority(),
            selected_agent_ids=selected,
            roles=roles,
            step_count=64,
        )

    assert str(raised.value) == "semantic plan schema authority is invalid"
    assert roles["worker_00"] not in str(raised.value)


@pytest.mark.parametrize(
    ("selected_count", "step_count"),
    [
        (1, 1),
        (3, 2),
        (65, 64),
    ],
)
def test_semantic_schema_rejects_selected_count_outside_step_authority(
    selected_count: int,
    step_count: int,
) -> None:
    selected = tuple(f"worker_{index:02d}" for index in range(selected_count))

    with pytest.raises(SemanticPlanSchemaAuthorityError):
        build_semantic_leader_plan_schema(
            semantic_authority=_open_semantic_authority(),
            selected_agent_ids=selected,
            roles={agent_id: "planning" for agent_id in selected},
            step_count=step_count,
        )

@pytest.mark.parametrize(
    "mutation",
    [
        "unresolved",
        "secret_ref",
        "draft_proposal",
        "worker_order",
        "step_count",
        "missing_config_agent",
        "arbitrary_diagnostic",
    ],
)
def test_semantic_schema_rejects_invalid_authority_without_echo(mutation: str) -> None:
    request = _semantic_request()
    authority = deepcopy(request.semantic_authority)
    marker = "SECRET-raw-marker"
    if mutation == "unresolved":
        authority["unresolved"] = [
            {
                "unresolved_id": "unr_333333333333",
                "kind": "ambiguous",
                "phase": "implementation",
                "agent_id": "reviewer",
            }
        ]
        request = replace(request, semantic_authority=authority)
    elif mutation == "secret_ref":
        authority["requirements"][0]["sensitivity"] = "secret_ref"
        authority["requirements"][0]["literal"] = {"reference": marker}
        request = replace(request, semantic_authority=authority)
    elif mutation == "draft_proposal":
        authority["proposed_effects"] = [
            {
                "proposed_effect_id": "prp_333333333333",
                "target": "extra.txt",
                "operation": "create",
                "sensitivity": "ordinary",
            }
        ]
        request = replace(request, semantic_authority=authority)
    elif mutation == "worker_order":
        request = replace(request, selected_agent_ids=("planner", "reviewer"))
    elif mutation == "step_count":
        request = replace(request, step_count=3)
    elif mutation == "missing_config_agent":
        request = replace(
            request,
            config=replace(
                request.config,
                agents=tuple(
                    item for item in request.config.agents if item.agent_id != "reviewer"
                ),
            ),
        )
    else:
        request = replace(request, regeneration_diagnostic=marker)

    with pytest.raises(ProviderPlanValidationError) as raised:
        build_leader_plan_schema(request)

    assert raised.value.code == "authority_invalid"
    assert str(raised.value) == "provider plan authority is invalid"
    assert marker not in str(raised.value)


@pytest.mark.parametrize(
    ("selected", "count"),
    [
        (("planner", "reviewer"), None),
        (None, 2),
        ("planner", 2),
        ((["planner"], "reviewer"), 2),
        (("planner",), 2),
        (("planner", "planner"), 2),
        (("planner", "ghost"), 2),
        (("planner", "reviewer"), True),
        (("planner", "reviewer"), 1),
        (("planner", "reviewer"), 65),
    ],
)
def test_invalid_authority_is_reported_without_leaking_type_errors(
    selected: object, count: object
) -> None:
    request = _request(selected_agent_ids=selected, step_count=count)

    with pytest.raises(ProviderPlanValidationError) as raised:
        leader_plan_authority(request)

    assert raised.value.code == "authority_invalid"
    assert str(raised.value) == "provider plan authority is invalid"


def test_default_authority_uses_all_configured_workers() -> None:
    request = _request(selected_agent_ids=None, step_count=None)

    assert leader_plan_authority(request) == (("planner", "reviewer", "builder"), 3)


def _valid_plan() -> dict[str, object]:
    return {
        "goal": "Deliver the mission",
        "summary": "Use only the frozen workers",
        "steps": [
            {
                "step": 1,
                "agent_id": "reviewer",
                "role": "review",
                "task": "Review the requirements",
                "risk": "Human approval remains required",
                "requires_approval": True,
            },
            {
                "step": 2,
                "agent_id": "planner",
                "role": "planning",
                "task": "Prepare the implementation plan",
                "risk": "Human approval remains required",
                "requires_approval": True,
            },
        ],
    }


def _missing_goal(plan: dict[str, object]) -> None:
    plan.pop("goal")


def _empty_goal(plan: dict[str, object]) -> None:
    plan["goal"] = " "


def _empty_steps(plan: dict[str, object]) -> None:
    plan["steps"] = []


def _wrong_step_count(plan: dict[str, object]) -> None:
    plan["steps"] = plan["steps"][:1]


def _unknown_agent(plan: dict[str, object]) -> None:
    plan["steps"][0]["agent_id"] = "builder"


def _wrong_role(plan: dict[str, object]) -> None:
    plan["steps"][0]["role"] = "planning"


def _approval_false(plan: dict[str, object]) -> None:
    plan["steps"][0]["requires_approval"] = False


def _duplicate_step(plan: dict[str, object]) -> None:
    plan["steps"][1]["step"] = 1


def _misnumbered_step(plan: dict[str, object]) -> None:
    plan["steps"][1]["step"] = 3


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (_missing_goal, "missing_required_field"),
        (_empty_goal, "invalid_string_field"),
        (_empty_steps, "invalid_step_count"),
        (_wrong_step_count, "invalid_step_count"),
        (_unknown_agent, "unknown_agent"),
        (_wrong_role, "role_mismatch"),
        (_approval_false, "approval_not_required"),
        (_duplicate_step, "invalid_step_numbering"),
        (_misnumbered_step, "invalid_step_numbering"),
    ],
)
def test_semantic_validation_reports_stable_diagnostic_codes(mutate, code: str) -> None:
    plan = _valid_plan()
    mutate(plan)

    with pytest.raises(ProviderPlanValidationError) as raised:
        validate_provider_plan_schema(
            plan,
            config=_config(),
            selected_agent_ids=("reviewer", "planner"),
            step_count=2,
        )

    assert raised.value.code == code


@pytest.mark.parametrize(
    ("plan", "code"),
    [
        ([], "invalid_top_level_type"),
        ({"goal": "g", "summary": "s", "steps": ["not-an-object", "also-not"]}, "invalid_step_type"),
    ],
)
def test_semantic_validation_rejects_invalid_container_types(plan: object, code: str) -> None:
    with pytest.raises(ProviderPlanValidationError) as raised:
        validate_provider_plan_schema(
            plan,
            config=_config(),
            selected_agent_ids=("reviewer", "planner"),
            step_count=2,
        )

    assert raised.value.code == code


def test_semantic_validation_normalizes_output_envelope() -> None:
    plan = _valid_plan()
    plan["approval_required"] = False
    plan["dispatch_ready"] = True

    normalized = validate_provider_plan_schema(
        plan,
        config=_config(),
        selected_agent_ids=("reviewer", "planner"),
        step_count=2,
    )

    assert normalized is plan
    assert normalized["approval_required"] is True
    assert normalized["dispatch_ready"] is False


def test_legacy_validation_keeps_human_readable_exception_text() -> None:
    plan = deepcopy(_valid_plan())
    plan["steps"][0].pop("agent_id")

    with pytest.raises(ProviderPlanValidationError) as raised:
        validate_provider_plan_schema(plan, config=_config())

    assert raised.value.code == "missing_required_field"
    assert str(raised.value) == "provider plan step 1 missing required field: agent_id"


def _four_step_request() -> LeaderPlanRequest:
    config = _config()
    config = replace(
        config,
        agents=config.agents
        + (AgentSpec("writer", "documentation", "claude-cli", "claude"),),
    )
    return LeaderPlanRequest(
        task="Complete a four-step mission",
        config=config,
        model="leader-model-v1",
        selected_agent_ids=("builder", "reviewer", "planner", "writer"),
        step_count=4,
    )


def test_generation_provenance_normalizes_exact_authority_and_schema() -> None:
    request = _four_step_request()
    schema = build_leader_plan_schema(request)

    assert build_leader_generation_provenance(
        request=request,
        provider="codex-cli",
        constraint_mode="native_json_schema",
        schema=schema,
        attempt_count=1,
    ) == {
        "provider": "codex-cli",
        "model": "leader-model-v1",
        "constraint_mode": "native_json_schema",
        "schema_version": "leader-plan/v1",
        "schema_hash": canonical_leader_plan_schema_hash(schema),
        "attempt_count": 1,
        "regeneration_used": False,
        "selected_agent_ids": ["builder", "reviewer", "planner", "writer"],
        "step_count": 4,
    }


def test_generation_provenance_supports_schema_free_local_legacy_mode() -> None:
    request = LeaderPlanRequest(task="Legacy local plan", config=_config())

    assert build_leader_generation_provenance(
        request=request,
        provider="fake",
        constraint_mode="local",
    ) == {
        "provider": "fake",
        "model": None,
        "constraint_mode": "local",
        "schema_version": None,
        "schema_hash": None,
        "attempt_count": 1,
        "regeneration_used": False,
        "selected_agent_ids": ["planner", "reviewer", "builder"],
        "step_count": 3,
    }


def test_semantic_generation_provenance_rebuilds_native_schema_and_authority() -> None:
    request = _semantic_request(model="semantic-leader-v1")
    schema = build_leader_plan_schema(request)

    provenance = build_leader_generation_provenance(
        request=request,
        provider="codex-cli",
        constraint_mode="native_json_schema",
        schema=schema,
        attempt_count=2,
    )

    assert provenance == {
        "provider": "codex-cli",
        "model": "semantic-leader-v1",
        "constraint_mode": "native_json_schema",
        "schema_version": "leader-semantic-plan/v1",
        "schema_hash": canonical_leader_plan_schema_hash(schema),
        "attempt_count": 2,
        "regeneration_used": True,
        "selected_agent_ids": ["reviewer", "planner"],
        "step_count": 2,
        "semantic_authority_schema_version": "mission-semantic-authority/v1",
        "semantic_authority_hash": (
            "sha256:a72116d08f27ab829515686c38b68583a94b02f7250fb7304e0663a2ae29e37c"
        ),
    }
    assert validate_leader_generation_provenance(
        request=request,
        provider="codex-cli",
        provenance=provenance,
    ) == provenance


def test_semantic_schema_free_provenance_keeps_authority_without_schema_claim() -> None:
    request = _semantic_request()

    provenance = build_leader_generation_provenance(
        request=request,
        provider="fake",
        constraint_mode="local",
    )

    assert provenance["schema_version"] is None
    assert provenance["schema_hash"] is None
    assert provenance["semantic_authority_schema_version"] == "mission-semantic-authority/v1"
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", provenance["semantic_authority_hash"])
    assert validate_leader_generation_provenance(
        request=request,
        provider="fake",
        provenance=provenance,
    ) == provenance


@pytest.mark.parametrize(
    "mutation",
    [
        "authority_hash",
        "authority_requirement",
        "worker_order",
        "step_count",
        "attempt_count",
        "regeneration_used",
        "schema_version",
        "schema_hash",
        "extra",
    ],
)
def test_semantic_generation_provenance_rejects_mutation_without_echo(
    mutation: str,
) -> None:
    request = _semantic_request()
    provenance = build_leader_generation_provenance(
        request=request,
        provider="codex-cli",
        constraint_mode="native_json_schema",
        schema=build_leader_plan_schema(request),
    )
    if mutation == "authority_requirement":
        authority = deepcopy(request.semantic_authority)
        authority["requirements"][0]["literal"] = "changed-v3\n"
        request = replace(request, semantic_authority=authority)
    elif mutation == "worker_order":
        provenance["selected_agent_ids"] = ["planner", "reviewer"]
    elif mutation == "step_count":
        provenance["step_count"] = 3
    elif mutation == "attempt_count":
        provenance["attempt_count"] = 2
    elif mutation == "regeneration_used":
        provenance["regeneration_used"] = True
    elif mutation == "schema_version":
        provenance["schema_version"] = "leader-plan/v1"
    elif mutation == "schema_hash":
        provenance["schema_hash"] = f"sha256:{'b' * 64}"
    elif mutation == "extra":
        provenance["raw_authority"] = "SECRET-raw-marker"
    else:
        provenance["semantic_authority_hash"] = f"sha256:{'b' * 64}"

    with pytest.raises(ValueError) as raised:
        validate_leader_generation_provenance(
            request=request,
            provider="codex-cli",
            provenance=provenance,
        )

    assert str(raised.value) == "leader generation provenance is invalid"
    assert "SECRET" not in str(raised.value)


def test_semantic_generation_rejects_supplied_schema_from_changed_role() -> None:
    request = _semantic_request()
    schema = build_leader_plan_schema(request)
    changed = replace(
        request,
        config=replace(
            request.config,
            agents=tuple(
                replace(item, role="changed-role")
                if item.agent_id == "reviewer"
                else item
                for item in request.config.agents
            ),
        ),
    )

    with pytest.raises(ValueError) as raised:
        build_leader_generation_provenance(
            request=changed,
            provider="codex-cli",
            constraint_mode="native_json_schema",
            schema=schema,
        )

    assert str(raised.value) == "leader generation schema does not match request authority"


@pytest.mark.parametrize(
    "changed_request",
    [
        lambda request: replace(
            request,
            selected_agent_ids=("planner", "reviewer"),
        ),
        lambda request: replace(request, step_count=3),
        lambda request: replace(request, regeneration_diagnostic="raw-provider-output"),
    ],
)
def test_semantic_provenance_validation_closes_invalid_request_authority(
    changed_request,
) -> None:
    request = _semantic_request()
    provenance = build_leader_generation_provenance(
        request=request,
        provider="codex-cli",
        constraint_mode="native_json_schema",
        schema=build_leader_plan_schema(request),
    )

    with pytest.raises(ValueError) as raised:
        validate_leader_generation_provenance(
            request=changed_request(request),
            provider="codex-cli",
            provenance=provenance,
        )

    assert str(raised.value) == "leader generation provenance is invalid"


def test_generation_provenance_records_one_regeneration() -> None:
    request = _four_step_request()

    provenance = build_leader_generation_provenance(
        request=request,
        provider="claude-cli",
        constraint_mode="prompt_only",
        attempt_count=2,
    )

    assert provenance["attempt_count"] == 2
    assert provenance["regeneration_used"] is True


class _StringSubclass(str):
    pass


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"provider": ""}, "leader generation provider must be a non-empty string"),
        ({"provider": 7}, "leader generation provider must be a non-empty string"),
        (
            {"provider": _StringSubclass("codex-cli")},
            "leader generation provider must be a non-empty string",
        ),
        ({"constraint_mode": "unknown"}, "leader generation constraint mode is invalid"),
        (
            {"constraint_mode": _StringSubclass("local")},
            "leader generation constraint mode is invalid",
        ),
        ({"schema": []}, "leader generation schema must be a JSON object or null"),
        ({"schema": "mismatch"}, "leader generation schema does not match request authority"),
        ({"attempt_count": 0}, "leader generation attempt count is invalid"),
        ({"attempt_count": 3}, "leader generation attempt count is invalid"),
        ({"attempt_count": True}, "leader generation attempt count is invalid"),
    ],
)
def test_generation_provenance_rejects_invalid_inputs_without_echoing_values(
    overrides: dict[str, object], message: str
) -> None:
    request = _four_step_request()
    schema = build_leader_plan_schema(request)
    values: dict[str, object] = {
        "request": request,
        "provider": "codex-cli",
        "constraint_mode": "native_json_schema",
        "schema": schema,
        "attempt_count": 1,
    }
    if overrides.get("schema") == "mismatch":
        mismatched_schema = deepcopy(schema)
        mismatched_schema["properties"]["steps"]["maxItems"] = 3
        overrides = {"schema": mismatched_schema}
    values.update(overrides)

    with pytest.raises(ValueError) as raised:
        build_leader_generation_provenance(**values)

    assert str(raised.value) == message


@pytest.mark.parametrize(
    "mutation",
    ["boolean_as_integer", "integer_as_float", "non_json_value"],
)
def test_generation_provenance_rejects_noncanonical_schema_types(mutation: str) -> None:
    request = _four_step_request()
    schema = deepcopy(build_leader_plan_schema(request))
    if mutation == "boolean_as_integer":
        schema["properties"]["steps"]["items"]["properties"]["requires_approval"]["const"] = 1
    elif mutation == "integer_as_float":
        schema["properties"]["steps"]["maxItems"] = 4.0
    else:
        schema["properties"]["steps"]["maxItems"] = {4}

    with pytest.raises(ValueError) as raised:
        build_leader_generation_provenance(
            request=request,
            provider="codex-cli",
            constraint_mode="native_json_schema",
            schema=schema,
        )

    assert str(raised.value) == "leader generation schema does not match request authority"


def test_leader_plan_result_semantic_diagnostics_are_exact_and_defensive() -> None:
    diagnostic = {
        "code": "semantic_candidate_missing_requirement",
        "attempt_count": 1,
        "regeneration_used": False,
    }
    result = LeaderPlanResult(
        plan={"goal": "g"},
        leader_generation={"provider": "fake"},
        semantic_diagnostics=(diagnostic,),
    )

    diagnostic["code"] = "SECRET-raw-marker"
    assert result.semantic_diagnostics == (
        {
            "code": "semantic_candidate_missing_requirement",
            "attempt_count": 1,
            "regeneration_used": False,
        },
    )
    assert result.semantic_diagnostics[0] is not diagnostic


def test_legacy_leader_plan_result_construction_keeps_empty_diagnostics() -> None:
    plan = {"goal": "legacy"}
    provenance = {"provider": "fake"}

    result = LeaderPlanResult(plan=plan, leader_generation=provenance)

    assert result.plan is plan
    assert result.leader_generation is provenance
    assert result.semantic_diagnostics == ()


@pytest.mark.parametrize(
    "diagnostics",
    [
        [],
        ({"code": "semantic_candidate_missing_requirement", "attempt_count": 1},),
        (
            {
                "code": "semantic_candidate_missing_requirement",
                "attempt_count": 1,
                "regeneration_used": False,
                "raw": "SECRET-raw-marker",
            },
        ),
        (
            {
                "code": "SECRET-raw-marker",
                "attempt_count": 1,
                "regeneration_used": False,
            },
        ),
        (
            {
                "code": "semantic_candidate_missing_requirement",
                "attempt_count": True,
                "regeneration_used": True,
            },
        ),
        (
            {
                "code": "semantic_candidate_missing_requirement",
                "attempt_count": 2,
                "regeneration_used": False,
            },
        ),
    ],
)
def test_leader_plan_result_rejects_invalid_semantic_diagnostics_without_echo(
    diagnostics: object,
) -> None:
    with pytest.raises(ValueError) as raised:
        LeaderPlanResult(
            plan={},
            leader_generation={},
            semantic_diagnostics=diagnostics,
        )

    assert str(raised.value) == "leader plan semantic diagnostics are invalid"
    assert "SECRET" not in str(raised.value)


class _ExplosiveDict(dict):
    def __iter__(self):
        raise AssertionError("custom iterator must not run")

    def items(self):
        raise AssertionError("custom items must not run")

    def values(self):
        raise AssertionError("custom values must not run")


class _ExplosiveList(list):
    def __iter__(self):
        raise AssertionError("custom iterator must not run")


@pytest.mark.parametrize("hostile", [_ExplosiveDict(), _ExplosiveList()])
def test_semantic_schema_rejects_hostile_authority_containers_without_invoking_them(
    hostile: object,
) -> None:
    request = _semantic_request(semantic_authority=hostile)

    with pytest.raises(ProviderPlanValidationError) as raised:
        build_leader_plan_schema(request)

    assert raised.value.code == "authority_invalid"


def test_semantic_schema_rejects_hostile_nested_container_without_invoking_it() -> None:
    authority = _semantic_authority()
    authority["requirements"] = _ExplosiveList(authority["requirements"])

    with pytest.raises(ProviderPlanValidationError) as raised:
        build_leader_plan_schema(_semantic_request(semantic_authority=authority))

    assert raised.value.code == "authority_invalid"


def test_semantic_schema_and_provenance_are_bounded_and_repeatable() -> None:
    request = _semantic_request()
    first = build_leader_plan_schema(request)
    second = build_leader_plan_schema(request)
    first_provenance = build_leader_generation_provenance(
        request=request,
        provider="codex-cli",
        constraint_mode="native_json_schema",
        schema=first,
    )
    second_provenance = build_leader_generation_provenance(
        request=request,
        provider="codex-cli",
        constraint_mode="native_json_schema",
        schema=second,
    )

    assert first == second
    assert canonical_leader_plan_schema_hash(first) == canonical_leader_plan_schema_hash(second)
    assert first_provenance == second_provenance
    assert len(json.dumps(first, separators=(",", ":"))) < 100_000
    assert len(json.dumps(first_provenance, separators=(",", ":"))) < 2_000
