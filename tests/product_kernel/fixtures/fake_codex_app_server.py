"""Deterministic JSON-RPC v2 fake for the frozen Codex app-server surface."""

from __future__ import annotations

import argparse
import asyncio
from hashlib import sha256
import json
from pathlib import Path
import sys


FAKE_VERSION = "codex-cli 0.131.0"
SCHEMA_BYTES = b'{"protocol":"codex-app-server-v2","stable":true}\n'
SCHEMA_DIGEST = sha256(
    b'{"protocol":"codex-app-server-v2","stable":true}'
).hexdigest()


def fake_command(
    log_path: Path, *, mode: str = "success", proposal_path: Path | None = None,
) -> tuple[str, ...]:
    command = (
        sys.executable,
        str(Path(__file__).resolve()),
        "--log",
        str(log_path),
        "--mode",
        mode,
    )
    if proposal_path is not None:
        command += ("--proposal", str(proposal_path))
    return (*command, "app-server")


class FakeCodexAppServer:
    def __init__(self, log_path: Path, mode: str, proposal_path: Path | None) -> None:
        self.log_path = log_path
        self.mode = mode
        self.proposal_path = proposal_path
        self.thread_id = "thr_42"
        self.turn_id = "turn_42"
        self.permission_result: asyncio.Future[dict[str, object]] | None = None
        self.interrupted = asyncio.Event()
        self.turn_task: asyncio.Task[None] | None = None

    async def run(self) -> None:
        while line := await asyncio.to_thread(sys.stdin.buffer.readline):
            if self.mode == "oversize_output":
                sys.stdout.buffer.write(b"{" + b"x" * (1024 * 1024 + 1) + b"}\n")
                sys.stdout.buffer.flush()
                return
            if self.mode == "malformed_output":
                self._write_raw(b"token=RAW-FAKE-SECRET\n")
                return
            message = json.loads(line)
            self._record("received", message=message)
            if "jsonrpc" in message:
                return
            if "method" in message:
                await self._handle_method(message)
            elif message.get("id") == "perm_42" and self.permission_result is not None:
                if not self.permission_result.done():
                    self.permission_result.set_result(message)
        if self.turn_task is not None:
            await self.turn_task

    async def _handle_method(self, message: dict[str, object]) -> None:
        method = message["method"]
        request_id = message.get("id")
        if method == "initialized":
            return
        if method == "initialize":
            self._respond(request_id, {
                "userAgent": FAKE_VERSION,
                "codexHome": "/tmp/fake-codex-home",
                "platformFamily": "unix",
                "platformOs": "macos",
            })
        elif method in {"thread/start", "thread/resume"}:
            params = message.get("params", {})
            if method == "thread/resume":
                self.thread_id = params["threadId"]
            self._respond(request_id, {
                "thread": {"id": self.thread_id, "preview": "", "modelProvider": "openai", "createdAt": 0, "updatedAt": 0, "status": {"type": "idle"}, "path": "/tmp/thread", "cwd": "/tmp/project", "cliVersion": "0.131.0", "source": "appServer", "name": None, "turns": []},
                "model": "native-default", "modelProvider": "openai",
                "cwd": "/tmp/project", "approvalPolicy": "on-request",
                "sandbox": {"type": "workspaceWrite", "writableRoots": [], "networkAccess": False, "excludeTmpdirEnvVar": False, "excludeSlashTmp": False},
                "approvalsReviewer": "user",
            })
        elif method == "turn/start":
            params = message.get("params", {})
            if self.mode == "leader" and "outputSchema" not in params:
                self._error(request_id, -32602, "missing output schema")
                return
            self._respond(request_id, {"turn": self._turn("inProgress")})
            self.turn_task = asyncio.create_task(self._emit_turn())
        elif method == "turn/interrupt":
            self.interrupted.set()
            self._respond(request_id, {})
        else:
            self._error(request_id, -32601, "unsupported method")

    async def _emit_turn(self) -> None:
        self._notify("turn/started", {
            "threadId": self.thread_id, "turn": self._turn("inProgress")
        })
        if self.mode == "leader":
            assert self.proposal_path is not None
            self._notify("item/agentMessage/delta", {
                "threadId": self.thread_id, "turnId": self.turn_id,
                "itemId": "msg_42", "delta": self.proposal_path.read_text(),
            })
            self._notify("turn/completed", {
                "threadId": self.thread_id, "turn": self._turn("completed")
            })
            return
        if self.mode == "hang":
            await self.interrupted.wait()
            self._notify("turn/completed", {
                "threadId": self.thread_id, "turn": self._turn("interrupted")
            })
            return
        item = {
            "id": "item_42", "type": "commandExecution", "command": "secret command",
            "cwd": "/tmp/project", "status": "inProgress", "commandActions": [],
        }
        self._notify("item/started", {
            "threadId": self.thread_id, "turnId": self.turn_id,
            "item": item, "startedAtMs": 1,
        })
        if self.mode != "no_permission":
            self.permission_result = asyncio.get_running_loop().create_future()
            self._request("perm_42", "item/commandExecution/requestApproval", {
                "threadId": self.thread_id, "turnId": self.turn_id,
                "itemId": "item_42", "command": "token=RAW-COMMAND-SECRET",
                "cwd": "/tmp/project", "startedAtMs": 1,
            })
            response = await self.permission_result
            self._record("permission_response", message=response)
        self._notify("item/agentMessage/delta", {
            "threadId": self.thread_id, "turnId": self.turn_id,
            "itemId": "msg_42", "delta": "bounded result",
        })
        if self.mode == "stale_turn":
            self._notify("item/agentMessage/delta", {
                "threadId": self.thread_id, "turnId": "turn_previous",
                "itemId": "msg_stale", "delta": "RAW-STALE-TURN-SECRET",
            })
        if self.mode == "streamed_updates":
            for method, item_id in (
                ("item/commandExecution/outputDelta", "item_42"),
                ("item/fileChange/patchUpdated", "edit_42"),
                ("item/mcpToolCall/progress", "mcp_42"),
                ("item/reasoning/textDelta", "reason_42"),
                ("item/plan/delta", "plan_42"),
            ):
                self._notify(method, {
                    "threadId": self.thread_id, "turnId": self.turn_id,
                    "itemId": item_id, "delta": "token=RAW-STREAM-SECRET",
                })
        item["status"] = "completed"
        self._notify("item/completed", {
            "threadId": self.thread_id, "turnId": self.turn_id,
            "item": item, "completedAtMs": 2,
        })
        self._notify("turn/completed", {
            "threadId": self.thread_id, "turn": self._turn("completed")
        })

    def _turn(self, status: str) -> dict[str, object]:
        return {"id": self.turn_id, "items": [], "status": status}

    def _respond(self, request_id: object, result: object) -> None:
        self._write({"id": request_id, "result": result})

    def _error(self, request_id: object, code: int, message: str) -> None:
        self._write({"id": request_id, "error": {"code": code, "message": message}})

    def _request(self, request_id: object, method: str, params: object) -> None:
        self._write({"id": request_id, "method": method, "params": params})

    def _notify(self, method: str, params: object) -> None:
        self._write({"method": method, "params": params})

    def _write(self, payload: object) -> None:
        self._write_raw(json.dumps(payload, separators=(",", ":")).encode() + b"\n")

    @staticmethod
    def _write_raw(payload: bytes) -> None:
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()

    def _record(self, kind: str, **values: object) -> None:
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"kind": kind, **values}, separators=(",", ":")) + "\n")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--mode", default="success")
    parser.add_argument("--proposal", type=Path)
    parser.add_argument("--version", action="store_true")
    args, rest = parser.parse_known_args()
    args.rest = rest
    return args


def _generate_schema(out: Path, mode: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    if mode == "schema_drift":
        payload = b'{"drift":true}\n'
    elif mode == "schema_reordered":
        payload = b'{"stable":true,"protocol":"codex-app-server-v2"}\n'
    else:
        payload = SCHEMA_BYTES
    (out / "codex_app_server_protocol.v2.schemas.json").write_bytes(payload)


def main() -> None:
    args = _args()
    rest = args.rest
    if args.version:
        print("codex-cli 9.9.9" if args.mode == "version_drift" else FAKE_VERSION)
        return
    if len(rest) == 4 and rest[:2] == ["app-server", "generate-json-schema"] and rest[2] == "--out":
        _generate_schema(Path(rest[3]), args.mode)
        return
    if rest == ["app-server"]:
        asyncio.run(FakeCodexAppServer(args.log, args.mode, args.proposal).run())
        return
    raise SystemExit(2)


if __name__ == "__main__":
    main()
