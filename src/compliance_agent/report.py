"""
Report module — pure rendering functions for compliance check results.

No I/O in this module. All functions are input→output transformations.
"""

from __future__ import annotations

from compliance_agent.models import (
    ExtractedRule,
    OverallOutcome,
    RunRecord,
    VerdictResponse,
)


def compute_overall_outcome(verdicts: list[VerdictResponse]) -> OverallOutcome:
    """
    Compute the overall outcome with fail-safe, highest-first precedence.

    Precedence order (AC-27, AC-37, AC-38):
      1. ``"incomplete"``  — zero verdicts OR any ``error`` verdict (exit 2 wins)
      2. ``"not-assessed"`` — every verdict is ``not-applicable`` (no rule applied)
      3. ``"findings"``    — at least one ``non-compliant`` or ``unclear``
      4. ``"clean"``       — all verdicts are ``compliant`` or ``not-applicable``
    """
    if len(verdicts) == 0:
        # Defensive: pipeline catches zero-rules separately, but guard here too.
        return "incomplete"
    if any(v.outcome == "error" for v in verdicts):
        # Any error verdict → incomplete wins even when breaches are also present.
        return "incomplete"
    if all(v.outcome == "not-applicable" for v in verdicts):
        return "not-assessed"
    if any(v.outcome in ("non-compliant", "unclear") for v in verdicts):
        return "findings"
    return "clean"


def compute_exit_code(overall: OverallOutcome) -> int:
    """
    Map overall outcome to process exit code.

    2 — incomplete (pipeline failure or any error verdict)
    1 — findings (at least one non-compliant or unclear verdict)
    0 — clean or not-assessed
    """
    if overall == "incomplete":
        return 2
    if overall == "findings":
        return 1
    return 0  # "clean" or "not-assessed"


