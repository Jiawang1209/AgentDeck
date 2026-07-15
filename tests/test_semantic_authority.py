from __future__ import annotations

import builtins
from copy import deepcopy
import hashlib
import json
import locale
import os
import subprocess
import urllib.request

import pytest
import agentdeck.semantic_authority as semantic_authority_module

from agentdeck.semantic_authority import (
    SEMANTIC_AUTHORITY_SCHEMA_VERSION,
    SEMANTIC_OPERATIONS,
    SEMANTIC_REQUIREMENT_KINDS,
    SEMANTIC_SENSITIVITY,
    SemanticAuthorityError,
    compact_semantic_authority,
    extract_semantic_authority,
    semantic_authority_hash,
    semantic_text_contains_sensitive_value,
    validate_semantic_authority,
)


def valid_authority() -> dict[str, object]:
    """Return a fresh, fully literal state-transition authority."""
    return {
        "schema_version": "mission-semantic-authority/v1",
        "source_message_hash": f"sha256:{'a' * 64}",
        "requirements": [
            {
                "requirement_id": "req_0123456789ab",
                "kind": "state_transition",
                "target": "artifact.txt",
                "operation": "update",
                "before": {"content_equals": "draft-v1"},
                "after": {"content_equals": "accepted-v2"},
                "phase": "revision",
                "agent_id": "claude-worker",
                "sensitivity": "ordinary",
            }
        ],
        "proposed_effects": [
            {
                "proposed_effect_id": "prp_0123456789ab",
                "target": "artifact.txt",
                "operation": "update",
                "sensitivity": "ordinary",
            }
        ],
        "unresolved": [],
    }


@pytest.mark.parametrize(
    "text",
    [
        "DBPASSWORDHASH=super-secret-value",
        "APIKEYVALUE=super-secret-value",
        "private_key=super-secret-value",
        "accessToken=super-secret-value",
        "sk-DO_NOT_ECHO",
        "ghp_DO_NOT_ECHO",
    ],
)
def test_shared_sensitive_text_helper_uses_extraction_classifier(text: str) -> None:
    assert semantic_text_contains_sensitive_value(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "ordinary-value",
        "secretary=available",
        "tokenizer=enabled",
        "monkey_value=banana",
    ],
)
def test_shared_sensitive_text_helper_preserves_benign_assignments(text: str) -> None:
    assert semantic_text_contains_sensitive_value(text) is False


def test_shared_sensitive_text_helper_requires_exact_builtin_string() -> None:
    class StringSubclass(str):
        pass

    assert semantic_text_contains_sensitive_value(StringSubclass("password=SECRET")) is False
    assert semantic_text_contains_sensitive_value({"password": "SECRET"}) is False


def test_known_token_prefix_values_are_redacted_before_source_hashing() -> None:
    first = extract_semantic_authority(
        "First, worker creates artifact.txt with content exactly ghp_FIRSTSECRET",
        selected_agent_ids=("worker",),
        step_count=1,
        phases=("implementation",),
    )
    second = extract_semantic_authority(
        "First, worker creates artifact.txt with content exactly ghp_SECONDSECRET",
        selected_agent_ids=("worker",),
        step_count=1,
        phases=("implementation",),
    )
    assert first["source_message_hash"] == second["source_message_hash"]
    assert first["requirements"] == second["requirements"] == []


_CHINESE_LIVE_REQUEST = (
    "让 claude-worker 和 codex-worker 严格串行完成4轮。阶段必须精确为 "
    "implementation、review、revision、acceptance：第一轮 claude-worker 创建 "
    "artifact.txt 且内容为 draft-v1 换行；第二轮 codex-worker 只读审查并要求 "
    "accepted-v2；第三轮 claude-worker 将 artifact.txt 精确改为 accepted-v2 换行；"
    "第四轮 codex-worker 只读验收精确字节。共4轮。\n"
)
_ENGLISH_LIVE_REQUEST = (
    "Have claude-worker and codex-worker complete 4 steps strictly sequentially. "
    "The phases must be exactly implementation, review, revision, acceptance: "
    "First, claude-worker creates artifact.txt with content exactly draft-v1 newline; "
    "Second, codex-worker performs a read-only review and requires accepted-v2; "
    "Third, claude-worker updates artifact.txt to exactly accepted-v2 newline; "
    "Fourth, codex-worker performs read-only verification of the exact bytes. "
    "There are 4 steps.\n"
)
_EXTRACTION_ARGS = {
    "selected_agent_ids": ("claude-worker", "codex-worker"),
    "step_count": 4,
    "phases": ("implementation", "review", "revision", "acceptance"),
}


class _ExtractionString(str):
    pass


def _requirement_id(body: dict[str, object]) -> str:
    canonical = json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return f"req_{hashlib.sha256(canonical).hexdigest()[:12]}"


def _expected_extracted_requirements() -> list[dict[str, object]]:
    bodies = [
        {
            "kind": "create",
            "target": "artifact.txt",
            "operation": "create",
            "literal": "draft-v1\n",
            "phase": "implementation",
            "agent_id": "claude-worker",
            "sensitivity": "ordinary",
        },
        {
            "kind": "review",
            "target": "artifact.txt",
            "operation": "review",
            "literal": "accepted-v2\n",
            "phase": "review",
            "agent_id": "codex-worker",
            "sensitivity": "ordinary",
        },
        {
            "kind": "state_transition",
            "target": "artifact.txt",
            "operation": "update",
            "before": {"content_equals": "draft-v1\n"},
            "after": {"content_equals": "accepted-v2\n"},
            "phase": "revision",
            "agent_id": "claude-worker",
            "sensitivity": "ordinary",
        },
        {
            "kind": "verify",
            "target": "artifact.txt",
            "operation": "verify",
            "literal": "accepted-v2\n",
            "phase": "acceptance",
            "agent_id": "codex-worker",
            "sensitivity": "ordinary",
        },
    ]
    return [{"requirement_id": _requirement_id(body), **body} for body in bodies]


@pytest.mark.parametrize("message", [_CHINESE_LIVE_REQUEST, _ENGLISH_LIVE_REQUEST])
def test_extract_live_request_produces_exact_ordered_atomic_requirements(
    message: str,
) -> None:
    authority = extract_semantic_authority(message, **_EXTRACTION_ARGS)

    assert authority == {
        "schema_version": SEMANTIC_AUTHORITY_SCHEMA_VERSION,
        "source_message_hash": f"sha256:{hashlib.sha256(message.encode('utf-8')).hexdigest()}",
        "requirements": _expected_extracted_requirements(),
        "proposed_effects": [],
        "unresolved": [],
    }


def test_extract_is_stable_and_keeps_semantic_generation_order() -> None:
    first = extract_semantic_authority(_CHINESE_LIVE_REQUEST, **_EXTRACTION_ARGS)
    second = extract_semantic_authority(_CHINESE_LIVE_REQUEST, **_EXTRACTION_ARGS)

    assert first == second
    assert semantic_authority_hash(first) == semantic_authority_hash(second)
    assert [item["phase"] for item in first["requirements"]] == list(
        _EXTRACTION_ARGS["phases"]
    )
    for item in first["requirements"]:
        body = {key: value for key, value in item.items() if key != "requirement_id"}
        assert item["requirement_id"] == _requirement_id(body)


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        (
            "第一轮 claude-worker 创建 a.txt 和 b.txt 且内容为 draft 换行。",
            "ambiguous_target",
        ),
        (
            "第一轮 claude-worker 将 artifact.txt 精确改为 accepted 换行。",
            "missing_transition_origin",
        ),
        (
            "第一轮 claude-worker 创建 /tmp/artifact.txt 且内容为 draft 换行。",
            "unsafe_target",
        ),
        (
            "第一轮 claude-worker 创建 artifact.txt 且内容精确匹配 /draft-.*/。",
            "unsupported_literal",
        ),
        (
            "第一轮 claude-worker 创建 artifact.txt 且内容为 api_key=SECRET。",
            "sensitive_content",
        ),
    ],
)
def test_explicit_unsafe_or_ambiguous_clauses_become_bounded_unresolved(
    message: str, kind: str
) -> None:
    authority = extract_semantic_authority(
        message,
        selected_agent_ids=("claude-worker",),
        step_count=1,
        phases=("implementation",),
    )

    assert authority["requirements"] == []
    assert len(authority["unresolved"]) == 1
    unresolved = authority["unresolved"][0]
    assert set(unresolved) == {"unresolved_id", "kind", "phase", "agent_id"}
    assert unresolved["kind"] == kind
    assert unresolved["phase"] == "implementation"
    assert unresolved["agent_id"] == "claude-worker"
    assert unresolved["unresolved_id"].startswith("unr_")
    serialized = json.dumps(authority, ensure_ascii=False)
    for forbidden in ("/tmp/artifact.txt", "api_key=SECRET", "SECRET", "/draft-.*/"):
        assert forbidden not in serialized


