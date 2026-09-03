"""
LLM gateway — the ONLY module permitted to import the Anthropic SDK.

This module is the mock boundary for the test suite. Every test that touches
LLM behavior patches functions from this module. No other module calls
`anthropic` directly.

IMPORTANT constraints on API calls (both cause HTTP 400 on claude-opus-5):
  - Do NOT add assistant prefill to any call.
  - Do NOT add `budget_tokens` to any call.
`thinking={"type": "adaptive"}` is used on extraction only, NOT on evaluation.
"""

from __future__ import annotations

import asyncio
import random
from typing import Literal

import anthropic
from anthropic import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    RateLimitError,
)

from compliance_agent.config import Settings
from compliance_agent.models import (
    ExtractedRule,
    ExtractedRulesList,
    TokenUsage,
    VerdictResponse,
)


# Output-token budget for the one-shot rule-extraction call. Adaptive thinking
# tokens are drawn from this same budget, and decomposing a full regulation
# produces a long rules array, so this needs far more headroom than a per-rule
# evaluation. Too low and the JSON is truncated mid-string; the call is streamed
# so a budget this size cannot hit the HTTP request timeout.
EXTRACTION_MAX_TOKENS = 32000


# ---------------------------------------------------------------------------
# Custom exception hierarchy
# ---------------------------------------------------------------------------


class LLMError(Exception):
    """Base exception for all LLM gateway errors."""


class LLMAuthError(LLMError):
    """
    Fail-fast, non-retryable authentication or resource-not-found error.
    Wraps: AuthenticationError, NotFoundError.
    """


class LLMBadRequestError(LLMError):
    """
    Fail-fast, non-retryable bad request error.
    Wraps: BadRequestError.
    """


class LLMRetryExhaustedError(LLMError):
    """
    Raised when a retryable error persists beyond max_retries attempts.
    Wraps the last underlying exception.
    """


# ---------------------------------------------------------------------------
# Client factories (patched in tests)
# ---------------------------------------------------------------------------


def _make_sync_client(settings: Settings) -> anthropic.Anthropic:
    """Construct and return a synchronous Anthropic client from settings."""
    if not settings.api_key:
        raise LLMAuthError(
            "No API key configured. Set ANTHROPIC_API_KEY in .env or "
            "as an environment variable."
        )
    return anthropic.Anthropic(api_key=settings.api_key)


def _make_async_client(settings: Settings) -> anthropic.AsyncAnthropic:
    """Construct and return an asynchronous Anthropic client from settings."""
    if not settings.api_key:
        raise LLMAuthError(
            "No API key configured. Set ANTHROPIC_API_KEY in .env or "
            "as an environment variable."
        )
    return anthropic.AsyncAnthropic(api_key=settings.api_key)


# ---------------------------------------------------------------------------
# Exception classification
# ---------------------------------------------------------------------------


def _classify_exception(
    exc: Exception,
) -> Literal["fail-fast", "retryable"]:
    """
    Map an SDK exception to its retry category.

    Rules applied most-specific-first per the locked taxonomy:
      fail-fast:  AuthenticationError, NotFoundError, BadRequestError
      retryable:  RateLimitError, APIStatusError (status >= 500), APIConnectionError
    """
    if isinstance(exc, (AuthenticationError, NotFoundError)):
        return "fail-fast"
    if isinstance(exc, BadRequestError):
        return "fail-fast"
    if isinstance(exc, RateLimitError):
        return "retryable"
    if isinstance(exc, APIStatusError) and exc.status_code >= 500:
        return "retryable"
    if isinstance(exc, APIConnectionError):
        return "retryable"
    # Unknown errors: treat as retryable to avoid silent data loss
    return "retryable"


# ---------------------------------------------------------------------------
# Retry helpers
# ---------------------------------------------------------------------------


def _retry_sleep(attempt: int) -> float:
    """
    Compute exponential backoff with jitter for attempt N (0-indexed).

    Formula: min(30, 2 ** attempt) + uniform(0, 1) seconds.
    """
    return min(30.0, 2.0 ** attempt) + random.uniform(0.0, 1.0)


# ---------------------------------------------------------------------------
# Synchronous extraction call
# ---------------------------------------------------------------------------


