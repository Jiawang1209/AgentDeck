"""Read-only Product command for a tmux human Observer pane."""

from __future__ import annotations

import argparse
from pathlib import Path

from agentdeck.adapters.observer_ipc import ObserverIPCError, UnixObserverClient
from agentdeck.adapters.tmux_observer import TmuxObservationSink
from agentdeck.ports.observer import ObserverCursor, ObserverSubscription
from agentdeck.product.observer import ObserverError, ObserverStream


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise ValueError("observer_command_invalid")


def _arguments(argv: list[str]) -> argparse.Namespace:
    parser = _ArgumentParser(prog="agentdeck observer", add_help=False)
    parser.add_argument("--mode")
    parser.add_argument("--read-only", action="store_true")
    parser.add_argument("--project-id")
    parser.add_argument("--session-id")
    parser.add_argument("--instance-id")
    parser.add_argument("--view")
    values = parser.parse_args(argv)
    if values.mode != "event-subscription" or values.read_only is not True:
        raise ValueError("observer_command_invalid")
    worker = (
        values.session_id is not None
        and values.instance_id is not None
        and values.view is None
    )
    overview = (
        values.view == "overview"
        and values.session_id is None
        and values.instance_id is None
    )
    if values.project_id is None or not (worker or overview):
        raise ValueError("observer_command_invalid")
    if overview:
        values.session_id, values.instance_id = "ses_overview", "agt_overview"
    ObserverSubscription(values.project_id, values.session_id, values.instance_id)
    return values


class _AcknowledgementProxy:
    def __init__(
        self, client: UnixObserverClient, prior: ObserverCursor | None,
    ) -> None:
        self._client, self._prior = client, prior

    def load(self) -> ObserverCursor | None:
        return self._prior

    def acknowledge(self, cursor: ObserverCursor) -> None:
        self._client.acknowledge(cursor)
        self._prior = cursor


def run_observer_command(
    argv: list[str], *, project_root: Path | None = None,
    connect=UnixObserverClient.connect, writer=print,
    max_events: int | None = None,
) -> int:
    """Connect, render decoded events, and acknowledge only after sink emission."""

    client = None
    try:
        values = _arguments(argv)
        if max_events is not None and (
            type(max_events) is not int or not 1 <= max_events <= 256
        ):
            raise ValueError("observer_command_invalid")
        root = (project_root or Path.cwd()).resolve(strict=True)
        subscription = ObserverSubscription(
            values.project_id, values.session_id, values.instance_id,
        )
        client = connect(project_root=root, subscription=subscription)
        binding, prior = client.receive_binding()
        proxy = _AcknowledgementProxy(client, prior)
        stream = ObserverStream(
            subscription=binding, cursor_store=proxy,
            sink=TmuxObservationSink(writer=writer),
        )
        observed = 0
        while max_events is None or observed < max_events:
            event = client.receive_event()
            if event is None:
                break
            stream.render((event,))
            observed += 1
        return 0
    except (ObserverError, ObserverIPCError, TypeError, ValueError):
        return 1
    finally:
        if client is not None:
            client.close()


__all__ = ["run_observer_command"]