def test_unresolved_ids_are_stable_opaque_and_do_not_reorder_items() -> None:
    message = (
        "第一轮 claude-worker 创建 a.txt 和 b.txt 且内容为 draft 换行；"
        "第二轮 codex-worker 将 artifact.txt 精确改为 accepted 换行。"
    )
    args = {
        "selected_agent_ids": ("claude-worker", "codex-worker"),
        "step_count": 2,
        "phases": ("implementation", "revision"),
    }
    first = extract_semantic_authority(message, **args)
    second = extract_semantic_authority(message, **args)
    assert first["unresolved"] == second["unresolved"]
    assert [item["kind"] for item in first["unresolved"]] == [
        "ambiguous_target",
        "missing_transition_origin",
    ]


def test_open_goal_remains_open_but_unbound_explicit_detail_is_unresolved() -> None:
    open_goal = extract_semantic_authority(
        "让两个 agent 改进项目文档",
        selected_agent_ids=("claude-worker", "codex-worker"),
        step_count=2,
    )
    assert open_goal["requirements"] == []
    assert open_goal["unresolved"] == []

    explicit_filename = extract_semantic_authority(
        "让两个 agent 改进 project.md",
        selected_agent_ids=("claude-worker", "codex-worker"),
        step_count=2,
    )
    assert explicit_filename["requirements"] == []
    assert [item["kind"] for item in explicit_filename["unresolved"]] == [
        "unbound_explicit_detail"
    ]


@pytest.mark.parametrize(
    ("message", "agents", "steps", "phases", "code"),
    [
        (1, ("claude-worker",), 1, None, "extraction_message_invalid"),
        (
            _ExtractionString("safe"),
            ("claude-worker",),
            1,
            None,
            "extraction_message_invalid",
        ),
        ("safe", ["claude-worker"], 1, None, "extraction_agents_invalid"),
        ("safe", ("claude-worker", 1), 1, None, "extraction_agents_invalid"),
        ("safe", ("claude-worker",), True, None, "extraction_step_count_invalid"),
        ("safe", ("claude-worker",), 0, None, "extraction_step_count_invalid"),
        (
            "safe",
            ("claude-worker",),
            2,
            ("implementation",),
            "extraction_consistency_invalid",
        ),
        (
            "safe",
            ("claude-worker",),
            1,
            ["implementation"],
            "extraction_phases_invalid",
        ),
        (
            "safe",
            ("claude-worker",),
            1,
            ("bad phase",),
            "extraction_phases_invalid",
        ),
        (
            "claude-worker then codex-worker",
            ("codex-worker", "claude-worker"),
            2,
            ("implementation", "review"),
            "extraction_agent_order_invalid",
        ),
    ],
)
def test_extract_input_boundary_fails_closed_without_echo(
    message: object,
    agents: object,
    steps: object,
    phases: object,
    code: str,
) -> None:
    with pytest.raises(SemanticAuthorityError) as raised:
        extract_semantic_authority(
            message,
            selected_agent_ids=agents,
            step_count=steps,
            phases=phases,
        )
    assert raised.value.code == code
    assert str(raised.value) == code
    assert "safe" not in str(raised.value)


def test_extract_respects_existing_bounds_and_does_not_change_process_context() -> None:
    cwd = os.getcwd()
    locale_before = locale.setlocale(locale.LC_ALL)
    with pytest.raises(SemanticAuthorityError) as raised:
        extract_semantic_authority(
            "x" * 4097,
            selected_agent_ids=("claude-worker",),
            step_count=1,
        )
    assert raised.value.code == "extraction_message_invalid"
    assert os.getcwd() == cwd
    assert locale.setlocale(locale.LC_ALL) == locale_before


def test_extract_is_pure_and_performs_no_io(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args, **kwargs):
        raise AssertionError("I/O is forbidden")

    monkeypatch.setattr(builtins, "open", fail)
    monkeypatch.setattr(subprocess, "run", fail)
    monkeypatch.setattr(urllib.request, "urlopen", fail)

    authority = extract_semantic_authority(_CHINESE_LIVE_REQUEST, **_EXTRACTION_ARGS)
    assert authority["requirements"] == _expected_extracted_requirements()


def test_extract_rejects_colon_style_raw_sensitive_assignment_without_echo() -> None:
    message = "第一轮 claude-worker 创建 artifact.txt 且内容为 password:SECRET。"
    authority = extract_semantic_authority(
        message,
        selected_agent_ids=("claude-worker",),
        step_count=1,
        phases=("implementation",),
    )
    assert authority["requirements"] == []
    assert [item["kind"] for item in authority["unresolved"]] == [
        "sensitive_content"
    ]
    assert "SECRET" not in json.dumps(authority)


@pytest.mark.parametrize(
    ("secret_key", "separator"),
    [
        ("token", ":"),
        ("access token", ":"),
        ("refresh_token", ":"),
        ("client_secret", "="),
        ("db_password", "="),
        ("service_passwd", ":"),
        ("client_api_key", "="),
        ("aws_access_key", ":"),
        ("service_credential", "="),
    ],
)
def test_extract_rejects_raw_sensitive_key_forms_without_truncation_or_echo(
    secret_key: str,
    separator: str,
) -> None:
    message = (
        "第一轮 claude-worker 创建 artifact.txt 且内容为 "
        f"{secret_key}{separator}SECRET。"
    )
    authority = extract_semantic_authority(
        message,
        selected_agent_ids=("claude-worker",),
        step_count=1,
        phases=("implementation",),
    )
    assert authority["requirements"] == []
    assert [item["kind"] for item in authority["unresolved"]] == [
        "sensitive_content"
    ]
    serialized = json.dumps(authority)
    assert "SECRET" not in serialized
    assert "access" not in serialized


def test_extract_does_not_treat_secretary_as_a_sensitive_key_component() -> None:
    message = "第一轮 claude-worker 创建 artifact.txt 且内容为 secretary:PUBLIC。"
    authority = extract_semantic_authority(
        message,
        selected_agent_ids=("claude-worker",),
        step_count=1,
        phases=("implementation",),
    )
    assert authority["requirements"][0]["literal"] == "secretary:PUBLIC"
    assert authority["unresolved"] == []


@pytest.mark.parametrize(
    "assignment",
    [
        "dbPassword:SECRET",
        "accessToken:SECRET",
        "db.password=SECRET",
        "access token:SECRET",
        "access-token:SECRET",
        "DB_PASSWORD:SECRET",
        "APIKey:SECRET",
        "DBPassword=SECRET",
    ],
)
def test_extract_redacts_sensitive_assignment_key_variants(assignment: str) -> None:
    message = f"第一轮 claude-worker 创建 artifact.txt 且内容为 {assignment}。"
    authority = extract_semantic_authority(
        message,
        selected_agent_ids=("claude-worker",),
        step_count=1,
        phases=("implementation",),
    )
    assert authority["requirements"] == []
    assert [item["kind"] for item in authority["unresolved"]] == [
        "sensitive_content"
    ]
    serialized = json.dumps(authority)
    assert "SECRET" not in serialized
    assert assignment not in serialized


