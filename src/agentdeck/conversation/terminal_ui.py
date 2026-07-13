from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from typing import TextIO

from .session import ConversationSession


MAX_RENDER_BYTES = 64 * 1024
DOUBLE_CTRL_C_SECONDS = 1.5


class TerminalConversationUI:
    def __init__(
        self,
        session: ConversationSession,
        *,
        input_fn: Callable[[str], str] = input,
        stdout: TextIO = sys.stdout,
        stderr: TextIO = sys.stderr,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.session = session
        self.input_fn = input_fn
        self.stdout = stdout
        self.stderr = stderr
        self.monotonic = monotonic
        self._last_idle_interrupt: float | None = None

    @staticmethod
    def _bounded_json(payload: object) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if len(encoded) <= MAX_RENDER_BYTES:
            return encoded.decode("utf-8")
        return json.dumps(
            {
                "truncated": True,
                "byte_count": len(encoded),
                "message": "response exceeds terminal render limit; use deterministic inspect commands",
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    def run(self) -> int:
        while True:
            try:
                text = self.input_fn("agentdeck> ")
                self._last_idle_interrupt = None
            except EOFError:
                self.stderr.write("\nSession closed (EOF).\n")
                return 0
            except KeyboardInterrupt:
                now = self.monotonic()
                if (
                    self._last_idle_interrupt is not None
                    and now - self._last_idle_interrupt <= DOUBLE_CTRL_C_SECONDS
                ):
                    self.stderr.write("\nSession closed.\n")
                    return 0
                self._last_idle_interrupt = now
                self.stderr.write("\nInput cleared. Press Ctrl-C again to exit.\n")
                continue
            try:
                response = self.session.handle(text)
            except KeyboardInterrupt:
                self.session.cancel_current_turn()
                self.stderr.write("\nTurn cancelled; session remains open.\n")
                continue
            if response.kind == "exit":
                self.stderr.write("Session closed.\n")
                return 0
            self.stdout.write(self._bounded_json(response.payload) + "\n")
            self.stdout.flush()
