from __future__ import annotations

from copy import deepcopy

import pytest

from agentdeck.semantic_authority import (
    SEMANTIC_AUTHORITY_SCHEMA_VERSION,
    SEMANTIC_OPERATIONS,
    SEMANTIC_REQUIREMENT_KINDS,
    SEMANTIC_SENSITIVITY,
    SemanticAuthorityError,
    compact_semantic_authority,
    semantic_authority_hash,
    validate_semantic_authority,
)


def valid_authority() -> dict[str, object]:
    """Return a fresh, fully literal state-transition authority."""
    return {
        "schema_version": "mission-semantic-authority/v1",
        "source_message_hash": f"sha256:{'a' * 64}",
        "requirements": [
            {
                "requirement_id": "req_0123456789ab",
                "kind": "state_transition",
                "target": "artifact.txt",
                "operation": "update",
                "before": {"content_equals": "draft-v1"},
                "after": {"content_equals": "accepted-v2"},
                "phase": "revision",
                "agent_id": "claude-worker",
                "sensitivity": "ordinary",
            }
        ],
        "proposed_effects": [
            {
                "proposed_effect_id": "prp_0123456789ab",
                "target": "artifact.txt",
                "operation": "update",
                "sensitivity": "ordinary",
            }
        ],
        "unresolved": [],
    }


def test_constants_are_the_closed_domain() -> None:
    assert SEMANTIC_AUTHORITY_SCHEMA_VERSION == "mission-semantic-authority/v1"
    assert SEMANTIC_REQUIREMENT_KINDS == frozenset(
        {"create", "read", "review", "update", "verify", "state_transition"}
    )
    assert SEMANTIC_OPERATIONS == frozenset(
        {"create", "read", "review", "update", "verify"}
    )
    assert SEMANTIC_SENSITIVITY == frozenset({"ordinary", "secret_ref"})


def test_valid_authority_returns_an_equal_defensive_copy_and_stable_hash() -> None:
    authority = valid_authority()
    validated = validate_semantic_authority(authority)

    assert validated == authority
    assert validated is not authority
    assert validated["requirements"] is not authority["requirements"]
    validated["requirements"][0]["after"]["content_equals"] = "mutated"
    assert authority["requirements"][0]["after"]["content_equals"] == "accepted-v2"

    authority_hash = semantic_authority_hash(authority)
    assert authority_hash.startswith("sha256:")
    assert len(authority_hash) == len("sha256:") + 64

    reordered = {key: authority[key] for key in reversed(tuple(authority))}
    reordered["requirements"] = [
        {key: authority["requirements"][0][key] for key in reversed(tuple(authority["requirements"][0]))}
    ]
    assert semantic_authority_hash(reordered) == authority_hash


def test_valid_authority_helper_returns_fresh_nested_values() -> None:
    first = valid_authority()
    second = valid_authority()
    first["requirements"][0]["before"]["content_equals"] = "changed"
    assert second["requirements"][0]["before"]["content_equals"] == "draft-v1"


@pytest.mark.parametrize("kind", ["create", "read", "review", "update", "verify"])
def test_literal_requirement_kinds_use_their_exact_field_set(kind: str) -> None:
    authority = valid_authority()
    authority["requirements"] = [
        {
            "requirement_id": "req_0123456789ab",
            "kind": kind,
            "target": "artifact.txt",
            "operation": kind,
            "literal": "required-content",
            "phase": "implementation",
            "agent_id": "claude-worker",
            "sensitivity": "ordinary",
        }
    ]
    assert validate_semantic_authority(authority) == authority

    authority["requirements"][0]["before"] = {"content_equals": "unexpected"}
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(authority)
    assert raised.value.code == "requirement_fields_invalid"


