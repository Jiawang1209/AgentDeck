"""Real subprocess daemon fixture; production CLI wiring arrives in Task 6."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from agentdeck import __version__
from agentdeck.daemon.lifecycle import (
    acquire_daemon_ownership,
    cleanup_daemon_endpoint,
)
from agentdeck.daemon.server import DaemonServer
from agentdeck.models import PROJECT_VIEW_SCHEMA_VERSION


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--lifetime", type=float, default=0.5)
    args = parser.parse_args()
    owner = acquire_daemon_ownership(
        args.project,
        start_nonce="fake-daemon-server",
        health_probe=lambda metadata: metadata,
        wait_timeout_seconds=1,
        poll_interval_seconds=0.01,
    )
    if owner.role != "owner":
        owner.release()
        return
    server = DaemonServer(
        endpoint=owner.endpoint.socket_path,
        instance_id=owner.instance_id,
        project_root_hash=owner.project_root_hash,
        start_nonce_hash=owner.start_nonce_hash,
        daemon_version=__version__,
        project_view_schema_version=PROJECT_VIEW_SCHEMA_VERSION,
        max_frame_bytes=4096,
        allowed_methods={"handshake", "status", "subscribe", "mission.pause"},
        status_provider=lambda: {"mode": "daemon_status", "state": "ready"},
    )
    try:
        await server.start()
        await asyncio.sleep(args.lifetime)
    finally:
        await server.close()
        cleanup_daemon_endpoint(owner)


if __name__ == "__main__":
    asyncio.run(main())
