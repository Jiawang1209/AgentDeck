from __future__ import annotations

import asyncio
import functools
import hashlib
import inspect
import json
import os
from pathlib import Path
import pty
import re
import select
import signal
import subprocess
import sys
import time
from typing import Any

import pytest
from acp import schema
from acp.exceptions import RequestError

import agentdeck.runtime.acp_client as acp_client_module
from agentdeck.runtime.acp_client import AgentDeckAcpClient, PermissionDecision
from agentdeck.runtime.acp_mapping import MAX_ACP_TURN_PAYLOAD_BYTES, MAX_ACP_UPDATES_PER_TURN
from agentdeck.state import StateStore


FAKE_AGENT = Path(__file__).parent / "fixtures" / "fake_acp_agent.py"
LIVE_PTY_CAPTURE_BYTES = 64 * 1024


def test_live_ledger_rejects_synthetic_load_without_replayed_history() -> None:
    ledger = _example_live_ledger()
    ledger["transport_updates"] = [
        item for item in ledger["transport_updates"] if item["turn_id"] != "turn-load"
    ] + [{
        "turn_id": "turn-load", "sequence": 0, "kind": "completion",
        "payload": {"stop_reason": "loaded"},
    }]
    with pytest.raises(AssertionError, match="replayed history"):
        _assert_live_ledger(ledger, "turn-first", "turn-load", "turn-resume")


def test_live_ledger_requires_replay_hash_from_prior_conversation() -> None:
    ledger = _example_live_ledger()
    replay = next(
        item for item in ledger["transport_updates"]
        if item["turn_id"] == "turn-load" and item["kind"] != "completion"
    )
    replay["payload"] = {"content": {"type": "text", "text": "different"}}
    with pytest.raises(AssertionError, match="prior conversation"):
        _assert_live_ledger(ledger, "turn-first", "turn-load", "turn-resume")


def test_live_ledger_accepts_exact_prompt_load_resume_lineage() -> None:
    _assert_live_ledger(_example_live_ledger(), "turn-first", "turn-load", "turn-resume")


