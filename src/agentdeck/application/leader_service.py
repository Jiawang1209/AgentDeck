"""Application sequencing for one bounded Leader proposal repair."""

from __future__ import annotations

from dataclasses import dataclass

from agentdeck.ports.leader import (
    LeaderFailure,
    LeaderFailureCode,
    LeaderPort,
    LeaderProposal,
    LeaderRequest,
)


@dataclass(frozen=True)
class LeaderProposalResult:
    proposal: LeaderProposal
    repair_count: int


class LeaderService:
    def __init__(self, leader: LeaderPort) -> None:
        if not callable(getattr(leader, "propose_mission", None)):
            raise TypeError("leader must satisfy the Leader Port")
        self._leader = leader

    def propose(self, request: LeaderRequest) -> LeaderProposalResult:
        if type(request) is not LeaderRequest:
            raise TypeError("request must be a LeaderRequest")
        current = request
        for repair_count in (0, 1):
            failure_code: LeaderFailureCode | None = None
            try:
                raw = self._leader.propose_mission(current)
                if type(raw) is LeaderProposal:
                    raw = raw.to_mapping()
                proposal = LeaderProposal.from_mapping(raw, request=current)
            except LeaderFailure as error:
                try:
                    code = error.code
                except Exception:
                    code = LeaderFailureCode.TRANSPORT
                failure_code = (
                    code if type(code) is LeaderFailureCode
                    else LeaderFailureCode.TRANSPORT
                )
            except Exception:
                failure_code = LeaderFailureCode.TRANSPORT
            if failure_code is not None:
                if failure_code is LeaderFailureCode.SCHEMA and repair_count == 0:
                    current = request.for_schema_repair()
                    continue
                raise LeaderFailure(failure_code)
            return LeaderProposalResult(proposal, repair_count)
        raise AssertionError("bounded Leader repair loop exhausted")