def test_source_hash_redacts_secret_values_but_preserves_non_sensitive_identity() -> None:
    def extract(message: str) -> dict[str, object]:
        return extract_semantic_authority(
            message,
            selected_agent_ids=("claude-worker",),
            step_count=1,
            phases=("implementation",),
        )

    first = extract(
        "第一轮 claude-worker 创建 artifact.txt 且内容为 accessToken:SECRET_ONE。"
    )
    second = extract(
        "第一轮 claude-worker 创建 artifact.txt 且内容为 accessToken:SECRET_TWO。"
    )
    assert first["source_message_hash"] == second["source_message_hash"]
    assert "SECRET" not in json.dumps(first)
    assert extract("让 agent 改进项目文档 A")["source_message_hash"] != extract(
        "让 agent 改进项目文档 B"
    )["source_message_hash"]
    acronym_first = extract(
        "第一轮 claude-worker 创建 artifact.txt 且内容为 APIKey:SECRET_ONE。"
    )
    acronym_second = extract(
        "第一轮 claude-worker 创建 artifact.txt 且内容为 APIKey:SECRET_TWO。"
    )
    assert acronym_first["source_message_hash"] == acronym_second["source_message_hash"]


@pytest.mark.parametrize(
    "tail",
    [
        "without approval",
        "provided that approved",
        "but is not authorized",
    ],
)
def test_extract_rejects_unsupported_english_clause_tail(tail: str) -> None:
    message = (
        "First, claude-worker creates artifact.txt with content exactly draft newline "
        f"{tail}"
    )
    authority = extract_semantic_authority(
        message,
        selected_agent_ids=("claude-worker",),
        step_count=1,
        phases=("implementation",),
    )
    assert authority["requirements"] == []
    assert [item["kind"] for item in authority["unresolved"]] == [
        "unsupported_clause_logic"
    ]


@pytest.mark.parametrize("tail", ["前提是获得批准", "但不得执行"])
def test_extract_rejects_unsupported_chinese_clause_tail(tail: str) -> None:
    message = f"第一轮 claude-worker 创建 artifact.txt 且内容为 draft 换行 {tail}。"
    authority = extract_semantic_authority(
        message,
        selected_agent_ids=("claude-worker",),
        step_count=1,
        phases=("implementation",),
    )
    assert authority["requirements"] == []
    assert [item["kind"] for item in authority["unresolved"]] == [
        "unsupported_clause_logic"
    ]


def test_extract_rejects_chinese_clause_tail_without_whitespace() -> None:
    message = "第一轮 claude-worker 创建 artifact.txt 且内容为 draft换行但不得执行。"
    authority = extract_semantic_authority(
        message,
        selected_agent_ids=("claude-worker",),
        step_count=1,
        phases=("implementation",),
    )
    assert authority["requirements"] == []
    assert [item["kind"] for item in authority["unresolved"]] == [
        "unsupported_clause_logic"
    ]


@pytest.mark.parametrize(
    "assignment",
    [
        "APIKEY:SECRET",
        "ACCESSKEY:SECRET",
        "DBPASSWORD:SECRET",
        "private_key:SECRET",
        "privateKey:SECRET",
        "PRIVATE_KEY:SECRET",
        "signing_key:SECRET",
        "encryption_key:SECRET",
        "ssh_key:SECRET",
        "client_secret:SECRET",
        "refresh_token:SECRET",
        "password_hash:SECRET",
        "hash-password:SECRET",
        "token_value:SECRET",
        "valueToken:SECRET",
        "secret_value:SECRET",
        "value.credential.id:SECRET",
        "api_key_value:SECRET",
        "accessKeyId:SECRET",
        "PRIVATE.KEY.ID:SECRET",
        "signing-key-id:SECRET",
        "encryption key value:SECRET",
        "SSHKeyValue:SECRET",
        "client_key_id:SECRET",
        "APIKEYVALUE:SECRET",
        "PRIVATEKEYID:SECRET",
        "PASSWORDHASH:SECRET",
        "TOKENVALUE:SECRET",
        "SECRETVALUE:SECRET",
        "DBPASSWORDHASH:SECRET",
    ],
)
def test_extract_uses_canonical_sensitive_key_family_classifier(
    assignment: str,
) -> None:
    message = f"第一轮 claude-worker 创建 artifact.txt 且内容为 {assignment}。"
    first = extract_semantic_authority(
        message,
        selected_agent_ids=("claude-worker",),
        step_count=1,
        phases=("implementation",),
    )
    other_assignment = f"{assignment.removesuffix('SECRET')}OTHER_SECRET"
    second = extract_semantic_authority(
        f"第一轮 claude-worker 创建 artifact.txt 且内容为 {other_assignment}。",
        selected_agent_ids=("claude-worker",),
        step_count=1,
        phases=("implementation",),
    )
    assert first["requirements"] == []
    assert [item["kind"] for item in first["unresolved"]] == [
        "sensitive_content"
    ]
    assert first["source_message_hash"] == second["source_message_hash"]
    assert "SECRET" not in json.dumps(first)


def test_extract_does_not_treat_generic_key_substring_as_sensitive() -> None:
    authority = extract_semantic_authority(
        "第一轮 claude-worker 创建 artifact.txt 且内容为 monkey_value:PUBLIC。",
        selected_agent_ids=("claude-worker",),
        step_count=1,
        phases=("implementation",),
    )
    assert authority["unresolved"] == []
    assert authority["requirements"][0]["literal"] == "monkey_value:PUBLIC"


def test_extract_does_not_treat_tokenizer_as_sensitive() -> None:
    authority = extract_semantic_authority(
        "第一轮 claude-worker 创建 artifact.txt 且内容为 tokenizer:PUBLIC。",
        selected_agent_ids=("claude-worker",),
        step_count=1,
        phases=("implementation",),
    )
    assert authority["unresolved"] == []
    assert authority["requirements"][0]["literal"] == "tokenizer:PUBLIC"


def test_extract_read_requires_complete_clause_consumption() -> None:
    authority = extract_semantic_authority(
        "First, claude-worker reads artifact.txt without approval",
        selected_agent_ids=("claude-worker",),
        step_count=1,
        phases=("implementation",),
    )
    assert authority["requirements"] == []
    assert [item["kind"] for item in authority["unresolved"]] == [
        "unsupported_clause_logic"
    ]


def test_extract_read_never_restarts_target_from_middle() -> None:
    authority = extract_semantic_authority(
        "First, claude-worker reads foo bar/artifact.txt",
        selected_agent_ids=("claude-worker",),
        step_count=1,
        phases=("implementation",),
    )
    assert authority["requirements"] == []
    assert authority["unresolved"]


def test_target_omitted_review_never_binds_to_a_future_target() -> None:
    authority = extract_semantic_authority(
        "First, claude-worker performs a read-only review and requires draft; "
        "Second, claude-worker creates artifact.txt with content exactly draft",
        selected_agent_ids=("claude-worker",),
        step_count=2,
        phases=("review", "implementation"),
    )
    assert [item["operation"] for item in authority["requirements"]] == [
        "create"
    ]
    assert [item["kind"] for item in authority["unresolved"]] == [
        "missing_target"
    ]


@pytest.mark.parametrize(
    ("future_update", "unresolved_kind"),
    [
        (
            "Third, claude-worker updates artifact.txt to exactly accepted "
            "newline without approval",
            "unsupported_clause_logic",
        ),
        (
            "Third, codex-worker updates artifact.txt to exactly accepted newline",
            "wrong_or_unknown_agent",
        ),
        (
            "Third, claude-worker updates ../artifact.txt to exactly accepted newline",
            "unsafe_target",
        ),
        (
            "Third, claude-worker updates artifact.txt to exactly accepted "
            "newline and reads audit.txt",
            "unsupported_clause_logic",
        ),
    ],
)
def test_rejected_future_update_never_aligns_review_literal(
    future_update: str,
    unresolved_kind: str,
) -> None:
    authority = extract_semantic_authority(
        "First, claude-worker creates artifact.txt with content exactly draft newline; "
        "Second, claude-worker performs a read-only review and requires accepted; "
        f"{future_update}",
        selected_agent_ids=("claude-worker",),
        step_count=3,
        phases=("implementation", "review", "revision"),
    )
    reviews = [
        item for item in authority["requirements"] if item["operation"] == "review"
    ]
    assert [item["literal"] for item in reviews] == ["accepted"]
    assert [item["kind"] for item in authority["unresolved"]] == [
        unresolved_kind
    ]


