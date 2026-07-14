"""Deterministic official-SDK ACP Agent used only by process-level tests."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import re

from acp import run_agent, schema


class FakeAgent:
    def __init__(self, scenario: str, args: list[str]) -> None:
        self.scenario = scenario
        self.args = args
        self.client = None
        self.cancelled = asyncio.Event()

    def log_request(self, method: str, **facts: object) -> None:
        if self.scenario == "m2c_worker":
            if not self.args:
                return
            allowed = {
                "dispatch_token",
                "prompt_sha256",
                "recorded_handoff_ids",
            }
            record = {
                "method": method,
                "label": self.args[1] if len(self.args) > 1 else "worker",
                **{key: value for key, value in facts.items() if key in allowed},
            }
            with Path(self.args[0]).open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, sort_keys=True) + "\n")
            return
        if self.scenario not in {
            "full_reconnect_log",
            "mission_worker",
            "mission_worker_permission",
        } or not self.args:
            return
        with Path(self.args[0]).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"method": method, **facts}, sort_keys=True) + "\n")

    def on_connect(self, conn: object) -> None:
        self.client = conn

    async def initialize(self, protocol_version: int, client_capabilities=None, client_info=None, **kwargs):
        self.log_request("initialize", protocol_version=protocol_version)
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
        self.log_request("new", cwd=cwd, mcp_servers=mcp_servers or [])
        return schema.NewSessionResponse(sessionId="fake-session-1")

    async def load_session(self, cwd: str, session_id: str, mcp_servers=None, **kwargs):
        self.log_request("load", cwd=cwd, session_id=session_id, mcp_servers=mcp_servers or [])
        if self.scenario == "load_eof_before_response":
            os._exit(0)
        if self.scenario == "load_malformed_update":
            writer = self.client._conn._writer
            writer.write((json.dumps({
                "jsonrpc": "2.0", "method": "session/update",
                "params": {"sessionId": session_id, "update": {"sessionUpdate": "agent_message_chunk"}},
            }) + "\n").encode())
            await writer.drain()
        if self.scenario in {"load_replay", "full_reconnect", "full_reconnect_log"}:
            for text in ("one", "two"):
                await self.client.session_update(session_id, schema.AgentMessageChunk(
                    sessionUpdate="agent_message_chunk",
                    content=schema.TextContentBlock(type="text", text=text),
                ))
        return schema.LoadSessionResponse()

    async def resume_session(self, session_id: str, cwd: str, mcp_servers=None, **kwargs):
        self.log_request("resume", cwd=cwd, session_id=session_id, mcp_servers=mcp_servers or [])
        if self.scenario == "resume_illegal_replay":
            await self.client.session_update(session_id, schema.AgentMessageChunk(
                sessionUpdate="agent_message_chunk",
                content=schema.TextContentBlock(type="text", text="illegal"),
            ))
        return schema.ResumeSessionResponse()

    async def prompt(self, session_id: str, prompt: list[object], **kwargs):
        text = prompt[0].text
        dispatch_tokens = re.findall(r"dsp_[0-9a-f]{32}", text)
        if self.scenario == "m2c_worker":
            state_path = Path.cwd() / ".agentdeck" / "state" / "state.json"
            durable = json.loads(state_path.read_text(encoding="utf-8"))
            self.log_request(
                "prompt",
                dispatch_token=dispatch_tokens[-1] if dispatch_tokens else None,
                prompt_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                recorded_handoff_ids=[
                    item.get("attempt_id")
                    for item in durable.get("mission_handoffs", [])
                    if item.get("state") == "recorded"
                ],
            )
        else:
            self.log_request(
                "prompt",
                session_id=session_id,
                block_count=len(prompt),
                dispatch_token=dispatch_tokens[-1] if dispatch_tokens else None,
            )
        if self.scenario == "timeout":
            await self.cancelled.wait()
            return schema.PromptResponse(stopReason="cancelled")
        if self.scenario == "eof_before_response":
            os._exit(0)
        if self.scenario == "cancel_or_ignore_terminate":
            await asyncio.sleep(60)
        if self.scenario in {"permission", "mission_worker_permission"}:
            await self.client.request_permission(
                session_id,
                schema.ToolCallUpdate(toolCallId="call-1", title="Edit notes", kind="edit"),
                [
                    schema.PermissionOption(optionId="allow", name="Allow once", kind="allow_once"),
                    schema.PermissionOption(optionId="always", name="Always", kind="allow_always"),
                    schema.PermissionOption(optionId="reject", name="Reject once", kind="reject_once"),
                ],
            )
        if self.scenario == "m2c_worker":
            if not dispatch_tokens:
                raise RuntimeError("missing Mission dispatch token")
            if "revision: replace draft-v1 with accepted-v2" in text:
                phase = "revision"
                content = b"accepted-v2\n"
            elif "implementation: create artifact.txt containing draft-v1" in text:
                phase = "implementation"
                content = b"draft-v1\n"
            else:
                raise RuntimeError("unsupported M2c ACP phase")
            permission = await self.client.request_permission(
                session_id,
                schema.ToolCallUpdate(
                    toolCallId=f"m2c-{phase}",
                    title=f"Edit artifact.txt for {phase}",
                    kind="edit",
                ),
                [
                    schema.PermissionOption(
                        optionId="allow", name="Allow once", kind="allow_once"
                    ),
                    schema.PermissionOption(
                        optionId="reject", name="Reject once", kind="reject_once"
                    ),
                ],
            )
            if (
                permission.outcome.outcome != "selected"
                or permission.outcome.option_id != "allow"
            ):
                raise RuntimeError("M2c edit permission denied")
            artifact = Path.cwd() / "artifact.txt"
            artifact.write_bytes(content)
            token = dispatch_tokens[-1]
            digest = hashlib.sha256(content).hexdigest()
            text = "\n".join(
                (
                    f"handoff_token: {token}",
                    "status: completed",
                    f"summary: {phase} artifact update complete",
                    f"verification: artifact_sha256={digest}",
                    "risks: none",
                    "next_steps: continue",
                )
            )
        if self.scenario in {"mission_worker", "mission_worker_permission"}:
            label = self.args[1] if len(self.args) > 1 else "worker"
            state_path = Path.cwd() / ".agentdeck" / "state" / "state.json"
            durable = json.loads(state_path.read_text(encoding="utf-8"))
            self.log_request(
                "mission_prompt",
                worker=label,
                prompt=text,
                recorded_handoffs=[
                    item.get("attempt_id")
                    for item in durable.get("mission_handoffs", [])
                    if item.get("state") == "recorded"
                ],
            )
            token_matches = re.findall(r"dsp_[0-9a-f]{32}", text)
            if not token_matches:
                raise RuntimeError("missing Mission dispatch token")
            token = token_matches[-1]
            text = "\n".join(
                (
                    f"handoff_token: {token}",
                    "status: completed",
                    f"summary: {label} compact summary",
                    f"verification: {label} deterministic verification",
                    "risks: none",
                    "next_steps: continue",
                    "private_reasoning: PRIVATE_REASONING_MARKER",
                    "full_transcript: FULL_TRANSCRIPT_MARKER",
                    "secret: SECRET_MARKER",
                )
            )
        if self.scenario == "mission_worker_permission":
            self.log_request(
                "session_update",
                payload={
                    "role": "agent",
                    "content": {"type": "text", "text": text},
                },
            )
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
