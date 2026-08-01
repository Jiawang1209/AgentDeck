from __future__ import annotations

import pytest

from agentdeck.providers.cli_failure import (
    CLI_FAILURE_REASONS,
    classify_cli_failure,
)


def test_reason_enum_is_closed() -> None:
    assert CLI_FAILURE_REASONS == (
        "credits_exhausted",
        "auth_required",
        "model_unavailable",
        "rate_limited",
        "unknown",
    )


@pytest.mark.parametrize(
    ("stdout", "stderr", "expected"),
    [
        # 真实观测(2026-08-01 round 14):claude CLI 额度耗尽,原因在 stdout
        ("You're out of usage credits. Run /usage-credits to keep using Fable 5.",
         "", "credits_exhausted"),
        ("", "usage limit reached for this account", "credits_exhausted"),
        ("Please run /login to authenticate", "", "auth_required"),
        ("", "Error: not logged in", "auth_required"),
        ("", "401 Unauthorized", "auth_required"),
        ("unknown model: claude-nope", "", "model_unavailable"),
        ("", "model not found", "model_unavailable"),
        ("", "429 Too Many Requests", "rate_limited"),
        ("", "rate limit exceeded, retry later", "rate_limited"),
        ("some entirely unrelated failure", "boom", "unknown"),
        ("", "", "unknown"),
    ],
)
def test_classify_marker_matrix(stdout: str, stderr: str, expected: str) -> None:
    assert classify_cli_failure(stdout, stderr) == expected


def test_classify_is_case_insensitive_and_bounded() -> None:
    assert classify_cli_failure("OUT OF USAGE CREDITS", "") == "credits_exhausted"
    # 超长输出不影响判定(只扫有界前缀,绝不整篇留存)
    assert classify_cli_failure("x" * 100_000 + "out of usage credits", "") == "unknown"
    assert classify_cli_failure("out of usage credits" + "x" * 100_000, "") == "credits_exhausted"


def test_classify_never_returns_provider_text() -> None:
    """分类器只返回闭合枚举码,绝不回传 provider 输出片段。"""
    secret = "sk-super-secret-token-value"
    for reason in (
        classify_cli_failure(f"out of usage credits {secret}", ""),
        classify_cli_failure("", f"401 Unauthorized {secret}"),
        classify_cli_failure(secret, secret),
    ):
        assert reason in CLI_FAILURE_REASONS
        assert secret not in reason


def test_error_carries_exit_code_and_reason_without_leaking_text() -> None:
    from agentdeck.providers.cli_subprocess import CliLeaderProviderError

    error = CliLeaderProviderError(
        "nonzero", exit_code=1, failure_reason="credits_exhausted"
    )
    assert error.exit_code == 1
    assert error.failure_reason == "credits_exhausted"
    text = str(error)
    assert "nonzero" in text
    assert "credits_exhausted" in text
    assert "exit=1" in text
    # 绝不含 provider 输出
    assert "usage credits" not in text

    # 缺省仍是今天的形状
    plain = CliLeaderProviderError("nonzero")
    assert plain.exit_code is None
    assert plain.failure_reason is None
    assert str(plain) == "CLI Leader planning failed at stage: nonzero"


def test_error_rejects_unknown_reason() -> None:
    from agentdeck.providers.cli_subprocess import CliLeaderProviderError

    with pytest.raises(ValueError):
        CliLeaderProviderError("nonzero", failure_reason="made_up")


def test_brief_stage_nonzero_reports_classified_reason(monkeypatch, tmp_path) -> None:
    """capture_output 路径(G2 planner brief 段)能分类:退出码 + 闭合枚举
    reason,且错误文本不含 provider 原文。"""
    import subprocess

    from agentdeck.providers.cli_subprocess import (
        CliLeaderProviderError,
        CodexCliProvider,
    )

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["codex"],
            returncode=1,
            stdout="You're out of usage credits. Run /usage-credits to keep going.",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(CliLeaderProviderError) as excinfo:
        CodexCliProvider().plan_brief(task="demo")

    error = excinfo.value
    assert error.stage == "nonzero"
    assert error.exit_code == 1
    assert error.failure_reason == "credits_exhausted"
    assert "usage credits" not in str(error)
    assert "exit=1" in str(error) and "reason=credits_exhausted" in str(error)


def test_planning_path_reports_exit_code_without_guessing_reason(
    monkeypatch, tmp_path
) -> None:
    """两个具体 provider 的 planning 路径刻意把 stdout/stderr 丢给 DEVNULL
    (plan 从私有文件读),没有可分类的输出——只记退出码,**绝不臆测**
    reason。今天 round 14 那次真实失败正属此路径。"""
    import subprocess

    from agentdeck.config import load_config, write_default_config
    from agentdeck.providers.base import LeaderPlanRequest
    from agentdeck.providers.cli_subprocess import (
        CliLeaderProviderError,
        CodexCliProvider,
    )

    root = tmp_path / "repo"
    root.mkdir()
    write_default_config(root)
    config = load_config(root)

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["codex"], returncode=1, stdout="", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(CliLeaderProviderError) as excinfo:
        CodexCliProvider().plan_result(
            LeaderPlanRequest(config=config, task="demo", model="m")
        )

    error = excinfo.value
    assert error.stage == "nonzero"
    assert error.exit_code == 1
    assert error.failure_reason is None
