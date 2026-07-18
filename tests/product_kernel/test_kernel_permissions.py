from dataclasses import FrozenInstanceError

import pytest

from agentdeck.kernel.permissions import (
    Effect,
    PermissionDecision,
    PermissionError,
    PermissionProfile,
    PermissionScope,
)


ALL_EFFECTS = frozenset(Effect)


def test_permission_profiles_are_exactly_the_three_declared_product_profiles() -> None:
    assert {profile.value for profile in PermissionProfile} == {
        "ask_for_approval",
        "approve_for_me",
        "full_access",
    }


@pytest.mark.parametrize("profile", tuple(PermissionProfile))
def test_each_profile_ceiling_keeps_every_declared_effect_requestable(
    profile: PermissionProfile,
) -> None:
    scope = PermissionScope.for_profile(profile)

    assert scope.profile is profile
    assert scope.effects == ALL_EFFECTS
    assert all(scope.allows(effect) for effect in Effect)


def test_approve_for_me_is_the_default_profile_semantics() -> None:
    scope = PermissionScope.for_profile()

    assert scope.profile is PermissionProfile.APPROVE_FOR_ME
    assert scope.effects == ALL_EFFECTS


def test_permission_can_narrow_but_never_expand() -> None:
    mission = PermissionScope.for_profile(PermissionProfile.APPROVE_FOR_ME)
    task = mission.narrow({Effect.READ, Effect.WRITE_PROJECT})

    assert task is not mission
    assert task.effects == frozenset({Effect.READ, Effect.WRITE_PROJECT})
    assert task.allows(Effect.WRITE_PROJECT)
    with pytest.raises(PermissionError, match="cannot expand"):
        task.narrow({*task.effects, Effect.PUBLISH})


def test_narrowed_scope_fails_closed_before_profile_policy() -> None:
    scope = PermissionScope.for_profile(PermissionProfile.FULL_ACCESS).narrow(
        {Effect.READ}
    )

    decision = scope.decide(Effect.NETWORK, actor="agt_1")

    assert decision == PermissionDecision(
        effect=Effect.NETWORK,
        actor="agt_1",
        allowed=False,
        requires_human=False,
        requires_independent_reviewer=False,
        auditable=True,
        reason="outside_scope",
    )


@pytest.mark.parametrize(
    ("profile", "effect", "allowed", "requires_human", "requires_reviewer"),
    (
        *(
            (PermissionProfile.ASK_FOR_APPROVAL, effect, effect is Effect.READ, effect is not Effect.READ, False)
            for effect in Effect
        ),
        (PermissionProfile.APPROVE_FOR_ME, Effect.READ, True, False, False),
        (PermissionProfile.APPROVE_FOR_ME, Effect.WRITE_PROJECT, True, False, False),
        (PermissionProfile.APPROVE_FOR_ME, Effect.COMMAND_PROJECT, True, False, False),
        (PermissionProfile.APPROVE_FOR_ME, Effect.NETWORK, False, False, True),
        (PermissionProfile.APPROVE_FOR_ME, Effect.WRITE_EXTERNAL, False, True, False),
        (PermissionProfile.APPROVE_FOR_ME, Effect.CREDENTIAL, False, True, False),
        (PermissionProfile.APPROVE_FOR_ME, Effect.DESTRUCTIVE, False, True, False),
        (PermissionProfile.APPROVE_FOR_ME, Effect.PUBLISH, False, True, False),
        *((PermissionProfile.FULL_ACCESS, effect, True, False, False) for effect in Effect),
    ),
)
def test_profile_effect_matrix_is_explicit_and_auditable(
    profile: PermissionProfile,
    effect: Effect,
    allowed: bool,
    requires_human: bool,
    requires_reviewer: bool,
) -> None:
    decision = PermissionScope.for_profile(profile).decide(effect, actor="agt_1")

    assert decision.effect is effect
    assert decision.actor == "agt_1"
    assert decision.allowed is allowed
    assert decision.requires_human is requires_human
    assert decision.requires_independent_reviewer is requires_reviewer
    assert decision.auditable is True


