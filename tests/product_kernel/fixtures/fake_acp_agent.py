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
        self.cancel_gate = asyncio.Event()
        self.permission_outcomes: list[str] = []
        self.pending_permission_task: asyncio.Task[Any] | None = None

    def on_connect(self, client: object) -> None:
        self.client = client

    async def initialize(
        self, protocol_version: int, client_capabilities: object = None,
        client_info: object = None, **kwargs: Any,
    ) -> InitializeResponse:
        if self.scenario == "initialization_failure":
            raise ConnectionError("RAW-INITIALIZATION-BODY")
        resolved = PROTOCOL_VERSION + 1 if self.scenario == "protocol_mismatch" else PROTOCOL_VERSION
        return InitializeResponse(
            protocol_version=resolved,
            agent_capabilities=AgentCapabilities(),
        )

    async def new_session(
        self, cwd: str, additional_directories: object = None,
        mcp_servers: object = None, **kwargs: Any,
    ) -> NewSessionResponse:
        if self.scenario == "session_failure":
            raise ConnectionError("RAW-SESSION-BODY")
        return NewSessionResponse(session_id="raw-acp-session")

    async def prompt(
        self, session_id: str, prompt: object, **kwargs: Any,
    ) -> PromptResponse:
        if self.scenario in {"cancel_race", "cancel_failure"}:
            await self.cancel_gate.wait()
            return PromptResponse(stop_reason="end_turn")
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

        if self.scenario == "terminal_with_pending_permission":
            await self.client.session_update(
                session_id,
                ToolCallStart(
                    session_update="tool_call", tool_call_id="call_pending",
                    title="Edit project", kind="edit", status="in_progress",
                ),
            )
            self.pending_permission_task = asyncio.create_task(
                self.client.request_permission(
                    session_id,
                    ToolCallUpdate(
                        tool_call_id="call_pending", kind="edit",
                        title="Edit project",
                        raw_input={"note": "RAW-PENDING-PERMISSION-BODY"},
                    ),
                    [
                        PermissionOption(
                            option_id="allow_pending", name="Allow once",
                            kind="allow_once",
                        ),
                        PermissionOption(
                            option_id="reject_pending", name="Reject",
                            kind="reject_once",
                        ),
                    ],
                )
            )
            await asyncio.sleep(0)
            return PromptResponse(stop_reason="end_turn")

        authority_failures = {
            "effect_sensitive": ("edit", "sensitive"),
            "read_sensitive": ("read", "sensitive"),
            "effect_oversize": ("edit", "oversize"),
            "read_oversize": ("read", "oversize"),
            "effect_sequence": ("edit", "sequence"),
            "read_sequence": ("read", "sequence"),
            "effect_invalid_result": ("edit", "invalid_result"),
            "read_invalid_result": ("read", "invalid_result"),
        }
        if self.scenario in authority_failures:
            tool_kind, failure = authority_failures[self.scenario]
            title = {
                "sensitive": "token=secret-token",
                "oversize": "x" * 70_000,
            }.get(failure, "Inspect project")
            metadata = {"sequence": 0} if failure == "sequence" else None
            await self.client.session_update(
                session_id,
                ToolCallStart(
                    session_update="tool_call", tool_call_id="call_authority",
                    title=title, kind=tool_kind, status="in_progress",
                    field_meta=metadata,
                ),
            )
            if failure == "invalid_result":
                return {"stop_reason": "end_turn", "raw": "RAW-RESULT"}  # type: ignore[return-value]
            return PromptResponse(stop_reason="end_turn")

        progress_failures = {
            "effect_progress_sensitive": ("edit", "sensitive"),
            "read_progress_sensitive": ("read", "sensitive"),
            "effect_progress_oversize": ("edit", "oversize"),
            "read_progress_oversize": ("read", "oversize"),
            "effect_progress_sequence": ("edit", "sequence"),
            "read_progress_sequence": ("read", "sequence"),
            "effect_progress_invalid_result": ("edit", "invalid_result"),
            "read_progress_invalid_result": ("read", "invalid_result"),
        }
        if self.scenario in progress_failures:
            tool_kind, failure = progress_failures[self.scenario]
            title = {
                "sensitive": "token=secret-token",
                "oversize": "x" * 70_000,
            }.get(failure, "Inspect project")
            metadata = {"sequence": 0} if failure == "sequence" else None
            await self.client.session_update(
                session_id,
                ToolCallProgress(
                    session_update="tool_call_update", tool_call_id="call_progress",
                    title=title, kind=tool_kind, status="in_progress",
                    field_meta=metadata,
                ),
            )
            if failure == "invalid_result":
                return {"stop_reason": "end_turn", "raw": "RAW-RESULT"}  # type: ignore[return-value]
            return PromptResponse(stop_reason="end_turn")

        tool_kind = {
            "disconnect_during_read": "read",
            "disconnect_during_search": "search",
            "disconnect_during_think": "think",
            "disconnect_during_other": "other",
            "disconnect_during_none": None,
        }.get(self.scenario, "edit")
        await self.client.session_update(
            session_id,
            ToolCallStart(
                session_update="tool_call", tool_call_id="call_1",
                title="Edit project" if tool_kind == "edit" else "Inspect project",
                kind=tool_kind, status="in_progress",
            ),
        )
        if self.scenario in {
            "disconnect_during_effect", "disconnect_during_read",
            "disconnect_during_search", "disconnect_during_think",
            "disconnect_during_other", "disconnect_during_none",
        }:
            raise ConnectionError("RAW-DISCONNECT-BODY")
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
        if self.scenario in {"cancel_race", "cancel_failure"}:
            self.cancel_gate.set()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        if self.scenario == "cancel_failure":
            raise ConnectionError("RAW-CANCEL-BODY")

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
