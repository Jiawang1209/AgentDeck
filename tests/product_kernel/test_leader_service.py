from __future__ import annotations

from copy import deepcopy
import traceback

import pytest

from agentdeck.application.leader_service import LeaderService
from agentdeck.ports.leader import (
    LeaderFailure,
    LeaderFailureCode,
    LeaderProposal,
    LeaderRequest,
    ProposalError,
)

from .test_leader_contract import (
    exception_chain_text,
    proposal_with_hostile_key,
    request,
    valid_proposal,
)


class FakeLeader:
    def __init__(self, results: list[object]) -> None:
        self.results = list(results)
        self.requests: list[LeaderRequest] = []

    def propose_mission(self, leader_request: LeaderRequest) -> object:
        self.requests.append(leader_request)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return deepcopy(result)


class RawFakeLeader(FakeLeader):
    def propose_mission(self, leader_request: LeaderRequest) -> object:
        self.requests.append(leader_request)
        return self.results.pop(0)


def test_valid_proposal_needs_no_repair() -> None:
    leader = FakeLeader([valid_proposal()])

    result = LeaderService(leader).propose(request())

    assert result.repair_count == 0
    assert result.proposal.objective == "Build an accessible page"
    assert len(leader.requests) == 1
    assert leader.requests[0].schema_repair is None


def test_one_bounded_schema_repair_preserves_the_request_context() -> None:
    invalid = valid_proposal()
    invalid.pop("tasks")
    leader = FakeLeader([invalid, valid_proposal()])
    original = request()

    result = LeaderService(leader).propose(original)

    assert result.repair_count == 1
    assert len(leader.requests) == 2
    repair = leader.requests[1].schema_repair
    assert repair is not None
    assert (repair.attempt, repair.code) == (1, LeaderFailureCode.SCHEMA)
    assert leader.requests[1].user_goal == original.user_goal
    assert leader.requests[1].project_context == original.project_context
    assert leader.requests[1].available_agents == original.available_agents
    assert leader.requests[1].permission_ceiling is original.permission_ceiling
    assert leader.requests[1].resolved_model == original.resolved_model


def test_second_schema_failure_is_returned_without_a_third_call() -> None:
    first = valid_proposal()
    first.pop("tasks")
    second = valid_proposal()
    second["unexpected"] = True
    leader = FakeLeader([first, second, valid_proposal()])

    with pytest.raises(LeaderFailure) as error:
        LeaderService(leader).propose(request())

    assert type(error.value) is LeaderFailure
    assert error.value.code is LeaderFailureCode.SCHEMA
    assert len(leader.requests) == 2


def test_semantic_failure_is_never_sent_for_schema_repair() -> None:
    payload = valid_proposal()
    payload["permission_profile"] = "full_access"
    leader = FakeLeader([payload, valid_proposal()])

    with pytest.raises(LeaderFailure) as error:
        LeaderService(leader).propose(request())

    assert type(error.value) is LeaderFailure
    assert error.value.code is LeaderFailureCode.SEMANTIC
    assert len(leader.requests) == 1


@pytest.mark.parametrize(
    "code",
    [
        LeaderFailureCode.TIMEOUT,
        LeaderFailureCode.NONZERO,
        LeaderFailureCode.AUTHENTICATION,
        LeaderFailureCode.TRANSPORT,
        LeaderFailureCode.SEMANTIC,
        LeaderFailureCode.CANCELLATION,
        LeaderFailureCode.OVERSIZE,
    ],
)
def test_non_schema_diagnostic_categories_are_preserved(
    code: LeaderFailureCode,
) -> None:
    failure = LeaderFailure(code)
    leader = FakeLeader([failure])

    with pytest.raises(LeaderFailure) as error:
        LeaderService(leader).propose(request())

    assert type(error.value) is LeaderFailure
    assert error.value is not failure
    assert error.value.code is code
    assert len(leader.requests) == 1


