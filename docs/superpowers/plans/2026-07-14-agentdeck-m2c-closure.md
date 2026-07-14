# AgentDeck Phase 3 M2c Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the final Phase 3 M2c gap by making CLI Leader plans authority-aware and schema-constrained, then prove one real four-stage Claude-ACP/Codex-tmux Mission with recovery, permission, visibility, takeover, handoff, audit, and cleanup evidence.

**Architecture:** Add one provider-neutral Leader plan schema and typed validation layer, then adapt Codex and Claude CLI subprocesses to their native structured-output surfaces without changing AgentDeck's Mission authority. Carry compact generation provenance through the existing Gateway → Candidate → plan record → ProjectView path, retain fail-closed diagnostics and no fallback, and reuse the existing authoritative daemon/governance primitives for deterministic and live four-stage acceptance.

**Tech Stack:** Python 3.12 standard library, dataclasses, subprocess, JSON/JSONL, SHA-256, pytest, official ACP Python SDK, tmux, Codex CLI, Claude CLI, conda environment `agentdeck`.

---

## Scope discipline

Implement tasks strictly in order. Run every command in the `agentdeck` conda
environment. Every semantic task uses RED → minimal GREEN → focused regression
→ broader regression → `HISTORY.md` update → local commit. Do not push, merge,
install packages, change authentication, or stage the user-owned `.omc/` and
`AGENTS.md` paths.

Before Task 1, use `superpowers:using-git-worktrees` to create an isolated
`codex/m2c-closure` worktree from the reviewed plan commit. Reinstall that
worktree with `conda run -n agentdeck python -m pip install -e .` inside the
worktree and verify `python -c 'import agentdeck; print(agentdeck.__file__)'`
resolves into it. Do not implement directly in the dirty `main` checkout.

The authoritative design is:

```text
docs/superpowers/specs/2026-07-14-agentdeck-m2c-closure-design.md
```

Do not mark M2c PASS from schema tests alone. M2c remains BLOCKED until the real
four-stage gate and cleanup audit pass. Do not begin M3 in this plan.

## Locked file map

### New production file

- `src/agentdeck/providers/plan_schema.py` — canonical schema version, frozen
  authority validation, schema construction/hash, typed plan validation codes,
  and compact generation-provenance normalization.

### Existing production files

- `src/agentdeck/providers/base.py` — `LeaderPlanRequest`,
  `LeaderPlanResult`, provider protocol, and backward-compatible validation
  import/export.
- `src/agentdeck/orchestration/leader.py` — pass frozen authority/deadline to
  providers and expose `plan_result()` while preserving `plan()`.
- `src/agentdeck/providers/cli_subprocess.py` — native Codex/Claude schema
  commands, secure bounded result extraction, one regeneration, and typed safe
  failures.
- `src/agentdeck/providers/openai_compatible.py` — unchanged HTTP behavior but
  compatible request/result wrapping.
- `src/agentdeck/providers/fake.py` — unchanged deterministic plan semantics but
  compatible result wrapping.
- `src/agentdeck/providers/__init__.py` — export new public internal types.
- `src/agentdeck/conversation/leader_gateway.py` — capability probe injection,
  authority propagation, result/provenance handling, and safe diagnostics.
- `src/agentdeck/conversation/session.py` — persist compact failure facts and
  preserve zero domain effects.
- `src/agentdeck/mission_orchestration.py` — carry `leader_generation` on
  `LeaderMissionCandidate` and into the immutable plan record.
- `src/agentdeck/state.py` — validate/store/project compact plan generation
  provenance.
- `src/agentdeck/contracts.py` — ProjectView plan item fields, examples, and
  validators for `leader_generation`.

### Tests and fixtures

- Create `tests/test_leader_plan_schema.py` — pure schema/authority/diagnostic
  unit tests.
- Create `tests/test_cli_structured_output.py` — native Codex/Claude command,
  result-file/envelope, retry/deadline, cleanup, and redaction tests.
- Modify `tests/test_provider_openai_compatible.py` — legacy provider contract
  compatibility.
- Modify `tests/test_conversation_leader_gateway.py` — authority/result/probe
  propagation and no-fallback tests.
- Modify `tests/test_conversation_leader_diagnostics.py` — durable diagnostic
  shape and zero-effect assertions.
- Modify `tests/test_conversation_session.py` — response/event compatibility.
- Modify `tests/test_mission_orchestration.py` — candidate/plan provenance.
- Modify `tests/test_contracts.py` — ProjectView field/example/strict validation.
- Modify `tests/test_daemon_acceptance.py` — deterministic real-process
  four-stage acceptance using controlled adapters.
- Modify `tests/fixtures/fake_acp_agent.py` — deterministic M2c ACP Worker mode.
- Create `tests/test_m2c_live_acceptance.py` — opt-in real Codex/Claude M2c gate.

### Contracts and docs

- Modify `docs/contracts/project-view-schema.md`.
- Modify `docs/contracts/leader-backend-schema.md` if capability labels change.
- Create `docs/validation/phase3-m2c-live-acceptance-sop.md`.
- Modify `docs/validation/2026-07-13-phase3-m2-project-daemon.md` only after a
  real result exists.
- Modify `docs/roadmap/product-north-star.md` only after real PASS/BLOCKED
  evidence exists.
- Modify `README.md` and `README.zh-CN.md` only after user-visible behavior is
  proven.
- Modify `docs/handoff/current-development-state.md` at each milestone.
- Modify `HISTORY.md` in every commit.

## Locked type and payload names

Use these names consistently throughout the plan:

```python
LEADER_PLAN_SCHEMA_VERSION = "leader-plan/v1"
LEADER_CONSTRAINT_MODES = frozenset(
    {"local", "json_object", "prompt_only", "native_json_schema"}
)
LEADER_PLAN_DIAGNOSTIC_CODES = frozenset(
    {
        "missing_required_field",
        "invalid_top_level_type",
        "invalid_string_field",
        "invalid_step_type",
        "invalid_step_count",
        "invalid_step_numbering",
        "unknown_agent",
        "role_mismatch",
        "approval_not_required",
        "invalid_output_envelope",
        "native_schema_unavailable",
        "authority_invalid",
    }
)
```

`leader_generation` has this exact compact shape:

```json
{
  "provider": "codex-cli",
  "model": "gpt-5.5",
  "constraint_mode": "native_json_schema",
  "schema_version": "leader-plan/v1",
  "schema_hash": "sha256:<64 lowercase hex>",
  "attempt_count": 1,
  "regeneration_used": false,
  "selected_agent_ids": ["claude-worker", "codex-worker"],
  "step_count": 4
}
```

The field is provenance, not part of `canonical_workflow_plan_hash()` and not
execution authority. Old plans project a deterministic legacy-compatible
summary using their existing provider/model/step count, `constraint_mode` from
their provider class, nullable schema version/hash, `attempt_count=1`,
`regeneration_used=false`, and an empty selected-agent list when the historical
selection cannot be proven.

### Task 1: Canonical authority-aware Leader plan schema

