"""
Pipeline orchestrator — the ONLY module that sequences stages, catches errors,
records stage results, and determines the exit code.

Stages (ingest, decompose, evaluate, report) are pure input→output functions;
none of them write to the run log. Only this module orchestrates them.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from compliance_agent.config import (
    Settings,
    repo_root,
    rules_dir,
    runs_dir,
    source_path,
)
from compliance_agent.decompose import get_rules
from compliance_agent.evaluate import evaluate_all_rules
from compliance_agent.ingest import (
    IngestionError,
    read_source,
    source_id_from_path,
    validate_marketing_text,
)
from compliance_agent.llm import LLMAuthError, LLMBadRequestError, LLMRetryExhaustedError
from compliance_agent.models import (
    FailureCause,
    HistoryLine,
    OverallOutcome,
    RunRecord,
    StageResult,
    TokenUsage,
    VerdictResponse,
)
from compliance_agent.report import (
    compute_exit_code,
    compute_overall_outcome,
    render_markdown,
)
from compliance_agent.runlog import (
    append_history,
    create_run_dir,
    create_run_id,
    write_run_artifacts,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _fail_stage(
    stages: list[StageResult],
    stage_name: str,
    cause: FailureCause,
    detail: str,
) -> None:
    """Append a failed ``StageResult`` to *stages*."""
    stages.append(
        StageResult(
            stage=stage_name,
            success=False,
            failure_cause=cause,
            detail=detail,
        )
    )


def _build_run_record(
    run_id: str,
    marketing_text: str,
    source_id: str,
    rules_used: list,
    verdicts: list[VerdictResponse],
    overall: OverallOutcome,
    stages: list[StageResult],
    usages: list[TokenUsage],
) -> RunRecord:
    """Construct a ``RunRecord`` from accumulated pipeline state."""
    return RunRecord(
        run_id=run_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        marketing_input=marketing_text,
        source_id=source_id,
        rules_used=rules_used,
        verdicts=verdicts,
        overall_outcome=overall,
        stages=stages,
        token_usage=usages,
    )


def _save_incomplete_run(
    run_id: str,
    marketing_text: str,
    source_id: str,
    rules_used: list,
    verdicts: list[VerdictResponse],
    stages: list[StageResult],
    usages: list[TokenUsage],
    run_dir: Path,
    runs_base: Path,
) -> None:
    """
    Best-effort: write run artifacts and append history for a failed run.

    Stage failures at any point must still attempt to write whatever artifacts
    are available and append to the history index before returning exit 2.
    Exceptions here are silently swallowed to avoid masking the original failure.
    """
    overall: OverallOutcome = "incomplete"
    markdown = render_markdown(
        verdicts, rules_used, overall, marketing_text, run_id
    )

    run_record = _build_run_record(
        run_id=run_id,
        marketing_text=marketing_text,
        source_id=source_id,
        rules_used=rules_used,
        verdicts=verdicts,
        overall=overall,
        stages=stages,
        usages=usages,
    )

    try:
        write_run_artifacts(run_dir, run_record, markdown)
        append_history(
            runs_base,
            HistoryLine(
                run_id=run_id,
                timestamp=run_record.timestamp,
                overall_outcome=overall,
                exit_code=2,
                run_dir=str(run_dir.relative_to(runs_base.parent)),
            ),
        )
    except Exception:
        pass  # best-effort; do not mask the original failure


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def run_check(marketing_text_raw: str, settings: Settings, refresh: bool) -> int:
    """
    Run the full compliance check pipeline and return an exit code.

    Exit codes:
      0 — clean or not-assessed (all rules compliant / not-applicable)
      1 — findings (at least one non-compliant or unclear verdict)
      2 — incomplete (any pipeline-stage failure or any error verdict)

    Error precedence is fail-safe: exit 2 wins even when confirmed breaches
    are also present (AC-27).

    Stage failures at any point attempt to write partial artifacts and append
    to history before returning exit 2.
    """
    stages: list[StageResult] = []
    usages: list[TokenUsage] = []
    run_id = create_run_id()
    root = repo_root()
    runs_base = runs_dir(root)
    run_dir = create_run_dir(runs_base, run_id)

    # Accumulate as we progress through stages.
    source_id = "unknown"
    rules_used: list = []
    verdicts: list[VerdictResponse] = []

    # -----------------------------------------------------------------------
    # Stage 1 — Ingestion
    # -----------------------------------------------------------------------
    try:
        validate_marketing_text(marketing_text_raw, settings.marketing_text_cap)
    except IngestionError as exc:
        print(str(exc), file=sys.stderr)
        _fail_stage(stages, "ingestion", "fail-fast-nonretryable", str(exc))
        _save_incomplete_run(
            run_id, marketing_text_raw, source_id, rules_used, verdicts,
            stages, usages, run_dir, runs_base,
        )
        return 2

    try:
        src_path = source_path(root)
        source_text = read_source(src_path)
        source_id = source_id_from_path(src_path)
    except IngestionError as exc:
        print(str(exc), file=sys.stderr)
        _fail_stage(stages, "ingestion", "fail-fast-nonretryable", str(exc))
        _save_incomplete_run(
            run_id, marketing_text_raw, source_id, rules_used, verdicts,
            stages, usages, run_dir, runs_base,
        )
        return 2

    stages.append(StageResult(stage="ingestion", success=True))

    # -----------------------------------------------------------------------
    # Stage 2 — Decomposition
    # -----------------------------------------------------------------------
    try:
        artifact, _was_extracted, extraction_usage = get_rules(
            source_text, source_id, rules_dir(root), settings, refresh
        )
        rules_used = artifact.rules

        if not rules_used:
            _fail_stage(
                stages, "decomposition", "internal-error",
                "Extraction yielded zero rules — AC-37",
            )
            _save_incomplete_run(
                run_id, marketing_text_raw, source_id, rules_used, verdicts,
                stages, usages, run_dir, runs_base,
            )
            return 2

        if extraction_usage is not None:
            usages.append(extraction_usage)

    except (LLMAuthError, LLMBadRequestError) as exc:
        _fail_stage(stages, "decomposition", "fail-fast-nonretryable", str(exc))
        _save_incomplete_run(
            run_id, marketing_text_raw, source_id, rules_used, verdicts,
            stages, usages, run_dir, runs_base,
        )
        return 2

    except LLMRetryExhaustedError as exc:
        _fail_stage(stages, "decomposition", "retryable-exhausted", str(exc))
        _save_incomplete_run(
            run_id, marketing_text_raw, source_id, rules_used, verdicts,
            stages, usages, run_dir, runs_base,
        )
        return 2

    except Exception as exc:
        _fail_stage(stages, "decomposition", "internal-error", str(exc))
        _save_incomplete_run(
            run_id, marketing_text_raw, source_id, rules_used, verdicts,
            stages, usages, run_dir, runs_base,
        )
        return 2

    stages.append(StageResult(stage="decomposition", success=True))

    # -----------------------------------------------------------------------
    # Stage 3 — Evaluation
    # -----------------------------------------------------------------------
    try:
        verdicts, eval_usages = evaluate_all_rules(
            rules_used, marketing_text_raw, settings
        )
        usages.extend(eval_usages)
    except Exception as exc:
        # Per-rule failures are isolated inside evaluate_all_rules; this branch
        # handles an unexpected failure of the evaluation stage itself.
        _fail_stage(stages, "evaluation", "internal-error", str(exc))
        _save_incomplete_run(
            run_id, marketing_text_raw, source_id, rules_used, verdicts,
            stages, usages, run_dir, runs_base,
        )
        return 2

    # Per-rule isolation keeps one bad rule from sinking the run, but it must not
    # let a wholesale failure be recorded as a successful stage. If an API key is
    # invalid and the rules cache is fresh, every per-rule call fails here and the
    # stage record would otherwise read success=True while nothing was evaluated.
    error_count = sum(1 for v in verdicts if v.outcome == "error")
    if verdicts and error_count == len(verdicts):
        _fail_stage(
            stages,
            "evaluation",
            "fail-fast-nonretryable",
            f"All {error_count} rule evaluations failed. "
            f"First error: {verdicts[0].reasoning}",
        )
    else:
        stages.append(
            StageResult(
                stage="evaluation",
                success=True,
                detail=(
                    f"{error_count} of {len(verdicts)} rule evaluations failed"
                    if error_count
                    else None
                ),
            )
        )

    # -----------------------------------------------------------------------
    # Stage 4 — Reporting
    # -----------------------------------------------------------------------
    overall = compute_overall_outcome(verdicts)
    exit_code = compute_exit_code(overall)
    markdown = render_markdown(
        verdicts, rules_used, overall, marketing_text_raw, run_id
    )

    # Print to stdout — this is the primary output of the tool.
    print(markdown)

    stages.append(StageResult(stage="reporting", success=True))

    timestamp = datetime.now(timezone.utc).isoformat()
    run_record = _build_run_record(
        run_id=run_id,
        marketing_text=marketing_text_raw,
        source_id=source_id,
        rules_used=rules_used,
        verdicts=verdicts,
        overall=overall,
        stages=stages,
        usages=usages,
    )
    # Overwrite the timestamp from the helper with the one already captured.
    run_record = run_record.model_copy(update={"timestamp": timestamp})

    write_run_artifacts(run_dir, run_record, markdown)
    append_history(
        runs_base,
        HistoryLine(
            run_id=run_id,
            timestamp=timestamp,
            overall_outcome=overall,
            exit_code=exit_code,
            run_dir=str(run_dir.relative_to(runs_base.parent)),
        ),
    )

    return exit_code


def run_extract_rules(settings: Settings, refresh: bool) -> int:
    """
    Simplified flow: read source → cache decision → extract if needed.

    Returns 0 on success, 2 on failure.
    """
    root = repo_root()

    try:
        src_path = source_path(root)
        source_text = read_source(src_path)
        s_id = source_id_from_path(src_path)
    except IngestionError as exc:
        print(f"Error reading source: {exc}", file=sys.stderr)
        return 2

    try:
        artifact, was_extracted, _usage = get_rules(
            source_text, s_id, rules_dir(root), settings, refresh
        )
    except (LLMAuthError, LLMBadRequestError, LLMRetryExhaustedError) as exc:
        print(f"Error during extraction: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Unexpected error during extraction: {exc}", file=sys.stderr)
        return 2

    count = len(artifact.rules)
    if was_extracted:
        print(
            f"Rules extracted and saved to rules/{s_id}.json ({count} rules)"
        )
    else:
        print(f"Rules loaded from cache. ({count} rules)")

    return 0