def test_sensitive_literal_must_be_an_explicit_reference() -> None:
    authority = valid_authority()
    authority["requirements"] = [
        {
            "requirement_id": "req_0123456789ab",
            "kind": "create",
            "target": "artifact.txt",
            "operation": "create",
            "literal": "DO_NOT_ECHO",
            "phase": "implementation",
            "agent_id": "claude-worker",
            "sensitivity": "secret_ref",
        }
    ]
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(authority)
    assert raised.value.code == "secret_value_not_reference"
    assert "DO_NOT_ECHO" not in str(raised.value)

    authority["requirements"][0]["literal"] = {
        "reference": "secret:artifact-content"
    }
    assert validate_semantic_authority(authority) == authority


def test_compact_authority_is_exact_non_leaking_and_defensive() -> None:
    authority = valid_authority()
    blockers: list[str] = []
    compact = compact_semantic_authority(
        authority,
        state="preview",
        compiled_step_count=4,
        blockers=blockers,
    )

    assert set(compact) == {
        "schema_version",
        "state",
        "authority_hash",
        "requirement_count",
        "proposed_effect_count",
        "unresolved_count",
        "compiled_step_count",
        "blockers",
    }
    assert compact == {
        "schema_version": "mission-semantic-authority/v1",
        "state": "preview",
        "authority_hash": semantic_authority_hash(authority),
        "requirement_count": 1,
        "proposed_effect_count": 1,
        "unresolved_count": 0,
        "compiled_step_count": 4,
        "blockers": [],
    }
    serialized = repr(compact)
    for forbidden in ("artifact.txt", "req_0123456789ab", "draft-v1", "accepted-v2"):
        assert forbidden not in serialized

    blockers.append("late_mutation")
    assert compact["blockers"] == []


def _extra_top_level(authority: dict[str, object]) -> None:
    authority["hostile"] = "DO_NOT_ECHO"


def _reordered_ids(authority: dict[str, object]) -> None:
    second = deepcopy(authority["requirements"][0])
    second["requirement_id"] = "req_000000000000"
    authority["requirements"].append(second)


def _duplicate_ids(authority: dict[str, object]) -> None:
    authority["requirements"].append(deepcopy(authority["requirements"][0]))


def _unknown_kind(authority: dict[str, object]) -> None:
    authority["requirements"][0]["kind"] = "DO_NOT_ECHO"


def _absolute_target(authority: dict[str, object]) -> None:
    authority["requirements"][0]["target"] = "/tmp/DO_NOT_ECHO"


def _escaping_target(authority: dict[str, object]) -> None:
    authority["requirements"][0]["target"] = "../DO_NOT_ECHO"


def _wrong_operation(authority: dict[str, object]) -> None:
    authority["requirements"][0]["operation"] = "read"


def _missing_before(authority: dict[str, object]) -> None:
    del authority["requirements"][0]["before"]


def _missing_after(authority: dict[str, object]) -> None:
    del authority["requirements"][0]["after"]


def _malformed_hash(authority: dict[str, object]) -> None:
    authority["source_message_hash"] = "sha256:DO_NOT_ECHO"


def _unknown_sensitivity(authority: dict[str, object]) -> None:
    authority["requirements"][0]["sensitivity"] = "DO_NOT_ECHO"


def _raw_secret_field(authority: dict[str, object]) -> None:
    authority["requirements"][0]["secret"] = "DO_NOT_ECHO"


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (_extra_top_level, "authority_fields_invalid"),
        (_reordered_ids, "requirement_ids_not_ordered"),
        (_duplicate_ids, "requirement_ids_not_unique"),
        (_unknown_kind, "requirement_kind_invalid"),
        (_absolute_target, "target_invalid"),
        (_escaping_target, "target_invalid"),
        (_wrong_operation, "requirement_operation_invalid"),
        (_missing_before, "requirement_fields_invalid"),
        (_missing_after, "requirement_fields_invalid"),
        (_malformed_hash, "source_message_hash_invalid"),
        (_unknown_sensitivity, "sensitivity_invalid"),
        (_raw_secret_field, "requirement_fields_invalid"),
    ],
)
def test_hostile_authority_mutations_fail_with_closed_non_echoing_errors(
    mutation, code: str
) -> None:
    authority = valid_authority()
    mutation(authority)

    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(authority)

    assert raised.value.code == code
    assert str(raised.value) == code
    assert "DO_NOT_ECHO" not in str(raised.value)