def test_full_access_still_records_an_auditable_decision() -> None:
    decision = PermissionScope.for_profile(PermissionProfile.FULL_ACCESS).decide(
        Effect.NETWORK, actor="agt_1"
    )

    assert decision.allowed is True
    assert decision.requires_human is False
    assert decision.requires_independent_reviewer is False
    assert decision.auditable is True


def test_permission_facts_are_frozen_and_copy_mutable_inputs() -> None:
    effects = {Effect.READ, Effect.WRITE_PROJECT}
    scope = PermissionScope.for_profile().narrow(effects)
    effects.clear()

    assert scope.effects == frozenset({Effect.READ, Effect.WRITE_PROJECT})
    with pytest.raises(FrozenInstanceError):
        scope.profile = PermissionProfile.FULL_ACCESS  # type: ignore[misc]

    decision = scope.decide(Effect.READ, actor="agt_1")
    with pytest.raises(FrozenInstanceError):
        decision.allowed = False  # type: ignore[misc]


@pytest.mark.parametrize(
    ("constructor", "error"),
    (
        (lambda: PermissionScope.for_profile("approve_for_me"), TypeError),  # type: ignore[arg-type]
        (lambda: PermissionScope(PermissionProfile.APPROVE_FOR_ME, {Effect.READ}), TypeError),  # type: ignore[arg-type]
        (lambda: PermissionScope(PermissionProfile.APPROVE_FOR_ME, frozenset({"read"})), TypeError),  # type: ignore[arg-type]
        (lambda: PermissionScope.for_profile().narrow([Effect.READ]), TypeError),  # type: ignore[arg-type]
        (lambda: PermissionScope.for_profile().narrow({"read"}), TypeError),  # type: ignore[arg-type]
        (lambda: PermissionScope.for_profile().allows("read"), TypeError),  # type: ignore[arg-type]
        (lambda: PermissionScope.for_profile().decide("read", actor="agt_1"), TypeError),  # type: ignore[arg-type]
        (lambda: PermissionScope.for_profile().decide(Effect.READ, actor=""), ValueError),
        (lambda: PermissionScope.for_profile().decide(Effect.READ, actor=" "), ValueError),
        (lambda: PermissionScope.for_profile().decide(Effect.READ, actor=1), TypeError),  # type: ignore[arg-type]
    ),
)
def test_permission_boundary_rejects_invalid_types_and_actor(
    constructor: object, error: type[Exception]
) -> None:
    with pytest.raises(error):
        constructor()  # type: ignore[operator]


def test_decision_flags_are_mutually_exclusive() -> None:
    for profile in PermissionProfile:
        for effect in Effect:
            decision = PermissionScope.for_profile(profile).decide(effect, actor="agt_1")
            assert not (
                decision.allowed
                and (
                    decision.requires_human
                    or decision.requires_independent_reviewer
                )
            )
            assert not (
                decision.requires_human
                and decision.requires_independent_reviewer
            )


@pytest.mark.parametrize(
    ("allowed", "requires_human", "requires_reviewer", "reason"),
    (
        (True, False, False, "allowed"),
        (False, True, False, "human_approval_required"),
        (False, False, True, "independent_reviewer_required"),
        (False, False, False, "outside_scope"),
    ),
)
def test_direct_permission_decisions_cannot_be_non_auditable(
    allowed: bool,
    requires_human: bool,
    requires_reviewer: bool,
    reason: str,
) -> None:
    with pytest.raises(ValueError, match="auditable"):
        PermissionDecision(
            effect=Effect.READ,
            actor="agt_1",
            allowed=allowed,
            requires_human=requires_human,
            requires_independent_reviewer=requires_reviewer,
            auditable=False,
            reason=reason,
        )


def test_corrupted_unsupported_profile_fails_closed() -> None:
    scope = object.__new__(PermissionScope)
    object.__setattr__(scope, "profile", object())
    object.__setattr__(scope, "effects", ALL_EFFECTS)

    with pytest.raises(PermissionError, match="unsupported permission profile"):
        scope.decide(Effect.READ, actor="agt_1")
