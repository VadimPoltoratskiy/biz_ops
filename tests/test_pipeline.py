"""
Tests for compliance_agent.pipeline — exit-code wiring and stage-failure recording.

All tests use the tmp_root fixture for hermetic runs/ isolation. The real
runs/ directory is never touched.
"""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from compliance_agent.config import Settings
from compliance_agent.llm import LLMAuthError, LLMBadRequestError, LLMRetryExhaustedError
from compliance_agent.models import (
    ExtractedRule,
    ExtractedRulesList,
    TokenUsage,
    VerdictResponse,
)
from compliance_agent import pipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings(max_retries: int = 0) -> Settings:
    return Settings(
        api_key=None,
        model="claude-opus-5",
        marketing_text_cap=2000,
        max_concurrency=4,
        max_retries=max_retries,
    )


def _rule(rule_id: str = "COBS-4.2.1R-prohibition-1") -> ExtractedRule:
    return ExtractedRule(
        rule_id=rule_id,
        citation="COBS 4.2.1 [R] (effective 01/01/2020)",
        source_quote="A firm must not mislead.",
        obligation_type="prohibition",
        check_question="Does the text mislead?",
        precondition="Always applicable.",
        severity="high",
        failure_indicators=["misleading claim"],
    )


def _verdict(rule_id: str, outcome: str) -> VerdictResponse:
    return VerdictResponse(
        rule_id=rule_id,
        outcome=outcome,
        reasoning="test",
        confidence="high",
        evidence_quote=None,
        suggested_fix=None,
    )


def _patch_extraction(rules: list[ExtractedRule]):
    """Patch llm.extract_rules to return the given rules."""
    artifact = ExtractedRulesList(rules=rules)
    usage = TokenUsage(input_tokens=100, output_tokens=50)
    return patch("compliance_agent.llm.extract_rules", return_value=(artifact, usage))


def _patch_evaluation(verdicts: list[VerdictResponse]):
    """
    Patch evaluate_all_rules as imported by pipeline.

    The pipeline does `from compliance_agent.evaluate import evaluate_all_rules`,
    so we must patch the name in the pipeline module's namespace.
    """
    usages = [TokenUsage(input_tokens=50, output_tokens=20)] * len(verdicts)
    return patch(
        "compliance_agent.pipeline.evaluate_all_rules",
        return_value=(verdicts, usages),
    )


