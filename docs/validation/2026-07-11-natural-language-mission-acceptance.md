# Natural-language Mission acceptance — 2026-07-11

## Environment

- Implementation checkout: `codex/natural-language-mission`
- Acceptance project: `/Users/liuyue/Desktop/agentdeck-protocol-v2-phase0-acceptance`
- Python: conda environment `agentdeck`
- Leader: `codex-cli` / `gpt-5.5`
- Workers: `planner` / Codex and `reviewer` / Claude
- Runtime: project-isolated tmux socket
- Preflight: `agentdeck doctor` reported the configured Leader, both Worker CLIs, and tmux ready.

No token, email address, provider credential, or complete private terminal transcript is included here.

## User interaction exactly two messages

The successful fresh project contains exactly two Leader chat turns:

1. `让 Codex 和 Claude 一人一句接龙百家姓，共8轮`
2. `批准执行 mis_1d5c2a569173`

There was no third natural-language message, manual Worker task input, or extra Enter used to advance the workflow.

## Frozen Mission and selected Workers

- Mission: `mis_1d5c2a569173`
- Plan: `pln_c13709530632`
- Workflow run: `wfr_7d309ae9c507`
- Frozen step count: 8
- Plan hash remained unchanged between preview and execution.
- Selected Worker set was exactly `planner` and `reviewer`; `coder` was neither selected nor spawned.
- Preview was `pending_confirmation`, `can_start=true`, and exposed the exact confirmation command used for message 2.

## Eight-turn transcript

Only compact audited handoff summaries are reproduced:

| Step | Worker | Status | Summary |
| ---: | --- | --- | --- |
| 1 | planner | completed | 赵钱孙李 |
| 2 | reviewer | completed | 周吴郑王 |
| 3 | planner | completed | 冯陈褚卫 |
| 4 | reviewer | completed | 蒋沈韩杨 |
| 5 | planner | completed | 朱秦尤许 |
| 6 | reviewer | completed | 何吕施张 |
| 7 | planner | completed | 孔曹严华 |
| 8 | reviewer | completed | 金魏陶姜 |

Every turn carried a compact structured handoff with `status=completed`, matching step and agent identity, a summary, verification, risks, next steps, and a reply trace command.

## Public status and ProjectView agreement

Fresh read-only checks agreed on the same identity and terminal state:

- `agentdeck mission status --mission-id mis_1d5c2a569173`: `completed`, `current_step=8`, workflow `wfr_7d309ae9c507`.
- `agentdeck status`: latest Mission `mis_1d5c2a569173`, `completed`, `current_step=8`, same workflow ID.
- `agentdeck workbench`: `mission_card` reported the same Mission, plan, workflow, status, and current step.
- The confirmation result contained eight completed turns in the frozen alternating order.

## Audit counts and lineage

The successful project event ledger contains:

- `mission_confirmed`: 1
- `mission_worker_ready`: 2
- `workflow_step_completed`: 8
- `mission_completed`: 1
- `leader_chat_turn`: 2

The eight step-completion event IDs, in order, are:

1. `evt_4f80ca77d234`
2. `evt_6fa1a5cebc50`
3. `evt_32043aacdd24`
4. `evt_afdcee66a0b4`
5. `evt_f2261527957c`
6. `evt_f62451a9efd0`
7. `evt_bdb84d50854f`
8. `evt_3d76a39c25fd`

The Mission confirmation event is `evt_faf26ddfec8f`; completion is `evt_9110dff7f35d`. Each compact turn exposes its own `agentdeck trace --id rep_...` lineage control without copying a private full transcript into this report.

## First-run trust boundary

The first isolated attempt correctly stopped at readiness because both CLIs requested directory trust. Trust was handled only as setup: the trust choices were accepted without entering a Worker task. That failed project was discarded and a fresh project was used for the successful two-message acceptance.

Directory trust is intentionally not automated by AgentDeck. A human must review and accept it once; Mission execution must stop rather than type through an unknown setup screen.

## Failed attempts and defects converted to tests

After trust setup, two fresh attempts exposed readiness false negatives before any workflow step:

1. Codex 0.131 used a split bars-style context/usage footer; Claude release notes moved stable chrome beyond the common 40-line tail and did not always show an MCP warning.
2. Real tmux captures retained Codex box borders, while post-trust Claude omitted the `Accessing workspace:` label and retained only organization/path boxes plus the empty prompt and mode footer.

Both defects were reproduced as RED tests before production changes. The final classifier remains bounded and structural: Codex uses a 40-line current frame; Claude uses an 80-line current frame; exact ordered chrome, an empty prompt, and setup/fatal/current-input precedence remain required. Commits `41779f4e` and `343abf63` contain the two minimal fixes and their regression tests.

## Cleanup

- Every acceptance attempt used a newly initialized Git project at the required path.
- Failed tmux servers were terminated before rebuilding the project.
- Runtime state stayed under the acceptance project's ignored `.agentdeck/`; no acceptance state was written into the implementation checkout.
- Sanitized failed-attempt evidence was kept outside the repository while diagnosing and was not committed.
- The final acceptance project is retained temporarily so the recorded public JSON surfaces can be rechecked; it is not a product fixture or source dependency.

## Verdict

**PASS.** The natural-language Mission baseline completed from exactly two user messages, executed eight ordered Codex/Claude turns, reached `completed/current_step=8`, emitted exactly one confirmation and eight step-completion audit events, and presented consistent Mission identity and state through the run response, Mission status, ProjectView status, and workbench.
