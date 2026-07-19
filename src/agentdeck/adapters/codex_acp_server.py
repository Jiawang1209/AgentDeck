"""Official ACP Agent surface backed only by Codex app-server JSON-RPC v2."""
from __future__ import annotations
import argparse
import asyncio
from collections.abc import Sequence
import json
from pathlib import Path
import sys
from typing import Any, Final
from acp import PROTOCOL_VERSION, run_agent
from acp.helpers import (
    embedded_text_resource, resource_block, start_tool_call,
    update_agent_message, update_agent_message_text, update_tool_call,
)
from acp.schema import (
    AgentCapabilities, AllowedOutcome, DeniedOutcome, InitializeResponse,
    EmbeddedResourceContentBlock,
    LoadSessionResponse, NewSessionResponse, PermissionOption, PromptCapabilities,
    PromptResponse, ResumeSessionResponse, SessionCapabilities,
    SessionResumeCapabilities, TextContentBlock, TextResourceContents, ToolCallUpdate,
)
from agentdeck.adapters.codex_app_server import (
    AppServerPermissionRequest, AppServerProtocolError, CodexAppServerClient,
)
from agentdeck.ports.leader import leader_proposal_json_schema
_MAX_PROMPT_BYTES: Final = 1024 * 1024
_MAX_COMMAND_JSON_BYTES: Final = 64 * 1024
_REQUEST_URI: Final = "agentdeck://leader/mission-request"
_REQUEST_MIME: Final = "application/vnd.agentdeck.request+json"
_PROPOSAL_URI: Final = "agentdeck://leader/mission-proposal"
_PROPOSAL_MIME: Final = "application/vnd.agentdeck.mission+json"
_LEADER_INSTRUCTION: Final = (
    "Propose one AgentDeck Mission. Return the proposal only as the "
    "declared structured ACP resource; do not execute tools or work."
)
_TURN_NOTIFICATIONS: Final = frozenset({
    "error", "thread/goal/updated", "thread/tokenUsage/updated", "turn/started",
    "hook/started", "turn/completed", "hook/completed", "turn/diff/updated",
    "turn/plan/updated", "item/started", "item/autoApprovalReview/started",
    "item/autoApprovalReview/completed", "item/completed",
    "item/agentMessage/delta", "item/plan/delta",
    "item/commandExecution/outputDelta", "item/commandExecution/terminalInteraction",
    "item/fileChange/outputDelta", "item/fileChange/patchUpdated",
    "item/mcpToolCall/progress", "item/reasoning/summaryTextDelta",
    "item/reasoning/summaryPartAdded", "item/reasoning/textDelta",
    "thread/compacted", "model/rerouted", "model/verification",
})
_THREAD_NOTIFICATIONS: Final = frozenset({
    "thread/started", "thread/status/changed", "thread/archived",
    "thread/unarchived", "thread/closed", "thread/name/updated",
    "thread/goal/cleared", "serverRequest/resolved", "warning",
    "guardianWarning", "thread/realtime/started", "thread/realtime/itemAdded",
    "thread/realtime/transcript/delta", "thread/realtime/transcript/done",
    "thread/realtime/outputAudio/delta", "thread/realtime/sdp",
    "thread/realtime/error", "thread/realtime/closed",
})
_GLOBAL_NOTIFICATIONS: Final = frozenset({
    "skills/changed", "command/exec/outputDelta", "process/outputDelta",
    "process/exited", "mcpServer/oauthLogin/completed",
    "mcpServer/startupStatus/updated", "account/updated",
    "account/rateLimits/updated", "app/list/updated", "remoteControl/status/changed",
    "externalAgentConfig/import/completed", "fs/changed", "deprecationNotice",
    "configWarning", "fuzzyFileSearch/sessionUpdated",
    "fuzzyFileSearch/sessionCompleted", "windows/worldWritableWarning",
    "windowsSandbox/setupCompleted", "account/login/completed",
})
STABLE_NOTIFICATION_METHODS: Final = frozenset().union(
    _TURN_NOTIFICATIONS, _THREAD_NOTIFICATIONS, _GLOBAL_NOTIFICATIONS,
)
_PROGRESS_KINDS: Final = {
    "item/commandExecution/outputDelta": "execute",
    "item/commandExecution/terminalInteraction": "execute",
    "item/fileChange/outputDelta": "edit",
    "item/fileChange/patchUpdated": "edit",
    "item/mcpToolCall/progress": "other",
}
_ITEM_KINDS: Final = {
    "commandExecution": ("execute", "Codex command execution"),
    "fileChange": ("edit", "Codex file change"),
    "mcpToolCall": ("other", "Codex MCP tool call"),
    "webSearch": ("search", "Codex web search"),
    "dynamicTool": ("execute", "Codex dynamic tool call"),
}


