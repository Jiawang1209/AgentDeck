"""Official-model ACP stdio fake for the Product Kernel Leader transport."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from typing import Any

from acp import PROTOCOL_VERSION, run_agent
from acp.helpers import (
    embedded_text_resource,
    resource_block,
    start_tool_call,
    update_agent_message,
    update_agent_message_text,
)
from acp.schema import (
    AgentCapabilities,
    InitializeResponse,
    NewSessionResponse,
    LoadSessionResponse,
    PermissionOption,
    PromptCapabilities,
    PromptResponse,
    ResumeSessionResponse,
    SessionCapabilities,
    SessionResumeCapabilities,
    ToolCallUpdate,
)


PROPOSAL_URI = "agentdeck://leader/mission-proposal"
PROPOSAL_MIME = "application/vnd.agentdeck.mission+json"
DECOY_MARKER = "terminal-text-is-not-proposal-authority"
OVERSIZE_MARKER = "oversize-structured-artifact-marker"


def fake_command(
    *, log_path: Path, proposal_path: Path, mode: str = "success"
) -> tuple[str, ...]:
    return (
        sys.executable,
        str(Path(__file__).resolve()),
        "--log",
        str(log_path),
        "--proposal",
        str(proposal_path),
        "--mode",
        mode,
    )


class FakeACPStdioAgent:
    def __init__(self, *, log_path: Path, proposal_path: Path, mode: str) -> None:
        self._log_path = log_path
        self._proposal_path = proposal_path
        self._mode = mode
        self._connection: object | None = None
        self._sessions = 0
        self._cancelled = asyncio.Event()

    def on_connect(self, connection: object) -> None:
        self._connection = connection

    async def initialize(
        self, protocol_version: int, **_kwargs: Any
    ) -> InitializeResponse:
        self._record("initialize", protocol_version=protocol_version)
        embedded_context = self._mode != "capability_missing"
        return InitializeResponse(
            protocol_version=PROTOCOL_VERSION,
            agent_capabilities=AgentCapabilities(
                load_session=True,
                prompt_capabilities=PromptCapabilities(
                    embedded_context=embedded_context
                ),
                session_capabilities=SessionCapabilities(
                    resume=SessionResumeCapabilities()
                ),
            ),
        )

    async def new_session(self, cwd: str, **_kwargs: Any) -> NewSessionResponse:
        self._sessions += 1
        session_id = f"raw-{os.getpid()}-{self._sessions}"
        self._record("session/new", cwd=cwd, session_id=session_id)
        return NewSessionResponse(session_id=session_id)

    async def resume_session(
        self, session_id: str, cwd: str, **_kwargs: Any
    ) -> ResumeSessionResponse:
        self._record("session/resume", cwd=cwd, session_id=session_id)
        return ResumeSessionResponse()

    async def load_session(
        self, session_id: str, cwd: str, **_kwargs: Any
    ) -> LoadSessionResponse:
        self._record("session/resume", cwd=cwd, session_id=session_id)
        return LoadSessionResponse()

    async def prompt(
        self, session_id: str, prompt: list[object], **_kwargs: Any
    ) -> PromptResponse:
        self._record(
            "session/prompt",
            session_id=session_id,
            prompt_types=[getattr(item, "type", type(item).__name__) for item in prompt],
        )
        connection = self._connection
        if connection is None:
            raise RuntimeError("missing connection")
        if self._mode in {"permission", "permission_hang", "cancel_failure"}:
            response = await connection.request_permission(
                session_id,
                ToolCallUpdate(tool_call_id="tool-1", kind="read", status="pending"),
                [
                    PermissionOption(
                        option_id="allow-once", name="Allow once", kind="allow_once"
                    ),
                    PermissionOption(
                        option_id="reject-once", name="Reject", kind="reject_once"
                    ),
                ],
            )
            self._record(
                "permission/result",
                session_id=session_id,
                outcome=response.outcome.model_dump(
                    mode="json", by_alias=True, exclude_none=True
                ),
            )
            if self._mode != "permission":
                await self._cancelled.wait()
                return PromptResponse(stop_reason="cancelled")
            return PromptResponse(stop_reason="end_turn")
        if self._mode == "tool_hang":
            await connection.session_update(
                session_id,
                start_tool_call(
                    "tool-1", "unexpected read", kind="read", status="pending"
                ),
            )
            await self._cancelled.wait()
            return PromptResponse(stop_reason="cancelled")
        proposal = self._proposal_path.read_text(encoding="utf-8")
        await connection.session_update(
            session_id, update_agent_message_text(f"{DECOY_MARKER}: {proposal}")
        )
        if self._mode == "text_only":
            return PromptResponse(stop_reason="end_turn")
        if self._mode == "oversize":
            proposal = OVERSIZE_MARKER * 2048
        elif self._mode == "invalid_resource":
            proposal = "{not-json"
        resource = resource_block(
            embedded_text_resource(PROPOSAL_URI, proposal, mime_type=PROPOSAL_MIME)
        )
        await connection.session_update(
            session_id, update_agent_message(resource)
        )
        return PromptResponse(stop_reason="end_turn")

    async def cancel(self, session_id: str, **_kwargs: Any) -> None:
        self._record("session/cancel", session_id=session_id)
        if self._mode == "cancel_failure":
            raise RuntimeError("secret-cancel-failure")
        self._cancelled.set()

    def _record(self, call: str, **fields: object) -> None:
        payload = {"call": call, "pid": os.getpid(), **fields}
        with self._log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, separators=(",", ":")) + "\n")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--proposal", type=Path, required=True)
    parser.add_argument(
        "--mode",
        choices=(
            "success",
            "capability_missing",
            "text_only",
            "oversize",
            "invalid_resource",
            "permission",
            "permission_hang",
            "tool_hang",
            "cancel_failure",
        ),
        default="success",
    )
    return parser.parse_args()


async def _main() -> None:
    args = _arguments()
    await run_agent(
        FakeACPStdioAgent(
            log_path=args.log, proposal_path=args.proposal, mode=args.mode
        ),
        stdio_buffer_limit_bytes=128 * 1024,
    )


if __name__ == "__main__":
    asyncio.run(_main())