**Files:**
- Create: `src/agentdeck/providers/plan_schema.py`
- Modify: `src/agentdeck/providers/base.py:9-122`
- Create: `tests/test_leader_plan_schema.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write the failing schema construction tests**

Create `tests/test_leader_plan_schema.py` with a project helper and these first
tests:

```python
from dataclasses import replace
import hashlib
import json

import pytest

from agentdeck.config import load_config, write_default_config
from agentdeck.providers import LeaderPlanRequest
from agentdeck.providers.plan_schema import (
    LEADER_PLAN_SCHEMA_VERSION,
    ProviderPlanValidationError,
    build_leader_plan_schema,
    canonical_leader_plan_schema_hash,
    validate_provider_plan_schema,
)


def _request(tmp_path, *, selected=("planner", "reviewer"), step_count=4):
    (tmp_path / ".git").mkdir()
    write_default_config(tmp_path)
    config = load_config(tmp_path)
    return LeaderPlanRequest(
        task="four stage mission",
        config=config,
        model="gpt-test",
        selected_agent_ids=selected,
        step_count=step_count,
        timeout_seconds=180,
    )


@pytest.fixture
def valid_plan():
    phases = ("implementation", "review", "revision", "acceptance")
    agents = (("planner", "planning"), ("reviewer", "review"))
    return {
        "goal": "complete four stage mission",
        "summary": "strict serial handoff",
        "steps": [
            {
                "step": index,
                "agent_id": agents[(index - 1) % 2][0],
                "role": agents[(index - 1) % 2][1],
                "task": phase,
                "risk": "requires review",
                "requires_approval": True,
            }
            for index, phase in enumerate(phases, start=1)
        ],
    }


def test_schema_is_bound_to_frozen_workers_and_step_count(tmp_path):
    schema = build_leader_plan_schema(_request(tmp_path))
    steps = schema["properties"]["steps"]
    item = steps["items"]
    assert schema["$id"] == LEADER_PLAN_SCHEMA_VERSION
    assert schema["additionalProperties"] is False
    assert steps["minItems"] == steps["maxItems"] == 4
    assert item["properties"]["agent_id"]["enum"] == ["planner", "reviewer"]
    assert item["properties"]["requires_approval"] == {
        "type": "boolean", "const": True,
    }
    assert item["additionalProperties"] is False


def test_schema_hash_is_canonical_and_does_not_include_project_path(tmp_path):
    schema = build_leader_plan_schema(_request(tmp_path))
    encoded = json.dumps(
        schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    expected = f"sha256:{hashlib.sha256(encoded).hexdigest()}"
    assert canonical_leader_plan_schema_hash(schema) == expected
    assert str(tmp_path) not in encoded.decode()
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_leader_plan_schema.py -q
```

Expected: collection fails with
`ModuleNotFoundError: No module named 'agentdeck.providers.plan_schema'`.

- [ ] **Step 3: Add the request fields and canonical schema implementation**

In `src/agentdeck/providers/base.py`, extend the request without changing the
existing first four fields:

```python
@dataclass(frozen=True)
class LeaderPlanRequest:
    task: str
    config: ProjectConfig
    model: str | None = None
    skill_context: dict[str, Any] | None = None
    selected_agent_ids: tuple[str, ...] | None = None
    step_count: int | None = None
    timeout_seconds: int | None = None
```

Move the provider-plan field constants and validation implementation to
`plan_schema.py`, and re-export/import `validate_provider_plan_schema` from
`base.py` for compatibility. Implement frozen authority resolution exactly as:

```python
def leader_plan_authority(
    request: LeaderPlanRequest,
) -> tuple[tuple[str, ...], int]:
    selected = request.selected_agent_ids
    count = request.step_count
    if (selected is None) != (count is None):
        raise ProviderPlanValidationError("authority_invalid")
    if selected is None:
        selected = tuple(agent.agent_id for agent in request.config.agents)
        count = len(request.config.agents)
    configured = {agent.agent_id for agent in request.config.agents}
    if (
        type(count) is not int
        or count < 2
        or count > 64
        or len(selected) < 2
        or len(set(selected)) != len(selected)
        or any(type(item) is not str or not item or item not in configured for item in selected)
    ):
        raise ProviderPlanValidationError("authority_invalid")
    return selected, count
```

Build a Draft-07-compatible common-subset schema with `type`, `properties`,
`required`, `additionalProperties`, `minLength`, `minimum`, `enum`, `const`,
`minItems`, and `maxItems` only. Do not put roles, prompts, commands, paths, or
credentials into the schema; role equality remains local semantic validation.

- [ ] **Step 4: Add typed validation tests**

Append tests that call `validate_provider_plan_schema()` with the explicit
frozen selection and count. Assert these exact codes:

```python
@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda p: p.pop("goal"), "missing_required_field"),
        (lambda p: p.update({"goal": ""}), "invalid_string_field"),
        (lambda p: p.update({"steps": []}), "invalid_step_count"),
        (lambda p: p["steps"][0].update({"agent_id": "ghost"}), "unknown_agent"),
        (lambda p: p["steps"][0].update({"role": "wrong"}), "role_mismatch"),
        (lambda p: p["steps"][0].update({"requires_approval": False}), "approval_not_required"),
        (lambda p: p["steps"][1].update({"step": 1}), "invalid_step_numbering"),
    ],
)
def test_validator_raises_allowlisted_code(tmp_path, valid_plan, mutation, code):
    request = _request(tmp_path)
    mutation(valid_plan)
    with pytest.raises(ProviderPlanValidationError) as raised:
        validate_provider_plan_schema(
            valid_plan,
            config=request.config,
            selected_agent_ids=request.selected_agent_ids,
            step_count=request.step_count,
        )
    assert raised.value.code == code
```

`ProviderPlanValidationError` must subclass `RuntimeError`, retain the existing
human-readable messages for legacy API tests, and add only a validated `.code`.
Never put rejected values into `.code`.

- [ ] **Step 5: Run focused and compatibility tests**

Run:

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_leader_plan_schema.py \
         tests/test_provider_openai_compatible.py -q
```

Expected: PASS. Existing OpenAI-compatible exception strings remain unchanged.

- [ ] **Step 6: Update history and commit**

Add a `HISTORY.md` entry describing the canonical schema as generation
constraint rather than authorization, then commit:

```bash
git add src/agentdeck/providers/base.py \
  src/agentdeck/providers/plan_schema.py \
  tests/test_leader_plan_schema.py \
  tests/test_provider_openai_compatible.py HISTORY.md
git commit -m "Add authority-aware Leader plan schema"
```

### Task 2: Preserve legacy `plan()` while propagating authority and results

