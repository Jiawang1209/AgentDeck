from __future__ import annotations

from agentdeck.delegation_match import CompositeMatch, MatchedSegment, normalize_match

PREFIXES = ["node tests/", "git add", "git commit", "git diff", "git status"]

# round 12 逐字样本 ①:env 前缀赋值
ENV_SAMPLE = "REPRODUCE_UNCONTROLLED_BOOTSTRAP=1 node tests/focus-carousel-tab-order.mjs"

# round 12 逐字样本 ②:for 循环包装(重定向落 /tmp、条件 tail、exit)
LOOP_SAMPLE = (
    "for run_id in 1 2 3 4 5; "
    "do node tests/focus-carousel-tab-order.mjs > /tmp/msg-target-${run_id}.log 2>&1; "
    "run_code=$?; "
    'echo "target_run_${run_id}_exit=${run_code}"; '
    "if [ ${run_code} -ne 0 ]; "
    "then tail -80 /tmp/msg-target-${run_id}.log; "
    "exit ${run_code}; "
    "fi; done"
)

# round 12 逐字样本 ③:多命令链(node --check 段无对应委托)
CHAIN_SAMPLE = (
    "node tests/focus-carousel-tab-order.mjs > /tmp/final-focus.log 2>&1; "
    "focus_code=$?; "
    'echo "final_focus_exit=${focus_code}"; '
    "node tests/back-to-top.mjs > /tmp/final-b2t.log 2>&1; "
    "node --check tests/focus-carousel-tab-order.mjs; "
    "git diff --check"
)


def test_env_prefix_sample_matches() -> None:
    result = normalize_match(ENV_SAMPLE, PREFIXES)
    assert isinstance(result, CompositeMatch)
    assert len(result.segments) == 1
    assert result.segments[0].via == "node tests/"
    assert result.segments[0].segment == ENV_SAMPLE


def test_loop_sample_matches_with_glue_provenance() -> None:
    result = normalize_match(LOOP_SAMPLE, PREFIXES)
    assert result is not None
    vias = [s.via for s in result.segments]
    # 9 段:for 头/do node…/赋值/echo/if [ ]/then tail/exit/fi/done
    assert len(vias) == 9
    assert vias.count("node tests/") == 1
    assert all(v in ("node tests/", "glue") for v in vias)


def test_chain_sample_rejected_without_node_check_prefix() -> None:
    # node --check 段不命中任何委托 → 整体 None(绝不部分放行)
    assert normalize_match(CHAIN_SAMPLE, PREFIXES) is None
    # 人类显式补 grant node --check tests/ 前缀后整链命中
    widened = PREFIXES + ["node --check tests/"]
    result = normalize_match(CHAIN_SAMPLE, widened)
    assert result is not None
    vias = [s.via for s in result.segments]
    assert "node --check tests/" in vias
    assert "git diff" in vias


def test_dangerous_chain_rejected() -> None:
    assert normalize_match("node tests/x.mjs; rm -rf /", PREFIXES) is None


def test_command_substitution_and_eval_rejected() -> None:
    assert normalize_match("node tests/$(whoami).mjs", PREFIXES) is None
    assert normalize_match("node tests/`id`.mjs", PREFIXES) is None
    assert normalize_match("eval node tests/x.mjs", PREFIXES) is None
    assert normalize_match("node tests/x.mjs; source /tmp/env.sh", PREFIXES) is None
    assert normalize_match("node tests/x.mjs <(cat /etc/passwd)", PREFIXES) is None
    assert normalize_match("node tests/x.mjs << EOF", PREFIXES) is None


def test_input_redirect_and_background_rejected() -> None:
    assert normalize_match("node tests/x.mjs < /etc/passwd", PREFIXES) is None
    assert normalize_match("node tests/x.mjs & node tests/y.mjs", PREFIXES) is None


def test_redirect_targets_must_be_tmp_confined() -> None:
    assert normalize_match("node tests/x.mjs > /etc/evil", PREFIXES) is None
    assert normalize_match("node tests/x.mjs > /tmp/../etc/evil", PREFIXES) is None
    assert normalize_match("node tests/x.mjs > /tmp/ok.log", PREFIXES) is not None
    assert normalize_match("node tests/x.mjs >> /tmp/ok.log 2>&1", PREFIXES) is not None


def test_tail_glue_is_tmp_confined() -> None:
    assert normalize_match("node tests/x.mjs; tail -5 /tmp/x.log", PREFIXES) is not None
    assert normalize_match("node tests/x.mjs; tail -5 /tmp/../etc/passwd", PREFIXES) is None
    assert normalize_match("node tests/x.mjs; tail -5 /etc/passwd", PREFIXES) is None


def test_glue_alone_never_matches() -> None:
    assert normalize_match('echo "hi"; exit 0', PREFIXES) is None
    assert normalize_match("x=1", PREFIXES) is None


def test_quoted_separator_does_not_split() -> None:
    result = normalize_match('node tests/x.mjs; echo "a; rm -rf /"', PREFIXES)
    assert result is not None
    assert len(result.segments) == 2


def test_unbalanced_quote_rejected() -> None:
    assert normalize_match('node tests/x.mjs; echo "broken', PREFIXES) is None


def test_pipe_segments_require_coverage() -> None:
    # 管道两侧都是独立段:ps/rg 均不在委托或胶水内 → 拒
    assert normalize_match("ps -axo pid= | rg agentdeck", PREFIXES) is None


def test_empty_and_no_prefixes_rejected() -> None:
    assert normalize_match("", PREFIXES) is None
    assert normalize_match("   ", PREFIXES) is None
    assert normalize_match("node tests/x.mjs", []) is None


def test_matched_segment_shape() -> None:
    result = normalize_match("node tests/x.mjs", PREFIXES)
    assert result == CompositeMatch((MatchedSegment("node tests/x.mjs", "node tests/"),))
