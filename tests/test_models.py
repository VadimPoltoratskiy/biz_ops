"""
Tests for compliance_agent.models — AC-33, AC-34.

Validates that Pydantic rejects invalid enumeration values at model construction.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from compliance_agent.models import (
    ExtractedRule,
    RulesCacheArtifact,
    VerdictResponse,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valid_rule_kwargs() -> dict:
    return {
        "rule_id": "COBS-4.2.1R-prohibition-1",
        "citation": "COBS 4.2.1 [R] (effective 01/12/2001)",
        "source_quote": "A firm must not mislead.",
        "obligation_type": "prohibition",
        "check_question": "Does the text mislead?",
        "precondition": "Always applicable.",
        "severity": "high",
        "failure_indicators": ["misleading claim"],
    }


# ---------------------------------------------------------------------------
# obligation_type validation (AC-33)
# ---------------------------------------------------------------------------


def test_obligation_type_invalid_value_rejected():
    """AC-33: obligation_type must be one of the six defined values."""
    kwargs = _valid_rule_kwargs()
    kwargs["obligation_type"] = "undefined_type"
    with pytest.raises(ValidationError):
        ExtractedRule(**kwargs)


def test_obligation_type_all_six_values_accepted():
    """AC-33: all six obligation_type values are valid."""
    valid_types = (
        "mandatory_disclosure",
        "prohibition",
        "balance",
        "presentation",
        "substantiation",
        "identification",
    )
    for ot in valid_types:
        kwargs = _valid_rule_kwargs()
        kwargs["obligation_type"] = ot
        rule = ExtractedRule(**kwargs)
        assert rule.obligation_type == ot


def test_obligation_type_empty_string_rejected():
    """Empty string is not a valid obligation type."""
    kwargs = _valid_rule_kwargs()
    kwargs["obligation_type"] = ""
    with pytest.raises(ValidationError):
        ExtractedRule(**kwargs)


# ---------------------------------------------------------------------------
# severity validation (AC-34)
# ---------------------------------------------------------------------------


def test_severity_invalid_value_rejected():
    """AC-34: severity must be 'high', 'medium', or 'low'."""
    kwargs = _valid_rule_kwargs()
    kwargs["severity"] = "critical"
    with pytest.raises(ValidationError):
        ExtractedRule(**kwargs)


def test_severity_all_three_values_accepted():
    """AC-34: all three severity values are valid."""
    for sev in ("high", "medium", "low"):
        kwargs = _valid_rule_kwargs()
        kwargs["severity"] = sev
        rule = ExtractedRule(**kwargs)
        assert rule.severity == sev


def test_severity_uppercase_rejected():
    """Severity values are case-sensitive — 'HIGH' is invalid."""
    kwargs = _valid_rule_kwargs()
    kwargs["severity"] = "HIGH"
    with pytest.raises(ValidationError):
        ExtractedRule(**kwargs)


# ---------------------------------------------------------------------------
# failure_indicators validation
# ---------------------------------------------------------------------------


def test_failure_indicators_empty_list_rejected():
    """failure_indicators must have at least one element (Field min_length=1)."""
    kwargs = _valid_rule_kwargs()
    kwargs["failure_indicators"] = []
    with pytest.raises(ValidationError):
        ExtractedRule(**kwargs)


def test_failure_indicators_single_element_accepted():
    """A single-element failure_indicators list is valid."""
    kwargs = _valid_rule_kwargs()
    kwargs["failure_indicators"] = ["one indicator"]
    rule = ExtractedRule(**kwargs)
    assert len(rule.failure_indicators) == 1


# ---------------------------------------------------------------------------
# Full model construction
# ---------------------------------------------------------------------------


def test_valid_rule_constructs_without_error():
    """Happy path: a fully valid ExtractedRule constructs correctly."""
    rule = ExtractedRule(**_valid_rule_kwargs())
    assert rule.rule_id == "COBS-4.2.1R-prohibition-1"
    assert rule.citation == "COBS 4.2.1 [R] (effective 01/12/2001)"


def test_verdict_response_invalid_outcome_rejected():
    """VerdictResponse rejects an outcome outside the closed vocabulary."""
    with pytest.raises(ValidationError):
        VerdictResponse(
            rule_id="r1",
            outcome="maybe",  # not a valid VerdictOutcome
            reasoning="ok",
            confidence="high",
            evidence_quote=None,
            suggested_fix=None,
        )


def test_verdict_response_valid_outcomes_accepted():
    """All five valid VerdictOutcome values are accepted."""
    for outcome in ("compliant", "non-compliant", "not-applicable", "unclear", "error"):
        v = VerdictResponse(
            rule_id="r1",
            outcome=outcome,
            reasoning="test",
            confidence="high",
            evidence_quote=None,
            suggested_fix=None,
        )
        assert v.outcome == outcome


def test_rules_cache_artifact_constructs():
    """RulesCacheArtifact constructs from minimal valid data."""
    artifact = RulesCacheArtifact(
        source_id="fca-cobs-4-financial-promotions",
        source_hash="abc123",
        retrieved_date="2026-09-03",
        extracted_at="2026-09-03T00:00:00",
        rules=[],
    )
    assert artifact.source_id == "fca-cobs-4-financial-promotions"
    assert artifact.rules == []
