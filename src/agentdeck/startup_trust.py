"""Classify whether an agent's CLI has already trusted the project directory.

Both codex and Claude Code ask, the first time they start in a directory, "do
you trust the contents of this directory?" and then wait. The pane exists, tmux
reports it running, and the agent has never reached its REPL. Round 1 spent
three human keypresses here before a walk-away segment could even begin, and
`agent ready` reported `all_running: True, 3/3` the whole time, with
`next_command` pointing at `approval dispatch-ready` -- a command that could not
possibly have worked. The prompt is not the defect; claiming readiness over it
is.

This module does not press that prompt and nothing built on it ever should.
Trusting a directory is what lets project-local config, hooks and exec policies
load: it is a decision about which code runs on the human's machine, and
`CLAUDE.md` freezes it as human setup that worker input and a silent Enter must
never bypass. What it *can* do is say so **before** the panes are spawned, from
the CLI's own on-disk record -- a file read, never a tmux read, so the surfaces
that consume it keep their "never inspects tmux" contract intact.

Three states, deliberately not a boolean:

| state | meaning |
| --- | --- |
| `trusted` | that CLI's own record says this directory is already trusted |
| `untrusted` | recognized CLI, and this directory is absent from its record |
| `unknown` | we could not tell: unrecognized provider, or unreadable record |

`unknown` must never render as `trusted`, for the usual reason -- "could not
check" and "checked, fine" read identically once they collapse into one
boolean, and only one of them is a fact. It must not collapse into `untrusted`
either: telling someone to go press a box that is not there spends the one
thing a preflight has, which is being believed.

Pure module: zero IO, zero LLM, no tmux, no subprocess; it does not import
`cli`, `state` or `config`. The caller resolves each CLI's record and passes
the already-resolved answer in as `recorded`.
"""
from __future__ import annotations

from typing import Any

TRUST_STATES = ("trusted", "untrusted", "unknown")

TRUST_UNKNOWN_REASONS = (
    # 不是我们认得的 CLI(自定义 command),连它有没有 trust 这个概念都不知道。
    "provider_not_recognized",
    # 认得的 CLI,但它的记录这次读不出来(文件缺失/损坏/结构变了)。
    "trust_state_unreadable",
)

# 我们知道 trust 记录存在哪的 provider。认不出来的一律 `unknown`——
# 这里**只增不猜**:多一个名字就要多一处真实的读取实现。
RECOGNIZED_TRUST_PROVIDERS = ("codex", "claude")


def classify_startup_trust(
    *, provider: str | None, recorded: bool | None
) -> dict[str, Any]:
    """该 provider 的 CLI 是否已信任本项目目录。

    `recorded` 三值:`True` 记录里有且已信任、`False` 记录可读但没有这个目录、
    `None` 读不出来。最后一档**绝不能**塌进任何一边——塌进 `trusted` 会让人
    在一个注定冻住的 pane 上开跑,塌进 `untrusted` 会让人被指去按一道并不
    存在的框,两种错法都会让这个预检失去信用。
    """
    name = (provider or "").strip().lower()
    if name not in RECOGNIZED_TRUST_PROVIDERS:
        return {
            "provider": provider,
            "state": "unknown",
            "reason": "provider_not_recognized",
        }
    if recorded is None:
        return {
            "provider": provider,
            "state": "unknown",
            "reason": "trust_state_unreadable",
        }
    return {
        "provider": provider,
        "state": "trusted" if recorded else "untrusted",
        "reason": None,
    }