**Files:**
- Modify: `src/agentdeck/providers/base.py`
- Modify: `src/agentdeck/providers/__init__.py`
- Modify: `src/agentdeck/orchestration/leader.py:28-39`
- Modify: `src/agentdeck/conversation/leader_gateway.py:227-270`
- Modify: `tests/test_conversation_leader_gateway.py`
- Modify: `tests/test_provider_openai_compatible.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write failing authority propagation tests**

Add a recording provider to `tests/test_conversation_leader_gateway.py` and
assert the exact request:

```python
def test_gateway_passes_frozen_authority_and_deadline_to_provider(tmp_path):
    config = _config(tmp_path)
    config = replace(
        config,
        leader=replace(config.leader, provider="fake", model="fake-plan"),
    )
    seen = []

    class RecordingProvider:
        name = "fake"
        def plan(self, request):
            seen.append(request)
            return _plan()

    candidate = LeaderGateway(
        provider_factory=lambda _name: RecordingProvider()
    ).generate_mission(
        LeaderRequest(
            config, "mission", "structured mission task", 180, None,
            selected_agent_ids=("planner", "reviewer"), step_count=2,
        ),
        CancellationToken(),
    )
    assert seen[0].selected_agent_ids == ("planner", "reviewer")
    assert seen[0].step_count == 2
    assert seen[0].timeout_seconds == 180
    assert candidate.selected_agent_ids == seen[0].selected_agent_ids
    assert candidate.step_count == seen[0].step_count
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_conversation_leader_gateway.py::test_gateway_passes_frozen_authority_and_deadline_to_provider -q
```

Expected: FAIL because `LeaderOrchestrator.plan()` does not forward the new
request fields.

- [ ] **Step 3: Add `LeaderPlanResult` and backward-compatible orchestration**

Define in `base.py`:

```python
@dataclass(frozen=True)
class LeaderPlanResult:
    plan: dict[str, object]
    leader_generation: dict[str, object]
```

In `LeaderOrchestrator`, add `plan_result()` with keyword-only
`selected_agent_ids`, `step_count`, and `timeout_seconds`. It constructs one
`LeaderPlanRequest`, calls `provider.plan(request)`, validates the returned
dict, and wraps non-native providers with
`build_leader_generation_provenance()` from `plan_schema.py`:

```python
def plan(self, task, model=None, *, skill_context=None,
         selected_agent_ids=None, step_count=None, timeout_seconds=None):
    return self.plan_result(
        task, model, skill_context=skill_context,
        selected_agent_ids=selected_agent_ids,
        step_count=step_count, timeout_seconds=timeout_seconds,
    ).plan
```

If the provider exposes callable `plan_result`, call it; otherwise wrap its
existing `plan()` response. This keeps every direct provider test and API
caller returning a plain plan dict.

- [ ] **Step 4: Make Gateway use `plan_result()`**

Change only the non-ACP branch:

```python
result = LeaderOrchestrator(request.config, provider).plan_result(
    request.planning_task,
    request.config.leader.model,
    skill_context=request.skill_context,
    selected_agent_ids=request.selected_agent_ids,
    step_count=request.step_count,
    timeout_seconds=request.timeout_seconds,
)
plan = result.plan
```

Do not add retry or persistence in this task. Add the result provenance to the
candidate only after Task 6 defines the final candidate field.

- [ ] **Step 5: Run compatibility suites**

Run:

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_conversation_leader_gateway.py \
         tests/test_provider_openai_compatible.py \
         tests/test_mission_orchestration.py -q
```

Expected: PASS; direct `.plan()` callers still receive dicts.

- [ ] **Step 6: Update history and commit**

```bash
git add src/agentdeck/providers/base.py src/agentdeck/providers/__init__.py \
  src/agentdeck/orchestration/leader.py \
  src/agentdeck/conversation/leader_gateway.py \
  tests/test_conversation_leader_gateway.py \
  tests/test_provider_openai_compatible.py HISTORY.md
git commit -m "Propagate frozen Leader planning authority"
```

### Task 3: Native Codex CLI structured output with bounded files

**Files:**
- Modify: `src/agentdeck/providers/cli_subprocess.py`
- Create: `tests/test_cli_structured_output.py`
- Modify: `tests/test_provider_openai_compatible.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write the failing Codex command/result test**

Create `tests/test_cli_structured_output.py`. The fake subprocess must write to
the exact `--output-last-message` path rather than stdout:

```python
from pathlib import Path
import json
import subprocess

import pytest

from agentdeck.config import load_config, write_default_config
from agentdeck.providers import LeaderPlanRequest
from agentdeck.providers.cli_subprocess import ClaudeCliProvider, CodexCliProvider


@pytest.fixture
def valid_plan():
    phases = ("implementation", "review", "revision", "acceptance")
    agents = (("planner", "planning"), ("reviewer", "review"))
    return {
        "goal": "complete four stage mission",
        "summary": "strict serial handoff",
        "steps": [
            {
                "step": index,
                "agent_id": agents[(index - 1) % 2][0],
                "role": agents[(index - 1) % 2][1],
                "task": phase,
                "risk": "requires review",
                "requires_approval": True,
            }
            for index, phase in enumerate(phases, start=1)
        ],
    }


@pytest.fixture
def plan_request(tmp_path):
    (tmp_path / ".git").mkdir()
    write_default_config(tmp_path)
    return LeaderPlanRequest(
        task="four stage mission",
        config=load_config(tmp_path),
        model="gpt-test",
        selected_agent_ids=("planner", "reviewer"),
        step_count=4,
        timeout_seconds=180,
    )
```

Then add the command/result test:

```python
def test_codex_uses_native_schema_and_reads_only_last_message(
    tmp_path, monkeypatch, plan_request, valid_plan
):
    seen = {}
    def fake_run(command, **kwargs):
        seen["command"] = command
        schema_path = Path(command[command.index("--output-schema") + 1])
        result_path = Path(command[command.index("--output-last-message") + 1])
        seen["schema"] = json.loads(schema_path.read_text())
        result_path.write_text(json.dumps(valid_plan), encoding="utf-8")
        return subprocess.CompletedProcess(
            command, 0, stdout="SECRET_STATUS", stderr="SECRET_STDERR"
        )
    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    result = CodexCliProvider().plan_result(plan_request)

    assert result.plan["steps"] == valid_plan["steps"]
    assert "--output-schema" in seen["command"]
    assert "--output-last-message" in seen["command"]
    assert seen["schema"]["properties"]["steps"]["minItems"] == 4
    assert result.leader_generation["constraint_mode"] == "native_json_schema"
    assert result.leader_generation["attempt_count"] == 1
    assert "SECRET" not in repr(result)
```

Also record both temporary paths and assert they no longer exist after
`plan_result()` returns.

- [ ] **Step 2: Run the Codex test and verify RED**

Run:

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_cli_structured_output.py::test_codex_uses_native_schema_and_reads_only_last_message -q
```

Expected: FAIL because `CodexCliProvider` has no `plan_result()` and does not
pass native schema flags.

- [ ] **Step 3: Implement exclusive temporary resources and bounded reads**

Use `tempfile.TemporaryDirectory(prefix="agentdeck-leader-")`; write the schema
with mode `0o600`; leave the result path absent for Codex to create. After the
subprocess exits, open the result with `os.open(path, os.O_RDONLY | os.O_NOFOLLOW)`,
require `stat.S_ISREG(fstat.st_mode)`, reject `st_size > 2 MiB`, read at most
`MAX_CLI_LEADER_OUTPUT_BYTES + 1`, then JSON-decode. Never read stdout as the
Codex plan source.