class CodexACPServer:
    """Translate official ACP lifecycle to one stable Codex app-server process."""

    def __init__(
        self, *, app_server_command: Sequence[str], model: str = "native-default",
    ) -> None:
        command = tuple(app_server_command)
        if not command or any(type(part) is not str or not part for part in command):
            raise ValueError("app_server_command is invalid")
        if type(model) is not str or not model.strip():
            raise ValueError("model is invalid")
        self._app_command = command
        self._model = model
        self._app = CodexAppServerClient(command)
        self._connection: object | None = None
        self._initialized = False
        self._sessions: dict[str, tuple[str, str]] = {}
        self._active_session: str | None = None
        self._active_turn: str | None = None
        self._leader_output: list[str] | None = None
        self._server_version: str | None = None

    @property
    def command(self) -> tuple[str, ...]:
        encoded = json.dumps(self._app_command, separators=(",", ":"))
        return (
            sys.executable, "-m", "agentdeck.adapters.codex_acp_server",
            "--app-server-command-json", encoded, "--model", self._model,
        )

    def on_connect(self, connection: object) -> None:
        if self._connection is not None:
            raise ValueError("ACP client is already connected")
        self._connection = connection

    async def initialize(
        self, protocol_version: int, **_kwargs: Any,
    ) -> InitializeResponse:
        if protocol_version != PROTOCOL_VERSION or self._initialized:
            raise AppServerProtocolError("codex_acp_protocol_mismatch")
        await self._app.initialize()
        self._server_version = self._app.server_version
        self._initialized = True
        return InitializeResponse(
            protocol_version=PROTOCOL_VERSION,
            agent_capabilities=AgentCapabilities(
                load_session=True,
                prompt_capabilities=PromptCapabilities(embedded_context=True),
                session_capabilities=SessionCapabilities(
                    resume=SessionResumeCapabilities()
                ),
            ),
        )

    async def new_session(self, cwd: str, **_kwargs: Any) -> NewSessionResponse:
        self._require_initialized()
        thread = await self._app.start_thread(cwd=cwd, model=self._model)
        self._sessions[thread.thread_id] = (cwd, thread.model)
        return NewSessionResponse(
            session_id=thread.thread_id,
            field_meta={"agentdeck": {
                "resolved_model": thread.model,
                "server_version": self._server_version,
            }},
        )

    async def load_session(
        self, cwd: str, session_id: str, **_kwargs: Any,
    ) -> LoadSessionResponse:
        await self._resume(cwd, session_id)
        return LoadSessionResponse()

    async def resume_session(
        self, session_id: str, cwd: str, **_kwargs: Any,
    ) -> ResumeSessionResponse:
        await self._resume(cwd, session_id)
        return ResumeSessionResponse()

    async def prompt(
        self, session_id: str, prompt: list[object], **_kwargs: Any,
    ) -> PromptResponse:
        self._require_session(session_id)
        if self._active_session is not None:
            raise ValueError("Codex ACP turn already active")
        text, output_schema = _prompt_contract(
            prompt, model=self._sessions[session_id][1], version=self._server_version,
        )
        self._leader_output = [] if output_schema is not None else None
        self._active_session = session_id
        try:
            result = await self._app.start_turn(
                thread_id=session_id, text=text, output_schema=output_schema,
                on_notification=self._map_notification,
                on_permission=self._map_permission,
            )
            self._active_turn = result.turn_id
            if output_schema is not None and result.status == "completed":
                raw = "".join(self._leader_output or ())
                _proposal_object(raw)
                await self._require_connection().session_update(
                    session_id,
                    update_agent_message(resource_block(embedded_text_resource(
                        _PROPOSAL_URI, raw, mime_type=_PROPOSAL_MIME,
                    ))),
                )
            stop_reason = {
                "completed": "end_turn", "interrupted": "cancelled",
                "failed": "refusal",
            }[result.status]
            return PromptResponse(stop_reason=stop_reason)
        finally:
            self._active_session = None
            self._active_turn = None
            self._leader_output = None

    async def cancel(self, session_id: str, **_kwargs: Any) -> None:
        self._require_session(session_id)
        if session_id != self._active_session:
            raise ValueError("Codex ACP session has no active turn")
        await self._app.interrupt_active_turn(thread_id=session_id)

    async def close(self) -> None:
        await self._app.close()

    async def _resume(self, cwd: str, session_id: str) -> None:
        self._require_initialized()
        if session_id in self._sessions:
            raise ValueError("Codex ACP session already loaded")
        thread = await self._app.resume_thread(session_id, cwd=cwd, model=self._model)
        self._sessions[thread.thread_id] = (cwd, thread.model)

    async def _map_notification(
        self, method: str, params: dict[str, object],
    ) -> None:
        connection = self._require_connection()
        session_id = self._active_session
        _validate_notification_lineage(
            method, params, session_id=session_id,
            turn_id=self._app.active_turn_id,
        )
        if method == "model/rerouted":
            actual_model = self._sessions[session_id][1]
            if any(params.get(key) != actual_model for key in ("fromModel", "toModel")):
                raise AppServerProtocolError("codex_app_server_model_drift")
        update = None
        if method == "item/agentMessage/delta":
            delta = _bounded_text(params.get("delta"))
            if self._leader_output is not None:
                self._leader_output.append(delta)
                if len("".join(self._leader_output).encode("utf-8")) > _MAX_PROMPT_BYTES:
                    raise AppServerProtocolError("codex_acp_output_oversize")
                return
            update = update_agent_message_text(delta)
        elif method in {"item/started", "item/completed"}:
            item = params.get("item")
            if type(item) is not dict:
                raise AppServerProtocolError("codex_acp_protocol_mismatch")
            item_id = _bounded_text(item.get("id"), limit=256)
            item_type = item.get("type")
            if item_type not in _ITEM_KINDS:
                return
            kind, title = _ITEM_KINDS[item_type]
            update = (
                start_tool_call(item_id, title, kind=kind, status="in_progress")
                if method == "item/started"
                else update_tool_call(item_id, kind=kind, status=_item_status(item))
            )
        elif method in _PROGRESS_KINDS:
            update = update_tool_call(
                _bounded_text(params.get("itemId"), limit=256),
                kind=_PROGRESS_KINDS[method], status="in_progress",
            )
        elif method == "error":
            raise AppServerProtocolError("codex_app_server_turn_failed")
        elif method in STABLE_NOTIFICATION_METHODS:
            return
        else:
            raise AppServerProtocolError("codex_app_server_notification_drift")
        await connection.session_update(session_id, update)

    async def _map_permission(self, request: AppServerPermissionRequest) -> object:
        connection = self._require_connection()
        session_id = self._active_session
        if (
            session_id is None or request.thread_id != session_id
            or request.turn_id != self._app.active_turn_id
        ):
            raise AppServerProtocolError("codex_acp_lineage_mismatch")
        tool_call = ToolCallUpdate(
            tool_call_id=request.item_id, kind=request.kind, status="pending",
            title="Codex permission request",
            field_meta={
                "native_method": request.method,
                "native_request_id": request.native_request_id,
            },
        )
        options = [
            PermissionOption(
                option_id="allow-once", name="Allow once", kind="allow_once"
            ),
            PermissionOption(
                option_id="reject-once", name="Reject", kind="reject_once"
            ),
        ]
        response = await connection.request_permission(session_id, tool_call, options)
        outcome = response.outcome
        if type(outcome) is AllowedOutcome:
            selected = next(
                (option for option in options if option.option_id == outcome.option_id), None
            )
            return selected is not None and selected.kind.startswith("allow_")
        if type(outcome) is DeniedOutcome:
            return False
        raise AppServerProtocolError("codex_acp_permission_response_invalid")

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise ValueError("Codex ACP bridge is not initialized")

    def _require_session(self, session_id: str) -> None:
        self._require_initialized()
        if type(session_id) is not str or session_id not in self._sessions:
            raise ValueError("Codex ACP session is invalid")

    def _require_connection(self) -> object:
        connection = self._connection
        if connection is None or not all(callable(getattr(connection, name, None)) for name in (
            "session_update", "request_permission",
        )):
            raise AppServerProtocolError("codex_acp_client_unavailable")
        return connection