def render_markdown(
    verdicts: list[VerdictResponse],
    rules: list[ExtractedRule],
    overall: OverallOutcome,
    marketing_text: str,
    run_id: str,
) -> str:
    """
    Render the compliance check report as a Markdown string (AC-39).

    Section order (breaches listed first, even for incomplete runs, per AC-35):
      1. Summary header — run ID, overall outcome, verdict counts
      2. Confirmed Breaches (non-compliant)
      3. Items Requiring Human Review (unclear)
      4. Evaluation Errors (error)
      5. Compliant
      6. Not Applicable

    Args:
        verdicts: All verdict objects in rule order.
        rules: The extracted rules used in this run (for citation lookup).
        overall: The computed overall outcome.
        marketing_text: The original marketing text (not currently rendered but
            kept for future use by callers).
        run_id: Unique run identifier to include in the report header.

    Returns:
        A Markdown-formatted string suitable for printing to stdout or writing
        to ``report.md``.
    """
    rule_map: dict[str, ExtractedRule] = {r.rule_id: r for r in rules}

    counts = {
        "compliant": sum(1 for v in verdicts if v.outcome == "compliant"),
        "non-compliant": sum(1 for v in verdicts if v.outcome == "non-compliant"),
        "unclear": sum(1 for v in verdicts if v.outcome == "unclear"),
        "not-applicable": sum(1 for v in verdicts if v.outcome == "not-applicable"),
        "error": sum(1 for v in verdicts if v.outcome == "error"),
    }
    total = len(verdicts)

    lines: list[str] = []

    # ------------------------------------------------------------------
    # 1. Summary header (AC-39) — must open the report, never buried.
    # ------------------------------------------------------------------
    lines.append("# Compliance Check Report")
    lines.append("")
    lines.append(f"**Run ID**: {run_id}")
    lines.append(f"**Overall outcome**: {overall.upper()}")
    lines.append(
        f"**Verdict counts**: "
        f"{counts['compliant']} compliant | "
        f"{counts['non-compliant']} non-compliant | "
        f"{counts['unclear']} unclear | "
        f"{counts['not-applicable']} not-applicable | "
        f"{counts['error']} error"
    )

    if overall == "not-assessed":
        # AC-38: must state "0 of N rules applicable" so it never reads as a clean pass.
        lines.append(f"**0 of {total} rules applicable**")

    if overall == "incomplete":
        # AC-35: state plainly that the check did not complete.
        lines.append(
            "**This check was incomplete — see error details below.**"
        )

    lines.append("")

    # ------------------------------------------------------------------
    # 2. Confirmed Breaches (non-compliant) — rendered first, even for
    #    incomplete runs (AC-35).
    # ------------------------------------------------------------------
    nc_verdicts = [v for v in verdicts if v.outcome == "non-compliant"]
    if nc_verdicts:
        lines.append("## Confirmed Breaches")
        lines.append("")
        for v in nc_verdicts:
            rule = rule_map.get(v.rule_id)
            citation = rule.citation if rule else v.rule_id
            lines.append(f"### {v.rule_id}")
            lines.append(f"**Citation**: {citation}")
            lines.append(f"**Outcome**: NON-COMPLIANT")
            lines.append(f"**Confidence**: {v.confidence}")
            lines.append(f"**Reasoning**: {v.reasoning}")
            if v.evidence_quote:
                lines.append(f'**Evidence**: "{v.evidence_quote}"')
            if v.suggested_fix:
                lines.append(f"**Suggested fix**: {v.suggested_fix}")
            lines.append("")

    # ------------------------------------------------------------------
    # 3. Items Requiring Human Review (unclear)
    # ------------------------------------------------------------------
    unclear_verdicts = [v for v in verdicts if v.outcome == "unclear"]
    if unclear_verdicts:
        lines.append("## Items Requiring Human Review (not a pass)")
        lines.append("")
        lines.append(
            "_These are not compliant — they need manual assessment._"
        )
        lines.append("")
        for v in unclear_verdicts:
            rule = rule_map.get(v.rule_id)
            citation = rule.citation if rule else v.rule_id
            lines.append(f"### {v.rule_id}")
            lines.append(f"**Citation**: {citation}")
            lines.append(f"**Outcome**: UNCLEAR")
            lines.append(f"**Confidence**: {v.confidence}")
            lines.append(f"**Reasoning**: {v.reasoning}")
            if v.evidence_quote:
                lines.append(f'**Evidence**: "{v.evidence_quote}"')
            if v.suggested_fix:
                lines.append(f"**Suggested fix**: {v.suggested_fix}")
            lines.append("")

    # ------------------------------------------------------------------
    # 4. Evaluation Errors
    # ------------------------------------------------------------------
    error_verdicts = [v for v in verdicts if v.outcome == "error"]
    if error_verdicts:
        lines.append("## Evaluation Errors (check incomplete)")
        lines.append("")
        for v in error_verdicts:
            lines.append(f"### {v.rule_id}")
            lines.append(f"**Outcome**: ERROR")
            lines.append(f"**Reasoning**: {v.reasoning}")
            lines.append("")

    # ------------------------------------------------------------------
    # 5. Compliant
    # ------------------------------------------------------------------
    compliant_verdicts = [v for v in verdicts if v.outcome == "compliant"]
    if compliant_verdicts:
        lines.append("## Compliant")
        lines.append("")
        for v in compliant_verdicts:
            rule = rule_map.get(v.rule_id)
            citation = rule.citation if rule else v.rule_id
            lines.append(f"### {v.rule_id}")
            lines.append(f"**Citation**: {citation}")
            lines.append(f"**Outcome**: COMPLIANT")
            lines.append(f"**Reasoning**: {v.reasoning}")
            lines.append("")

    # ------------------------------------------------------------------
    # 6. Not Applicable
    # ------------------------------------------------------------------
    na_verdicts = [v for v in verdicts if v.outcome == "not-applicable"]
    if na_verdicts:
        lines.append("## Not Applicable")
        lines.append("")
        for v in na_verdicts:
            rule = rule_map.get(v.rule_id)
            citation = rule.citation if rule else v.rule_id
            lines.append(f"### {v.rule_id}")
            lines.append(f"**Citation**: {citation}")
            lines.append(f"**Outcome**: NOT-APPLICABLE")
            lines.append(f"**Reasoning**: {v.reasoning}")
            lines.append("")

    return "\n".join(lines)


def render_json_artifact(run_record: RunRecord) -> str:
    """Return the full run record serialised as pretty-printed JSON."""
    return run_record.model_dump_json(indent=2)