def test_future_update_without_prior_origin_never_aligns_review_literal() -> None:
    authority = extract_semantic_authority(
        "First, worker reviews artifact.txt and requires accepted; "
        "Second, worker updates artifact.txt to exactly accepted newline",
        selected_agent_ids=("worker",),
        step_count=2,
        phases=("review", "revision"),
    )
    reviews = [
        item for item in authority["requirements"] if item["operation"] == "review"
    ]
    assert [item["literal"] for item in reviews] == ["accepted"]
    assert [item["kind"] for item in authority["unresolved"]] == [
        "missing_transition_origin"
    ]


@pytest.mark.parametrize(
    "future_clauses",
    [
        "Second, worker creates artifact.txt with content exactly accepted newline",
        (
            "Third, worker updates artifact.txt to exactly accepted newline; "
            "Fourth, worker updates artifact.txt to exactly accepted newline"
        ),
        (
            "Second, worker creates artifact.txt with content exactly draft newline "
            "without approval; Third, worker updates artifact.txt to exactly accepted "
            "newline"
        ),
    ],
)
def test_non_unique_or_unestablished_future_state_never_aligns_review_literal(
    future_clauses: str,
) -> None:
    if future_clauses.startswith("Third"):
        message = (
            "First, worker creates artifact.txt with content exactly draft newline; "
            "Second, worker reviews artifact.txt and requires accepted; "
            f"{future_clauses}"
        )
        step_count = 4
        phases = ("implementation", "review", "revision", "acceptance")
    else:
        message = (
            "First, worker reviews artifact.txt and requires accepted; "
            f"{future_clauses}"
        )
        step_count = future_clauses.count("; ") + 2
        phases = tuple(f"step-{index}" for index in range(step_count))
    authority = extract_semantic_authority(
        message,
        selected_agent_ids=("worker",),
        step_count=step_count,
        phases=phases,
    )
    reviews = [
        item for item in authority["requirements"] if item["operation"] == "review"
    ]
    assert [item["literal"] for item in reviews] == ["accepted"]


def test_extractor_has_no_legacy_target_finditer_scanner() -> None:
    assert not hasattr(semantic_authority_module, "_TARGET_RE")


def test_read_repeated_path_uses_one_whole_target_fullmatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_pattern = semantic_authority_module._SAFE_TARGET_RE

    class CountingPattern:
        calls = 0

        def fullmatch(self, value: str):
            self.calls += 1
            return real_pattern.fullmatch(value)

    counting = CountingPattern()
    monkeypatch.setattr(semantic_authority_module, "_SAFE_TARGET_RE", counting)
    path = "/".join(["folder"] * 32 + ["artifact.txt"])
    authority = extract_semantic_authority(
        f"First, claude-worker reads {path}",
        selected_agent_ids=("claude-worker",),
        step_count=1,
        phases=("implementation",),
    )
    assert authority["requirements"] == []
    assert [item["kind"] for item in authority["unresolved"]] == [
        "missing_literal"
    ]
    assert counting.calls == 1


@pytest.mark.parametrize(
    "target",
    [
        "./artifact.txt",
        ".hidden/artifact.txt",
        "$HOME/artifact.txt",
        "%2e%2e/artifact.txt",
        "@scope/artifact.txt",
        "foo bar/artifact.txt",
    ],
)
def test_extract_never_starts_target_matching_from_the_middle(target: str) -> None:
    message = f"第一轮 claude-worker 创建 {target} 且内容为 draft 换行。"
    authority = extract_semantic_authority(
        message,
        selected_agent_ids=("claude-worker",),
        step_count=1,
        phases=("implementation",),
    )
    assert authority["requirements"] == []
    assert authority["unresolved"]
    assert authority["unresolved"][0]["kind"] in {
        "unsafe_target",
        "unsupported_clause_logic",
    }


def test_extract_validates_each_explicit_target_as_one_whole_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_pattern = semantic_authority_module._SAFE_TARGET_RE

    class CountingPattern:
        calls = 0

        def fullmatch(self, value: str):
            self.calls += 1
            return real_pattern.fullmatch(value)

    counting = CountingPattern()
    monkeypatch.setattr(semantic_authority_module, "_SAFE_TARGET_RE", counting)
    authority = extract_semantic_authority(
        "第一轮 claude-worker 创建 folder/artifact.txt 且内容为 draft 换行。",
        selected_agent_ids=("claude-worker",),
        step_count=1,
        phases=("implementation",),
    )
    assert len(authority["requirements"]) == 1
    assert counting.calls <= 2


def test_extract_collapses_more_than_maximum_unresolved_items() -> None:
    clauses = [
        f"claude-worker 创建 artifact-{index}.txt 且内容为 value-{index}"
        for index in range(65)
    ]
    phases = tuple(f"phase-{index}" for index in range(65))
    authority = extract_semantic_authority(
        "；".join(clauses),
        selected_agent_ids=("claude-worker",),
        step_count=65,
        phases=phases,
    )
    assert authority["requirements"] == []
    assert len(authority["unresolved"]) == 1
    assert authority["unresolved"][0]["kind"] == "unresolved_count_exceeded"


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        (
            "第一轮 claude-worker 不要创建 artifact.txt 且内容为 draft 换行。",
            "unsupported_clause_logic",
        ),
        (
            "第一轮 claude-worker 如果需要则创建 artifact.txt 且内容为 draft 换行。",
            "unsupported_clause_logic",
        ),
        (
            "第一轮 claude-worker 创建或更新 artifact.txt 且内容为 draft 换行。",
            "ambiguous_operation",
        ),
        (
            "第一轮 claude-worker 创建并更新 artifact.txt 且内容为 draft 换行。",
            "ambiguous_operation",
        ),
        (
            "第一轮 claude-worker 创建并审查 artifact.txt 且内容为 draft 换行。",
            "ambiguous_operation",
        ),
        (
            "First, claude-worker do not create artifact.txt with content exactly draft newline",
            "unsupported_clause_logic",
        ),
        (
            "First, claude-worker if needed create artifact.txt with content exactly draft newline",
            "unsupported_clause_logic",
        ),
        (
            "First, claude-worker unless approved create artifact.txt with content exactly draft newline",
            "unsupported_clause_logic",
        ),
        (
            "First, claude-worker create or update artifact.txt with content exactly draft newline",
            "ambiguous_operation",
        ),
        (
            "First, claude-worker create and update artifact.txt with content exactly draft newline",
            "ambiguous_operation",
        ),
        (
            "First, claude-worker create and review artifact.txt with content exactly draft newline",
            "ambiguous_operation",
        ),
    ],
)
def test_extract_rejects_negated_conditional_or_multi_operation_clauses(
    message: str,
    kind: str,
) -> None:
    authority = extract_semantic_authority(
        message,
        selected_agent_ids=("claude-worker",),
        step_count=1,
        phases=("implementation",),
    )
    assert authority["requirements"] == []
    assert [item["kind"] for item in authority["unresolved"]] == [kind]
    assert set(authority["unresolved"][0]) == {
        "unresolved_id",
        "kind",
        "phase",
        "agent_id",
    }


def test_extract_never_truncates_unsafe_target_suffix() -> None:
    message = (
        r"第一轮 claude-worker 创建 artifact.txt\escape 且内容为 draft 换行。"
    )
    authority = extract_semantic_authority(
        message,
        selected_agent_ids=("claude-worker",),
        step_count=1,
        phases=("implementation",),
    )
    assert authority["requirements"] == []
    assert [item["kind"] for item in authority["unresolved"]] == ["unsafe_target"]


