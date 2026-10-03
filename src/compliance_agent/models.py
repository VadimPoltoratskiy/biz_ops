"""
Single shared data layer for the compliance agent.

Every other module imports types from here.
This module never imports from sibling modules.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Enumerations (expressed as Literal types for Pydantic v2 compatibility)
# ---------------------------------------------------------------------------

ObligationType = Literal[
    "mandatory_disclosure",
    "prohibition",
    "balance",
    "presentation",
    "substantiation",
    "identification",
]

SeverityLevel = Literal["high", "medium", "low"]

ConfidenceLevel = Literal["high", "medium", "low"]

VerdictOutcome = Literal[
    "compliant",
    "non-compliant",
    "not-applicable",
    "unclear",
    "error",
]

OverallOutcome = Literal["clean", "findings", "incomplete", "not-assessed"]

FailureCause = Literal[
    "fail-fast-nonretryable",
    "retryable-exhausted",
    "validation-error",
    "internal-error",
]


# ---------------------------------------------------------------------------
# Rule models
# ---------------------------------------------------------------------------


class ExtractedRule(BaseModel):
    """A single discrete, checkable rule extracted from a regulation source."""

    rule_id: str
    """Stable slug identifier, e.g. 'COBS-4.2.1R-prohibition-1'."""

    citation: str
    """Exact provision marker as it appears in the source, e.g. 'COBS 4.2.1 [R] (effective 01/12/2001)'."""

    source_quote: str
    """Verbatim excerpt from the regulation, copied character-for-character."""

    obligation_type: ObligationType
    """Category of obligation this rule represents."""

    check_question: str
    """Binary yes/no question answerable from the marketing text alone."""

    precondition: str
    """Condition under which this rule applies to a marketing communication."""

    severity: SeverityLevel
    """
    high — breaching a binding rule [R] with prescribed wording;
    medium — breaching a guidance provision [G];
    low — evidential [E] provisions.
    """

    failure_indicators: list[str] = Field(min_length=1)
    """Specific textual signals (1-5) that indicate a breach of this rule."""


class ExtractedRulesList(BaseModel):
    """Output format for the extraction LLM call."""

    rules: list[ExtractedRule]


class RulesCacheArtifact(BaseModel):
    """Committed JSON artifact storing the extracted rules for a regulation source."""

    source_id: str
    """Identifier derived from the source filename stem, e.g. 'fca-cobs-4-financial-promotions'."""

    source_hash: str
    """SHA-256 hex digest of the regulation source file at extraction time."""

    retrieved_date: str
    """Retrieval date parsed from the provenance header in the source file."""

    extracted_at: str
    """ISO 8601 timestamp of when this extraction was performed."""

    rules: list[ExtractedRule]


# ---------------------------------------------------------------------------
# Verdict models
# ---------------------------------------------------------------------------


class VerdictResponse(BaseModel):
    """Structured verdict for a single rule evaluation — also the output_format for evaluation LLM calls."""

    rule_id: str
    outcome: VerdictOutcome
    reasoning: str
    confidence: ConfidenceLevel
    evidence_quote: str | None
    """
    Python-verified exact substring of the marketing text, or null.
    Populated only when outcome is 'non-compliant' or 'unclear' and a specific
    passage supports the verdict. Never paraphrased; the value is verified by
    a substring check in evaluate.py before being stored.
    """
    suggested_fix: str | None
    """Specific, actionable change to bring the text into compliance; null if not-applicable."""


# ---------------------------------------------------------------------------
# Pipeline / run models
# ---------------------------------------------------------------------------


class StageResult(BaseModel):
    """Records the outcome of a single pipeline stage."""

    stage: str
    """One of 'ingestion', 'decomposition', 'evaluation', 'reporting'."""

    success: bool

    failure_cause: FailureCause | None = None
    """Populated only on failure."""

    detail: str | None = None
    """Human-readable failure description."""


class TokenUsage(BaseModel):
    """Token consumption for a single LLM call."""

    input_tokens: int
    output_tokens: int


class RunRecord(BaseModel):
    """Complete record of a single compliance-check run."""

    run_id: str
    timestamp: str
    """ISO 8601 timestamp of when this run started."""

    marketing_input: str
    source_id: str
    rules_used: list[ExtractedRule]
    verdicts: list[VerdictResponse]
    overall_outcome: OverallOutcome
    stages: list[StageResult]
    token_usage: list[TokenUsage]
    """One entry per LLM call made in this run."""


class HistoryLine(BaseModel):
    """One-line summary appended to runs/history.jsonl after each run."""

    run_id: str
    timestamp: str
    overall_outcome: OverallOutcome
    exit_code: int
    run_dir: str
    """Relative path to the per-run artifact directory."""
