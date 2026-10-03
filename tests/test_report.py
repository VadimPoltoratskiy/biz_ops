"""
Tests for compliance_agent.report — AC-27, AC-35, AC-36, AC-37, AC-38, AC-39.

Pure functions; no I/O, no LLM calls.
"""
from __future__ import annotations

import pytest

from compliance_agent.models import ExtractedRule, VerdictResponse
from compliance_agent.report import (
    compute_exit_code,
    compute_overall_outcome,
    render_markdown,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _v(rule_id: str, outcome: str) -> VerdictResponse:
    """Minimal VerdictResponse for testing outcome logic."""
    return VerdictResponse(
        rule_id=rule_id,
        outcome=outcome,
        reasoning="test reasoning",
        confidence="high",
        evidence_quote=None,
        suggested_fix=None,
    )


def _r(rule_id: str = "COBS-4.2.1R-prohibition-1") -> ExtractedRule:
    """Minimal ExtractedRule for testing report rendering."""
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


def _render(
    verdicts,
    rules=None,
    overall=None,
    marketing_text="test text",
    run_id="test-run-42",
) -> str:
    if rules is None:
        rules = [_r(v.rule_id) for v in verdicts]
    if overall is None:
        overall = compute_overall_outcome(verdicts)
    return render_markdown(verdicts, rules, overall, marketing_text, run_id)


# ---------------------------------------------------------------------------
# compute_overall_outcome
# ---------------------------------------------------------------------------


def test_outcome_all_compliant_is_clean():
    """All compliant verdicts → clean."""
    verdicts = [_v("r1", "compliant"), _v("r2", "compliant")]
    assert compute_overall_outcome(verdicts) == "clean"


def test_outcome_compliant_and_not_applicable_is_clean():
    """Compliant + not-applicable → clean (not not-assessed)."""
    verdicts = [_v("r1", "compliant"), _v("r2", "not-applicable")]
    assert compute_overall_outcome(verdicts) == "clean"


def test_outcome_any_noncompliant_is_findings():
    """Any non-compliant verdict → findings (exit 1)."""
    verdicts = [_v("r1", "compliant"), _v("r2", "non-compliant")]
    assert compute_overall_outcome(verdicts) == "findings"


def test_outcome_unclear_is_findings():
    """
    Any unclear verdict → findings (exit 1, not a pass).

    This is the AC-36 requirement: unclear is not a soft pass — it flags
    for human review and counts as a finding.
    """
    verdicts = [_v("r1", "compliant"), _v("r2", "unclear")]
    assert compute_overall_outcome(verdicts) == "findings"


def test_outcome_any_error_is_incomplete():
    """
    Any error verdict → incomplete (AC-27 fail-safe).

    Error takes precedence over both clean and findings.
    """
    verdicts = [_v("r1", "compliant"), _v("r2", "error")]
    assert compute_overall_outcome(verdicts) == "incomplete"


def test_outcome_all_not_applicable_is_not_assessed():
    """AC-38: when every verdict is not-applicable → not-assessed."""
    verdicts = [_v("r1", "not-applicable"), _v("r2", "not-applicable")]
    assert compute_overall_outcome(verdicts) == "not-assessed"


def test_outcome_zero_verdicts_is_incomplete():
    """Defensive: zero verdicts → incomplete (pipeline catches this separately)."""
    assert compute_overall_outcome([]) == "incomplete"


# ---------------------------------------------------------------------------
# Exit-code precedence (AC-27, AC-36, AC-38)
# ---------------------------------------------------------------------------


def test_exit_code_error_beats_noncompliant():
    """
    AC-27: any error verdict → exit 2 even when a confirmed breach is also present.

    Precedence: incomplete (exit 2) > findings (exit 1) > clean/not-assessed (exit 0).
    """
    verdicts = [_v("r1", "non-compliant"), _v("r2", "error")]
    outcome = compute_overall_outcome(verdicts)
    assert outcome == "incomplete"
    assert compute_exit_code(outcome) == 2


def test_exit_code_unclear_is_1_not_0():
    """AC-36: unclear → findings → exit 1 (not a pass, not exit 0)."""
    verdicts = [_v("r1", "unclear")]
    outcome = compute_overall_outcome(verdicts)
    assert outcome == "findings"
    assert compute_exit_code(outcome) == 1


def test_exit_code_noncompliant_is_1():
    """Non-compliant → findings → exit 1."""
    assert compute_exit_code("findings") == 1


def test_exit_code_clean_is_0():
    """Clean → exit 0."""
    assert compute_exit_code("clean") == 0


def test_exit_code_not_assessed_is_0():
    """AC-38: not-assessed → exit 0."""
    assert compute_exit_code("not-assessed") == 0


def test_exit_code_incomplete_is_2():
    """Incomplete (any stage failure or any error verdict) → exit 2."""
    assert compute_exit_code("incomplete") == 2


# ---------------------------------------------------------------------------
# render_markdown — structure (AC-39, AC-35, AC-36, AC-38)
# ---------------------------------------------------------------------------


def test_markdown_starts_with_h1_heading():
    """AC-39: the report MUST open with '# Compliance Check Report'."""
    md = _render([_v("r1", "compliant")])
    assert md.startswith("# Compliance Check Report")


def test_markdown_overall_outcome_near_top():
    """AC-39: 'Overall outcome' must appear in the first 300 characters."""
    md = _render([_v("r1", "compliant")])
    assert "Overall outcome" in md[:300]


def test_markdown_run_id_present():
    """The run ID is included in the report header."""
    md = _render([_v("r1", "compliant")], run_id="my-unique-run-id")
    assert "my-unique-run-id" in md


def test_markdown_verdict_counts_in_header():
    """Verdict counts appear in the header summary."""
    verdicts = [_v("r1", "compliant"), _v("r2", "non-compliant"), _v("r3", "unclear")]
    md = _render(verdicts)
    assert "compliant" in md
    assert "non-compliant" in md
    assert "unclear" in md


def test_markdown_not_assessed_states_0_of_n():
    """AC-38: not-assessed report must state '0 of N rules applicable'."""
    verdicts = [_v("r1", "not-applicable"), _v("r2", "not-applicable")]
    md = _render(verdicts, overall="not-assessed")
    assert "0 of 2" in md


def test_markdown_incomplete_states_plainly():
    """AC-35: incomplete report states the check was incomplete."""
    verdicts = [_v("r1", "error")]
    md = _render(verdicts, overall="incomplete")
    # The phrase must appear somewhere in the report header area.
    assert "incomplete" in md.lower()


def test_markdown_unclear_labeled_as_requiring_human_review():
    """
    AC-36: unclear verdicts must be labelled as requiring human review,
    explicitly not as a pass.
    """
    verdicts = [_v("r1", "unclear")]
    md = _render(verdicts, overall="findings")
    assert "Human Review" in md or "not a pass" in md or "not compliant" in md.lower()


def test_markdown_noncompliant_under_confirmed_breaches():
    """Non-compliant verdicts are grouped under 'Confirmed Breaches'."""
    verdicts = [_v("r1", "non-compliant")]
    md = _render(verdicts, overall="findings")
    assert "Confirmed Breaches" in md


def test_markdown_noncompliant_rendered_first_even_in_incomplete():
    """AC-35: confirmed breaches are rendered before error sections."""
    verdicts = [_v("r1", "non-compliant"), _v("r2", "error")]
    md = _render(verdicts, overall="incomplete")
    nc_pos = md.index("Confirmed Breaches")
    err_pos = md.index("Evaluation Errors")
    assert nc_pos < err_pos


def test_markdown_citation_included_for_noncompliant():
    """The citation from the extracted rule is included in breach details."""
    rule = _r("COBS-4.2.1R-prohibition-1")
    verdict = VerdictResponse(
        rule_id="COBS-4.2.1R-prohibition-1",
        outcome="non-compliant",
        reasoning="misleading claim",
        confidence="high",
        evidence_quote=None,
        suggested_fix="Remove it.",
    )
    md = render_markdown([verdict], [rule], "findings", "some text", "run-1")
    assert "COBS 4.2.1 [R]" in md


def test_markdown_compliant_verdicts_in_compliant_section():
    """Compliant verdicts appear under a 'Compliant' heading."""
    verdicts = [_v("r1", "compliant")]
    md = _render(verdicts)
    assert "## Compliant" in md


def test_markdown_not_applicable_in_own_section():
    """Not-applicable verdicts appear under a 'Not Applicable' heading."""
    verdicts = [_v("r1", "not-applicable")]
    md = _render(verdicts, overall="not-assessed")
    assert "## Not Applicable" in md


def test_markdown_error_verdict_outcome_in_uppercase():
    """Error verdict outcome is presented as 'ERROR' in the report."""
    verdicts = [_v("r1", "error")]
    md = _render(verdicts, overall="incomplete")
    assert "ERROR" in md