Build the command deterministically:

```python
def _command_for_attempt(self, request, schema_path, result_path):
    command = self._command_for_request(request)
    insert_at = len(command) - 1
    return [
        *command[:insert_at],
        "--output-schema", str(schema_path),
        "--output-last-message", str(result_path),
        *command[insert_at:],
    ]
```

Keep argv as a list, `shell=False` implicitly, `cwd=request.config.root`,
`capture_output=True`, and discard stdout/stderr.

Keep the provider-facing compatibility method explicit:

```python
def plan(self, request: LeaderPlanRequest) -> dict[str, object]:
    return self.plan_result(request).plan
```

No native CLI success path may call the old stdout/fenced-JSON parser after
`plan_result()` has started.

- [ ] **Step 4: Add file-safety and cleanup tests**

Cover these exact cases:

- result absent after exit → `json_parse/invalid_output_envelope`;
- result is symlink → fail closed without reading target;
- result is directory/FIFO → fail closed;
- result exceeds 2 MiB → `oversize`;
- nonzero exit containing secrets → `nonzero` with no secret in exception;
- timeout containing secret output → `timeout` with no secret;
- temp directory removed after success, exception, and timeout;
- model remains in the command exactly once;
- `subprocess.run` receives no `shell` keyword set to true.

- [ ] **Step 5: Update legacy CLI tests**

In `tests/test_provider_openai_compatible.py`, change the Codex fake runner to
write the result file and assert the new flags. Keep fenced-stdout parsing tests
only for the non-native compatibility parser if it remains used by ACP/API;
do not let Codex native success fall back to fenced stdout.

- [ ] **Step 6: Run focused tests**

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_cli_structured_output.py \
         tests/test_provider_openai_compatible.py \
         tests/test_conversation_leader_diagnostics.py -q
```

Expected: PASS.

- [ ] **Step 7: Update history and commit**

```bash
git add src/agentdeck/providers/cli_subprocess.py \
  tests/test_cli_structured_output.py \
  tests/test_provider_openai_compatible.py \
  tests/test_conversation_leader_diagnostics.py HISTORY.md
git commit -m "Use native Codex Leader structured output"
```

### Task 4: Native Claude CLI envelope and capability preflight

**Files:**
- Modify: `src/agentdeck/providers/cli_subprocess.py`
- Modify: `src/agentdeck/conversation/leader_gateway.py`
- Modify: `tests/test_cli_structured_output.py`
- Modify: `tests/test_conversation_leader_gateway.py`
- Modify: `docs/contracts/leader-backend-schema.md`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write failing Claude envelope tests**

Use the current Claude JSON envelope contract: the structured result is the
top-level `structured_output` object when `--json-schema` is supplied.

```python
def test_claude_uses_json_schema_and_extracts_structured_output(
    monkeypatch, plan_request, valid_plan
):
    seen = {}
    def fake_run(command, **kwargs):
        seen["command"] = command
        raw_schema = command[command.index("--json-schema") + 1]
        seen["schema"] = json.loads(raw_schema)
        envelope = {
            "type": "result", "subtype": "success", "is_error": False,
            "result": "ignored text", "structured_output": valid_plan,
        }
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(envelope), stderr="")
    monkeypatch.setattr("agentdeck.providers.cli_subprocess.subprocess.run", fake_run)

    result = ClaudeCliProvider().plan_result(plan_request)

    assert "--json-schema" in seen["command"]
    assert seen["command"][seen["command"].index("--output-format") + 1] == "json"
    assert result.plan["goal"] == valid_plan["goal"]
    assert result.leader_generation["constraint_mode"] == "native_json_schema"
```

Add failures for missing `structured_output`, non-object output, `is_error=true`,
malformed envelope JSON, multiple JSON objects, and >2 MiB stdout. Every one
must return only allowlisted stage/code.

- [ ] **Step 2: Run the Claude tests and verify RED**

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_cli_structured_output.py -k claude -q
```

Expected: FAIL because Claude still uses text output and generic parsing.

- [ ] **Step 3: Implement provider-specific native hooks**

Keep shared orchestration in `CliLeaderProvider`, but override:

```python
class CodexCliProvider(CliLeaderProvider):
    native_schema_flag = "--output-schema"
    native_help_command = ("codex", "exec", "--help")

class ClaudeCliProvider(CliLeaderProvider):
    native_schema_flag = "--json-schema"
    native_help_command = ("claude", "--help")
```

Claude command must use `--output-format json`, serialize the canonical schema
with sorted compact JSON, and accept only a single JSON object whose
`type=result`, `subtype=success`, `is_error=false`, and `structured_output` is a
dict. Ignore but never persist `result`, cost, duration, and session id.

- [ ] **Step 4: Add a bounded read-only capability probe**

Implement:

```python
def cli_native_schema_ready(provider: str) -> tuple[bool, str | None]:
    selected = {"codex-cli": CodexCliProvider, "claude-cli": ClaudeCliProvider}.get(provider)
    if selected is None:
        return False, "Leader CLI native JSON schema is unsupported"
    adapter = selected()
    if shutil.which(adapter.command_name) is None:
        return False, "Leader CLI executable is not available"
    try:
        result = subprocess.run(
            list(adapter.native_help_command), text=True, capture_output=True,
            timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, "Leader CLI native JSON schema capability is unavailable"
    help_text = result.stdout + result.stderr
    if result.returncode != 0 or adapter.native_schema_flag not in help_text:
        return False, "Leader CLI native JSON schema capability is unavailable"
    return True, None
```

The probe discards help output. Inject it into `LeaderGateway.__init__` as
`leader_cli_probe` so tests never call real executables. For a CLI subprocess
Leader, `describe()` includes capabilities `("plan", "native_json_schema")`
only on success; otherwise readiness is blocked with the fixed blocker. Keep
`fallback={"automatic": False, "transport": None}`.

- [ ] **Step 5: Test probe and no fallback**

Add Gateway tests for supported, missing flag, timeout, nonzero, and unknown
provider. Assert blocked status never constructs another provider and never
persists raw help/stdout/stderr.

- [ ] **Step 6: Run focused tests and update the contract doc**

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_cli_structured_output.py \
         tests/test_conversation_leader_gateway.py \
         tests/test_conversation_transports.py -q
git diff --check
```

Expected: PASS. Document `native_json_schema` as generation capability, not
readiness for Worker execution or authorization.

- [ ] **Step 7: Update history and commit**

```bash
git add src/agentdeck/providers/cli_subprocess.py \
  src/agentdeck/conversation/leader_gateway.py \
  tests/test_cli_structured_output.py \
  tests/test_conversation_leader_gateway.py \
  docs/contracts/leader-backend-schema.md HISTORY.md
git commit -m "Add native Claude Leader structured output"
```

### Task 5: One same-provider, same-model regeneration under one total planning deadline

**Files:**
- Modify: `src/agentdeck/providers/cli_subprocess.py`
- Modify: `tests/test_cli_structured_output.py`
- Modify: `tests/test_conversation_leader_diagnostics.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write failing retry matrix tests**

