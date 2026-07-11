"""Deterministic official-SDK ACP Agent used only by process-level tests."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import signal
import sys

from acp import run_agent, schema


class FakeAgent:
    def __init__(self, scenario: str, args: list[str]) -> None:
        self.scenario = scenario
        self.args = args
        self.client = None
        self.cancelled = asyncio.Event()

    def on_connect(self, conn: object) -> None:
        self.client = conn

    async def initialize(self, protocol_version: int, client_capabilities=None, client_info=None, **kwargs):
        if self.scenario == "eof_initialize":
            os._exit(0)
        if self.scenario == "record_argv":
            Path(self.args[0]).write_text(json.dumps({"argv": self.args[1:], "cwd": os.getcwd()}))
        if self.scenario == "record_env":
            Path(self.args[0]).write_text(json.dumps({
                "sentinel": os.environ.get("ACP_TEST_SENTINEL"),
                "unreviewed": os.environ.get("ACP_UNREVIEWED_SECRET"),
                "anthropic": os.environ.get("ANTHROPIC_API_KEY"),
            }))
            sys.stderr.write(f"ACP_TEST_SENTINEL={os.environ.get('ACP_TEST_SENTINEL')}\n")
            sys.stderr.flush()
        if self.scenario == "descendant_stderr":
            import subprocess
            subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(1)"],
                stdout=subprocess.DEVNULL,
            )
        if self.scenario == "stderr_noise":
            secret = os.environ.get("ACP_TEST_SECRET", "missing")
            sys.stderr.write((f"ACP_TEST_SECRET={secret}\n" + "x" * 100000))
            sys.stderr.flush()
        return schema.InitializeResponse(
            protocolVersion=2 if self.scenario == "version_mismatch" else 1,
            agentCapabilities=schema.AgentCapabilities(
                loadSession=self.scenario not in {"no_load_capability"},
                sessionCapabilities=schema.SessionCapabilities(
                    resume=schema.SessionResumeCapabilities()
                    if self.scenario not in {"no_resume_capability"} else None
                ),
            ),
            agentInfo=schema.Implementation(name="fake", version="1.0.0"),
        )

    async def new_session(self, cwd: str, mcp_servers=None, **kwargs):
        return schema.NewSessionResponse(sessionId="fake-session-1")

    async def load_session(self, cwd: str, session_id: str, mcp_servers=None, **kwargs):
        if self.scenario == "load_eof_before_response":
            os._exit(0)
        if self.scenario == "load_replay":
            for text in ("one", "two"):
                await self.client.session_update(session_id, schema.AgentMessageChunk(
                    sessionUpdate="agent_message_chunk",
                    content=schema.TextContentBlock(type="text", text=text),
                ))
        return schema.LoadSessionResponse()

    async def resume_session(self, session_id: str, cwd: str, mcp_servers=None, **kwargs):
        if self.scenario == "resume_illegal_replay":
            await self.client.session_update(session_id, schema.AgentMessageChunk(
                sessionUpdate="agent_message_chunk",
                content=schema.TextContentBlock(type="text", text="illegal"),
            ))
        return schema.ResumeSessionResponse()

    async def prompt(self, session_id: str, prompt: list[object], **kwargs):
        if self.scenario == "timeout":
            await self.cancelled.wait()
            return schema.PromptResponse(stopReason="cancelled")
        if self.scenario == "eof_before_response":
            os._exit(0)
        if self.scenario == "cancel_or_ignore_terminate":
            await asyncio.sleep(60)
        if self.scenario == "permission":
            await self.client.request_permission(
                session_id,
                schema.ToolCallUpdate(toolCallId="call-1", title="Edit notes", kind="edit"),
                [
                    schema.PermissionOption(optionId="allow", name="Allow once", kind="allow_once"),
                    schema.PermissionOption(optionId="always", name="Always", kind="allow_always"),
                    schema.PermissionOption(optionId="reject", name="Reject once", kind="reject_once"),
                ],
            )
        text = prompt[0].text
        await self.client.session_update(
            session_id,
            schema.AgentMessageChunk(
                sessionUpdate="agent_message_chunk",
                content=schema.TextContentBlock(type="text", text=text),
            ),
        )
        return schema.PromptResponse(stopReason="end_turn")

    async def cancel(self, session_id: str, **kwargs):
        if self.args:
            Path(self.args[0]).write_text("cancelled")
        self.cancelled.set()

    async def ext_method(self, method: str, params: dict):
        return {}

    async def ext_notification(self, method: str, params: dict):
        return None


async def main() -> None:
    scenario, *args = sys.argv[1:]
    if scenario == "malformed_frame":
        sys.stdout.write("not-json\n")
        sys.stdout.flush()
        await asyncio.sleep(60)
        return
    if scenario == "oversize_frame":
        sys.stdout.write("x" * (64 * 1024 + 1) + "\n")
        sys.stdout.flush()
        await asyncio.sleep(60)
        return
    if scenario == "cancel_or_ignore_terminate":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    await run_agent(
        FakeAgent(scenario, args), use_unstable_protocol=True,
        stdio_buffer_limit_bytes=64 * 1024,
    )


asyncio.run(main())