def _prompt_contract(
    prompt: list[object], *, model: str, version: str | None,
) -> tuple[str, dict[str, object] | None]:
    if type(prompt) is not list or not prompt:
        raise ValueError("ACP prompt must contain text")
    if all(type(block) is TextContentBlock for block in prompt):
        return _bounded_text(
            "\n".join(block.text for block in prompt), limit=_MAX_PROMPT_BYTES,
        ), None
    if (
        len(prompt) != 2 or type(prompt[0]) is not TextContentBlock
        or prompt[0].text != _LEADER_INSTRUCTION
        or type(prompt[1]) is not EmbeddedResourceContentBlock
    ):
        raise ValueError("Codex bridge accepts one frozen Leader resource")
    block = prompt[1]
    resource = block.resource
    if (
        type(resource) is not TextResourceContents
        or resource.uri != _REQUEST_URI or resource.mime_type != _REQUEST_MIME
        or block.annotations is not None or block.field_meta is not None
        or resource.field_meta is not None
    ):
        raise ValueError("Codex bridge accepts one frozen Leader resource")
    request = _leader_request_object(resource.text)
    if request["resolved_leader"]["model_id"] != model:
        raise ValueError("Codex bridge Leader model identity does not match")
    if request["resolved_leader"]["version"] != version:
        raise ValueError("Codex bridge Leader version identity does not match")
    text = _bounded_text(
        f"{_LEADER_INSTRUCTION}\n\nAgentDeck Mission request JSON:\n{resource.text}",
        limit=_MAX_PROMPT_BYTES,
    )
    return text, request["proposal_schema"]