@pytest.mark.parametrize(
    "target",
    ["", "a//b", "a/../b", "a\\b", "nul\x00name", ".", "./a"],
)
def test_target_rejects_ambiguous_or_escaping_paths(target: str) -> None:
    authority = valid_authority()
    authority["requirements"][0]["target"] = target
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(authority)
    assert raised.value.code == "target_invalid"
    if target:
        assert target not in str(raised.value)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("proposed_effect_id", "prp_xyz", "proposal_id_invalid"),
        ("target", "/tmp/DO_NOT_ECHO", "target_invalid"),
        ("operation", "state_transition", "proposal_operation_invalid"),
        ("sensitivity", "DO_NOT_ECHO", "sensitivity_invalid"),
    ],
)
def test_proposed_effects_enforce_id_target_operation_and_sensitivity(
    field: str, value: object, code: str
) -> None:
    authority = valid_authority()
    authority["proposed_effects"][0][field] = value
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(authority)
    assert raised.value.code == code
    assert "DO_NOT_ECHO" not in str(raised.value)


def test_proposed_effects_reject_unknown_fields_and_duplicate_or_reordered_ids() -> None:
    authority = valid_authority()
    authority["proposed_effects"][0]["literal"] = "DO_NOT_ECHO"
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(authority)
    assert raised.value.code == "proposal_fields_invalid"

    duplicate = valid_authority()
    duplicate["proposed_effects"].append(deepcopy(duplicate["proposed_effects"][0]))
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(duplicate)
    assert raised.value.code == "proposal_ids_not_unique"

    reordered = valid_authority()
    second = deepcopy(reordered["proposed_effects"][0])
    second["proposed_effect_id"] = "prp_000000000000"
    reordered["proposed_effects"].append(second)
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(reordered)
    assert raised.value.code == "proposal_ids_not_ordered"


def test_nested_values_are_exact_json_scalars_and_secret_values_are_references() -> None:
    non_scalar = valid_authority()
    non_scalar["requirements"][0]["after"] = {"content_equals": ["DO_NOT_ECHO"]}
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(non_scalar)
    assert raised.value.code == "value_constraint_invalid"

    raw_secret = valid_authority()
    raw_secret["requirements"][0]["sensitivity"] = "secret_ref"
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(raw_secret)
    assert raised.value.code == "secret_value_not_reference"

    referenced = valid_authority()
    referenced["requirements"][0]["sensitivity"] = "secret_ref"
    referenced["requirements"][0]["before"] = {"reference": "secret:artifact-before"}
    referenced["requirements"][0]["after"] = {"reference": "secret:artifact-after"}
    assert validate_semantic_authority(referenced) == referenced


@pytest.mark.parametrize("field", ["phase", "agent_id"])
def test_phase_and_agent_id_are_bounded_ordinary_scalars(field: str) -> None:
    authority = valid_authority()
    authority["requirements"][0][field] = "x" * 129
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(authority)
    assert raised.value.code == "ordinary_scalar_invalid"


@pytest.mark.parametrize("state", ["draft", "blocked", "preview", "frozen"])
def test_compact_accepts_only_closed_states(state: str) -> None:
    assert compact_semantic_authority(
        valid_authority(), state=state, compiled_step_count=0, blockers=[]
    )["state"] == state


@pytest.mark.parametrize(
    ("state", "count", "blockers", "code"),
    [
        ("DO_NOT_ECHO", 0, [], "compact_state_invalid"),
        ("preview", True, [], "compiled_step_count_invalid"),
        ("preview", -1, [], "compiled_step_count_invalid"),
        ("preview", 0, "not-a-list", "blockers_invalid"),
        ("preview", 0, ["DO_NOT_ECHO/path"], "blockers_invalid"),
        ("preview", 0, ["x" * 129], "blockers_invalid"),
    ],
)
def test_compact_rejects_invalid_state_count_and_unredacted_blockers(
    state: object, count: object, blockers: object, code: str
) -> None:
    with pytest.raises(SemanticAuthorityError) as raised:
        compact_semantic_authority(
            valid_authority(),
            state=state,
            compiled_step_count=count,
            blockers=blockers,
        )
    assert raised.value.code == code
    assert "DO_NOT_ECHO" not in str(raised.value)


