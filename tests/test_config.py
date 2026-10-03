"""
Tests for compliance_agent.config — environment parsing.

A mistyped COMPLIANCE_* value is user error, so it must surface as a readable
message like every other fail-fast path, not as a bare ValueError traceback.
"""
from __future__ import annotations

import pytest

from compliance_agent.config import ConfigError, load_settings


NUMERIC_VARS = [
    "COMPLIANCE_MARKETING_TEXT_CAP",
    "COMPLIANCE_MAX_CONCURRENCY",
    "COMPLIANCE_MAX_RETRIES",
]


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch):
    """Read only the variables each test sets — never a developer's .env."""
    monkeypatch.setattr("compliance_agent.config.load_dotenv", lambda *a, **k: False)


def test_defaults_when_nothing_is_set(monkeypatch):
    for name in NUMERIC_VARS:
        monkeypatch.delenv(name, raising=False)

    settings = load_settings()

    assert settings.marketing_text_cap == 2000
    assert settings.max_concurrency == 4
    assert settings.max_retries == 3


def test_numeric_overrides_are_honoured(monkeypatch):
    monkeypatch.setenv("COMPLIANCE_MARKETING_TEXT_CAP", "500")
    monkeypatch.setenv("COMPLIANCE_MAX_CONCURRENCY", "2")
    monkeypatch.setenv("COMPLIANCE_MAX_RETRIES", "0")

    settings = load_settings()

    assert settings.marketing_text_cap == 500
    assert settings.max_concurrency == 2
    assert settings.max_retries == 0


@pytest.mark.parametrize("name", NUMERIC_VARS)
def test_non_integer_value_raises_config_error(name, monkeypatch):
    """`int()` would raise a bare ValueError and print a traceback."""
    monkeypatch.setenv(name, "yes")

    with pytest.raises(ConfigError) as exc_info:
        load_settings()

    message = str(exc_info.value)
    assert name in message
    assert "yes" in message
