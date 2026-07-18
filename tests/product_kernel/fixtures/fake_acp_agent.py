from __future__ import annotations

import asyncio
from typing import Any

from acp import PROTOCOL_VERSION
from acp.schema import (
    AgentCapabilities,
    AgentMessageChunk,
    InitializeResponse,
    NewSessionResponse,
    PermissionOption,
    PromptResponse,
    TextContentBlock,
    ToolCallLocation,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
)


class FakeACPAgent:
    """Official-model ACP fake with deterministic adversarial scripts."""

    def __init__(self, scenario: str = "success") -> None:
        self.scenario = scenario
        self.client: Any = None
        self.cancelled = False
        self.permission_outcomes: list[str] = []

    def on_connect(self, client: object) -> None:
        self.client = client

    async def initialize(
        self, protocol_version: int, client_capabilities: object = None,
        client_info: object = None, **kwargs: Any,
    ) -> InitializeResponse:
        resolved = PROTOCOL_VERSION + 1 if self.scenario == "protocol_mismatch" else PROTOCOL_VERSION
        return InitializeResponse(
            protocol_version=resolved,
            agent_capabilities=AgentCapabilities(),
        )

    async def new_session(
        self, cwd: str, additional_directories: object = None,
        mcp_servers: object = None, **kwargs: Any,
    ) -> NewSessionResponse:
        return NewSessionResponse(session_id="raw-acp-session")

    async def prompt(
        self, session_id: str, prompt: object, **kwargs: Any,
    ) -> PromptResponse:
        if self.scenario == "disconnect_before_work":
            raise ConnectionError("RAW-DISCONNECT-BODY")
        if self.scenario == "oversize":
            await self._message(session_id, "x" * 70_000)
            return PromptResponse(stop_reason="end_turn")
        if self.scenario == "total_oversize":
            await self._message(session_id, "x" * 40_000)
            await self._message(session_id, "y" * 40_000)
            return PromptResponse(stop_reason="end_turn")
        if self.scenario == "secret_output":
            await self._message(session_id, "token=secret-token")
            return PromptResponse(stop_reason="end_turn")
        if self.scenario in {"duplicate_event", "out_of_order"}:
            sequence = 2 if self.scenario == "duplicate_event" else 3
            await self._message(session_id, "first", sequence=sequence, event_id="acp_evt_1")
            second_sequence = sequence if self.scenario == "duplicate_event" else 2
            await self._message(
                session_id, "second", sequence=second_sequence,
                event_id="acp_evt_1" if self.scenario == "duplicate_event" else "acp_evt_2",
            )
            return PromptResponse(stop_reason="end_turn")

        await self.client.session_update(
            session_id,
            ToolCallStart(
                session_update="tool_call", tool_call_id="call_1",
                title="Edit project", kind="edit", status="in_progress",
            ),
        )
        permission_count = 2 if self.scenario == "two_permissions" else 1
        for index in range(1, permission_count + 1):
            raw_input = (
                {"blob": "x" * 70_000}
                if self.scenario == "permission_oversize"
                else None
            )
            response = await self.client.request_permission(
                session_id,
                ToolCallUpdate(
                    tool_call_id=f"call_{index}", kind="edit",
                    title="Edit project", raw_input=raw_input,
                ),
                [
                    PermissionOption(option_id=f"allow_{index}", name="Allow once", kind="allow_once"),
                    PermissionOption(option_id=f"reject_{index}", name="Reject", kind="reject_once"),
                ],
            )
            self.permission_outcomes.append(response.outcome.outcome)
        await self.client.session_update(
            session_id,
            ToolCallProgress(
                session_update="tool_call_update", tool_call_id="call_1",
                kind="edit", title="Edit project", status="in_progress",
            ),
        )
        await self.client.session_update(
            session_id,
            ToolCallProgress(
                session_update="tool_call_update", tool_call_id="call_1",
                kind="edit", title="Edit project", status="completed",
                locations=[ToolCallLocation(path="index.html", line=1)],
            ),
        )
        if self.scenario == "disconnect_after_effect":
            raise ConnectionError("RAW-DISCONNECT-BODY")
        await self._message(session_id, "Implementation complete")
        if self.scenario == "invalid_result":
            return {"stop_reason": "end_turn", "raw": "RAW-RESULT"}  # type: ignore[return-value]
        if self.scenario == "refusal":
            return PromptResponse(stop_reason="refusal")
        return PromptResponse(stop_reason="end_turn")

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        self.cancelled = True

    async def _message(
        self, session_id: str, text: str, *, sequence: int | None = None,
        event_id: str | None = None,
    ) -> None:
        metadata = None
        if sequence is not None or event_id is not None:
            metadata = {"sequence": sequence, "event_id": event_id}
        await self.client.session_update(
            session_id,
            AgentMessageChunk(
                session_update="agent_message_chunk",
                content=TextContentBlock(type="text", text=text),
                field_meta=metadata,
            ),
        )


__all__ = ["FakeACPAgent"]
