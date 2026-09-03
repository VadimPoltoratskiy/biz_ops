"""
Evaluation module — fan-out evaluation with per-rule isolation and evidence-quote
verification.

``evaluate_all_rules`` is the single ``asyncio.run()`` call site in the pipeline.
``pipeline.py`` calls it synchronously; async does not bleed beyond this module.
"""

from __future__ import annotations

import asyncio

from compliance_agent.config import Settings
from compliance_agent.models import ExtractedRule, TokenUsage, VerdictResponse
from compliance_agent import llm, prompts


def verify_evidence_quote(quote: str | None, marketing_text: str) -> str | None:
    """
    Verify that the model-returned evidence quote is an exact substring.

    Uses Python's ``in`` operator which is case- and whitespace-sensitive.
    The marketing text is never stripped or normalised before the check so that
    the user's original input is the authoritative source of truth (AC-20, AC-21).

    Returns:
        - ``quote`` if ``quote in marketing_text`` — the model cited real text.
        - ``None`` if the quote is ``None`` or does not appear verbatim in the
          marketing text (hallucinated or paraphrased quote is silently nulled).
    """
    if quote is None:
        return None
    if quote in marketing_text:
        return quote
    return None


async def _evaluate_one(
    rule: ExtractedRule,
    marketing_text: str,
    settings: Settings,
    semaphore: asyncio.Semaphore,
    client,  # anthropic.AsyncAnthropic — type not imported directly (AC grep constraint)
) -> tuple[VerdictResponse, TokenUsage | None]:
    """
    Evaluate a single rule.

    Does NOT catch exceptions — ``evaluate_all_rules`` wraps each coroutine with
    ``asyncio.gather(..., return_exceptions=True)`` for per-rule isolation (AC-17).
    """
    prompt = prompts.format_evaluation_prompt(rule, marketing_text)
    verdict, usage = await llm.evaluate_rule(
        rule, marketing_text, settings, prompt, semaphore, client
    )
    # Replace the model-returned evidence_quote with the Python-verified value.
    verified_quote = verify_evidence_quote(verdict.evidence_quote, marketing_text)
    verified_verdict = verdict.model_copy(update={"evidence_quote": verified_quote})
    return (verified_verdict, usage)


def evaluate_all_rules(
    rules: list[ExtractedRule],
    marketing_text: str,
    settings: Settings,
) -> tuple[list[VerdictResponse], list[TokenUsage]]:
    """
    Fan-out evaluate every rule against the marketing text.

    One ``AsyncAnthropic`` client is constructed here and shared across all
    concurrent coroutines (connection pooling; no per-rule connection leaks).
    The client is closed after all coroutines finish via ``asyncio.run`` which
    drives ``_run_all``.

    Per-rule failure isolation: if a single coroutine raises, that rule receives
    an ``error`` verdict with the exception details; all other rules are unaffected.

    Args:
        rules: The extracted rules to evaluate. An empty list returns immediately
               with ``([], [])``; the pipeline catches zero-rules at a higher level.
        marketing_text: The marketing text to evaluate each rule against.
        settings: Runtime configuration (model, concurrency, retries, etc.).

    Returns:
        ``(verdicts_in_rule_order, usages)`` — verdicts preserve the input order;
        no rule is ever missing from the output list.
    """
    if not rules:
        return ([], [])

    return asyncio.run(_run_all(rules, marketing_text, settings))


async def _run_all(
    rules: list[ExtractedRule],
    marketing_text: str,
    settings: Settings,
) -> tuple[list[VerdictResponse], list[TokenUsage]]:
    """Async implementation of ``evaluate_all_rules``."""
    semaphore = asyncio.Semaphore(settings.max_concurrency)
    client = llm._make_async_client()

    try:
        coroutines = [
            _evaluate_one(rule, marketing_text, settings, semaphore, client)
            for rule in rules
        ]

        # return_exceptions=True ensures one failure does not cancel the rest.
        results = await asyncio.gather(*coroutines, return_exceptions=True)

        verdicts: list[VerdictResponse] = []
        usages: list[TokenUsage] = []

        for rule, result in zip(rules, results):
            if isinstance(result, BaseException):
                # Per-rule failure: produce an error verdict and continue (AC-25).
                verdicts.append(
                    VerdictResponse(
                        rule_id=rule.rule_id,
                        outcome="error",
                        reasoning=(
                            f"Evaluation failed: "
                            f"{type(result).__name__}: {result}"
                        ),
                        confidence="low",
                        evidence_quote=None,
                        suggested_fix=None,
                    )
                )
            else:
                verdict, usage = result
                verdicts.append(verdict)
                if usage is not None:
                    usages.append(usage)

        return (verdicts, usages)
    finally:
        await client.close()