def _leader_request_object(raw: str) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, RecursionError):
        raise ValueError("Codex bridge received a malformed Leader resource") from None
    if type(value) is not dict or set(value) != {
        "user_goal", "project_context", "available_agents", "permission_ceiling",
        "resolved_leader", "schema_repair", "proposal_schema",
    }:
        raise ValueError("Codex bridge received a malformed Leader resource")
    project = value["project_context"]
    agents = value["available_agents"]
    leader = value["resolved_leader"]
    valid = (
        type(value["user_goal"]) is str and bool(value["user_goal"].strip())
        and type(project) is dict and set(project) == {"project_root", "summary"}
        and all(type(project[key]) is str for key in project)
        and type(agents) is list and all(
            type(agent) is dict
            and set(agent) == {"instance_id", "role", "backend_id", "acp_route_id"}
            and all(type(agent[key]) is str and agent[key].strip() for key in agent)
            for agent in agents
        )
        and value["permission_ceiling"] in {
            "ask_for_approval", "approve_for_me", "full_access",
        }
        and type(leader) is dict
        and set(leader) == {"backend_id", "adapter_id", "model_id", "version"}
        and leader.get("backend_id") == "codex-cli" and leader.get("adapter_id") == "acp"
        and all(type(leader[key]) is str and leader[key].strip() for key in leader)
        and type(value["schema_repair"]) is bool
        and value["proposal_schema"] == leader_proposal_json_schema()
    )
    if not valid:
        raise ValueError("Codex bridge received a malformed Leader resource")
    return value