def test_port_schema_failure_receives_exactly_one_repair() -> None:
    first = LeaderFailure(LeaderFailureCode.SCHEMA)
    leader = FakeLeader([first, valid_proposal()])

    result = LeaderService(leader).propose(request())

    assert result.repair_count == 1
    assert len(leader.requests) == 2


def test_unexpected_port_result_is_a_repairable_schema_failure() -> None:
    leader = FakeLeader(["not a mapping", valid_proposal()])

    result = LeaderService(leader).propose(request())

    assert result.repair_count == 1


@pytest.mark.parametrize("location", ["proposal", "task", "budgets"])
def test_hostile_key_is_schema_repaired_exactly_once(location: str) -> None:
    leader = RawFakeLeader([proposal_with_hostile_key(location), valid_proposal()])

    result = LeaderService(leader).propose(request())

    assert result.repair_count == 1
    assert len(leader.requests) == 2
    assert leader.requests[1].schema_repair is not None


def test_port_semantic_message_is_rebuilt_without_marker_and_not_repaired() -> None:
    marker = "attacker-controlled-port-semantic-marker"
    leader = FakeLeader([ProposalError(LeaderFailureCode.SEMANTIC, marker)])

    with pytest.raises(LeaderFailure) as error:
        LeaderService(leader).propose(request())

    assert type(error.value) is LeaderFailure
    assert error.value.code is LeaderFailureCode.SEMANTIC
    assert marker not in "".join(traceback.format_exception(error.value))
    assert len(leader.requests) == 1


def test_second_port_schema_message_is_rebuilt_without_marker() -> None:
    marker = "attacker-controlled-port-schema-marker"
    leader = FakeLeader([
        ProposalError(LeaderFailureCode.SCHEMA, marker),
        ProposalError(LeaderFailureCode.SCHEMA, marker),
    ])

    with pytest.raises(LeaderFailure) as error:
        LeaderService(leader).propose(request())

    assert type(error.value) is LeaderFailure
    assert error.value.code is LeaderFailureCode.SCHEMA
    assert marker not in "".join(traceback.format_exception(error.value))
    assert len(leader.requests) == 2


@pytest.mark.parametrize(
    ("code", "expected_calls"),
    [
        (LeaderFailureCode.SCHEMA, 2),
        (LeaderFailureCode.SEMANTIC, 1),
    ],
)
def test_port_marker_is_absent_from_recursive_service_chain(
    code: LeaderFailureCode, expected_calls: int
) -> None:
    marker = f"recursive-port-{code.value}-marker"
    leader = FakeLeader([
        ProposalError(code, marker),
        ProposalError(code, marker),
    ])

    with pytest.raises(LeaderFailure) as error:
        LeaderService(leader).propose(request())

    assert marker not in exception_chain_text(error.value)
    assert len(leader.requests) == expected_calls


def test_typed_proposal_is_revalidated_against_the_current_request() -> None:
    mismatched = request()
    different_root = valid_proposal()
    different_root["project_root"] = "/tmp/other"
    typed = LeaderProposal.from_mapping(different_root)
    leader = FakeLeader([typed])

    with pytest.raises(LeaderFailure) as error:
        LeaderService(leader).propose(mismatched)

    assert type(error.value) is LeaderFailure
    assert error.value.code is LeaderFailureCode.SEMANTIC


@pytest.mark.parametrize("bad_leader", [object(), None, lambda _: {}])
def test_service_requires_the_leader_port(bad_leader: object) -> None:
    with pytest.raises(TypeError, match="Leader Port"):
        LeaderService(bad_leader)  # type: ignore[arg-type]


def test_service_rejects_non_request_input_before_calling_port() -> None:
    leader = FakeLeader([valid_proposal()])

    with pytest.raises(TypeError, match="LeaderRequest"):
        LeaderService(leader).propose({})  # type: ignore[arg-type]

    assert leader.requests == []