def extract_rules(
    source_text: str,
    settings: Settings,
    prompt: str,
) -> tuple[ExtractedRulesList, TokenUsage]:
    """
    Call the Anthropic API (sync) to extract structured rules from a regulation source.

    Uses `thinking={"type": "adaptive"}` for improved reasoning on this complex
    decomposition task. No assistant prefill; no budget_tokens (both cause HTTP 400).

    Args:
        source_text: The raw regulation text (passed for future use; prompt is pre-built).
        settings: Runtime configuration (model, retry limits, etc.).
        prompt: The fully-rendered user message from prompts.format_extraction_prompt().

    Returns:
        A tuple of (ExtractedRulesList, TokenUsage).

    Raises:
        LLMAuthError: On AuthenticationError or NotFoundError (fail-fast).
        LLMBadRequestError: On BadRequestError (fail-fast).
        LLMRetryExhaustedError: When a retryable error persists beyond max_retries.
    """
    last_exc: Exception | None = None

    for attempt in range(settings.max_retries + 1):
        try:
            client = _make_sync_client(settings)
            # Streamed because the budget is large: a full regulation yields a long
            # rules array, and adaptive thinking tokens are drawn from the SAME
            # max_tokens budget. At 8192 the response was truncated mid-string, which
            # surfaced as an unparseable-JSON ValidationError rather than as a clear
            # limit error — and then burned every retry re-running a call that could
            # not succeed. Streaming keeps a budget this size from hitting the HTTP
            # request timeout.
            with client.messages.stream(
                model=settings.model,
                max_tokens=EXTRACTION_MAX_TOKENS,
                system=_extraction_system(),
                messages=[{"role": "user", "content": prompt}],
                output_format=ExtractedRulesList,
                # Do NOT add budget_tokens — causes HTTP 400 on this model family.
                # Do NOT add assistant prefill — causes HTTP 400 on this model family.
                thinking={"type": "adaptive"},
            ) as stream:
                response = stream.get_final_message()

            # Truncation is not retryable: the same prompt at the same budget will
            # truncate again. Fail fast with a message that names the actual fix.
            if response.stop_reason == "max_tokens":
                raise LLMBadRequestError(
                    f"Rule extraction hit the {EXTRACTION_MAX_TOKENS}-token output limit "
                    f"and was truncated. The regulation source is too large to decompose "
                    f"in one call. Raise EXTRACTION_MAX_TOKENS in llm.py, or split the "
                    f"source file into smaller sections and extract each separately."
                )

            usage = TokenUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )
            return (response.parsed_output, usage)

        except (AuthenticationError, NotFoundError) as exc:
            raise LLMAuthError(str(exc)) from exc

        except BadRequestError as exc:
            raise LLMBadRequestError(str(exc)) from exc

        # Our own fail-fast errors (e.g. the truncation guard above) must not be
        # swallowed by the generic retry handler below and re-attempted.
        except LLMBadRequestError:
            raise

        # Key guard raised by factory must propagate immediately, not be retried.
        except LLMAuthError:
            raise

        except Exception as exc:
            category = _classify_exception(exc)
            if category == "fail-fast":
                raise LLMBadRequestError(str(exc)) from exc

            last_exc = exc
            if attempt < settings.max_retries:
                sleep_seconds = _retry_sleep(attempt)
                import time
                time.sleep(sleep_seconds)
            # else: fall through to raise LLMRetryExhaustedError

    raise LLMRetryExhaustedError(
        f"Extraction failed after {settings.max_retries + 1} attempts. "
        f"Last error: {type(last_exc).__name__}: {last_exc}"
    ) from last_exc


# ---------------------------------------------------------------------------
# Asynchronous evaluation call
# ---------------------------------------------------------------------------


async def evaluate_rule(
    rule: ExtractedRule,
    marketing_text: str,
    settings: Settings,
    prompt: str,
    semaphore: asyncio.Semaphore,
    client: anthropic.AsyncAnthropic,
) -> tuple[VerdictResponse, TokenUsage]:
    """
    Call the Anthropic API (async) to evaluate a single rule against marketing text.

    No `thinking` parameter — evaluation calls omit extended reasoning per spec.
    No assistant prefill; no budget_tokens (both cause HTTP 400).

    Args:
        rule: The extracted rule to evaluate.
        marketing_text: The marketing text under review.
        settings: Runtime configuration.
        prompt: The fully-rendered user message from prompts.format_evaluation_prompt().
        semaphore: Concurrency limiter shared across all concurrent evaluation calls.
        client: A shared AsyncAnthropic client constructed once per evaluate_all_rules()
                call in evaluate.py. One client per fan-out avoids connection leaks.

    Returns:
        A tuple of (VerdictResponse, TokenUsage).

    Raises:
        LLMAuthError: On AuthenticationError or NotFoundError (fail-fast).
        LLMBadRequestError: On BadRequestError (fail-fast).
        LLMRetryExhaustedError: When a retryable error persists beyond max_retries.
    """
    async with semaphore:
        last_exc: Exception | None = None

        for attempt in range(settings.max_retries + 1):
            try:
                response = await client.messages.parse(
                    model=settings.model,
                    max_tokens=1024,
                    system=_evaluation_system(),
                    messages=[{"role": "user", "content": prompt}],
                    output_format=VerdictResponse,
                    # No thinking parameter for evaluation calls — spec constraint.
                    # Do NOT add budget_tokens — causes HTTP 400 on this model family.
                    # Do NOT add assistant prefill — causes HTTP 400 on this model family.
                )
                usage = TokenUsage(
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                )
                return (response.parsed_output, usage)

            except (AuthenticationError, NotFoundError) as exc:
                raise LLMAuthError(str(exc)) from exc

            except BadRequestError as exc:
                raise LLMBadRequestError(str(exc)) from exc

            # Defensive: re-raise immediately so a future refactor that moves
            # _make_async_client inside this loop cannot accidentally retry on a
            # missing key.
            except LLMAuthError:
                raise

            except Exception as exc:
                category = _classify_exception(exc)
                if category == "fail-fast":
                    raise LLMBadRequestError(str(exc)) from exc

                last_exc = exc
                if attempt < settings.max_retries:
                    await asyncio.sleep(_retry_sleep(attempt))
                # else: fall through to raise LLMRetryExhaustedError

        raise LLMRetryExhaustedError(
            f"Evaluation of rule '{rule.rule_id}' failed after "
            f"{settings.max_retries + 1} attempts. "
            f"Last error: {type(last_exc).__name__}: {last_exc}"
        ) from last_exc


# ---------------------------------------------------------------------------
# System prompt accessors (thin delegation to prompts module would create a
# circular import risk — keep them here as private helpers)
# ---------------------------------------------------------------------------


def _extraction_system() -> str:
    """Return the extraction system prompt string."""
    from compliance_agent.prompts import EXTRACTION_SYSTEM
    return EXTRACTION_SYSTEM


def _evaluation_system() -> str:
    """Return the evaluation system prompt string."""
    from compliance_agent.prompts import EVALUATION_SYSTEM
    return EVALUATION_SYSTEM