def _proposal_object(raw: str) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, RecursionError):
        raise AppServerProtocolError("codex_acp_protocol_mismatch") from None
    if type(value) is not dict:
        raise AppServerProtocolError("codex_acp_protocol_mismatch")
    return value


def _validate_notification_lineage(
    method: str, params: dict[str, object], *,
    session_id: str | None, turn_id: str | None,
) -> None:
    if method not in STABLE_NOTIFICATION_METHODS:
        raise AppServerProtocolError("codex_app_server_notification_drift")
    if method in _GLOBAL_NOTIFICATIONS:
        return
    if method == "warning" and params.get("threadId") is None:
        return
    actual_thread = params.get("threadId")
    if method == "thread/started":
        thread = params.get("thread")
        if type(thread) is not dict:
            raise AppServerProtocolError("codex_acp_protocol_mismatch")
        actual_thread = thread.get("id")
    if session_id is None or actual_thread != session_id:
        raise AppServerProtocolError("codex_acp_lineage_mismatch")
    if method not in _TURN_NOTIFICATIONS:
        return
    actual_turn = params.get("turnId")
    if method in {"turn/started", "turn/completed"}:
        turn = params.get("turn")
        if type(turn) is not dict:
            raise AppServerProtocolError("codex_acp_protocol_mismatch")
        actual_turn = turn.get("id")
    if actual_turn is None and method in {
        "error", "thread/goal/updated", "hook/started", "hook/completed",
    }:
        return
    if turn_id is None or actual_turn != turn_id:
        raise AppServerProtocolError("codex_acp_lineage_mismatch")


def _bounded_text(value: object, *, limit: int = 64 * 1024) -> str:
    if type(value) is not str or not value.strip():
        raise AppServerProtocolError("codex_acp_protocol_mismatch")
    try:
        if len(value.encode("utf-8", "strict")) > limit:
            raise ValueError
    except (UnicodeEncodeError, ValueError):
        raise AppServerProtocolError("codex_acp_output_oversize") from None
    return value


def _item_status(item: dict[str, object]) -> str:
    status = item.get("status")
    if status in {"completed", "declined"}:
        return "completed"
    if status in {"failed", "cancelled"}:
        return "failed"
    raise AppServerProtocolError("codex_acp_protocol_mismatch")


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="agentdeck-codex-acp")
    parser.add_argument("--app-server-command-json", default='["codex","app-server"]')
    parser.add_argument("--model", default="native-default")
    return parser.parse_args(argv)


def _decode_command(value: str) -> tuple[str, ...]:
    try:
        if len(value.encode("utf-8", "strict")) > _MAX_COMMAND_JSON_BYTES:
            raise ValueError
        decoded = json.loads(value)
    except (UnicodeEncodeError, json.JSONDecodeError, ValueError):
        raise SystemExit("invalid app-server command") from None
    if type(decoded) is not list or not decoded or any(
        type(part) is not str or not part for part in decoded
    ):
        raise SystemExit("invalid app-server command")
    return tuple(decoded)


async def _main(argv: Sequence[str] | None = None) -> None:
    args = _arguments(argv)
    server = CodexACPServer(
        app_server_command=_decode_command(args.app_server_command_json),
        model=args.model,
    )
    try:
        await run_agent(server, stdio_buffer_limit_bytes=1024 * 1024)
    finally:
        await server.close()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()


__all__ = ["CodexACPServer", "STABLE_NOTIFICATION_METHODS", "main"]