def test_error_constructor_rejects_arbitrary_hostile_codes() -> None:
    with pytest.raises(ValueError) as raised:
        SemanticAuthorityError("DO_NOT_ECHO")
    assert "DO_NOT_ECHO" not in str(raised.value)

    with pytest.raises(ValueError) as raised:
        SemanticAuthorityError([])
    assert str(raised.value) == "semantic_authority_error_code_invalid"


@pytest.mark.parametrize(
    ("location", "value", "code"),
    [
        ("kind", [], "requirement_kind_invalid"),
        ("requirement_operation", [], "requirement_operation_invalid"),
        ("requirement_sensitivity", {}, "sensitivity_invalid"),
        ("proposal_operation", [], "proposal_operation_invalid"),
        ("proposal_sensitivity", {}, "sensitivity_invalid"),
    ],
)
def test_unhashable_hostile_enum_values_fail_closed(
    location: str, value: object, code: str
) -> None:
    authority = valid_authority()
    if location == "kind":
        authority["requirements"][0]["kind"] = value
    elif location == "requirement_operation":
        authority["requirements"][0]["operation"] = value
    elif location == "requirement_sensitivity":
        authority["requirements"][0]["sensitivity"] = value
    elif location == "proposal_operation":
        authority["proposed_effects"][0]["operation"] = value
    else:
        authority["proposed_effects"][0]["sensitivity"] = value

    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(authority)
    assert raised.value.code == code


def test_unhashable_compact_state_fails_closed() -> None:
    with pytest.raises(SemanticAuthorityError) as raised:
        compact_semantic_authority(
            valid_authority(), state=[], compiled_step_count=0, blockers=[]
        )
    assert raised.value.code == "compact_state_invalid"


class _HostileString(str):
    def __deepcopy__(self, memo):
        raise RuntimeError("DO_NOT_ECHO")


def test_scalar_subclasses_and_non_string_mapping_keys_fail_closed() -> None:
    schema_subclass = valid_authority()
    schema_subclass["schema_version"] = _HostileString(
        "mission-semantic-authority/v1"
    )
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(schema_subclass)
    assert raised.value.code == "schema_version_invalid"

    scalar_subclass = valid_authority()
    scalar_subclass["requirements"][0]["phase"] = _HostileString("revision")
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(scalar_subclass)
    assert raised.value.code == "ordinary_scalar_invalid"

    non_string_key = valid_authority()
    non_string_key["requirements"][0][1] = "DO_NOT_ECHO"
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(non_string_key)
    assert raised.value.code == "requirement_fields_invalid"
    assert "DO_NOT_ECHO" not in str(raised.value)


def test_compact_blocker_list_has_a_fixed_item_bound() -> None:
    with pytest.raises(SemanticAuthorityError) as raised:
        compact_semantic_authority(
            valid_authority(),
            state="blocked",
            compiled_step_count=0,
            blockers=["blocked"] * 65,
        )
    assert raised.value.code == "blockers_invalid"


@pytest.mark.parametrize("target", ["C:outside.txt", "z:folder/file.txt"])
def test_windows_drive_relative_targets_are_rejected_for_all_items(target: str) -> None:
    requirement = valid_authority()
    requirement["requirements"][0]["target"] = target
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(requirement)
    assert raised.value.code == "target_invalid"

    proposal = valid_authority()
    proposal["proposed_effects"][0]["target"] = target
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(proposal)
    assert raised.value.code == "target_invalid"