Add a parameterized test with fake results for:

```text
json_parse → success       calls=2
schema → success           calls=2
json_parse → json_parse    calls=2, final attempt_count=2
nonzero                    calls=1
timeout                    calls=1
oversize                   calls=1
```

For the success case assert the second prompt contains only the allowlisted
diagnostic code and original task, not the first raw output:

```python
assert "Regenerate the complete plan" in prompts[1]
assert "invalid_step_count" in prompts[1]
assert "SECRET_BAD_OUTPUT" not in prompts[1]
assert result.leader_generation["attempt_count"] == 2
assert result.leader_generation["regeneration_used"] is True
```

Record both subprocess argv lists and assert the executable/provider and model
arguments are byte-for-byte identical across attempts. Assert no provider
factory, transport selector, model selector, or fallback hook is called during
regeneration.

- [ ] **Step 2: Run retry tests and verify RED**

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_cli_structured_output.py -k regeneration -q
```

Expected: FAIL because only one attempt exists.

- [ ] **Step 3: Implement the shared monotonic deadline loop**

At `plan_result()` entry:

```python
budget = min(float(request.timeout_seconds or self.timeout), float(self.timeout))
deadline = time.monotonic() + budget
last_error = None
for attempt_number in (1, 2):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CliLeaderProviderError(
            "timeout", attempt_count=attempt_number - 1,
            constraint_mode="native_json_schema",
        )
    try:
        plan = self._native_attempt(
            request, timeout=remaining,
            regeneration_code=last_error.diagnostic_code if last_error else None,
        )
        return LeaderPlanResult(
            plan=plan,
            leader_generation=build_leader_generation_provenance(
                request=request, provider=self.name,
                constraint_mode="native_json_schema",
                schema=build_leader_plan_schema(request),
                attempt_count=attempt_number,
            ),
        )
    except CliLeaderProviderError as error:
        last_error = error.with_attempt_count(attempt_number)
        if attempt_number == 2 or error.stage not in {"json_parse", "schema"}:
            raise last_error
```

`CliLeaderProviderError` validates stage, optional diagnostic code, exact-int
attempt count in `0..2`, and constraint mode. Its string/repr/`__dict__` must
never contain raw output, prompt, argv, path, or exception text.

- [ ] **Step 4: Test shared deadline exhaustion**

Inject or monkeypatch `time.monotonic` with a deterministic sequence proving
the second call gets only the remaining budget and is skipped when none
remains. Assert the subprocess timeout never exceeds the original request
budget and retry does not double wall-clock authority.

- [ ] **Step 5: Run focused diagnostics suites**

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_cli_structured_output.py \
         tests/test_conversation_leader_diagnostics.py \
         tests/test_provider_openai_compatible.py -q
```

Expected: PASS.

- [ ] **Step 6: Update history and commit**

```bash
git add src/agentdeck/providers/cli_subprocess.py \
  tests/test_cli_structured_output.py \
  tests/test_conversation_leader_diagnostics.py HISTORY.md
git commit -m "Bound CLI Leader plan regeneration"
```

### Task 6: Durable safe diagnostics and candidate generation provenance

**Files:**
- Modify: `src/agentdeck/conversation/leader_gateway.py`
- Modify: `src/agentdeck/conversation/session.py:443-620`
- Modify: `src/agentdeck/mission_orchestration.py:194-203,578-675`
- Modify: `tests/test_conversation_leader_diagnostics.py`
- Modify: `tests/test_conversation_session.py`
- Modify: `tests/test_mission_orchestration.py`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write failing Gateway diagnostic propagation tests**

Construct a provider that raises:

```python
CliLeaderProviderError(
    "schema",
    diagnostic_code="role_mismatch",
    attempt_count=2,
    constraint_mode="native_json_schema",
)
```

Assert `LeaderGatewayError` preserves only those four compact facts. Assert
every secret placed in provider exception cause, stdout, prompt, path, and task
is absent from `str`, `repr`, and `__dict__` of the Gateway error.

- [ ] **Step 2: Write failing durable session tests**

For a failed natural-language turn, assert the response and terminal event:

```python
assert response.payload == {
    "blocker": "Leader planning failed at stage: schema",
    "stage": "schema",
    "diagnostic_code": "role_mismatch",
    "attempt_count": 2,
    "constraint_mode": "native_json_schema",
}
assert event["payload"] == {
    "conversation_id": session.conversation_id,
    "turn_id": turn_id,
    "state": "failed",
    "stage": "schema",
    "diagnostic_code": "role_mismatch",
    "attempt_count": 2,
    "constraint_mode": "native_json_schema",
}
```

Also assert plans, missions, attempts, permissions, sessions, messages, jobs,
and inboxes remain empty. Pre-provider authority rejection uses
`diagnostic_code=authority_invalid`, `attempt_count=0`, and
`constraint_mode=prompt_only` without invoking a provider.

- [ ] **Step 3: Run tests and verify RED**

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_conversation_leader_diagnostics.py \
         tests/test_conversation_session.py -q
```

Expected: FAIL because Gateway/session currently persist only `stage`.

- [ ] **Step 4: Extend error and candidate types**

Give `LeaderGatewayError` exact validated attributes:

```python
stage: str
diagnostic_code: str | None
attempt_count: int
constraint_mode: str
```

Extend `LeaderMissionCandidate`:

```python
leader_generation: dict[str, object] | None = None
```

Gateway uses `LeaderPlanResult.leader_generation` for subprocess/API/local
providers. ACP Leader builds `prompt_only` provenance after strict JSON parsing
and local validation. A candidate never receives raw provider content as
provenance.

- [ ] **Step 5: Persist failure facts atomically**

Build one helper returning the four allowlisted diagnostic fields. Use it for
the response and the existing `conversation_turn_terminal` event. Keep the
existing exact state transition and fail-stop recovery behavior; do not add an
intermediate write per generation attempt.

- [ ] **Step 6: Validate candidate provenance before Mission landing**

In `create_mission_preview_from_candidate()`, require a non-null generation
record for natural-language candidates carrying frozen authority, validate it
against configured provider/model/selected ids/step count, and pass it to
`store.build_plan_record()`. Legacy direct candidates with both authority fields
and generation absent retain the explicit compatibility behavior covered by an
existing/new regression.

- [ ] **Step 7: Run focused suites**

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_conversation_leader_diagnostics.py \
         tests/test_conversation_session.py \
         tests/test_conversation_leader_gateway.py \
         tests/test_mission_orchestration.py -q
```

Expected: PASS.

- [ ] **Step 8: Update history and commit**

```bash
git add src/agentdeck/conversation/leader_gateway.py \
  src/agentdeck/conversation/session.py \
  src/agentdeck/mission_orchestration.py \
  tests/test_conversation_leader_diagnostics.py \
  tests/test_conversation_session.py \
  tests/test_conversation_leader_gateway.py \
  tests/test_mission_orchestration.py HISTORY.md
git commit -m "Persist safe Leader generation diagnostics"
```

### Task 7: Plan record and ProjectView generation provenance contract

