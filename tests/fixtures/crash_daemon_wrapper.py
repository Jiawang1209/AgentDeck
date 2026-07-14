"""Launch the real daemon and stop it at one test-only durable boundary."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import sys

from agentdeck import cli
from agentdeck.config import load_config
from agentdeck.daemon.service import DaemonTransitionEffects
from agentdeck.runtime.tmux import TmuxBackend
from agentdeck.state import StateStore


def install(point: str, marker: Path, root: Path) -> None:
    def stop_at_boundary() -> None:
        if marker.exists():
            return
        state = StateStore(root).load()
        payload = {
            "crash_point": point,
            "attempt_states": [item.get("state") for item in state.get("mission_attempts", [])],
            "reply_states": [item.get("state") for item in state.get("mission_worker_replies", [])],
            "handoff_states": [item.get("state") for item in state.get("mission_handoffs", [])],
            "permission_states": [item.get("status") for item in state.get("permission_requests", [])],
        }
        temporary = marker.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        os.replace(temporary, marker)
        os.kill(os.getpid(), signal.SIGSTOP)

    original_apply = DaemonTransitionEffects.apply

    def apply(self, decision):
        if point == "observe_recovery" and decision.kind != "idle":
            stop_at_boundary()
        if point == "before_prepare" and decision.kind == "prepare_dispatch":
            stop_at_boundary()
        result = original_apply(self, decision)
        if point == "after_prepare_before_dispatch" and decision.kind == "prepare_dispatch":
            stop_at_boundary()
        return result

    DaemonTransitionEffects.apply = apply

    def wrap_after(method_name: str, predicate) -> None:
        original = getattr(StateStore, method_name)

        def wrapped(self, *args, **kwargs):
            result = original(self, *args, **kwargs)
            if predicate(result, args, kwargs):
                stop_at_boundary()
            return result

        setattr(StateStore, method_name, wrapped)

    if point == "after_receipt_before_reply":
        wrap_after("record_mission_attempt_submitted", lambda *_: True)
    elif point == "after_reply_before_handoff":
        wrap_after("record_tmux_mission_attempt_completion", lambda *_: True)
    elif point == "after_handoff_before_next_dispatch":
        wrap_after(
            "record_mission_handoff_evidence",
            lambda result, _args, _kwargs: result.get("state") == "recorded",
        )
    elif point == "permission_pending":
        wrap_after("record_mission_acp_permission_pending", lambda *_: True)
    elif point == "outbox_flush":
        wrap_after(
            "flush_protocol_event_outbox",
            lambda result, _args, _kwargs: isinstance(result, int) and result > 0,
        )
    elif point == "shutdown":
        wrap_after(
            "force_stop_with_governance_preview",
            lambda result, _args, _kwargs: result.get("state") == "stopping",
        )

    if point == "after_dispatch_before_receipt":
        original_send = TmuxBackend.send_input

        def send_input(self, config, pane_id, text):
            result = original_send(self, config, pane_id, text)
            stop_at_boundary()
            return result

        TmuxBackend.send_input = send_input


def main() -> int:
    point, marker, root = sys.argv[1:]
    project = Path(root).resolve()
    install(point, Path(marker), project)
    return cli._run_daemon_event_loop(
        cli._serve_daemon(project, load_config(project), StateStore(project))
    )


if __name__ == "__main__":
    raise SystemExit(main())