def test_unresolved_items_have_an_exact_bounded_canonical_shape() -> None:
    authority = valid_authority()
    authority["unresolved"] = [
        {
            "unresolved_id": "unr_0123456789ab",
            "kind": "ambiguous_target",
            "phase": "revision",
            "agent_id": "claude-worker",
        }
    ]
    validated = validate_semantic_authority(authority)
    assert validated == authority
    assert validated["unresolved"] is not authority["unresolved"]

    authority["unresolved"][0]["literal"] = "DO_NOT_ECHO"
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(authority)
    assert raised.value.code == "unresolved_fields_invalid"
    assert "DO_NOT_ECHO" not in str(raised.value)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("unresolved_id", "unr_bad", "unresolved_id_invalid"),
        ("kind", "Bad kind/DO_NOT_ECHO", "unresolved_kind_invalid"),
        ("phase", "x" * 129, "ordinary_scalar_invalid"),
        ("agent_id", [], "ordinary_scalar_invalid"),
    ],
)
def test_unresolved_item_fields_fail_closed(field: str, value: object, code: str) -> None:
    authority = valid_authority()
    authority["unresolved"] = [
        {
            "unresolved_id": "unr_0123456789ab",
            "kind": "ambiguous_target",
            "phase": "revision",
            "agent_id": "claude-worker",
        }
    ]
    authority["unresolved"][0][field] = value
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(authority)
    assert raised.value.code == code
    assert "DO_NOT_ECHO" not in str(raised.value)


class _HashCollidingKey:
    armed = False

    def __init__(self, target: str) -> None:
        self.target = target

    def __hash__(self) -> int:
        return hash(self.target)

    def __eq__(self, other: object) -> bool:
        if self.armed:
            raise RuntimeError("DO_NOT_ECHO")
        return False


@pytest.mark.parametrize("container", ["requirement", "proposal"])
def test_nested_hostile_keys_are_rejected_before_any_lookup(container: str) -> None:
    authority = valid_authority()
    if container == "requirement":
        del authority["requirements"][0]["requirement_id"]
        hostile_key = _HashCollidingKey("requirement_id")
        authority["requirements"][0][hostile_key] = "hostile"
        code = "requirement_fields_invalid"
    else:
        del authority["proposed_effects"][0]["proposed_effect_id"]
        hostile_key = _HashCollidingKey("proposed_effect_id")
        authority["proposed_effects"][0][hostile_key] = "hostile"
        code = "proposal_fields_invalid"
    hostile_key.armed = True
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(authority)
    assert raised.value.code == code
    assert "DO_NOT_ECHO" not in str(raised.value)


def test_validate_success_guarantees_representative_authority_can_be_hashed() -> None:
    validated = validate_semantic_authority(valid_authority())
    authority_hash = semantic_authority_hash(validated)
    assert authority_hash.startswith("sha256:")
    assert len(authority_hash) == 71


def test_lone_surrogate_is_rejected_during_validation_without_echo() -> None:
    authority = valid_authority()
    authority["requirements"][0]["after"] = {"content_equals": "\ud800"}
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(authority)
    assert raised.value.code == "unicode_scalar_invalid"
    assert str(raised.value) == "unicode_scalar_invalid"


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        (-(2**63), True),
        (2**63 - 1, True),
        (-(2**63) - 1, False),
        (2**63, False),
        (1.0e308, True),
        (-1.0e308, True),
        (1.1e308, False),
        (True, False),
    ],
)
def test_json_number_domain_is_explicit_and_deterministic(
    value: object, valid: bool
) -> None:
    authority = valid_authority()
    authority["requirements"][0]["after"] = {"content_equals": value}
    if valid:
        validated = validate_semantic_authority(authority)
        assert semantic_authority_hash(validated).startswith("sha256:")
    else:
        with pytest.raises(SemanticAuthorityError) as raised:
            validate_semantic_authority(authority)
        assert raised.value.code == "number_out_of_range"