**Files:**
- Modify: `src/agentdeck/state.py:8202-8252,8961-8995,9263-9291`
- Modify: `src/agentdeck/contracts.py:530-543,8174-8199`
- Modify: `tests/test_contracts.py`
- Modify: `tests/test_mission_orchestration.py`
- Modify: `docs/contracts/project-view-schema.md:112-118`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write failing persisted/projection tests**

Create a Mission preview from a candidate with native generation provenance.
Assert the stored plan and ProjectView item contain the same exact object, and
that `canonical_workflow_plan_hash()` is unchanged if only generation
provenance differs.

Add strict contract mutations for:

```text
unknown constraint_mode
schema_version != leader-plan/v1 for native mode
malformed sha256
attempt_count bool/0/>2
regeneration_used inconsistent with attempt_count
duplicate/unknown selected_agent_ids
step_count mismatch with projected plan step_count
provider/model mismatch with enclosing plan item
raw prompt/argv/path/credential semantic keys
```

- [ ] **Step 2: Run the tests and verify RED**

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_mission_orchestration.py -k leader_generation -q
conda run --no-capture-output -n agentdeck \
  pytest tests/test_contracts.py -k 'project_view and plan' -q
```

Expected: FAIL because plan records and ProjectView do not expose
`leader_generation`.

- [ ] **Step 3: Add one StateStore normalizer**

Implement `_plan_leader_generation(value, *, provider, model, step_count)` in
`StateStore`. It copies only the nine locked fields, validates native facts,
and returns a deterministic legacy projection for old records. Use it in
`record_plan()`, `build_plan_record()`, `_plan_summaries()`, and trace plan
projection. Never copy unknown nested keys.

Add `leader_generation` after `leader_backend` in
`PROJECT_VIEW_PLAN_ITEM_FIELDS`. Extend `_validate_project_view_plan_items()`
with exact field/type/coherence checks and recursive secret-key rejection.

- [ ] **Step 4: Update examples and contract documentation**

Update `project_view_example()`, payload field lists, tests, and
`docs/contracts/project-view-schema.md`. State explicitly that generation
provenance is immutable audit metadata, not readiness, pane binding, permission,
or dispatch authority. Keep `PROJECT_VIEW_SCHEMA_VERSION` at `project-view/v1`
under the repository's additive-v1 policy.

- [ ] **Step 5: Run contract and Mission suites**

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_contracts.py \
         tests/test_mission_orchestration.py \
         tests/test_leader_cli.py \
         tests/test_agent_cli.py -q
```

Expected: PASS.

- [ ] **Step 6: Update history and commit**

```bash
git add src/agentdeck/state.py src/agentdeck/contracts.py \
  tests/test_contracts.py tests/test_mission_orchestration.py \
  docs/contracts/project-view-schema.md HISTORY.md
git commit -m "Expose Leader generation provenance"
```

### Task 8: Deterministic four-stage ACP/tmux Mission

**Files:**
- Modify: `tests/fixtures/fake_acp_agent.py`
- Modify: `tests/test_daemon_acceptance.py`
- Modify: `docs/superpowers/plans/2026-07-14-agentdeck-m2c-closure.md`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write the failing four-stage acceptance skeleton**

Add `test_daemon_acceptance_runs_four_stage_acp_tmux_mission()` using a fresh
temporary project and the existing collect-all resource guard. Configure two
Workers:

```text
claude-worker: transport=acp, steps 1 and 3
codex-worker: transport=tmux, steps 2 and 4
```

Seed or create one frozen plan with tasks named exactly:

```text
implementation: create artifact.txt containing draft-v1
review: require artifact.txt to contain accepted-v2
revision: replace draft-v1 with accepted-v2
acceptance: verify artifact.txt equals accepted-v2
```

The initial test must assert four succeeded attempts, four recorded canonical
handoff evidence rows (one per succeeded attempt), and exactly three
inter-stage predecessor-to-next-prompt links; it will fail until the fixture
can complete both invocations per Worker.

- [ ] **Step 2: Run the test and verify RED**

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_daemon_acceptance.py::test_daemon_acceptance_runs_four_stage_acp_tmux_mission -q -s
```

Expected: FAIL at missing M2c fake ACP/tmux behavior or incomplete four-step
result, not at test collection.

- [ ] **Step 3: Add the deterministic ACP M2c scenario**

Extend `fake_acp_agent.py` with scenario `m2c_worker`. It must:

1. log only method, label, dispatch token, prompt SHA-256, and recorded handoff
   ids;
2. identify implementation/revision from the task text;
3. request one `allow_once` edit permission before each artifact write;
4. write `draft-v1\n` for implementation and `accepted-v2\n` for revision only
   after the permission response returns;
5. emit one correlated reply containing no private reasoning/transcript/secret;
6. include a phase-specific compact summary and verification.

Do not log the full prompt. Existing scenarios must remain byte-compatible.

- [ ] **Step 4: Add deterministic fake tmux phase behavior**

Extend the acceptance-local fake tmux script so its first admitted prompt emits
review feedback `required_content=accepted-v2`, and its second prompt reads the
artifact and emits acceptance success only when bytes equal `accepted-v2\n`.
Log only phase, dispatch token, prompt hash, artifact hash, and ordering marker.

- [ ] **Step 5: Assert handoff and state agreement**

The test must assert:

```python
assert [a["agent_id"] for a in attempts] == [
    "claude-worker", "codex-worker", "claude-worker", "codex-worker",
]
assert [a["state"] for a in attempts] == ["succeeded"] * 4
assert len(state["mission_handoffs"]) == 4
assert all(item["state"] == "recorded" for item in state["mission_handoffs"])
# The first three handoffs link predecessor evidence into the next stage prompt;
# the fourth is the terminal attempt's canonical evidence, not another transition.
assert artifact.read_bytes() == b"accepted-v2\n"
assert mission["status"] == "completed"
assert mission["current_step"] == mission["step_count"] == 4
```

Verify each next prompt hash was recorded after its predecessor handoff event;
there is one submitted receipt per attempt; no unselected Worker exists; and
the state contains no full prompt, private reasoning, transcript, secret, or
raw tmux capture.

- [ ] **Step 6: Run focused daemon tests**

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_daemon_acceptance.py \
         tests/test_daemon_transports.py \
         tests/test_daemon_service.py \
         tests/test_daemon_scheduler.py -q
```

Expected: PASS.

- [ ] **Step 7: Update history and commit**

```bash
git add tests/fixtures/fake_acp_agent.py \
  tests/test_daemon_acceptance.py \
  docs/superpowers/plans/2026-07-14-agentdeck-m2c-closure.md HISTORY.md
git commit -m "Add deterministic four-stage M2c Mission"
```

### Task 9: Deterministic disconnect, permission, takeover, and return-control

**Files:**
- Modify: `tests/test_daemon_acceptance.py`
- Modify: `tests/test_daemon_governance.py`
- Modify: production daemon/governance files only if a new failing regression
  proves a defect inside the approved M2c boundary
- Modify: `HISTORY.md`