def _read_history(tmp_root: Path) -> list[dict]:
    """Parse runs/history.jsonl into a list of dicts."""
    history_file = tmp_root / "runs" / "history.jsonl"
    if not history_file.exists():
        return []
    return [
        json.loads(line)
        for line in history_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ---------------------------------------------------------------------------
# Stage 1 — ingestion failures (no LLM calls expected)
# ---------------------------------------------------------------------------


def test_empty_text_exits_2_no_llm_call(tmp_root):
    """Empty marketing text → exit 2 before any LLM call (AC-5)."""
    with patch("compliance_agent.llm.extract_rules") as mock_extract:
        code = pipeline.run_check("", _settings(), refresh=False)
    assert code == 2
    mock_extract.assert_not_called()


def test_whitespace_only_text_exits_2_no_llm_call(tmp_root):
    """Whitespace-only text → exit 2 before any LLM call (AC-5)."""
    with patch("compliance_agent.llm.extract_rules") as mock_extract:
        code = pipeline.run_check("   \n  ", _settings(), refresh=False)
    assert code == 2
    mock_extract.assert_not_called()


def test_overlimit_text_exits_2_no_llm_call(tmp_root):
    """2001-code-point text → exit 2 before any LLM call (AC-3)."""
    text = "a" * 2001
    with patch("compliance_agent.llm.extract_rules") as mock_extract:
        code = pipeline.run_check(text, _settings(), refresh=False)
    assert code == 2
    mock_extract.assert_not_called()


def test_exactly_2000_codepoints_proceeds_to_llm(tmp_root):
    """Exactly 2000-code-point text passes ingestion (AC-3 boundary)."""
    text = "a" * 2000
    rule = _rule()
    with _patch_extraction([rule]):
        with _patch_evaluation([_verdict(rule.rule_id, "compliant")]):
            code = pipeline.run_check(text, _settings(), refresh=False)
    assert code == 0


# ---------------------------------------------------------------------------
# Stage 2 — decomposition failures
# ---------------------------------------------------------------------------


def test_auth_error_during_extraction_exits_2(tmp_root):
    """LLMAuthError during extraction → exit 2 (fail-fast-nonretryable)."""
    with patch("compliance_agent.llm.extract_rules", side_effect=LLMAuthError("bad key")):
        code = pipeline.run_check("valid marketing text", _settings(), refresh=False)
    assert code == 2


def test_bad_request_error_during_extraction_exits_2(tmp_root):
    """LLMBadRequestError during extraction → exit 2."""
    with patch("compliance_agent.llm.extract_rules", side_effect=LLMBadRequestError("400")):
        code = pipeline.run_check("valid marketing text", _settings(), refresh=False)
    assert code == 2


def test_retry_exhausted_error_exits_2(tmp_root):
    """LLMRetryExhaustedError → exit 2 (retryable-exhausted)."""
    with patch(
        "compliance_agent.llm.extract_rules",
        side_effect=LLMRetryExhaustedError("gave up"),
    ):
        code = pipeline.run_check("valid marketing text", _settings(), refresh=False)
    assert code == 2


def test_zero_rules_exits_2(tmp_root):
    """AC-37: extraction yielding zero rules → exit 2."""
    with _patch_extraction([]):
        code = pipeline.run_check("valid marketing text", _settings(), refresh=False)
    assert code == 2


# ---------------------------------------------------------------------------
# Exit-code precedence (AC-27, AC-36, AC-38)
# ---------------------------------------------------------------------------


def test_error_verdict_plus_noncompliant_exits_2(tmp_root):
    """
    AC-27: any error verdict → exit 2 even when a confirmed breach is also present.

    The fail-safe rule: incomplete (exit 2) wins over findings (exit 1).
    """
    rule_a = _rule("rule-a")
    rule_b = _rule("rule-b")
    verdicts = [
        _verdict("rule-a", "non-compliant"),
        _verdict("rule-b", "error"),
    ]
    with _patch_extraction([rule_a, rule_b]):
        with _patch_evaluation(verdicts):
            code = pipeline.run_check("valid marketing text", _settings(), refresh=False)
    assert code == 2


def test_unclear_verdict_exits_1(tmp_root):
    """AC-36: unclear verdict → findings → exit 1 (not a pass)."""
    rule = _rule()
    with _patch_extraction([rule]):
        with _patch_evaluation([_verdict(rule.rule_id, "unclear")]):
            code = pipeline.run_check("valid marketing text", _settings(), refresh=False)
    assert code == 1


def test_noncompliant_verdict_exits_1(tmp_root):
    """Non-compliant verdict → findings → exit 1."""
    rule = _rule()
    with _patch_extraction([rule]):
        with _patch_evaluation([_verdict(rule.rule_id, "non-compliant")]):
            code = pipeline.run_check("valid marketing text", _settings(), refresh=False)
    assert code == 1


def test_all_not_applicable_exits_0(tmp_root):
    """AC-38: all not-applicable → not-assessed → exit 0."""
    rule_a = _rule("rule-a")
    rule_b = _rule("rule-b")
    verdicts = [
        _verdict("rule-a", "not-applicable"),
        _verdict("rule-b", "not-applicable"),
    ]
    with _patch_extraction([rule_a, rule_b]):
        with _patch_evaluation(verdicts):
            code = pipeline.run_check("valid marketing text", _settings(), refresh=False)
    assert code == 0


def test_all_compliant_exits_0(tmp_root):
    """All compliant → clean → exit 0."""
    rule = _rule()
    with _patch_extraction([rule]):
        with _patch_evaluation([_verdict(rule.rule_id, "compliant")]):
            code = pipeline.run_check("valid marketing text", _settings(), refresh=False)
    assert code == 0


# ---------------------------------------------------------------------------
# Stage-failure recording (AC-31)
# ---------------------------------------------------------------------------


def test_stage_failure_records_history_line(tmp_root):
    """
    AC-31: a failed run (LLMAuthError) still writes its run record and appends
    an entry to runs/history.jsonl.
    """
    with patch("compliance_agent.llm.extract_rules", side_effect=LLMAuthError("bad key")):
        code = pipeline.run_check("valid marketing text", _settings(), refresh=False)

    assert code == 2

    # history.jsonl must exist with exactly one entry.
    history = _read_history(tmp_root)
    assert len(history) == 1
    entry = history[0]
    assert entry["exit_code"] == 2
    assert entry["overall_outcome"] == "incomplete"
    assert "run_id" in entry


def test_stage_failure_writes_run_artifacts(tmp_root):
    """
    AC-31: report.md and run.json are written to the run directory even on failure.
    """
    with patch("compliance_agent.llm.extract_rules", side_effect=LLMAuthError("bad key")):
        pipeline.run_check("valid marketing text", _settings(), refresh=False)

    history = _read_history(tmp_root)
    assert len(history) == 1

    run_dir = tmp_root / history[0]["run_dir"]
    assert (run_dir / "report.md").exists()
    assert (run_dir / "run.json").exists()


def test_successful_run_appends_history(tmp_root):
    """A successful run also appends a history line with exit_code 0."""
    rule = _rule()
    with _patch_extraction([rule]):
        with _patch_evaluation([_verdict(rule.rule_id, "compliant")]):
            code = pipeline.run_check("valid marketing text", _settings(), refresh=False)

    assert code == 0
    history = _read_history(tmp_root)
    assert len(history) == 1
    assert history[0]["exit_code"] == 0
    assert history[0]["overall_outcome"] == "clean"


def test_multiple_runs_each_append_one_history_line(tmp_root):
    """AC-29: each run appends exactly one line; multiple runs accumulate correctly."""
    rule = _rule()
    for _ in range(3):
        with _patch_extraction([rule]):
            with _patch_evaluation([_verdict(rule.rule_id, "compliant")]):
                pipeline.run_check("valid marketing text", _settings(), refresh=False)

    history = _read_history(tmp_root)
    assert len(history) == 3


def test_ingestion_failure_still_writes_artifacts(tmp_root):
    """An ingestion failure (empty text) still writes partial artifacts."""
    code = pipeline.run_check("", _settings(), refresh=False)
    assert code == 2

    # runs/ should have exactly one run directory.
    run_dirs = [d for d in (tmp_root / "runs").iterdir() if d.is_dir()]
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    assert (run_dir / "report.md").exists()
    assert (run_dir / "run.json").exists()


# ---------------------------------------------------------------------------
# Stage 3 — the evaluation stage record must reflect per-rule failures
#
# Per-rule isolation (AC-17/AC-25) deliberately routes rule-level exceptions into
# `error` verdicts rather than failing the stage. The regression these tests guard
# is that isolation must not report a stage as successful when nothing was actually
# evaluated — e.g. an invalid API key with a fresh rules cache fails every call.
# ---------------------------------------------------------------------------


def _eval_stage(stages: list[dict]) -> dict:
    return next(s for s in stages if s["stage"] == "evaluation")


def test_all_rule_evaluations_failing_marks_stage_failed(tmp_root):
    """Every rule erroring is a stage failure, not a success with error verdicts."""
    rules = [_rule("rule-a"), _rule("rule-b")]
    verdicts = [_verdict("rule-a", "error"), _verdict("rule-b", "error")]

    with _patch_extraction(rules), _patch_evaluation(verdicts):
        exit_code = pipeline.run_check("Some marketing text", _settings(), False)

    assert exit_code == 2

    run_id = _read_history(tmp_root)[-1]["run_id"]
    record = json.loads(
        (tmp_root / "runs" / run_id / "run.json").read_text(encoding="utf-8")
    )
    stage = _eval_stage(record["stages"])
    assert stage["success"] is False
    assert stage["failure_cause"] == "fail-fast-nonretryable"
    assert "All 2 rule evaluations failed" in stage["detail"]


def test_partial_rule_failure_keeps_stage_successful_but_records_count(tmp_root):
    """One rule failing is isolated: the stage succeeded, but says how many failed."""
    rules = [_rule("rule-a"), _rule("rule-b")]
    verdicts = [_verdict("rule-a", "compliant"), _verdict("rule-b", "error")]

    with _patch_extraction(rules), _patch_evaluation(verdicts):
        exit_code = pipeline.run_check("Some marketing text", _settings(), False)

    assert exit_code == 2  # any error verdict still means an incomplete check

    run_id = _read_history(tmp_root)[-1]["run_id"]
    record = json.loads(
        (tmp_root / "runs" / run_id / "run.json").read_text(encoding="utf-8")
    )
    stage = _eval_stage(record["stages"])
    assert stage["success"] is True
    assert stage["detail"] == "1 of 2 rule evaluations failed"


def test_no_rule_failures_leaves_stage_detail_empty(tmp_root):
    """A fully successful evaluation carries no failure detail."""
    rules = [_rule("rule-a")]
    verdicts = [_verdict("rule-a", "compliant")]

    with _patch_extraction(rules), _patch_evaluation(verdicts):
        exit_code = pipeline.run_check("Some marketing text", _settings(), False)

    assert exit_code == 0

    run_id = _read_history(tmp_root)[-1]["run_id"]
    record = json.loads(
        (tmp_root / "runs" / run_id / "run.json").read_text(encoding="utf-8")
    )
    stage = _eval_stage(record["stages"])
    assert stage["success"] is True
    assert stage["detail"] is None