@pytest.mark.parametrize("suffix", [":escape", "?escape", "*escape"])
def test_extract_never_downgrades_forbidden_target_suffix_to_safe_prefix(
    suffix: str,
) -> None:
    message = f"第一轮 claude-worker 创建 artifact.txt{suffix} 且内容为 draft 换行。"
    authority = extract_semantic_authority(
        message,
        selected_agent_ids=("claude-worker",),
        step_count=1,
        phases=("implementation",),
    )
    assert authority["requirements"] == []
    assert [item["kind"] for item in authority["unresolved"]] == ["unsafe_target"]


def test_extract_rejects_explicit_unknown_agent_without_rebinding() -> None:
    message = "First, attacker creates artifact.txt with content exactly draft newline"
    authority = extract_semantic_authority(
        message,
        selected_agent_ids=("claude-worker",),
        step_count=1,
        phases=("implementation",),
    )
    assert authority["requirements"] == []
    assert [item["kind"] for item in authority["unresolved"]] == [
        "wrong_or_unknown_agent"
    ]
    assert "attacker" not in json.dumps(authority)


def test_extract_rejects_selected_agents_in_wrong_step_positions() -> None:
    message = (
        "claude-worker and codex-worker. "
        "First, codex-worker creates a.txt with content exactly a; "
        "Second, claude-worker creates b.txt with content exactly b"
    )
    authority = extract_semantic_authority(
        message,
        selected_agent_ids=("claude-worker", "codex-worker"),
        step_count=2,
        phases=("implementation", "review"),
    )
    assert authority["requirements"] == []
    assert [item["kind"] for item in authority["unresolved"]] == [
        "wrong_or_unknown_agent",
        "wrong_or_unknown_agent",
    ]


def test_extract_captures_complete_multi_extension_target() -> None:
    message = (
        "第一轮 claude-worker 创建 artifact.txt.bak 且内容为 draft 换行。"
    )
    authority = extract_semantic_authority(
        message,
        selected_agent_ids=("claude-worker",),
        step_count=1,
        phases=("implementation",),
    )
    assert authority["requirements"][0]["target"] == "artifact.txt.bak"
    assert authority["unresolved"] == []


@pytest.mark.parametrize("literal", ["draft/extra", "draft=extra"])
def test_extract_never_truncates_unsupported_literal_suffix(literal: str) -> None:
    message = f"第一轮 claude-worker 创建 artifact.txt 且内容为 {literal}。"
    authority = extract_semantic_authority(
        message,
        selected_agent_ids=("claude-worker",),
        step_count=1,
        phases=("implementation",),
    )
    assert authority["requirements"] == []
    assert [item["kind"] for item in authority["unresolved"]] == [
        "unsupported_value"
    ]


def test_extract_rejects_non_ordinal_explicit_clause_count_mismatch() -> None:
    message = (
        "claude-worker 创建 a.txt 且内容为 a；"
        "claude-worker 创建 b.txt 且内容为 b"
    )
    authority = extract_semantic_authority(
        message,
        selected_agent_ids=("claude-worker",),
        step_count=1,
        phases=("implementation",),
    )
    assert authority["requirements"] == []
    assert authority["unresolved"] == [
        {
            "unresolved_id": authority["unresolved"][0]["unresolved_id"],
            "kind": "clause_count_mismatch",
            "phase": "implementation",
            "agent_id": "claude-worker",
        }
    ]


def test_extract_keeps_explicit_operation_when_literal_has_same_word() -> None:
    message = (
        "First, claude-worker create artifact.txt with content exactly create newline"
    )
    authority = extract_semantic_authority(
        message,
        selected_agent_ids=("claude-worker",),
        step_count=1,
        phases=("implementation",),
    )
    assert len(authority["requirements"]) == 1
    requirement = authority["requirements"][0]
    assert requirement["kind"] == "create"
    assert requirement["operation"] == "create"
    assert requirement["literal"] == "create\n"
    assert authority["unresolved"] == []


@pytest.mark.parametrize(
    "message",
    [
        "处理 create.txt 且内容为 draft",
        "处理 artifact.txt 且内容为 create",
    ],
)
def test_extract_does_not_classify_operation_words_inside_targets_or_values(
    message: str,
) -> None:
    authority = extract_semantic_authority(
        message,
        selected_agent_ids=("claude-worker",),
        step_count=1,
        phases=("implementation",),
    )
    assert authority["requirements"] == []
    assert [item["kind"] for item in authority["unresolved"]] == [
        "unbound_explicit_detail"
    ]


@pytest.mark.parametrize(
    "message",
    [
        r"第一轮 claude-worker 创建 C:\tmp\artifact.txt 且内容为 draft 换行。",
        r"第一轮 claude-worker 创建 tmp\artifact.txt 且内容为 draft 换行。",
    ],
)
def test_extract_rejects_windows_backslash_target_without_narrowing_it(
    message: str,
) -> None:
    authority = extract_semantic_authority(
        message,
        selected_agent_ids=("claude-worker",),
        step_count=1,
        phases=("implementation",),
    )
    assert authority["requirements"] == []
    assert [item["kind"] for item in authority["unresolved"]] == ["unsafe_target"]
    assert "artifact.txt" not in json.dumps(authority)


@pytest.mark.parametrize(
    "message",
    [
        "第二轮 claude-worker 创建 a.txt 且内容为 a；第一轮 claude-worker 创建 b.txt 且内容为 b。",
        "第一轮 claude-worker 创建 a.txt 且内容为 a；第一轮 claude-worker 创建 b.txt 且内容为 b。",
    ],
)
def test_extract_rejects_out_of_order_or_duplicate_ordinals(message: str) -> None:
    with pytest.raises(SemanticAuthorityError) as raised:
        extract_semantic_authority(
            message,
            selected_agent_ids=("claude-worker",),
            step_count=2,
            phases=("implementation", "review"),
        )
    assert raised.value.code == "extraction_consistency_invalid"


def test_constants_are_the_closed_domain() -> None:
    assert SEMANTIC_AUTHORITY_SCHEMA_VERSION == "mission-semantic-authority/v1"
    assert SEMANTIC_REQUIREMENT_KINDS == frozenset(
        {"create", "read", "review", "update", "verify", "state_transition"}
    )
    assert SEMANTIC_OPERATIONS == frozenset(
        {"create", "read", "review", "update", "verify"}
    )
    assert SEMANTIC_SENSITIVITY == frozenset({"ordinary", "secret_ref"})


def test_valid_authority_returns_an_equal_defensive_copy_and_stable_hash() -> None:
    authority = valid_authority()
    validated = validate_semantic_authority(authority)

    assert validated == authority
    assert validated is not authority
    assert validated["requirements"] is not authority["requirements"]
    validated["requirements"][0]["after"]["content_equals"] = "mutated"
    assert authority["requirements"][0]["after"]["content_equals"] == "accepted-v2"

    authority_hash = semantic_authority_hash(authority)
    assert authority_hash.startswith("sha256:")
    assert len(authority_hash) == len("sha256:") + 64

    reordered = {key: authority[key] for key in reversed(tuple(authority))}
    reordered["requirements"] = [
        {key: authority["requirements"][0][key] for key in reversed(tuple(authority["requirements"][0]))}
    ]
    assert semantic_authority_hash(reordered) == authority_hash


def test_valid_authority_helper_returns_fresh_nested_values() -> None:
    first = valid_authority()
    second = valid_authority()
    first["requirements"][0]["before"]["content_equals"] = "changed"
    assert second["requirements"][0]["before"]["content_equals"] == "draft-v1"


def test_unique_requirement_ids_preserve_non_lexicographic_generation_order() -> None:
    authority = valid_authority()
    first = deepcopy(authority["requirements"][0])
    first["requirement_id"] = "req_ffffffffffff"
    second = deepcopy(authority["requirements"][0])
    second["requirement_id"] = "req_000000000000"
    authority["requirements"] = [first, second]

    validated = validate_semantic_authority(authority)
    assert [item["requirement_id"] for item in validated["requirements"]] == [
        "req_ffffffffffff",
        "req_000000000000",
    ]