- [ ] **Step 1: Extend the four-stage test to disconnect at permission**

After exact Mission admission, close the first client. Wait until step 1 has a
pending ACP permission. Render recovery through a second bare PTY and assert the
same Mission id, current step 1, pending permission id, and enabled exact
permission-preview control. Before decision, assert no artifact and no tmux
admission.

- [ ] **Step 2: Run the new assertions and verify RED**

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_daemon_acceptance.py::test_daemon_acceptance_runs_four_stage_acp_tmux_mission -q -s
```

Expected: FAIL at the first newly unimplemented harness/control assertion.

- [ ] **Step 3: Drive exact permission preview/confirm**

Use `DaemonClient` with a controller lease. Call `permission.decide` once
without `preview_id`, then once with the returned id and the same decision.
Assert the preview is consume-once, a replay is rejected with zero writes, only
the bound ACP waiter resumes, and step 2 begins only after step 1 handoff is
durable. Repeat for the revision permission if the deterministic fixture asks
again; never select `allow_always`.

- [ ] **Step 4: Add takeover at the safe window**

Pause at:

```text
step 2 succeeded
step 3 permission pending
codex-worker idle
step 4 not admitted
```

Call `worker.takeover` preview then confirm for `codex-worker`. Assert ownership
is `human_owned`, baseline is active, and tmux fake logs no automated input
while owned. Call `worker.return-control` preview then confirm with the exact
reported change set required by existing governance, assert reconciliation is
recorded and ownership returns to `agentdeck_owned`, then approve step 3 and
allow step 4.

- [ ] **Step 5: Add fail-closed governance regressions**

In `tests/test_daemon_governance.py`, add cases proving:

- takeover during target active attempt is blocked and zero-write;
- return with changed pane/session/worktree evidence remains human-owned;
- step 4 cannot dispatch while codex-worker is human-owned;
- exact successful return permits the already-frozen future step only;
- takeover/return preview replay is rejected;
- no governance response contains prompt, pane capture, command, path, or
  credential text.

If an existing production path fails, write the smallest direct regression
first, then change only the responsible function in
`src/agentdeck/daemon/governance.py`, `service.py`, or `state.py`. Do not alter
the scheduler or authority model merely to make the scenario easier.

- [ ] **Step 6: Run governance and acceptance suites**

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_daemon_acceptance.py \
         tests/test_daemon_governance.py \
         tests/test_daemon_reconnection.py \
         tests/test_daemon_recovery.py -q
```

Expected: PASS with exactly four logical attempts and no duplicate external
admission.

- [ ] **Step 7: Update history and commit**

Stage the test files and HISTORY. If a regression required a production fix,
stage only the exact production file named by that regression after inspecting
its diff:

```bash
git add tests/test_daemon_acceptance.py tests/test_daemon_governance.py HISTORY.md
git diff -- src/agentdeck/daemon/governance.py \
  src/agentdeck/daemon/service.py src/agentdeck/state.py
git commit -m "Prove M2c recovery and human takeover"
```

Before committing, inspect `git diff --cached --name-only` and unstage any
production file with no semantic diff. Never use `git add .`.

### Task 10: Opt-in real M2c acceptance gate and SOP

**Files:**
- Create: `tests/test_m2c_live_acceptance.py`
- Create: `docs/validation/phase3-m2c-live-acceptance-sop.md`
- Modify: `HISTORY.md`

- [ ] **Step 1: Write the skipped-by-default live gate**

At module scope:

```python
LIVE = os.environ.get("AGENTDECK_M2C_LIVE") == "1"
pytestmark = pytest.mark.skipif(
    not LIVE,
    reason="set AGENTDECK_M2C_LIVE=1 for real M2c acceptance",
)
```

Require exact executable paths through:

```text
AGENTDECK_M2C_CODEX
AGENTDECK_M2C_CLAUDE
AGENTDECK_M2C_CLAUDE_ACP
AGENTDECK_M2C_TMUX
```

The test rejects missing, relative, non-regular, or non-executable paths. It
does not search/install/login after opt-in. It records basenames, version
strings, and SHA-256 of the frozen AgentDeck commit only; it never records home
paths or environment values.

- [ ] **Step 2: Add a read-only preflight test that runs by default**

Keep preflight in a separate unskipped test. It checks:

- native Codex help contains `--output-schema` and `--output-last-message`;
- native Claude help contains `--json-schema` and JSON output;
- `claude-agent-acp --version` succeeds;
- tmux version succeeds;
- no package installation/auth command is invoked;
- a disposable project tree is unchanged by capability checks.

Use bounded five-second subprocesses and return only fixed blocker codes plus
version strings. The default test always passes when this read-only contract is
met: on a fully equipped machine it asserts `ready=true` and an empty blocker
list; when any tool/flag is absent it asserts `ready=false` and the exact
allowlisted blocker code for that probe. Missing capability therefore remains
an explicit non-PASS product result without making the portable test suite
environment-dependent. The opt-in live test must require `ready=true` and
refuse before project initialization otherwise.

- [ ] **Step 3: Implement the live scenario harness**

The live test must:

1. create a disposable Git project outside the implementation checkout;
2. run `agentdeck project init` and write only project-local configuration;
3. configure Codex CLI/model as Leader, Claude Agent ACP as
   `claude-worker`, and Codex tmux as `codex-worker`;
4. start bare `agentdeck` in a bounded PTY;
5. send one natural-language four-stage request;
6. assert one native-schema Mission preview and exact four semantic phases;
7. confirm the exact preview once;
8. close the first PTY after durable admission;
9. reconnect at a real ACP permission pause and confirm `allow_once` through
   the daemon preview/confirm RPC;
10. observe the Codex pane through exact ProjectView/workbench control;
11. perform takeover/return-control at the safe step-3 pause;
12. finish all four attempts and verify the disposable artifact bytes/hash;
13. compare ProjectView, Mission status, workbench, ledger, events, trace,
    snapshot, admission, receipt, and three handoff facts;
14. collect only compact sanitized evidence;
15. clean every process/socket/tmux/project resource in one collect-all guard.

All PTY buffers are bounded tails. Failure diagnostics contain byte count,
truncation bool, SHA-256, terminal stage/code, and state cardinalities only.
They never contain captured terminal text.

- [ ] **Step 4: Write the SOP before running live**

The SOP includes exact preflight and opt-in commands:

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py::test_m2c_live_preflight_is_read_only -q -s

AGENTDECK_M2C_LIVE=1 \
AGENTDECK_M2C_CODEX="$(command -v codex)" \
AGENTDECK_M2C_CLAUDE="$(command -v claude)" \
AGENTDECK_M2C_CLAUDE_ACP="$(command -v claude-agent-acp)" \
AGENTDECK_M2C_TMUX="$(command -v tmux)" \
conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py::test_real_four_stage_m2c_acceptance -q -s
```

The SOP states that a skip or setup blocker is not PASS and reserves the final
validation report update for a genuine completed run.

- [ ] **Step 5: Run default non-live verification**

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_m2c_live_acceptance.py -q
```