def test_live_pty_capture_and_shutdown_are_hard_bounded(tmp_path: Path) -> None:
    marker = "RAW_TRANSCRIPT_MUST_NOT_ESCAPE"
    script = tmp_path / "noisy_hung.py"
    pid_file = tmp_path / "child.pid"
    script.write_text(
        "import os, time\n"
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
        f"os.write(1, ({marker!r} * 8192).encode())\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    started = time.monotonic()
    with pytest.raises(AssertionError) as raised:
        _run_live_pty(
            [sys.executable, str(script)], cwd=tmp_path, reject_once=False,
            total_timeout=0.2, terminate_timeout=0.2, kill_timeout=0.2,
            capture_limit=1024,
        )
    elapsed = time.monotonic() - started
    diagnostic = str(raised.value)
    assert elapsed < 2.0
    assert "truncated=True" in diagnostic
    assert "sha256=" in diagnostic
    assert marker not in diagnostic
    pid = int(pid_file.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_live_pty_selects_reject_once_by_kind_marker_not_label(tmp_path: Path) -> None:
    script = tmp_path / "localized_permission.py"
    script.write_text(
        "print('1. 永久允许 [allow_always] [disabled]', flush=True)\n"
        "print('2. 这次不要 [reject_once]', flush=True)\n"
        "print('3. 仅这一次 [allow_once]', flush=True)\n"
        "choice = input()\n"
        "print('{\"selected\": ' + repr(choice) + '}', flush=True)\n",
        encoding="utf-8",
    )
    capture = _run_live_pty(
        [sys.executable, str(script)], cwd=tmp_path, reject_once=True,
        total_timeout=0.5, terminate_timeout=0.2, kill_timeout=0.2,
    )
    assert '"selected": \'2\'' in capture.text()


@pytest.mark.skipif(os.name != "posix", reason="process-group acceptance requires POSIX")
def test_live_pty_timeout_kills_cancellation_resistant_process_group(tmp_path: Path) -> None:
    pid_file = tmp_path / "tree-pids.json"
    script = tmp_path / "resistant_tree.py"
    script.write_text(
        "import json, os, signal, subprocess, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)'])\n"
        f"open({str(pid_file)!r}, 'w').write(json.dumps("
        "{'parent': os.getpid(), 'grandchild': child.pid, 'pgid': os.getpgrp()}))\n"
        "print('PROCESS_TREE_RAW_OUTPUT', flush=True)\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    pids: list[int] = []
    try:
        with pytest.raises(AssertionError) as raised:
            _run_live_pty(
                [sys.executable, str(script)], cwd=tmp_path, reject_once=False,
                total_timeout=0.2, terminate_timeout=0.2, kill_timeout=0.5,
            )
        identities = json.loads(pid_file.read_text(encoding="utf-8"))
        pids = [identities["parent"], identities["grandchild"]]
        assert identities["pgid"] == identities["parent"]
        assert identities["pgid"] != os.getpgrp()
        assert "PROCESS_TREE_RAW_OUTPUT" not in str(raised.value)
        assert all(not _pid_exists(pid) for pid in pids)
    finally:
        for pid in pids:
            if _pid_exists(pid):
                os.kill(pid, 9)


def _example_live_ledger() -> dict[str, Any]:
    message = {"content": {"type": "text", "text": "same reply"}}
    return {
        "protocol_turns": [
            {"turn_id": "turn-first", "kind": "prompt"},
            {"turn_id": "turn-load", "kind": "load_replay"},
            {"turn_id": "turn-resume", "kind": "prompt"},
        ],
        "transport_updates": [
            {"turn_id": "turn-first", "sequence": 0, "kind": "agent_message_chunk", "payload": message},
            {"turn_id": "turn-first", "sequence": 1, "kind": "completion", "payload": {"stop_reason": "end_turn"}},
            {"turn_id": "turn-load", "sequence": 0, "kind": "agent_message_chunk", "payload": message},
            {"turn_id": "turn-load", "sequence": 1, "kind": "completion", "payload": {"stop_reason": "loaded"}},
            {"turn_id": "turn-resume", "sequence": 0, "kind": "agent_message_chunk", "payload": {"content": {"type": "text", "text": "second"}}},
            {"turn_id": "turn-resume", "sequence": 1, "kind": "completion", "payload": {"stop_reason": "end_turn"}},
        ],
    }


class _BoundedPtyCapture:
    def __init__(self, limit: int) -> None:
        if limit <= 0:
            raise ValueError("capture limit must be positive")
        self.limit = limit
        self.total_bytes = 0
        self.truncated = False
        self._tail = bytearray()
        self._digest = hashlib.sha256()

    def append(self, chunk: bytes) -> None:
        self.total_bytes += len(chunk)
        self._digest.update(chunk)
        self._tail.extend(chunk)
        if len(self._tail) > self.limit:
            del self._tail[:len(self._tail) - self.limit]
            self.truncated = True

    def text(self) -> str:
        return self._tail.decode("utf-8", errors="replace")

    def diagnostic(self) -> str:
        return (
            f"byte_count={self.total_bytes} truncated={self.truncated} "
            f"sha256={self._digest.hexdigest()}"
        )


def _wait_bounded(process: subprocess.Popen[bytes], timeout: float) -> bool:
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return False
    return True


def _process_group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return False
    return True


def _wait_process_group_gone(pgid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while _process_group_exists(pgid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
    return True


def _signal_process_group(pgid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_bounded(
    process: subprocess.Popen[bytes], *, pgid: int | None,
    terminate_timeout: float, kill_timeout: float,
) -> None:
    if pgid is None:
        if process.poll() is None:
            process.terminate()
        if _wait_bounded(process, terminate_timeout):
            return
        process.kill()
        if not _wait_bounded(process, kill_timeout):
            raise AssertionError("PTY child could not be killed and reaped within bound")
        return
    if _process_group_exists(pgid):
        _signal_process_group(pgid, signal.SIGTERM)
    direct_reaped = _wait_bounded(process, terminate_timeout)
    group_gone = _wait_process_group_gone(pgid, terminate_timeout)
    if not group_gone:
        _signal_process_group(pgid, signal.SIGKILL)
        group_gone = _wait_process_group_gone(pgid, kill_timeout)
    if not direct_reaped:
        direct_reaped = _wait_bounded(process, kill_timeout)
    if not direct_reaped or not group_gone:
        raise AssertionError("PTY process group could not be killed and reaped within bound")


def _run_live_pty(
    argv: list[str], *, cwd: Path, reject_once: bool, total_timeout: float = 240.0,
    terminate_timeout: float = 2.0, kill_timeout: float = 2.0,
    capture_limit: int = LIVE_PTY_CAPTURE_BYTES,
) -> _BoundedPtyCapture:
    master, slave = pty.openpty()
    process = subprocess.Popen(
        argv, cwd=cwd, env=_live_cli_environment(), stdin=slave, stdout=slave, stderr=slave,
        close_fds=True, start_new_session=os.name == "posix",
    )
    pgid = process.pid if os.name == "posix" else None
    os.close(slave)
    capture = _BoundedPtyCapture(capture_limit)
    selection_scan = ""
    selected = False
    deadline = time.monotonic() + total_timeout
    timed_out = False
    try:
        while process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            ready, _, _ = select.select([master], [], [], min(0.25, remaining))
            if not ready:
                continue
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            capture.append(chunk)
            if reject_once and not selected:
                selection_scan = (selection_scan + chunk.decode("utf-8", errors="replace"))[-4096:]
                match = re.search(r"(?mi)^(\d+)\..*\[reject_once\](?:\s|$)", selection_scan)
                if match:
                    os.write(master, (match.group(1) + "\n").encode())
                    selected = True
        _terminate_bounded(
            process, pgid=pgid,
            terminate_timeout=terminate_timeout, kill_timeout=kill_timeout,
        )
        drain_deadline = time.monotonic() + min(0.25, kill_timeout)
        while time.monotonic() < drain_deadline:
            ready, _, _ = select.select([master], [], [], 0)
            if not ready:
                break
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            capture.append(chunk)
    finally:
        os.close(master)
        if process.poll() is None:
            _terminate_bounded(
                process, pgid=pgid,
                terminate_timeout=terminate_timeout, kill_timeout=kill_timeout,
            )
    if timed_out:
        raise AssertionError("live ACP CLI timed out; " + capture.diagnostic())
    if reject_once and not selected:
        raise AssertionError("ACP Agent did not request reject_once; " + capture.diagnostic())
    if process.returncode != 0:
        raise AssertionError(
            f"interactive CLI failed with exit {process.returncode}; " + capture.diagnostic()
        )
    return capture


def _update_fact_hash(update: dict[str, Any]) -> str:
    encoded = json.dumps(
        {"kind": update["kind"], "payload": update["payload"]},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _assert_live_ledger(
    ledger: dict[str, Any], first_turn_id: str, load_turn_id: str, resume_turn_id: str,
) -> None:
    turns = ledger["protocol_turns"]
    assert [(item["turn_id"], item["kind"]) for item in turns] == [
        (first_turn_id, "prompt"), (load_turn_id, "load_replay"),
        (resume_turn_id, "prompt"),
    ], "live ledger must contain exactly prompt/load_replay/prompt"
    by_turn = {
        turn_id: [item for item in ledger["transport_updates"] if item["turn_id"] == turn_id]
        for turn_id in (first_turn_id, load_turn_id, resume_turn_id)
    }
    for turn_id, updates in by_turn.items():
        assert [item["sequence"] for item in updates] == list(range(len(updates))), (
            f"{turn_id} updates must be contiguous"
        )
        assert updates and updates[-1]["kind"] == "completion"
    assert by_turn[first_turn_id][-1]["payload"]["stop_reason"] == "end_turn"
    assert by_turn[load_turn_id][-1]["payload"]["stop_reason"] == "loaded"
    assert by_turn[resume_turn_id][-1]["payload"]["stop_reason"] == "end_turn"
    replay = [item for item in by_turn[load_turn_id] if item["kind"] != "completion"]
    assert replay, "load must contain replayed history before completion"
    prior_hashes = {
        _update_fact_hash(item) for item in by_turn[first_turn_id] if item["kind"] != "completion"
    }
    replay_hashes = {_update_fact_hash(item) for item in replay}
    assert prior_hashes & replay_hashes, "load replay must match prior conversation by hash"
    resume_updates = [
        item for item in by_turn[resume_turn_id] if item["kind"] != "completion"
    ]
    assert all(item["kind"] != "user_message_chunk" for item in resume_updates), (
        "resume prompt must not persist pre-prompt history replay"
    )


def _assert_live_payload_counts(payload: dict[str, Any], ledger: dict[str, Any]) -> None:
    assert payload["session_count"] == len(ledger["agent_sessions"])
    assert payload["turn_count"] == len(ledger["protocol_turns"])
    assert payload["update_count"] == len(ledger["transport_updates"])
    assert payload["permission_count"] == len(ledger["permission_requests"])
    assert payload["transition_count"] == len(ledger["protocol_state_transitions"])


def _live_acp_executable() -> Path:
    if os.environ.get("AGENTDECK_ACP_LIVE") != "1":
        pytest.skip("real ACP acceptance requires AGENTDECK_ACP_LIVE=1")
    raw = os.environ.get("AGENTDECK_ACP_COMMAND", "")
    if not raw:
        pytest.skip("setup blocker: AGENTDECK_ACP_COMMAND is not set")
    executable = Path(raw).expanduser()
    if not executable.is_absolute() or not executable.is_file() or not os.access(executable, os.X_OK):
        pytest.skip("setup blocker: AGENTDECK_ACP_COMMAND must be an exact existing executable path")
    if executable.name in {"npx", "npm", "pip", "pip3"}:
        pytest.skip("setup blocker: auto-download or installer commands are forbidden")
    return executable.resolve()


def _live_cli_environment() -> dict[str, str]:
    environment = os.environ.copy()
    source = str(Path(__file__).resolve().parents[1] / "src")
    environment["PYTHONPATH"] = source + os.pathsep + environment.get("PYTHONPATH", "")
    return environment


def _sanitized_diagnostic(value: str) -> str:
    encoded = value.encode("utf-8", errors="replace")
    return f"bytes={len(encoded)} sha256={hashlib.sha256(encoded).hexdigest()}"


def _json_document(value: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    documents: list[tuple[int, dict[str, Any]]] = []
    for match in re.finditer(r"\{", value):
        try:
            candidate, end = decoder.raw_decode(value[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            documents.append((end, candidate))
    if not documents:
        raise AssertionError("CLI emitted no JSON document; diagnostic=" + _sanitized_diagnostic(value))
    return max(documents, key=lambda item: item[0])[1]


def _live_cli(project: Path, *arguments: str) -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, "-m", "agentdeck", *arguments],
        cwd=project,
        env=_live_cli_environment(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"CLI failed with exit {result.returncode}; diagnostic="
            + _sanitized_diagnostic(result.stderr)
        )
    return _json_document(result.stdout)


def _configure_live_acp(project: Path, adapter: Path) -> None:
    config_path = project / ".agentdeck" / "config.toml"
    original = config_path.read_text(encoding="utf-8")
    needle = 'provider = "claude"\ncommand = "claude"'
    replacement = (
        needle
        + '\ntransport = "acp"\ntransport_command = ['
        + json.dumps(str(adapter))
        + "]"
    )
    assert original.count(needle) == 1
    config_path.write_text(original.replace(needle, replacement), encoding="utf-8")


def _live_cli_tty_reject_once(project: Path, *arguments: str) -> dict[str, Any]:
    capture = _run_live_pty(
        [sys.executable, "-m", "agentdeck", *arguments], cwd=project, reject_once=True,
    )
    try:
        return _json_document(capture.text())
    except AssertionError as error:
        raise AssertionError("PTY emitted no valid JSON document; " + capture.diagnostic()) from error


def test_live_acp_gate_requires_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTDECK_ACP_LIVE", raising=False)
    monkeypatch.delenv("AGENTDECK_ACP_COMMAND", raising=False)
    with pytest.raises(pytest.skip.Exception, match="AGENTDECK_ACP_LIVE=1"):
        _live_acp_executable()


def test_live_acp_gate_reports_missing_command_as_setup_blocker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTDECK_ACP_LIVE", "1")
    monkeypatch.delenv("AGENTDECK_ACP_COMMAND", raising=False)
    with pytest.raises(pytest.skip.Exception, match="AGENTDECK_ACP_COMMAND"):
        _live_acp_executable()


def test_live_acp_gate_reports_non_executable_path_as_setup_blocker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = tmp_path / "claude-agent-acp"
    adapter.write_text("not executable", encoding="utf-8")
    monkeypatch.setenv("AGENTDECK_ACP_LIVE", "1")
    monkeypatch.setenv("AGENTDECK_ACP_COMMAND", str(adapter))
    with pytest.raises(pytest.skip.Exception, match="existing executable"):
        _live_acp_executable()


def test_real_preflight_rehearsal_reports_only_missing_claude_adapter_and_is_read_only(
    tmp_path: Path,
) -> None:
    project = tmp_path / "preflight-rehearsal"
    project.mkdir()
    (project / ".git").mkdir()
    _live_cli(project, "project", "init")
    missing = project / "missing" / "claude-agent-acp"
    _configure_live_acp(project, missing)
    before = {
        path.relative_to(project): (path.is_dir(), path.read_bytes() if path.is_file() else b"")
        for path in project.rglob("*")
    }

    payload = _live_cli(project, "protocol", "acp", "preflight", "--agent", "reviewer")

    after = {
        path.relative_to(project): (path.is_dir(), path.read_bytes() if path.is_file() else b"")
        for path in project.rglob("*")
    }
    assert payload["ready"] is False
    assert payload["adapter"]["argv"] == [str(missing)]
    assert payload["adapter"]["present"] is False
    assert payload["sdk"] == {
        "module": "acp", "package": "agent-client-protocol", "present": True, "version": "0.11.0",
    }
    assert payload["node"]["required"] is True
    assert payload["node"]["ready"] is True
    assert payload["blockers"] == ["ACP adapter executable was not found"]
    assert after == before


@pytest.mark.skipif(
    os.environ.get("AGENTDECK_ACP_LIVE") != "1",
    reason="explicit opt-in required: set AGENTDECK_ACP_LIVE=1",
)
def test_live_claude_agent_vertical_slice(tmp_path: Path) -> None:
    adapter = _live_acp_executable()
    project = tmp_path / "live-acp-project"
    project.mkdir()
    (project / ".git").mkdir()
    _live_cli(project, "project", "init")
    _configure_live_acp(project, adapter)

    preflight = _live_cli(project, "protocol", "acp", "preflight", "--agent", "reviewer")
    assert preflight["ready"] is True, f"ACP setup blocker: {preflight['blockers']}"
    assert Path(preflight["adapter"]["executable_path"]) == adapter

    forbidden = project / "agentdeck-acp-must-not-exist.txt"
    prompt = (
        "Reply briefly, then use your file-writing tool to create "
        f"{forbidden.name} in the current workspace with harmless test content."
    )
    first = _live_cli_tty_reject_once(
        project,
        "protocol", "acp", "run", "--agent", "reviewer", "--prompt", prompt, "--confirm",
    )
    assert first["protocol_version"] == 1
    assert first["turn_state"] == "completed"
    assert first["stop_reason"] == "end_turn"
    assert first["session_state"] == "disconnected"
    assert first["disconnect_reason"] == "clean_exit"
    assert first["permission_count"] >= 1
    assert not forbidden.exists()
    first_ledger = StateStore(project).load()
    _assert_live_payload_counts(first, first_ledger)

    loaded = _live_cli(
        project, "protocol", "acp", "load", "--session-id", first["session_id"], "--confirm"
    )
    assert loaded["session_id"] == first["session_id"]
    assert loaded["native_session_id"] == first["native_session_id"]
    assert loaded["turn_state"] == "completed"
    assert loaded["stop_reason"] == "loaded"
    assert loaded["session_state"] == "disconnected"
    loaded_ledger = StateStore(project).load()
    _assert_live_payload_counts(loaded, loaded_ledger)

    resumed = _live_cli(
        project, "protocol", "acp", "resume", "--session-id", first["session_id"],
        "--prompt", "Reply with a brief harmless acknowledgement.", "--confirm",
    )
    assert resumed["session_id"] == first["session_id"]
    assert resumed["native_session_id"] == first["native_session_id"]
    assert resumed["turn_state"] == "completed"
    assert resumed["stop_reason"] == "end_turn"
    assert resumed["session_state"] == "disconnected"

    status = _live_cli(project, "protocol", "status")
    ledger = StateStore(project).load()
    _assert_live_payload_counts(resumed, ledger)
    _assert_live_ledger(
        ledger, first["turn_id"], loaded["turn_id"], resumed["turn_id"],
    )
    assert status["agent_sessions"]["count"] == 1
    assert status["protocol_turns"]["count"] == 3
    assert status["transport_updates"]["count"] == len(ledger["transport_updates"])
    assert status["permission_requests"]["count"] == first["permission_count"]
    assert resumed["permission_count"] == first["permission_count"]
    assert status["protocol_state_transitions"]["count"] == len(
        ledger["protocol_state_transitions"]
    )
    assert all(item["state"] == "denied" for item in status["permission_requests"]["items"])
    assert status["agent_sessions"]["items"][0]["session_id"] == first["session_id"]
    assert ledger["agent_sessions"][0]["native_session_id"] == first["native_session_id"]
    assert status["protocol_state_transitions"]["items"][-1]["to_state"] == "disconnected"
    assert not forbidden.exists()


def async_test(function: Any) -> Any:
    @functools.wraps(function)
    def run(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(function(*args, **kwargs))
    return run


def _message(text: str) -> schema.AgentMessageChunk:
    return schema.AgentMessageChunk(
        sessionUpdate="agent_message_chunk",
        content=schema.TextContentBlock(type="text", text=text),
    )


def _tool() -> schema.ToolCallUpdate:
    return schema.ToolCallUpdate(toolCallId="call-1", title="Edit", kind="edit")


def _options() -> list[schema.PermissionOption]:
    return [
        schema.PermissionOption(optionId="allow", name="Allow once", kind="allow_once"),
        schema.PermissionOption(optionId="reject", name="Reject once", kind="reject_once"),
        schema.PermissionOption(optionId="always", name="Always", kind="allow_always"),
    ]


class FakeLedgerSink:
    def __init__(self) -> None:
        self.session_id = "native-1"
        self.completed = False
        self.updates: list[dict[str, Any]] = []
        self.permissions: list[dict[str, Any]] = []
        self.payload_bytes = 0

    async def append_update(self, session_id: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._active(session_id)
        encoded = len(str(payload).encode())
        if len(self.updates) >= MAX_ACP_UPDATES_PER_TURN:
            raise ValueError("ACP turn updates exceed bound")
        if self.payload_bytes + encoded > MAX_ACP_TURN_PAYLOAD_BYTES:
            raise ValueError("ACP turn payload exceeds bound")
        item = {"sequence": len(self.updates), "kind": kind, "payload": payload}
        self.updates.append(item)
        self.payload_bytes += encoded
        return item

    async def append_permission(
        self, session_id: str, summary: dict[str, Any], options: list[schema.PermissionOption]
    ) -> dict[str, Any]:
        self._active(session_id)
        item = {"permission_id": f"prm-{len(self.permissions)}", "summary": summary,
                "option_ids": [option.option_id for option in options], "decision": None}
        self.permissions.append(item)
        return item

    async def append_permission_decision(
        self, session_id: str, tool_call_id: str, decision: PermissionDecision
    ) -> None:
        pending = next(item for item in reversed(self.permissions)
                       if item["summary"]["tool_call_id"] == tool_call_id)
        if pending["decision"] is not None:
            raise ValueError("permission already settled")
        pending["decision"] = decision.ledger_status
        pending["reason"] = decision.reason

    def _active(self, session_id: str) -> None:
        if session_id != self.session_id:
            raise ValueError("ACP session correlation mismatch")
        if self.completed:
            raise ValueError("ACP turn is already complete")


@async_test
async def test_session_update_is_mapped_and_sequenced_exactly_once() -> None:
    sink = FakeLedgerSink()
    client = AgentDeckAcpClient(sink=sink, decide=lambda *_: PermissionDecision.cancelled("unused"))
    await client.session_update("native-1", _message("a"))
    await client.session_update("native-1", _message("b"))
    assert [item["sequence"] for item in sink.updates] == [0, 1]
    assert [item["payload"]["content"]["text"] for item in sink.updates] == ["a", "b"]


@async_test
async def test_session_update_propagates_wrong_session_completion_and_bounds() -> None:
    sink = FakeLedgerSink()
    client = AgentDeckAcpClient(sink=sink, decide=lambda *_: PermissionDecision.cancelled("unused"))
    with pytest.raises(ValueError, match="correlation"):
        await client.session_update("other", _message("x"))
    sink.completed = True
    with pytest.raises(ValueError, match="complete"):
        await client.session_update("native-1", _message("x"))
    sink.completed = False
    sink.updates = [{}] * MAX_ACP_UPDATES_PER_TURN
    with pytest.raises(ValueError, match="updates"):
        await client.session_update("native-1", _message("x"))
    sink.updates = []
    sink.payload_bytes = MAX_ACP_TURN_PAYLOAD_BYTES
    with pytest.raises(ValueError, match="payload"):
        await client.session_update("native-1", _message("x"))


@pytest.mark.parametrize(
    ("option_id", "ledger_status"), [("allow", "approved"), ("reject", "denied")]
)
@async_test
async def test_permission_records_pending_before_decision_and_returns_current_once_option(
    option_id: str, ledger_status: str
) -> None:
    sink = FakeLedgerSink()

    async def decide(pending: dict[str, Any], options: list[schema.PermissionOption]) -> PermissionDecision:
        assert pending["decision"] is None
        assert sink.permissions == [pending]
        return PermissionDecision.select(option_id)

    result = await AgentDeckAcpClient(sink=sink, decide=decide).request_permission(
        "native-1", _tool(), _options()
    )
    assert result.outcome.outcome == "selected"
    assert result.outcome.option_id == option_id
    assert sink.permissions[-1]["decision"] == ledger_status


@pytest.mark.parametrize("reason", ["non_tty", "ctrl_c", "timeout", "eof"])
@async_test
async def test_cancel_semantics_are_decider_results_and_never_auto_approve(reason: str) -> None:
    sink = FakeLedgerSink()

    async def decide(*_: Any) -> PermissionDecision:
        return PermissionDecision.cancelled(reason)

    result = await AgentDeckAcpClient(sink=sink, decide=decide).request_permission(
        "native-1", _tool(), _options()
    )
    assert result.outcome.outcome == "cancelled"
    assert sink.permissions[-1]["decision"] == "denied"
    assert sink.permissions[-1]["reason"] == reason


@pytest.mark.parametrize(
    ("error", "reason"), [(TimeoutError(), "timeout"), (EOFError(), "eof"),
                           (KeyboardInterrupt(), "ctrl_c")]
)
@async_test
async def test_interrupted_decider_is_settled_as_cancelled(error: BaseException, reason: str) -> None:
    sink = FakeLedgerSink()

    async def decide(*_: Any) -> PermissionDecision:
        raise error

    result = await AgentDeckAcpClient(sink=sink, decide=decide).request_permission(
        "native-1", _tool(), _options()
    )
    assert result.outcome.outcome == "cancelled"
    assert sink.permissions[-1]["decision"] == "denied"
    assert sink.permissions[-1]["reason"] == reason


@pytest.mark.parametrize("option_id", ["always", "missing"])
@async_test
async def test_disabled_always_and_unknown_options_fail_closed(option_id: str) -> None:
    sink = FakeLedgerSink()
    client = AgentDeckAcpClient(sink=sink, decide=lambda *_: PermissionDecision.select(option_id))
    result = await client.request_permission("native-1", _tool(), _options())
    assert result.outcome.outcome == "cancelled"
    assert sink.permissions[-1]["decision"] == "denied"
    assert sink.permissions[-1]["reason"] == "invalid_option"


@async_test
async def test_empty_options_wrong_session_and_completed_turn_reject_before_decider() -> None:
    sink = FakeLedgerSink()
    called = False

    async def decide(*_: Any) -> PermissionDecision:
        nonlocal called
        called = True
        return PermissionDecision.select("allow")

    client = AgentDeckAcpClient(sink=sink, decide=decide)
    with pytest.raises(ValueError, match="options"):
        await client.request_permission("native-1", _tool(), [])
    with pytest.raises(ValueError, match="correlation"):
        await client.request_permission("wrong", _tool(), _options())
    sink.completed = True
    with pytest.raises(ValueError, match="complete"):
        await client.request_permission("native-1", _tool(), _options())
    assert called is False


@async_test
async def test_concurrent_permission_request_fails_without_second_pending_record() -> None:
    sink = FakeLedgerSink()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def decide(*_: Any) -> PermissionDecision:
        entered.set()
        await release.wait()
        return PermissionDecision.cancelled("cancelled")

    client = AgentDeckAcpClient(sink=sink, decide=decide)
    first = asyncio.create_task(client.request_permission("native-1", _tool(), _options()))
    await entered.wait()
    with pytest.raises(RuntimeError, match="already pending"):
        await client.request_permission("native-1", _tool(), _options())
    assert len(sink.permissions) == 1
    release.set()
    await first


class CancellingLedgerSink(FakeLedgerSink):
    def __init__(self, block_at: str, *, fail_cancel_settlement: bool = False) -> None:
        super().__init__()
        self.block_at = block_at
        self.fail_cancel_settlement = fail_cancel_settlement
        self.blocked = asyncio.Event()
        self.release = asyncio.Event()
        self.final_decisions: list[PermissionDecision] = []

    async def append_permission(
        self, session_id: str, summary: dict[str, Any], options: list[schema.PermissionOption]
    ) -> dict[str, Any]:
        pending = await super().append_permission(session_id, summary, options)
        if self.block_at == "pending":
            self.blocked.set()
            await self.release.wait()
        return pending

    async def append_permission_decision(
        self, session_id: str, tool_call_id: str, decision: PermissionDecision
    ) -> None:
        pending = next(item for item in reversed(self.permissions)
                       if item["summary"]["tool_call_id"] == tool_call_id)
        if decision.reason == "cancelled" and self.fail_cancel_settlement:
            raise RuntimeError("cancel settlement failed")
        if self.block_at == "decision" and not self.blocked.is_set():
            pending["decision"] = decision.ledger_status
            pending["reason"] = decision.reason
            self.blocked.set()
            await self.release.wait()
        if decision.reason == "cancelled":
            pending["decision"] = "denied"
            pending["reason"] = "cancelled"
        elif pending["decision"] is None:
            await super().append_permission_decision(session_id, tool_call_id, decision)
        if not self.final_decisions or self.final_decisions[-1] != decision:
            self.final_decisions[:] = [decision]


@pytest.mark.parametrize("block_at", ["pending", "decision"])
@async_test
async def test_task_cancel_settles_possibly_written_permission_once_and_cleans_guard(
    block_at: str,
) -> None:
    sink = CancellingLedgerSink(block_at)
    client = AgentDeckAcpClient(sink=sink, decide=lambda *_: PermissionDecision.select("allow"))
    task = asyncio.create_task(client.request_permission("native-1", _tool(), _options()))
    await sink.blocked.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sink.permissions[0]["decision"] == "denied"
    assert sink.permissions[0]["reason"] == "cancelled"
    assert len(sink.final_decisions) == 1

    sink.block_at = "none"
    result = await client.request_permission("native-1", _tool(), _options())
    assert result.outcome.outcome == "selected"
    assert len(sink.permissions) == 2
    assert sink.permissions[0]["decision"] == "denied"
    assert sink.permissions[1]["decision"] == "approved"


@async_test
async def test_cancel_settlement_failure_raises_and_never_records_approval() -> None:
    sink = CancellingLedgerSink("pending", fail_cancel_settlement=True)
    client = AgentDeckAcpClient(sink=sink, decide=lambda *_: PermissionDecision.select("allow"))
    task = asyncio.create_task(client.request_permission("native-1", _tool(), _options()))
    await sink.blocked.wait()
    task.cancel()
    with pytest.raises(RuntimeError, match="cancel settlement failed"):
        await task
    assert sink.permissions[0]["decision"] is None
    assert all(decision.ledger_status != "approved" for decision in sink.final_decisions)
    sink.fail_cancel_settlement = False
    sink.block_at = "none"
    result = await client.request_permission("native-1", _tool(), _options())
    assert result.outcome.outcome == "selected"


class CancellationResistantSink(CancellingLedgerSink):
    def __init__(self) -> None:
        super().__init__("pending")
        self.settlement_started = asyncio.Event()
        self.settlement_cancelled = asyncio.Event()
        self.settlement_release = asyncio.Event()
        self.settlement_finished = asyncio.Event()

    async def append_permission_decision(
        self, session_id: str, tool_call_id: str, decision: PermissionDecision
    ) -> None:
        if decision.reason != "cancelled":
            await super().append_permission_decision(session_id, tool_call_id, decision)
            return
        self.settlement_started.set()
        while not self.settlement_release.is_set():
            try:
                await self.settlement_release.wait()
            except asyncio.CancelledError:
                self.settlement_cancelled.set()
        await super().append_permission_decision(session_id, tool_call_id, decision)
        self.settlement_finished.set()


@async_test
async def test_cancellation_resistant_settlement_has_a_hard_cleanup_bound(monkeypatch: Any) -> None:
    monkeypatch.setattr(acp_client_module, "_CANCEL_SETTLEMENT_TIMEOUT_SECONDS", 0.01)
    sink = CancellationResistantSink()
    client = AgentDeckAcpClient(sink=sink, decide=lambda *_: PermissionDecision.select("allow"))
    request = asyncio.create_task(client.request_permission("native-1", _tool(), _options()))
    await sink.blocked.wait()
    started = asyncio.get_running_loop().time()
    request.cancel()
    done, _ = await asyncio.wait({request}, timeout=0.25)
    bounded_elapsed = asyncio.get_running_loop().time() - started
    assert sink.settlement_started.is_set()
    assert sink.settlement_cancelled.is_set()

    sink.settlement_release.set()
    await asyncio.wait_for(sink.settlement_finished.wait(), timeout=0.25)
    result_or_error = await asyncio.wait_for(
        asyncio.gather(request, return_exceptions=True), timeout=0.25
    )
    assert request in done
    assert bounded_elapsed < 0.25
    assert isinstance(result_or_error[0], RuntimeError)
    assert sink.permissions[0]["decision"] == "denied"
    assert sink.permissions[0]["reason"] == "cancelled"
    assert all(decision.ledger_status != "approved" for decision in sink.final_decisions)

    sink.block_at = "none"
    result = await client.request_permission("native-1", _tool(), _options())
    assert result.outcome.outcome == "selected"


@async_test
async def test_all_unadvertised_callbacks_return_unsupported_without_side_effects(tmp_path: Path) -> None:
    client = AgentDeckAcpClient(
        sink=FakeLedgerSink(), decide=lambda *_: PermissionDecision.cancelled("unused")
    )
    target = tmp_path / "must-not-exist"
    calls = [
        client.read_text_file("native-1", str(target)),
        client.write_text_file("native-1", str(target), "secret"),
        client.create_terminal("native-1", "touch", [str(target)]),
        client.terminal_output("native-1", "term-1"),
        client.wait_for_terminal_exit("native-1", "term-1"),
        client.release_terminal("native-1", "term-1"),
        client.kill_terminal("native-1", "term-1"),
        client.create_elicitation("question", object()),
        client.complete_elicitation("eli-1"),
        client.ext_method("x/private", {"path": str(target)}),
        client.ext_notification("x/private", {"path": str(target)}),
    ]
    for call in calls:
        with pytest.raises(RequestError, match="not advertised|unsupported"):
            await call
    assert not target.exists()
    assert client.on_connect(object()) is None


def _transport_client(session_id: str = "fake-session-1") -> tuple[AgentDeckAcpClient, FakeLedgerSink]:
    sink = FakeLedgerSink()
    sink.session_id = session_id
    return AgentDeckAcpClient(
        sink=sink, decide=lambda *_: PermissionDecision.cancelled("non_tty")
    ), sink


@async_test
async def test_transport_initializes_streams_and_completes_prompt(tmp_path: Path) -> None:
    from agentdeck.runtime.acp import AcpTransport

    client, sink = _transport_client()
    transport = AcpTransport(
        (sys.executable, str(FAKE_AGENT), "stream_end_turn"), tmp_path, client
    )
    initialized = await transport.initialize()
    session = await transport.new_session()
    result = await transport.prompt(session.native_session_id, "hello")
    await transport.close()

    assert initialized.protocol_version == 1
    assert initialized.client_capabilities == {"fs": None, "terminal": False}
    assert session.native_session_id == "fake-session-1"
    assert result.stop_reason == "end_turn"
    assert result.outcome == "completed"
    assert result.disconnect_reason == "clean_exit"
    assert [item["payload"]["content"]["text"] for item in sink.updates] == ["hello"]


@async_test
async def test_transport_load_session_preserves_ordered_replay(tmp_path: Path) -> None:
    from agentdeck.runtime.acp import AcpTransport

    client, sink = _transport_client()
    transport = AcpTransport(
        (sys.executable, str(FAKE_AGENT), "load_replay"), tmp_path, client
    )
    initialized = await transport.initialize()
    await transport.load_session("fake-session-1")
    await transport.close()

    assert initialized.capabilities.resume_session is True
    assert [item["payload"]["content"]["text"] for item in sink.updates] == ["one", "two"]


@async_test
async def test_transport_load_response_waits_for_scheduled_replay_callbacks(tmp_path: Path) -> None:
    from agentdeck.runtime.acp import AcpTransport

    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingSink(FakeLedgerSink):
        async def append_update(self, session_id, kind, payload):
            started.set()
            await release.wait()
            return await super().append_update(session_id, kind, payload)

    sink = BlockingSink()
    sink.session_id = "fake-session-1"
    client = AgentDeckAcpClient(
        sink=sink, decide=lambda *_: PermissionDecision.cancelled("non_tty")
    )
    transport = AcpTransport(
        (sys.executable, str(FAKE_AGENT), "load_replay"), tmp_path, client
    )
    await transport.initialize()
    loading = asyncio.create_task(transport.load_session("fake-session-1"))
    await asyncio.wait_for(started.wait(), timeout=2)
    assert loading.done() is False
    release.set()
    await loading
    await transport.close()

    assert [item["payload"]["content"]["text"] for item in sink.updates] == ["one", "two"]


@async_test
async def test_transport_load_rejects_malformed_update_without_barrier_hang(tmp_path: Path) -> None:
    from agentdeck.runtime.acp import AcpTransport, AcpTransportError

    client, sink = _transport_client()
    transport = AcpTransport(
        (sys.executable, str(FAKE_AGENT), "load_malformed_update"),
        tmp_path, client, request_timeout=0.5,
    )
    await transport.initialize()
    with pytest.raises(AcpTransportError, match="invalid_session_update"):
        await asyncio.wait_for(transport.load_session("fake-session-1"), timeout=1)
    await transport.close()
    assert sink.updates == []


@async_test
async def test_transport_load_bounds_valid_update_callback_that_never_settles(tmp_path: Path) -> None:
    from agentdeck.runtime.acp import AcpTransport, AcpTransportError

    class UnsettledClient(AgentDeckAcpClient):
        async def session_update(self, session_id, update, **kwargs):
            raise RuntimeError("router swallowed callback failure")

    sink = FakeLedgerSink()
    sink.session_id = "fake-session-1"
    client = UnsettledClient(sink=sink, decide=lambda *_: PermissionDecision.cancelled("non_tty"))
    transport = AcpTransport(
        (sys.executable, str(FAKE_AGENT), "load_replay"),
        tmp_path, client, request_timeout=0.4,
    )
    await transport.initialize()
    with pytest.raises(AcpTransportError, match="callback settlement timed out"):
        await asyncio.wait_for(transport.load_session("fake-session-1"), timeout=1)
    await transport.close()
    assert sink.updates == []


@async_test
async def test_transport_resume_rejects_update_before_response(tmp_path: Path) -> None:
    from agentdeck.runtime.acp import AcpTransport, AcpTransportError

    client, _ = _transport_client()
    transport = AcpTransport(
        (sys.executable, str(FAKE_AGENT), "resume_illegal_replay"), tmp_path, client
    )
    await transport.initialize()
    with pytest.raises(AcpTransportError, match="unexpected_resume_replay"):
        await transport.resume_session("fake-session-1")
    await transport.close()


@async_test
async def test_client_phase_gate_rejects_update_after_load_is_sealed() -> None:
    client, _ = _transport_client()
    await client.switch_update_phase("load_replay")
    await client.switch_update_phase("sealed")
    update = schema.AgentMessageChunk(
        sessionUpdate="agent_message_chunk",
        content=schema.TextContentBlock(type="text", text="late"),
    )
    with pytest.raises(RuntimeError, match="unexpected ACP update during sealed phase"):
        await client.session_update("fake-session-1", update)
    assert client.take_callback_error() is not None


@pytest.mark.parametrize("update", [
    schema.AgentMessageChunk(sessionUpdate="agent_message_chunk", content=schema.TextContentBlock(type="text", text="late")),
    schema.UserMessageChunk(sessionUpdate="user_message_chunk", content=schema.TextContentBlock(type="text", text="late")),
    schema.ToolCallStart(sessionUpdate="tool_call", toolCallId="late-call", title="Late", kind="read"),
])
@async_test
async def test_callback_captured_while_sealed_cannot_enter_new_prompt_generation(update: object) -> None:
    client, sink = _transport_client()
    await client.switch_update_phase("sealed")
    await client._phase_lock.acquire()
    try:
        callback = asyncio.create_task(client.session_update("fake-session-1", update))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        client._phase_generation += 1
        client._update_phase = "prompt"
    finally:
        client._phase_lock.release()
    with pytest.raises(RuntimeError, match="stale generation"):
        await callback
    assert sink.updates == []
    assert client.take_callback_error() is not None


@async_test
async def test_transport_preserves_exact_argv_and_canonical_cwd(tmp_path: Path) -> None:
    from agentdeck.runtime.acp import AcpTransport

    marker = tmp_path / "argv.json"
    client, _ = _transport_client()
    transport = AcpTransport(
        (sys.executable, str(FAKE_AGENT), "record_argv", str(marker), "two words", "$(false)"),
        tmp_path / ".",
        client,
    )
    await transport.initialize()
    await transport.close()
    import json
    recorded = json.loads(marker.read_text())
    assert recorded["argv"] == ["two words", "$(false)"]
    assert recorded["cwd"] == str(tmp_path.resolve())
    assert "create_subprocess_shell" not in inspect.getsource(AcpTransport)


@pytest.mark.parametrize("argv", [(), ("",), (sys.executable, 1)])
def test_transport_rejects_non_exact_nonempty_argv(argv: tuple[object, ...], tmp_path: Path) -> None:
    from agentdeck.runtime.acp import AcpTransport

    client, _ = _transport_client()
    with pytest.raises((TypeError, ValueError), match="argv"):
        AcpTransport(argv, tmp_path, client)  # type: ignore[arg-type]


@async_test
async def test_never_started_close_is_closed_and_idempotent(tmp_path: Path) -> None:
    from agentdeck.runtime.acp import AcpTransport

    client, _ = _transport_client()
    transport = AcpTransport((sys.executable, "unused"), tmp_path, client)
    assert transport.lifecycle_state == "new"
    await asyncio.gather(transport.close(), transport.close())
    await transport.close()
    assert transport.lifecycle_state == "closed"
    with pytest.raises(RuntimeError, match="cannot start"):
        await transport.initialize()


@async_test
async def test_transport_blocks_exact_protocol_version_mismatch(tmp_path: Path) -> None:
    from agentdeck.runtime.acp import AcpProtocolVersionError, AcpTransport

    client, _ = _transport_client()
    transport = AcpTransport(
        (sys.executable, str(FAKE_AGENT), "version_mismatch"), tmp_path, client,
        close_grace=0.05, terminate_grace=0.05, kill_grace=0.1,
    )
    with pytest.raises(AcpProtocolVersionError, match="protocol version"):
        await transport.initialize()
    assert transport._process.returncode is not None
    assert transport._stderr_task.done()
    assert transport.lifecycle_state == "closed"


@async_test
async def test_initialize_eof_race_is_ambiguous_and_self_cleans(tmp_path: Path) -> None:
    from agentdeck.runtime.acp import AcpAmbiguousOutcome, AcpTransport

    client, _ = _transport_client()
    transport = AcpTransport(
        (sys.executable, str(FAKE_AGENT), "eof_initialize"), tmp_path, client,
        request_timeout=0.5, close_grace=0.05, terminate_grace=0.05, kill_grace=0.1,
    )
    with pytest.raises(AcpAmbiguousOutcome):
        await transport.initialize()
    assert transport._process.returncode is not None
    assert transport._stderr_task.done()
    assert transport.lifecycle_state == "closed"


@async_test
async def test_transport_bounds_timeout_and_eof_as_ambiguous(tmp_path: Path) -> None:
    from agentdeck.runtime.acp import AcpAmbiguousOutcome, AcpRequestTimeout, AcpTransport

    timeout_marker = tmp_path / "timeout-cancelled"
    for scenario, error, args in [
        ("timeout", AcpRequestTimeout, (str(timeout_marker),)),
        ("eof_before_response", AcpAmbiguousOutcome, ()),
    ]:
        client, _ = _transport_client()
        transport = AcpTransport(
            (sys.executable, str(FAKE_AGENT), scenario, *args), tmp_path, client,
            request_timeout=0.5,
        )
        await transport.initialize()
        session = await transport.new_session()
        with pytest.raises(error) as caught:
            await transport.prompt(session.native_session_id, "hello")
        if scenario == "timeout":
            await asyncio.sleep(0.05)
            assert timeout_marker.read_text() == "cancelled"
            assert caught.value.cancel_diagnostic.status == "sent"
            assert caught.value.cancel_diagnostic.session_id == "fake-session-1"
        await transport.close()


@pytest.mark.parametrize("cancel_behavior", ["hang", "error"])
@async_test
async def test_prompt_timeout_bounds_cancel_and_preserves_primary_error(
    tmp_path: Path, cancel_behavior: str
) -> None:
    from agentdeck.runtime.acp import AcpRequestTimeout, AcpTransport

    client, _ = _transport_client()
    transport = AcpTransport(
        (sys.executable, str(FAKE_AGENT), "timeout"), tmp_path, client,
        request_timeout=0.5, cancel_timeout=0.03,
    )
    await transport.initialize()
    session = await transport.new_session()
    transport._request_timeout = 0.15

    async def bad_cancel(**_: Any) -> None:
        if cancel_behavior == "hang":
            await asyncio.sleep(60)
        raise RuntimeError("cancel failed with sensitive detail")

    transport._connection.cancel = bad_cancel
    started = asyncio.get_running_loop().time()
    with pytest.raises(AcpRequestTimeout) as caught:
        await transport.prompt(session.native_session_id, "hello")
    elapsed = asyncio.get_running_loop().time() - started
    assert elapsed < 0.4
    assert caught.value.cancel_diagnostic.status == (
        "timed_out" if cancel_behavior == "hang" else "failed"
    )
    assert "sensitive detail" not in repr(caught.value.cancel_diagnostic)
    await transport.close()


@async_test
async def test_prompt_cumulative_bound_failure_sends_bounded_cancel(tmp_path: Path) -> None:
    from agentdeck.runtime.acp import AcpTransport, AcpTransportError

    marker = tmp_path / "bound-cancelled"
    client, sink = _transport_client()
    sink.payload_bytes = MAX_ACP_TURN_PAYLOAD_BYTES
    transport = AcpTransport(
        (sys.executable, str(FAKE_AGENT), "stream_end_turn", str(marker)),
        tmp_path,
        client,
        request_timeout=0.5,
    )
    await transport.initialize()
    session = await transport.new_session()
    with pytest.raises(AcpTransportError) as caught:
        await transport.prompt(session.native_session_id, "hello")
    await asyncio.sleep(0.05)
    assert marker.read_text() == "cancelled"
    assert caught.value.cancel_diagnostic.status == "sent"
    await transport.close()


@async_test
async def test_transport_rejects_malformed_or_oversize_frames(tmp_path: Path) -> None:
    from agentdeck.runtime.acp import AcpTransport, AcpTransportError

    for scenario in ("malformed_frame", "oversize_frame"):
        client, _ = _transport_client()
        transport = AcpTransport(
            (sys.executable, str(FAKE_AGENT), scenario), tmp_path, client,
            request_timeout=0.5, close_grace=0.05,
            terminate_grace=0.05, kill_grace=0.1,
        )
        with pytest.raises(AcpTransportError):
            await transport.initialize()
        assert transport._process.returncode is not None
        assert transport._stderr_task.done()


@async_test
async def test_transport_uses_reviewed_environment_plus_explicit_test_injection(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from agentdeck.runtime.acp import AcpTransport

    marker = tmp_path / "env.json"
    monkeypatch.setenv("ACP_UNREVIEWED_SECRET", "must-not-reach-child")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "reviewed-provider-value")
    client, _ = _transport_client()
    transport = AcpTransport(
        (sys.executable, str(FAKE_AGENT), "record_env", str(marker)), tmp_path, client,
        test_env={"ACP_TEST_SENTINEL": "harmless-sentinel"},
    )
    await transport.initialize()
    await transport.close()
    import json
    recorded = json.loads(marker.read_text())
    assert recorded == {
        "sentinel": "harmless-sentinel", "unreviewed": None,
        "anthropic": "reviewed-provider-value",
    }
    assert "reviewed-provider-value" not in repr(transport)
    assert "harmless-sentinel" not in repr(transport.stderr_summary)
    assert transport.stderr_summary["present"] is True


@async_test
async def test_close_is_shielded_concurrent_retryable_and_bounds_stderr_descendant(
    tmp_path: Path
) -> None:
    from agentdeck.runtime.acp import AcpTransport

    client, _ = _transport_client()
    transport = AcpTransport(
        (sys.executable, str(FAKE_AGENT), "descendant_stderr"), tmp_path, client,
        cancel_timeout=0.05, close_grace=0.05, terminate_grace=0.05, kill_grace=0.1,
    )
    await transport.initialize()
    child_pid = transport.child_pid

    original_close = transport._connection.close
    entered = asyncio.Event()
    release = asyncio.Event()
    close_calls = 0

    async def resistant_close() -> None:
        nonlocal close_calls
        close_calls += 1
        if close_calls > 1:
            await original_close()
            return
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        await original_close()

    transport._connection.close = resistant_close
    first = asyncio.create_task(transport.close())
    await entered.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert transport.lifecycle_state == "cleaning"
    await asyncio.shield(transport._cleanup_task)
    assert transport.lifecycle_state == "closed"
    await asyncio.gather(transport.close(), transport.close())
    assert transport.lifecycle_state == "closed"
    assert transport._cleanup_task.done()
    assert transport._stderr_task.done()
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)
    release.set()
    await asyncio.sleep(0)


@async_test
async def test_close_caller_cancellation_during_process_wait_does_not_stop_cleanup(
    tmp_path: Path
) -> None:
    from agentdeck.runtime.acp import AcpTransport

    marker = tmp_path / "later-cancel"
    client, _ = _transport_client()
    transport = AcpTransport(
        (sys.executable, str(FAKE_AGENT), "cancel_or_ignore_terminate", str(marker)),
        tmp_path, client, close_grace=0.05, terminate_grace=0.05, kill_grace=0.1,
    )
    await transport.initialize()
    child_pid = transport.child_pid
    entered_wait = asyncio.Event()
    original_wait = transport._process.wait

    async def observed_wait() -> int:
        entered_wait.set()
        return await original_wait()

    transport._process.wait = observed_wait
    closing = asyncio.create_task(transport.close())
    await entered_wait.wait()
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    await transport.close()
    assert transport.lifecycle_state == "closed"
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


@async_test
async def test_transport_bounds_and_redacts_stderr(tmp_path: Path, monkeypatch: Any) -> None:
    from agentdeck.runtime.acp import MAX_ACP_STDERR_BYTES, AcpTransport

    secret = "transport-secret-value"
    monkeypatch.setenv("ACP_TEST_SECRET", secret)
    client, _ = _transport_client()
    transport = AcpTransport(
        (sys.executable, str(FAKE_AGENT), "stderr_noise"), tmp_path, client
    )
    await transport.initialize()
    await transport.close()
    diagnostic = transport.stderr_summary
    assert diagnostic["byte_count"] <= MAX_ACP_STDERR_BYTES
    assert secret not in repr(diagnostic)
    assert diagnostic["present"] is True
    assert isinstance(diagnostic["sha256"], str)
    assert "ACP_TEST_SECRET" not in repr(transport)


@async_test
async def test_transport_cancellation_sends_cancel_and_kills_only_child(tmp_path: Path) -> None:
    from agentdeck.runtime.acp import AcpTransport

    marker = tmp_path / "cancelled"
    client, _ = _transport_client()
    transport = AcpTransport(
        (sys.executable, str(FAKE_AGENT), "cancel_or_ignore_terminate", str(marker)),
        tmp_path,
        client,
        request_timeout=5,
        terminate_grace=0.05,
        kill_grace=0.2,
    )
    await transport.initialize()
    session = await transport.new_session()
    prompt = asyncio.create_task(transport.prompt(session.native_session_id, "wait"))
    await asyncio.sleep(0.05)
    prompt.cancel()
    with pytest.raises(asyncio.CancelledError):
        await prompt
    child_pid = transport.child_pid
    await transport.close()
    assert marker.read_text() == "cancelled"
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)