def test_unique_proposed_effect_ids_preserve_non_lexicographic_generation_order() -> None:
    authority = valid_authority()
    first = deepcopy(authority["proposed_effects"][0])
    first["proposed_effect_id"] = "prp_ffffffffffff"
    second = deepcopy(authority["proposed_effects"][0])
    second["proposed_effect_id"] = "prp_000000000000"
    authority["proposed_effects"] = [first, second]

    validated = validate_semantic_authority(authority)
    assert [item["proposed_effect_id"] for item in validated["proposed_effects"]] == [
        "prp_ffffffffffff",
        "prp_000000000000",
    ]


def test_unique_unresolved_ids_preserve_non_lexicographic_generation_order() -> None:
    authority = valid_authority()
    authority["unresolved"] = [
        {
            "unresolved_id": "unr_ffffffffffff",
            "kind": "ambiguous_target",
            "phase": "revision",
            "agent_id": "claude-worker",
        },
        {
            "unresolved_id": "unr_000000000000",
            "kind": "missing_literal",
            "phase": "implementation",
            "agent_id": "codex-worker",
        },
    ]

    validated = validate_semantic_authority(authority)
    assert [item["unresolved_id"] for item in validated["unresolved"]] == [
        "unr_ffffffffffff",
        "unr_000000000000",
    ]


def test_duplicate_unresolved_ids_still_fail_closed() -> None:
    authority = valid_authority()
    item = {
        "unresolved_id": "unr_0123456789ab",
        "kind": "ambiguous_target",
        "phase": "revision",
        "agent_id": "claude-worker",
    }
    authority["unresolved"] = [deepcopy(item), deepcopy(item)]
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(authority)
    assert raised.value.code == "unresolved_ids_not_unique"


def test_reordering_requirement_array_changes_canonical_authority_hash() -> None:
    authority = valid_authority()
    first = deepcopy(authority["requirements"][0])
    first["requirement_id"] = "req_000000000000"
    second = deepcopy(authority["requirements"][0])
    second["requirement_id"] = "req_ffffffffffff"
    authority["requirements"] = [first, second]
    reordered = deepcopy(authority)
    reordered["requirements"] = list(reversed(reordered["requirements"]))

    assert semantic_authority_hash(authority) != semantic_authority_hash(reordered)


@pytest.mark.parametrize("kind", ["create", "read", "review", "update", "verify"])
def test_literal_requirement_kinds_use_their_exact_field_set(kind: str) -> None:
    authority = valid_authority()
    authority["requirements"] = [
        {
            "requirement_id": "req_0123456789ab",
            "kind": kind,
            "target": "artifact.txt",
            "operation": kind,
            "literal": "required-content",
            "phase": "implementation",
            "agent_id": "claude-worker",
            "sensitivity": "ordinary",
        }
    ]
    assert validate_semantic_authority(authority) == authority

    authority["requirements"][0]["before"] = {"content_equals": "unexpected"}
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(authority)
    assert raised.value.code == "requirement_fields_invalid"


def test_sensitive_literal_must_be_an_explicit_reference() -> None:
    authority = valid_authority()
    authority["requirements"] = [
        {
            "requirement_id": "req_0123456789ab",
            "kind": "create",
            "target": "artifact.txt",
            "operation": "create",
            "literal": "DO_NOT_ECHO",
            "phase": "implementation",
            "agent_id": "claude-worker",
            "sensitivity": "secret_ref",
        }
    ]
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(authority)
    assert raised.value.code == "secret_value_not_reference"
    assert "DO_NOT_ECHO" not in str(raised.value)

    authority["requirements"][0]["literal"] = {
        "reference": "secret:artifact-content"
    }
    assert validate_semantic_authority(authority) == authority


def test_compact_authority_is_exact_non_leaking_and_defensive() -> None:
    authority = valid_authority()
    blockers: list[str] = []
    compact = compact_semantic_authority(
        authority,
        state="preview",
        compiled_step_count=4,
        blockers=blockers,
    )

    assert set(compact) == {
        "schema_version",
        "state",
        "authority_hash",
        "requirement_count",
        "proposed_effect_count",
        "unresolved_count",
        "compiled_step_count",
        "blockers",
    }
    assert compact == {
        "schema_version": "mission-semantic-authority/v1",
        "state": "preview",
        "authority_hash": semantic_authority_hash(authority),
        "requirement_count": 1,
        "proposed_effect_count": 1,
        "unresolved_count": 0,
        "compiled_step_count": 4,
        "blockers": [],
    }
    serialized = repr(compact)
    for forbidden in ("artifact.txt", "req_0123456789ab", "draft-v1", "accepted-v2"):
        assert forbidden not in serialized

    blockers.append("late_mutation")
    assert compact["blockers"] == []


def _extra_top_level(authority: dict[str, object]) -> None:
    authority["hostile"] = "DO_NOT_ECHO"


def _duplicate_ids(authority: dict[str, object]) -> None:
    authority["requirements"].append(deepcopy(authority["requirements"][0]))


def _unknown_kind(authority: dict[str, object]) -> None:
    authority["requirements"][0]["kind"] = "DO_NOT_ECHO"


def _absolute_target(authority: dict[str, object]) -> None:
    authority["requirements"][0]["target"] = "/tmp/DO_NOT_ECHO"


def _escaping_target(authority: dict[str, object]) -> None:
    authority["requirements"][0]["target"] = "../DO_NOT_ECHO"


def _wrong_operation(authority: dict[str, object]) -> None:
    authority["requirements"][0]["operation"] = "read"


def _missing_before(authority: dict[str, object]) -> None:
    del authority["requirements"][0]["before"]


def _missing_after(authority: dict[str, object]) -> None:
    del authority["requirements"][0]["after"]


def _malformed_hash(authority: dict[str, object]) -> None:
    authority["source_message_hash"] = "sha256:DO_NOT_ECHO"


def _unknown_sensitivity(authority: dict[str, object]) -> None:
    authority["requirements"][0]["sensitivity"] = "DO_NOT_ECHO"


def _raw_secret_field(authority: dict[str, object]) -> None:
    authority["requirements"][0]["secret"] = "DO_NOT_ECHO"


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (_extra_top_level, "authority_fields_invalid"),
        (_duplicate_ids, "requirement_ids_not_unique"),
        (_unknown_kind, "requirement_kind_invalid"),
        (_absolute_target, "target_invalid"),
        (_escaping_target, "target_invalid"),
        (_wrong_operation, "requirement_operation_invalid"),
        (_missing_before, "requirement_fields_invalid"),
        (_missing_after, "requirement_fields_invalid"),
        (_malformed_hash, "source_message_hash_invalid"),
        (_unknown_sensitivity, "sensitivity_invalid"),
        (_raw_secret_field, "requirement_fields_invalid"),
    ],
)
def test_hostile_authority_mutations_fail_with_closed_non_echoing_errors(
    mutation, code: str
) -> None:
    authority = valid_authority()
    mutation(authority)

    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(authority)

    assert raised.value.code == code
    assert str(raised.value) == code
    assert "DO_NOT_ECHO" not in str(raised.value)


@pytest.mark.parametrize(
    "target",
    [
        "",
        "a//b",
        "a/../b",
        "a\\b",
        "nul\x00name",
        ".",
        "./a",
        "artifact.txt:escape",
        "artifact.txt?escape",
        "artifact.txt*escape",
        'artifact.txt"escape',
        "artifact.txt<escape",
        "artifact.txt>escape",
        "artifact.txt|escape",
    ],
)
def test_target_rejects_ambiguous_or_escaping_paths(target: str) -> None:
    authority = valid_authority()
    authority["requirements"][0]["target"] = target
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(authority)
    assert raised.value.code == "target_invalid"
    if target:
        assert target not in str(raised.value)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("proposed_effect_id", "prp_xyz", "proposal_id_invalid"),
        ("target", "/tmp/DO_NOT_ECHO", "target_invalid"),
        ("operation", "state_transition", "proposal_operation_invalid"),
        ("sensitivity", "DO_NOT_ECHO", "sensitivity_invalid"),
    ],
)
def test_proposed_effects_enforce_id_target_operation_and_sensitivity(
    field: str, value: object, code: str
) -> None:
    authority = valid_authority()
    authority["proposed_effects"][0][field] = value
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(authority)
    assert raised.value.code == code
    assert "DO_NOT_ECHO" not in str(raised.value)


