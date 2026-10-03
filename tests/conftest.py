"""
Shared fixtures for the compliance-agent test suite.

All LLM calls are mocked at the llm.py boundary. No ANTHROPIC_API_KEY is
required to run this suite.
"""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from compliance_agent.config import Settings
from compliance_agent.models import (
    ExtractedRule,
    ExtractedRulesList,
    VerdictResponse,
    TokenUsage,
)


# ---------------------------------------------------------------------------
# Settings fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def settings() -> Settings:
    """Minimal Settings with max_retries=0 to avoid sleep delays in tests."""
    return Settings(
        api_key=None,
        model="claude-opus-5",
        marketing_text_cap=2000,
        max_concurrency=4,
        max_retries=0,
    )


# ---------------------------------------------------------------------------
# Domain object fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_rule() -> ExtractedRule:
    return ExtractedRule(
        rule_id="COBS-4.2.1R-prohibition-1",
        citation="COBS 4.2.1 [R] (effective 01/12/2001)",
        source_quote="A firm must not communicate a financial promotion that is misleading.",
        obligation_type="prohibition",
        check_question=(
            "Does the marketing text contain any claim or implication that is "
            "false or likely to create a misleading impression?"
        ),
        precondition="Always applicable to any financial promotion.",
        severity="high",
        failure_indicators=[
            "guarantees returns",
            "uses 'risk-free'",
            "promises specific gains",
        ],
    )


@pytest.fixture
def sample_verdict_compliant(sample_rule: ExtractedRule) -> VerdictResponse:
    return VerdictResponse(
        rule_id=sample_rule.rule_id,
        outcome="compliant",
        reasoning="The text includes a risk warning.",
        confidence="high",
        evidence_quote=None,
        suggested_fix=None,
    )


@pytest.fixture
def sample_verdict_noncompliant(sample_rule: ExtractedRule) -> VerdictResponse:
    return VerdictResponse(
        rule_id=sample_rule.rule_id,
        outcome="non-compliant",
        reasoning="Claims guaranteed returns.",
        confidence="high",
        evidence_quote="get rich tomorrow",
        suggested_fix="Remove the earnings promise.",
    )


# ---------------------------------------------------------------------------
# LLM mock fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_async_client():
    """
    Patches llm._make_async_client to return a mock with an async close().

    Required for any test that calls evaluate_all_rules, which creates one
    shared AsyncAnthropic client per run and awaits client.close() in a
    finally block.
    """
    client = MagicMock()
    client.close = AsyncMock()
    with patch("compliance_agent.llm._make_async_client", return_value=client):
        yield client


@pytest.fixture
def mock_extract_rules(sample_rule: ExtractedRule):
    """Patches llm.extract_rules to return one rule without an API call."""
    artifact = ExtractedRulesList(rules=[sample_rule])
    usage = TokenUsage(input_tokens=100, output_tokens=50)
    with patch("compliance_agent.llm.extract_rules", return_value=(artifact, usage)):
        yield


@pytest.fixture
def mock_evaluate_rule_ok(sample_verdict_compliant: VerdictResponse):
    """
    Patches llm.evaluate_rule to return a compliant verdict.

    Uses the 6-parameter signature that matches llm.evaluate_rule:
      (rule, marketing_text, settings, prompt, semaphore, client)
    """
    usage = TokenUsage(input_tokens=50, output_tokens=20)

    async def _evaluate(rule, marketing_text, settings, prompt, semaphore, client):
        return (sample_verdict_compliant, usage)

    with patch("compliance_agent.llm.evaluate_rule", side_effect=_evaluate):
        yield


# ---------------------------------------------------------------------------
# Hermetic repository root for pipeline tests
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_root(tmp_path: Path, monkeypatch):
    """
    Hermetic repository root for pipeline tests.

    Creates the minimal directory structure that pipeline.run_check needs
    and patches repo_root in the pipeline module to point to tmp_path.
    Tests that use this fixture are fully isolated from the real runs/ directory.
    """
    # Create data/regulations with a minimal regulation file
    data_dir = tmp_path / "data" / "regulations"
    data_dir.mkdir(parents=True)
    (data_dir / "fca-cobs-4-financial-promotions.txt").write_text(
        "RETRIEVED: 2026-09-03\n"
        "A firm must not communicate a misleading financial promotion.\n",
        encoding="utf-8",
    )
    # Create rules/ and runs/ directories
    (tmp_path / "rules").mkdir()
    (tmp_path / "runs").mkdir()

    # Patch repo_root in the pipeline module so all path helpers derive from tmp_path
    monkeypatch.setattr("compliance_agent.pipeline.repo_root", lambda: tmp_path)
    return tmp_path
