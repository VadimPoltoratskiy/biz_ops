"""
Tests for compliance_agent.evaluate — AC-17, AC-20, AC-21, AC-25.

evaluate_all_rules() calls asyncio.run() internally. These tests are
ordinary synchronous functions — no pytest-asyncio, no running event loop.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch

from compliance_agent.config import Settings
from compliance_agent.evaluate import evaluate_all_rules, verify_evidence_quote
from compliance_agent.models import ExtractedRule, TokenUsage, VerdictResponse


# ---------------------------------------------------------------------------
# Minimal Settings for evaluate tests
# ---------------------------------------------------------------------------


def _settings() -> Settings:
    return Settings(
        api_key=None,
        model="claude-opus-5",
        marketing_text_cap=2000,
        max_concurrency=4,
        max_retries=0,  # no sleep between retries in tests
    )


# ---------------------------------------------------------------------------
# verify_evidence_quote — AC-20, AC-21
# ---------------------------------------------------------------------------


def test_verify_quote_exact_substring():
    """AC-20: an exact substring is returned unchanged."""
    assert verify_evidence_quote("get rich", "get rich tomorrow") == "get rich"


def test_verify_quote_not_substring_returns_none():
    """AC-21: a paraphrased / non-exact quote is nulled."""
    assert verify_evidence_quote("get wealthy", "get rich tomorrow") is None


def test_verify_quote_none_input_returns_none():
    """None input → None output (no-op guard)."""
    assert verify_evidence_quote(None, "any marketing text") is None


def test_verify_quote_case_sensitive():
    """The check is case-sensitive — Python 'in' operator on str."""
    assert verify_evidence_quote("Get Rich", "get rich tomorrow") is None


def test_verify_quote_full_text_is_own_substring():
    """The full text is trivially a substring of itself."""
    text = "get rich tomorrow"
    assert verify_evidence_quote(text, text) == text


def test_verify_quote_partial_overlap_not_accepted():
    """A quote with extra characters at the end is not a substring."""
    assert verify_evidence_quote("get rich tomorrowXXX", "get rich tomorrow") is None


# ---------------------------------------------------------------------------
# evaluate_all_rules — evidence-quote guard in the full pipeline
# ---------------------------------------------------------------------------


def test_evidence_quote_nulled_for_nonsubstring(sample_rule, mock_async_client):
    """
    AC-21: when the model returns a quote that is not a verbatim substring,
    evaluate.py nulls it before returning the verdict.

    The outcome field (non-compliant) is preserved — the nulling only affects
    the evidence_quote. The verdict is still a finding; it just cannot be
    presented as one anchored by a confirmed evidence passage.
    """
    marketing_text = "get rich tomorrow"
    bad_verdict = VerdictResponse(
        rule_id=sample_rule.rule_id,
        outcome="non-compliant",
        reasoning="Claims guaranteed returns.",
        confidence="high",
        evidence_quote="get wealthy fast",  # NOT a substring of marketing_text
        suggested_fix="Remove the claim.",
    )
    usage = TokenUsage(input_tokens=50, output_tokens=20)

    async def fake_evaluate(rule, mt, settings, prompt, semaphore, client):
        return (bad_verdict, usage)

    with patch("compliance_agent.llm.evaluate_rule", side_effect=fake_evaluate):
        verdicts, _ = evaluate_all_rules([sample_rule], marketing_text, _settings())

    assert len(verdicts) == 1
    assert verdicts[0].evidence_quote is None           # guard applied
    assert verdicts[0].outcome == "non-compliant"       # verdict intact


def test_evidence_quote_preserved_when_exact_substring(sample_rule, mock_async_client):
    """AC-20: a quote that IS a verbatim substring is preserved unchanged."""
    marketing_text = "get rich tomorrow"
    good_verdict = VerdictResponse(
        rule_id=sample_rule.rule_id,
        outcome="non-compliant",
        reasoning="Uses misleading phrase.",
        confidence="high",
        evidence_quote="get rich",  # IS a substring
        suggested_fix="Remove the claim.",
    )
    usage = TokenUsage(input_tokens=50, output_tokens=20)

    async def fake_evaluate(rule, mt, settings, prompt, semaphore, client):
        return (good_verdict, usage)

    with patch("compliance_agent.llm.evaluate_rule", side_effect=fake_evaluate):
        verdicts, _ = evaluate_all_rules([sample_rule], marketing_text, _settings())

    assert verdicts[0].evidence_quote == "get rich"


# ---------------------------------------------------------------------------
# Per-rule failure isolation — AC-17, AC-25
# ---------------------------------------------------------------------------


def test_per_rule_failure_isolation(sample_rule, mock_async_client):
    """
    AC-17, AC-25: one rule failing (RuntimeError) must not cancel or corrupt
    the evaluation of other rules.

    Expected result:
      - Both verdicts are present (no rule is silently dropped).
      - The failed rule (rule-b) has outcome 'error'.
      - The successful rule (rule-a) has outcome 'compliant'.
    """
    marketing_text = "get rich tomorrow"
    rule_a = sample_rule                                   # COBS-4.2.1R-prohibition-1
    rule_b = sample_rule.model_copy(update={"rule_id": "rule-b"})

    call_count = 0

    async def fake_evaluate(rule, mt, settings, prompt, semaphore, client):
        nonlocal call_count
        call_count += 1
        if rule.rule_id == "rule-b":
            raise RuntimeError("Simulated evaluation failure")
        usage = TokenUsage(input_tokens=10, output_tokens=5)
        verdict = VerdictResponse(
            rule_id=rule.rule_id,
            outcome="compliant",
            reasoning="No issue found.",
            confidence="high",
            evidence_quote=None,
            suggested_fix=None,
        )
        return (verdict, usage)

    with patch("compliance_agent.llm.evaluate_rule", side_effect=fake_evaluate):
        verdicts, usages = evaluate_all_rules(
            [rule_a, rule_b], marketing_text, _settings()
        )

    # Both rules evaluated.
    assert call_count == 2
    # Both verdicts returned (no rule missing from output).
    assert len(verdicts) == 2

    by_id = {v.rule_id: v for v in verdicts}

    assert by_id["COBS-4.2.1R-prohibition-1"].outcome == "compliant"

    assert by_id["rule-b"].outcome == "error"
    assert "RuntimeError" in by_id["rule-b"].reasoning


def test_per_rule_error_verdict_contains_exception_detail(sample_rule, mock_async_client):
    """AC-25: the error verdict reasoning includes the exception type and message."""
    async def fake_evaluate(rule, mt, settings, prompt, semaphore, client):
        raise ValueError("unexpected schema mismatch")

    with patch("compliance_agent.llm.evaluate_rule", side_effect=fake_evaluate):
        verdicts, _ = evaluate_all_rules([sample_rule], "some text", _settings())

    assert verdicts[0].outcome == "error"
    assert "ValueError" in verdicts[0].reasoning
    assert "unexpected schema mismatch" in verdicts[0].reasoning


# ---------------------------------------------------------------------------
# evaluate_all_rules — edge cases
# ---------------------------------------------------------------------------


def test_empty_rules_list_returns_immediately(settings):
    """evaluate_all_rules with no rules returns ([], []) without any LLM call."""
    verdicts, usages = evaluate_all_rules([], "some marketing text", settings)
    assert verdicts == []
    assert usages == []


def test_single_rule_returns_single_verdict(sample_rule, mock_async_client):
    """One rule → one verdict in the output list."""
    verdict = VerdictResponse(
        rule_id=sample_rule.rule_id,
        outcome="compliant",
        reasoning="ok",
        confidence="high",
        evidence_quote=None,
        suggested_fix=None,
    )
    usage = TokenUsage(input_tokens=10, output_tokens=5)

    async def fake_evaluate(rule, mt, settings, prompt, semaphore, client):
        return (verdict, usage)

    with patch("compliance_agent.llm.evaluate_rule", side_effect=fake_evaluate):
        verdicts, usages = evaluate_all_rules([sample_rule], "text", _settings())

    assert len(verdicts) == 1
    assert len(usages) == 1