def test_proposed_effects_reject_unknown_fields_and_duplicate_ids() -> None:
    authority = valid_authority()
    authority["proposed_effects"][0]["literal"] = "DO_NOT_ECHO"
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(authority)
    assert raised.value.code == "proposal_fields_invalid"

    duplicate = valid_authority()
    duplicate["proposed_effects"].append(deepcopy(duplicate["proposed_effects"][0]))
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(duplicate)
    assert raised.value.code == "proposal_ids_not_unique"

def test_nested_values_are_exact_json_scalars_and_secret_values_are_references() -> None:
    non_scalar = valid_authority()
    non_scalar["requirements"][0]["after"] = {"content_equals": ["DO_NOT_ECHO"]}
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(non_scalar)
    assert raised.value.code == "value_constraint_invalid"

    raw_secret = valid_authority()
    raw_secret["requirements"][0]["sensitivity"] = "secret_ref"
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(raw_secret)
    assert raised.value.code == "secret_value_not_reference"

    referenced = valid_authority()
    referenced["requirements"][0]["sensitivity"] = "secret_ref"
    referenced["requirements"][0]["before"] = {"reference": "secret:artifact-before"}
    referenced["requirements"][0]["after"] = {"reference": "secret:artifact-after"}
    assert validate_semantic_authority(referenced) == referenced


@pytest.mark.parametrize("field", ["phase", "agent_id"])
def test_phase_and_agent_id_are_bounded_ordinary_scalars(field: str) -> None:
    authority = valid_authority()
    authority["requirements"][0][field] = "x" * 129
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(authority)
    assert raised.value.code == "ordinary_scalar_invalid"


@pytest.mark.parametrize("state", ["draft", "blocked", "preview", "frozen"])
def test_compact_accepts_only_closed_states(state: str) -> None:
    assert compact_semantic_authority(
        valid_authority(), state=state, compiled_step_count=0, blockers=[]
    )["state"] == state


@pytest.mark.parametrize(
    ("state", "count", "blockers", "code"),
    [
        ("DO_NOT_ECHO", 0, [], "compact_state_invalid"),
        ("preview", True, [], "compiled_step_count_invalid"),
        ("preview", -1, [], "compiled_step_count_invalid"),
        ("preview", 0, "not-a-list", "blockers_invalid"),
        ("preview", 0, ["DO_NOT_ECHO/path"], "blockers_invalid"),
        ("preview", 0, ["x" * 129], "blockers_invalid"),
    ],
)
def test_compact_rejects_invalid_state_count_and_unredacted_blockers(
    state: object, count: object, blockers: object, code: str
) -> None:
    with pytest.raises(SemanticAuthorityError) as raised:
        compact_semantic_authority(
            valid_authority(),
            state=state,
            compiled_step_count=count,
            blockers=blockers,
        )
    assert raised.value.code == code
    assert "DO_NOT_ECHO" not in str(raised.value)


def test_error_constructor_rejects_arbitrary_hostile_codes() -> None:
    with pytest.raises(ValueError) as raised:
        SemanticAuthorityError("DO_NOT_ECHO")
    assert "DO_NOT_ECHO" not in str(raised.value)

    with pytest.raises(ValueError) as raised:
        SemanticAuthorityError([])
    assert str(raised.value) == "semantic_authority_error_code_invalid"


@pytest.mark.parametrize(
    ("location", "value", "code"),
    [
        ("kind", [], "requirement_kind_invalid"),
        ("requirement_operation", [], "requirement_operation_invalid"),
        ("requirement_sensitivity", {}, "sensitivity_invalid"),
        ("proposal_operation", [], "proposal_operation_invalid"),
        ("proposal_sensitivity", {}, "sensitivity_invalid"),
    ],
)
def test_unhashable_hostile_enum_values_fail_closed(
    location: str, value: object, code: str
) -> None:
    authority = valid_authority()
    if location == "kind":
        authority["requirements"][0]["kind"] = value
    elif location == "requirement_operation":
        authority["requirements"][0]["operation"] = value
    elif location == "requirement_sensitivity":
        authority["requirements"][0]["sensitivity"] = value
    elif location == "proposal_operation":
        authority["proposed_effects"][0]["operation"] = value
    else:
        authority["proposed_effects"][0]["sensitivity"] = value

    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(authority)
    assert raised.value.code == code


def test_unhashable_compact_state_fails_closed() -> None:
    with pytest.raises(SemanticAuthorityError) as raised:
        compact_semantic_authority(
            valid_authority(), state=[], compiled_step_count=0, blockers=[]
        )
    assert raised.value.code == "compact_state_invalid"


class _HostileString(str):
    def __deepcopy__(self, memo):
        raise RuntimeError("DO_NOT_ECHO")


def test_scalar_subclasses_and_non_string_mapping_keys_fail_closed() -> None:
    schema_subclass = valid_authority()
    schema_subclass["schema_version"] = _HostileString(
        "mission-semantic-authority/v1"
    )
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(schema_subclass)
    assert raised.value.code == "schema_version_invalid"

    scalar_subclass = valid_authority()
    scalar_subclass["requirements"][0]["phase"] = _HostileString("revision")
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(scalar_subclass)
    assert raised.value.code == "ordinary_scalar_invalid"

    non_string_key = valid_authority()
    non_string_key["requirements"][0][1] = "DO_NOT_ECHO"
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(non_string_key)
    assert raised.value.code == "requirement_fields_invalid"
    assert "DO_NOT_ECHO" not in str(raised.value)


def test_compact_blocker_list_has_a_fixed_item_bound() -> None:
    with pytest.raises(SemanticAuthorityError) as raised:
        compact_semantic_authority(
            valid_authority(),
            state="blocked",
            compiled_step_count=0,
            blockers=["blocked"] * 65,
        )
    assert raised.value.code == "blockers_invalid"


@pytest.mark.parametrize("target", ["C:outside.txt", "z:folder/file.txt"])
def test_windows_drive_relative_targets_are_rejected_for_all_items(target: str) -> None:
    requirement = valid_authority()
    requirement["requirements"][0]["target"] = target
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(requirement)
    assert raised.value.code == "target_invalid"

    proposal = valid_authority()
    proposal["proposed_effects"][0]["target"] = target
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(proposal)
    assert raised.value.code == "target_invalid"


def test_unresolved_items_have_an_exact_bounded_canonical_shape() -> None:
    authority = valid_authority()
    authority["unresolved"] = [
        {
            "unresolved_id": "unr_0123456789ab",
            "kind": "ambiguous_target",
            "phase": "revision",
            "agent_id": "claude-worker",
        }
    ]
    validated = validate_semantic_authority(authority)
    assert validated == authority
    assert validated["unresolved"] is not authority["unresolved"]

    authority["unresolved"][0]["literal"] = "DO_NOT_ECHO"
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(authority)
    assert raised.value.code == "unresolved_fields_invalid"
    assert "DO_NOT_ECHO" not in str(raised.value)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("unresolved_id", "unr_bad", "unresolved_id_invalid"),
        ("kind", "Bad kind/DO_NOT_ECHO", "unresolved_kind_invalid"),
        ("phase", "x" * 129, "ordinary_scalar_invalid"),
        ("agent_id", [], "ordinary_scalar_invalid"),
    ],
)
def test_unresolved_item_fields_fail_closed(field: str, value: object, code: str) -> None:
    authority = valid_authority()
    authority["unresolved"] = [
        {
            "unresolved_id": "unr_0123456789ab",
            "kind": "ambiguous_target",
            "phase": "revision",
            "agent_id": "claude-worker",
        }
    ]
    authority["unresolved"][0][field] = value
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(authority)
    assert raised.value.code == code
    assert "DO_NOT_ECHO" not in str(raised.value)


class _HashCollidingKey:
    armed = False

    def __init__(self, target: str) -> None:
        self.target = target

    def __hash__(self) -> int:
        return hash(self.target)

    def __eq__(self, other: object) -> bool:
        if self.armed:
            raise RuntimeError("DO_NOT_ECHO")
        return False


