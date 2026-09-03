"""
Tests for compliance_agent.llm — the SDK boundary.

The regression these tests exist for: rule extraction originally ran with an
output budget too small for a full regulation. Adaptive-thinking tokens are drawn
from the same `max_tokens` budget, so the response was truncated mid-string. That
surfaced as an unparseable-JSON validation error rather than as a limit error, and
the retry loop then spent every remaining attempt re-running a call that could not
succeed. Truncation is deterministic, not transient — it must fail fast.

No API key is required: the SDK client is mocked at the factory boundary.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from compliance_agent import llm
from compliance_agent.config import Settings
from compliance_agent.models import ExtractedRule, ExtractedRulesList


def _settings(max_retries: int = 3) -> Settings:
    return Settings(
        api_key="test-key",
        model="claude-opus-5",
        marketing_text_cap=2000,
        max_concurrency=4,
        max_retries=max_retries,
    )


def _stub_client(response) -> MagicMock:
    """A client whose streamed extraction call yields *response*."""
    client = MagicMock()
    stream_ctx = MagicMock()
    stream_ctx.__enter__.return_value.get_final_message.return_value = response
    client.messages.stream.return_value = stream_ctx
    return client


def _ok_response() -> MagicMock:
    response = MagicMock()
    response.stop_reason = "end_turn"
    response.usage.input_tokens = 17000
    response.usage.output_tokens = 9000
    response.parsed_output = ExtractedRulesList(
        rules=[
            ExtractedRule(
                rule_id="COBS-4.2.1R-prohibition-1",
                citation="COBS 4.2.1 [R] (effective 01/10/2018)",
                source_quote="A firm must ensure that a communication is fair, clear "
                "and not misleading.",
                obligation_type="prohibition",
                check_question="Does the text make a misleading claim?",
                precondition="Always applicable.",
                severity="high",
                failure_indicators=["promises guaranteed returns"],
            )
        ]
    )
    return response


def _truncated_response() -> MagicMock:
    response = MagicMock()
    response.stop_reason = "max_tokens"
    return response


def test_truncated_extraction_fails_fast_without_retrying():
    """Hitting the output cap must raise immediately, not burn the retry budget."""
    client = _stub_client(_truncated_response())

    with patch.object(llm, "_make_sync_client", return_value=client):
        with pytest.raises(llm.LLMBadRequestError) as exc_info:
            llm.extract_rules("source text", _settings(max_retries=3), "prompt")

    # One attempt only — the same prompt at the same budget truncates every time.
    assert client.messages.stream.call_count == 1

    # The message must name the actual remedy, not just report a parse failure.
    message = str(exc_info.value)
    assert str(llm.EXTRACTION_MAX_TOKENS) in message
    assert "truncated" in message.lower()


def test_extraction_budget_is_large_enough_for_a_full_regulation():
    """Guards the budget itself: 8192 was too small and caused the original bug."""
    assert llm.EXTRACTION_MAX_TOKENS >= 32000


def test_extraction_streams_the_call():
    """
    Large budgets must be streamed, or the request risks an HTTP timeout before
    the model finishes generating a long rules array.
    """
    client = _stub_client(_ok_response())

    with patch.object(llm, "_make_sync_client", return_value=client):
        llm.extract_rules("source text", _settings(), "prompt")

    assert client.messages.stream.called
    assert not client.messages.parse.called
    assert client.messages.stream.call_args.kwargs["max_tokens"] == (
        llm.EXTRACTION_MAX_TOKENS
    )


def test_successful_extraction_returns_rules_and_usage():
    client = _stub_client(_ok_response())

    with patch.object(llm, "_make_sync_client", return_value=client):
        rules, usage = llm.extract_rules("source text", _settings(), "prompt")

    assert len(rules.rules) == 1
    assert usage.input_tokens == 17000
    assert usage.output_tokens == 9000


def test_missing_api_key_raises_auth_error_without_retrying():
    """
    A missing API key must raise LLMAuthError immediately — no retry sleeps.

    Regression test for the pre-D3 behaviour: a missing key yielded a TypeError
    at HTTP-request time. _classify_exception returned 'retryable' for unknown
    errors, so each call slept 2^0 + 2^1 + 2^2 + jitter ≈ 9 s across its three
    retries before raising LLMRetryExhaustedError. On the evaluation path that
    cost is paid per rule — 75 rules at concurrency 4 is minutes of silence,
    ending in a report full of error verdicts rather than a clear failure.

    After D3, _make_sync_client raises LLMAuthError before constructing any client,
    and the extract_rules retry loop re-raises it immediately via
    `except LLMAuthError: raise`. The time.sleep patch proves zero sleeps occurred.
    """
    no_key = Settings(
        api_key=None,
        model="claude-haiku-4-5",
        marketing_text_cap=2000,
        max_concurrency=4,
        max_retries=3,  # would produce 3 retry sleeps if the bug regresses
    )
    with patch("time.sleep") as mock_sleep:
        with pytest.raises(llm.LLMAuthError, match="API key"):
            llm.extract_rules("source text", no_key, "prompt")
    mock_sleep.assert_not_called()