Expected: read-only preflight test passes or reports a deterministic setup
result through its asserted payload; real test is exactly one skip. A
`ready=false` preflight payload is not M2c PASS even though the read-only
contract test itself passes.

- [ ] **Step 6: Update history and commit**

```bash
git add tests/test_m2c_live_acceptance.py \
  docs/validation/phase3-m2c-live-acceptance-sop.md HISTORY.md
git commit -m "Add opt-in real M2c acceptance gate"
```

### Task 11: Run the real frozen-commit rehearsal and record evidence

**Files:**
- Modify: `docs/validation/2026-07-13-phase3-m2-project-daemon.md`
- Modify: `docs/roadmap/product-north-star.md`
- Modify: `docs/handoff/current-development-state.md`
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `HISTORY.md`

- [ ] **Step 1: Freeze the implementation commit and verify a clean tracked tree**

Run:

```bash
git rev-parse HEAD
git diff --check
git status --short
```

Record the commit id. Existing user-owned `.omc`/`AGENTS.md` changes may remain,
but every tracked implementation/doc file in this plan must be clean. Do not
stash or delete user-owned paths.

- [ ] **Step 2: Run the read-only preflight**

Run the exact SOP preflight. Expected: native schema flags, adapter, and tmux
are ready with no project/global writes. If blocked, update validation,
handoff, and HISTORY with the fixed compact blocker; do not run the live test
and do not mark M2c PASS.

- [ ] **Step 3: Run the opt-in live acceptance once**

Run the exact environment-bound command from Task 10. Expected for PASS:
`1 passed`, four succeeded attempts, three handoffs, at least one explicitly
approved permission, successful takeover/return-control, completed Mission,
and clean resource audit.

If it fails, do not improvise a second transport/provider or weaken policy.
Classify the compact failure. Any code fix requires a deterministic RED test,
minimal implementation, focused/full regression, a new commit, and a newly
frozen live attempt.

- [ ] **Step 4: Re-run once only after a proven semantic fix**

A second fresh-project attempt is permitted only when Step 3 exposed a specific
in-scope defect that is now covered by a committed deterministic regression.
Never retry an unknown external effect. If the same blocker recurs, keep M2c
BLOCKED and stop.

- [ ] **Step 5: Write PASS or BLOCKED evidence honestly**

Update the existing validation report without deleting historical evidence.
Include:

- frozen AgentDeck commit and sanitized tool versions;
- native constraint mode/schema hash/attempt count;
- plan/Mission cardinality and hash agreement;
- four phase/Worker/transport statuses;
- permission and ownership transition facts;
- disconnect/reconnect identity;
- three handoff lineage/hash facts;
- artifact byte count/hash and expected disposable content;
- ProjectView/ledger/events/trace/snapshot agreement;
- cleanup result and residual-process count;
- no install/auth/global-setting changes.

On BLOCKED, include the fixed stage/code and zero/not-reached facts. Do not
write a partial PASS.

- [ ] **Step 6: Update product docs according to evidence**

Only on real PASS may `product-north-star.md` mark delivery step 5 complete and
README state that the real four-stage Mission is proven. On BLOCKED, preserve
the blocker and do not advertise completion. Keep English/Chinese README
meaning aligned and concise.

- [ ] **Step 7: Commit the evidence boundary**

```bash
git add docs/validation/2026-07-13-phase3-m2-project-daemon.md \
  docs/roadmap/product-north-star.md \
  docs/handoff/current-development-state.md \
  README.md README.zh-CN.md HISTORY.md
git commit -m "Record real Phase 3 M2c acceptance"
```

If README or north-star files did not semantically change for a BLOCKED result,
do not stage them.

### Task 12: Final regression, self-review, and M2c handoff gate

**Files:**
- Modify: `docs/handoff/current-development-state.md`
- Modify: `HISTORY.md`
- Modify: any plan-touched file only through a new regression-backed fix

- [ ] **Step 1: Run focused Leader suites**

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_leader_plan_schema.py \
         tests/test_cli_structured_output.py \
         tests/test_provider_openai_compatible.py \
         tests/test_conversation_leader_gateway.py \
         tests/test_conversation_leader_diagnostics.py \
         tests/test_conversation_session.py -q
```

Expected: all pass, zero failures.

- [ ] **Step 2: Run focused Mission/contract suites**

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_mission_orchestration.py \
         tests/test_contracts.py \
         tests/test_leader_cli.py \
         tests/test_agent_cli.py -q
```

Expected: all pass, zero failures.

- [ ] **Step 3: Run focused daemon/governance/recovery suites**

```bash
conda run --no-capture-output -n agentdeck \
  pytest tests/test_daemon_acceptance.py \
         tests/test_daemon_governance.py \
         tests/test_daemon_reconnection.py \
         tests/test_daemon_recovery.py \
         tests/test_daemon_transports.py \
         tests/test_daemon_service.py \
         tests/test_daemon_scheduler.py \
         tests/test_daemon_crash_matrix.py -q
```

Expected: all deterministic tests pass, zero failures.

- [ ] **Step 4: Run the complete verification gate after the last semantic change**

```bash
conda run --no-capture-output -n agentdeck pytest -q
conda run --no-capture-output -n agentdeck python -m compileall -q src tests
git diff --check
```

Expected: full suite has zero failures; only explicitly opt-in live tests may be
skipped; compileall and diff check exit 0.

- [ ] **Step 5: Audit spec coverage and forbidden behavior**

Review every section of the approved design and prove:

- canonical schema is one source of truth;
- native Codex and Claude paths are tested;
- semantic validation still owns authority;
- no provider/model/transport fallback exists;
- no local plan repair guesses intent;
- retry is same-Leader and deadline-bounded;
- raw output/prompts/secrets are not durable;
- ProjectView provenance is compact and contract-valid;
- deterministic four-stage acceptance passes;
- live result is honestly PASS or BLOCKED;
- cleanup is evidence;
- A2A/remote/global/workspace/terminal-emulator work did not enter the diff;
- M3 did not begin.

- [ ] **Step 6: Inspect every commit and worktree boundary**

```bash
git log --oneline --decorate -15
git status --short
git diff --stat HEAD~12..HEAD
```

Confirm every semantic commit contains its `HISTORY.md` entry, no runtime
`.agentdeck/` state is tracked, and user-owned `.omc`/`AGENTS.md` paths remain
untouched.

- [ ] **Step 7: Record final verified counts and commit handoff**

Update handoff and HISTORY with fresh focused/full counts, compile/diff result,
real PASS/BLOCKED verdict, frozen live commit, and exact next gate. If M2c is
PASS, next is M3 brainstorming only. If BLOCKED, next remains the compact
in-scope blocker.

```bash
git add docs/handoff/current-development-state.md HISTORY.md
git commit -m "Close Phase 3 M2c verification boundary"
```

## Final completion rule

Do not call the M2 `/goal` complete unless Tasks 1–12 are committed, all fresh
deterministic/full verification is green, the live four-stage test is PASS, and
cleanup/residual-process audit is PASS. A correct BLOCKED report is valid work
but is not M2c completion and does not unlock M3.