@pytest.mark.parametrize("container", ["requirement", "proposal"])
def test_nested_hostile_keys_are_rejected_before_any_lookup(container: str) -> None:
    authority = valid_authority()
    if container == "requirement":
        del authority["requirements"][0]["requirement_id"]
        hostile_key = _HashCollidingKey("requirement_id")
        authority["requirements"][0][hostile_key] = "hostile"
        code = "requirement_fields_invalid"
    else:
        del authority["proposed_effects"][0]["proposed_effect_id"]
        hostile_key = _HashCollidingKey("proposed_effect_id")
        authority["proposed_effects"][0][hostile_key] = "hostile"
        code = "proposal_fields_invalid"
    hostile_key.armed = True
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(authority)
    assert raised.value.code == code
    assert "DO_NOT_ECHO" not in str(raised.value)


def test_validate_success_guarantees_representative_authority_can_be_hashed() -> None:
    validated = validate_semantic_authority(valid_authority())
    authority_hash = semantic_authority_hash(validated)
    assert authority_hash.startswith("sha256:")
    assert len(authority_hash) == 71


def test_lone_surrogate_is_rejected_during_validation_without_echo() -> None:
    authority = valid_authority()
    authority["requirements"][0]["after"] = {"content_equals": "\ud800"}
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(authority)
    assert raised.value.code == "unicode_scalar_invalid"
    assert str(raised.value) == "unicode_scalar_invalid"


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        (-(2**63), True),
        (2**63 - 1, True),
        (-(2**63) - 1, False),
        (2**63, False),
        (1.0e308, True),
        (-1.0e308, True),
        (1.1e308, True),
        (1.7976931348623157e308, True),
        (float("nan"), False),
        (float("inf"), False),
        (float("-inf"), False),
        (True, True),
    ],
)
def test_json_number_domain_is_explicit_and_deterministic(
    value: object, valid: bool
) -> None:
    authority = valid_authority()
    authority["requirements"][0]["after"] = {"content_equals": value}
    if valid:
        validated = validate_semantic_authority(authority)
        assert semantic_authority_hash(validated).startswith("sha256:")
    else:
        with pytest.raises(SemanticAuthorityError) as raised:
            validate_semantic_authority(authority)
        assert raised.value.code == "number_out_of_range"


def test_exact_bool_is_supported_in_literal_and_constraint_and_can_be_hashed() -> None:
    constraint = valid_authority()
    constraint["requirements"][0]["after"] = {"content_equals": False}
    validated_constraint = validate_semantic_authority(constraint)
    assert semantic_authority_hash(validated_constraint).startswith("sha256:")

    literal = valid_authority()
    literal["requirements"] = [
        {
            "requirement_id": "req_0123456789ab",
            "kind": "create",
            "target": "artifact.txt",
            "operation": "create",
            "literal": True,
            "phase": "implementation",
            "agent_id": "claude-worker",
            "sensitivity": "ordinary",
        }
    ]
    validated_literal = validate_semantic_authority(literal)
    assert semantic_authority_hash(validated_literal).startswith("sha256:")


def test_extremely_large_integer_is_rejected_before_hashing_without_echo() -> None:
    authority = valid_authority()
    authority["requirements"][0]["after"] = {"content_equals": 10**5000}
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(authority)
    assert raised.value.code == "number_out_of_range"
    assert str(raised.value) == "number_out_of_range"


def test_requirement_and_proposal_counts_are_rejected_before_item_walk() -> None:
    requirements = valid_authority()
    requirements["requirements"] = [requirements["requirements"][0]] * 20_000
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(requirements)
    assert raised.value.code == "requirements_count_exceeded"

    proposed = valid_authority()
    proposed["proposed_effects"] = [proposed["proposed_effects"][0]] * 20_000
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(proposed)
    assert raised.value.code == "proposed_effects_count_exceeded"


def test_compact_rejects_oversized_authority_with_the_same_closed_code() -> None:
    authority = valid_authority()
    authority["requirements"] = [authority["requirements"][0]] * 20_000
    with pytest.raises(SemanticAuthorityError) as raised:
        compact_semantic_authority(
            authority, state="preview", compiled_step_count=0, blockers=[]
        )
    assert raised.value.code == "requirements_count_exceeded"


def test_target_has_a_fixed_utf8_byte_bound_without_echo() -> None:
    at_boundary = valid_authority()
    at_boundary["requirements"][0]["target"] = "a" * 1024
    assert validate_semantic_authority(at_boundary) == at_boundary

    oversized = valid_authority()
    oversized["requirements"][0]["target"] = "DO_NOT_ECHO" + "a" * 4096
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(oversized)
    assert raised.value.code == "target_invalid"
    assert "DO_NOT_ECHO" not in str(raised.value)


def test_overall_canonical_authority_bytes_have_a_fixed_bound() -> None:
    authority = valid_authority()
    requirements = []
    for index in range(256):
        requirements.append(
            {
                "requirement_id": f"req_{index:012x}",
                "kind": "create",
                "target": f"artifact-{index:03d}.txt",
                "operation": "create",
                "literal": "x" * 4096,
                "phase": "implementation",
                "agent_id": "claude-worker",
                "sensitivity": "ordinary",
            }
        )
    authority["requirements"] = requirements

    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(authority)
    assert raised.value.code == "authority_size_exceeded"


@pytest.mark.parametrize("control", ["\n", "\r", "\t", "\x1f", "\x7f"])
def test_target_rejects_control_characters_without_echo(control: str) -> None:
    authority = valid_authority()
    authority["requirements"][0]["target"] = f"folder/{control}DO_NOT_ECHO.txt"
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(authority)
    assert raised.value.code == "target_invalid"
    assert "DO_NOT_ECHO" not in str(raised.value)


def test_unicode_text_must_already_be_nfc_and_is_never_silently_normalized() -> None:
    nfc = valid_authority()
    nfc["requirements"][0]["target"] = "caf\u00e9.txt"
    nfc["proposed_effects"][0]["target"] = "caf\u00e9.txt"
    assert validate_semantic_authority(nfc) == nfc

    nfd_target = valid_authority()
    nfd_target["requirements"][0]["target"] = "cafe\u0301.txt"
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(nfd_target)
    assert raised.value.code == "unicode_normalization_invalid"

    nfd_literal = valid_authority()
    nfd_literal["requirements"][0]["after"] = {
        "content_equals": "re\u0301vision"
    }
    with pytest.raises(SemanticAuthorityError) as raised:
        validate_semantic_authority(nfd_literal)
    assert raised.value.code == "unicode_normalization_invalid"


def test_canonical_hash_has_a_fixed_utf8_and_number_golden_vector() -> None:
    authority = valid_authority()
    authority["requirements"][0]["target"] = "caf\u00e9.txt"
    authority["requirements"][0]["before"] = {"content_equals": "r\u00e9vision"}
    authority["requirements"][0]["after"] = {"content_equals": 42}
    authority["proposed_effects"][0]["target"] = "caf\u00e9.txt"

    assert semantic_authority_hash(authority) == (
        "sha256:9777311c13a31bfbef7856ba1c7aba7d251440129ef8095e8d7e6fb3cd887e7f"
    )


@pytest.mark.parametrize(
    "count",
    [True, -1, 1_000_001, 10**5000],
    ids=["bool", "negative", "over-bound", "huge-int"],
)
def test_compiled_step_count_is_an_exact_bounded_integer(
    count: object,
) -> None:
    with pytest.raises(SemanticAuthorityError) as raised:
        compact_semantic_authority(
            valid_authority(),
            state="preview",
            compiled_step_count=count,
            blockers=[],
        )
    assert raised.value.code == "compiled_step_count_invalid"
    assert str(raised.value) == "compiled_step_count_invalid"


def test_compact_output_is_always_json_serializable_at_count_boundary() -> None:
    compact = compact_semantic_authority(
        valid_authority(),
        state="preview",
        compiled_step_count=1_000_000,
        blockers=["awaiting_confirmation"],
    )
    serialized = json.dumps(
        compact,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    assert isinstance(serialized, str)