def test_extremely_large_integer_is_rejected_before_hashing_without_echo() -> None:
    authority = valid_authority()
    authority["requirements"][0]["after"] = {"content_equals": 10**5000}
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(authority)
    assert raised.value.code == "number_out_of_range"
    assert str(raised.value) == "number_out_of_range"


def test_requirement_and_proposal_counts_are_rejected_before_item_walk() -> None:
    requirements = valid_authority()
    requirements["requirements"] = [requirements["requirements"][0]] * 20_000
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(requirements)
    assert raised.value.code == "requirements_count_exceeded"

    proposed = valid_authority()
    proposed["proposed_effects"] = [proposed["proposed_effects"][0]] * 20_000
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(proposed)
    assert raised.value.code == "proposed_effects_count_exceeded"


def test_compact_rejects_oversized_authority_with_the_same_closed_code() -> None:
    authority = valid_authority()
    authority["requirements"] = [authority["requirements"][0]] * 20_000
    with pytest.raises(SemanticAuthorityError) as raised:
        compact_semantic_authority(
            authority, state="preview", compiled_step_count=0, blockers=[]
        )
    assert raised.value.code == "requirements_count_exceeded"


def test_target_has_a_fixed_utf8_byte_bound_without_echo() -> None:
    at_boundary = valid_authority()
    at_boundary["requirements"][0]["target"] = "a" * 1024
    assert validate_semantic_authority(at_boundary) == at_boundary

    oversized = valid_authority()
    oversized["requirements"][0]["target"] = "DO_NOT_ECHO" + "a" * 4096
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(oversized)
    assert raised.value.code == "target_invalid"
    assert "DO_NOT_ECHO" not in str(raised.value)


def test_overall_canonical_authority_bytes_have_a_fixed_bound() -> None:
    authority = valid_authority()
    requirements = []
    for index in range(256):
        requirements.append(
            {
                "requirement_id": f"req_{index:012x}",
                "kind": "create",
                "target": f"artifact-{index:03d}.txt",
                "operation": "create",
                "literal": "x" * 4096,
                "phase": "implementation",
                "agent_id": "claude-worker",
                "sensitivity": "ordinary",
            }
        )
    authority["requirements"] = requirements

    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(authority)
    assert raised.value.code == "authority_size_exceeded"


@pytest.mark.parametrize("control", ["\n", "\r", "\t", "\x1f", "\x7f"])
def test_target_rejects_control_characters_without_echo(control: str) -> None:
    authority = valid_authority()
    authority["requirements"][0]["target"] = f"folder/{control}DO_NOT_ECHO.txt"
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(authority)
    assert raised.value.code == "target_invalid"
    assert "DO_NOT_ECHO" not in str(raised.value)


def test_unicode_text_must_already_be_nfc_and_is_never_silently_normalized() -> None:
    nfc = valid_authority()
    nfc["requirements"][0]["target"] = "caf\u00e9.txt"
    nfc["proposed_effects"][0]["target"] = "caf\u00e9.txt"
    assert validate_semantic_authority(nfc) == nfc

    nfd_target = valid_authority()
    nfd_target["requirements"][0]["target"] = "cafe\u0301.txt"
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(nfd_target)
    assert raised.value.code == "unicode_normalization_invalid"

    nfd_literal = valid_authority()
    nfd_literal["requirements"][0]["after"] = {
        "content_equals": "re\u0301vision"
    }
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(nfd_literal)
    assert raised.value.code == "unicode_normalization_invalid"


def test_canonical_hash_has_a_fixed_utf8_and_number_golden_vector() -> None:
    authority = valid_authority()
    authority["requirements"][0]["target"] = "caf\u00e9.txt"
    authority["requirements"][0]["before"] = {"content_equals": "r\u00e9vision"}
    authority["requirements"][0]["after"] = {"content_equals": 42}
    authority["proposed_effects"][0]["target"] = "caf\u00e9.txt"

    assert semantic_authority_hash(authority) == (
        "sha256:9777311c13a31bfbef7856ba1c7aba7d251440129ef8095e8d7e6fb3cd887e7f"
    )
