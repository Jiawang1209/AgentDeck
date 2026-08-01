# Frontdesk Contract (`frontdesk` via `project-view/v1`)

Discovery entrypoint: `agentdeck contract frontdesk` (`--example` adds a stable
GUI-ready example). Source of truth for fields, example, payload and the
validator is `src/agentdeck/contracts.py` (`FRONTDESK_RESPONSE_FIELDS`,
`FRONTDESK_CONTROL_FIELDS`, `FRONTDESK_CLASSIFICATIONS`, `frontdesk_example()`,
`frontdesk_contract_payload()`, `validate_frontdesk_contract()`). The
classification itself lives in the pure module `src/agentdeck/frontdesk.py`
(`FRONTDESK_ROUTES`, `FRONTDESK_CONFIDENCES`, `FRONTDESK_ROUTE_SAFETY`,
`classify_frontdesk()`), which has zero IO and imports nothing from
`cli`/`state`/`config`.

The frontdesk is the human-facing intake layer of the G1 north star: it tidies a
raw request into an intake summary, classifies it into candidate routes, and
hands back explicit next-step commands — without ever calling a Leader provider.
Frozen design:
`docs/superpowers/specs/2026-08-01-frontdesk-multiroute-design.md`.

## Two Entrypoints, One Classification

| Entrypoint | Writes | Use |
| --- | --- | --- |
| `agentdeck frontdesk --message <text>` | nothing at all | GUI/TUI/scripted routing preview |
| `agentdeck leader chat --message "frontdesk <goal>"` | one chat turn + one `leader_chat_turn` event | the auditable conversational path |

Both surfaces derive from the same card, so their `classification`, `route`,
`candidates`, `next_command`, and `controls` are identical for the same message.
Only the chat entrypoint records history; the standalone command is the
read-only twin, in the same family as `agentdeck controls` and
`agentdeck continue`. `agentdeck frontdesk` does not even load the project
config, so it cannot write `.agentdeck/state/state.json`, append to the events
journal, spawn, capture, or authenticate anything.

## Response Shape

`agentdeck frontdesk --message <text>` returns `ok`, then every field of the
leader-chat `frontdesk_card` (see `docs/contracts/leader-chat-schema.md`), then
`count` and `chat_command`:

```json
{
  "ok": true,
  "mode": "frontdesk",
  "title": "Frontdesk intake",
  "summary": "Frontdesk routed the request without calling a planning provider.",
  "user_message": "frontdesk 开始运行 冒烟测试",
  "intake_summary": "开始运行 冒烟测试",
  "classification": "planning_candidate",
  "next_command": "agentdeck leader plan --task '开始运行 冒烟测试'",
  "controls": [
    {"kind": "inspect", "label": "Open Leader help", "command": "agentdeck leader chat --message \"帮助\"", "safety": "inspect", "enabled": true, "blocker": null},
    {"kind": "plan", "label": "Create Leader plan", "command": "agentdeck leader plan --task '开始运行 冒烟测试'", "safety": "plan_only", "enabled": true, "blocker": null},
    {"kind": "route", "label": "Start approval-gated run", "command": "agentdeck run --task '开始运行 冒烟测试'", "safety": "approval_gated", "enabled": true, "blocker": null},
    {"kind": "route", "label": "Create Leader plan", "command": "agentdeck leader plan --task '开始运行 冒烟测试'", "safety": "plan_only", "enabled": true, "blocker": null}
  ],
  "candidates": [
    {"route": "run", "label": "Start approval-gated run", "command": "agentdeck run --task '开始运行 冒烟测试'", "confidence": "high", "rationale": "matched \"开始运行\""},
    {"route": "plan", "label": "Create Leader plan", "command": "agentdeck leader plan --task '开始运行 冒烟测试'", "confidence": "medium", "rationale": "goal text present without an explicit planning keyword"}
  ],
  "route": "run",
  "count": 2,
  "chat_command": "agentdeck leader chat --message \"frontdesk <goal>\""
}
```

## Routes

`routes` is a closed enum; a client may render exactly these and no others:

| Route | Command | Safety | Triggered by |
| --- | --- | --- | --- |
| `plan` | `agentdeck leader plan --task <goal>` | `plan_only` | extractable goal text |
| `run` | `agentdeck run --task <goal>` | `approval_gated` | a start-tone marker **and** extractable goal text |
| `status` | `agentdeck status` | `inspect` | 状态 / 进度 / 看板 / status / progress / 现在怎么样 |
| `help` | `agentdeck leader chat --message "帮助"` | `inspect` | 帮助 / help / 能做什么, **or** the fallback when nothing else matches |
| `skill` | `agentdeck skills list` | `inspect` | 技能 / skill / 工作流 / workflow |
| `memory` | `agentdeck memory suggestions` | `inspect` | 记忆 / memory / 记住 / 别忘了 |

The declared order above is also the deterministic tie-break: candidates are
sorted by confidence descending, and equal-confidence candidates keep this
order. `route` is the top candidate's route. Every route appears at most once.

## Confidence

`confidences` is a closed three-level enum, not a model score:

- `high` — a strong wording marker matched (e.g. `状态`, `开始运行`, `skill`).
- `medium` — only a weak cue matched (e.g. `跑一下`, `现在怎么样`, `工作流`), or
  the `plan` route fired on goal text alone with no planning keyword.
- `low` — the `help` fallback, used only when no route matched at all.

`rationale` is either `matched "<token>"` — where `<token>` is a marker that
literally occurs in the message — or one of the two fixed explanations
(`goal text present without an explicit planning keyword`,
`no route keyword matched`). It never speculates about intent.

## Backward Compatibility

`classification` remains the original two-level value (`planning_candidate` when
goal text exists, otherwise `needs_goal`) and `next_command` remains the
original goal-text rule (`agentdeck leader plan --task <goal>`, falling back to
`agentdeck leader chat --message "帮助"`). Multi-route classification is
**additive**: `route` may well differ from the route implied by `next_command`
(the example above routes to `run` while `next_command` still points at
planning). GUI clients that only knew the eight original card fields keep
working unchanged; clients that want routing should read `candidates`/`route`.

`controls[]` likewise keeps its original `inspect` and `plan` entries first, in
place, and then appends one `kind=route` control per candidate. Each route
control reuses the safety of the command it points at. A control whose command
still carries a `<placeholder>` ships `enabled=false` with a blocker, and
`validate_frontdesk_contract()` rejects an enabled placeholder command.

## Safety Boundary

- Fully read-only. Classification is pure text matching; the command writes no
  state, records no chat turn, appends no event, and creates no
  plan/action/approval/message/job/inbox.
- Never calls a Leader provider. That is the G1 acceptance criterion verbatim:
  the frontdesk must be deterministic and zero-cost.
- Never reads or writes tmux, never inspects panes, never sends input.
- Candidate commands are **suggestion text, not authorization**. The frontdesk
  never executes one; a human still runs it explicitly, and every downstream
  command keeps its own approval, runtime, and confirmation gates.
- `classification`, `route`, and `confidence` are routing display only. They
  change no gate, grant no permission, and must never be treated as readiness.
