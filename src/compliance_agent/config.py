"""
Configuration module — reads the environment and exposes a frozen Settings dataclass.

This module does NOT validate the Anthropic API key; that happens in llm.py when the
first call is made (fail-fast, AC-7). Storing None is valid at config time.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

from dotenv import load_dotenv


@dataclasses.dataclass(frozen=True)
class Settings:
    """Frozen configuration read from environment variables."""

    api_key: str | None
    """ANTHROPIC_API_KEY — required at runtime, validated in llm.py, not here."""

    model: str
    """LLM model name; default 'claude-opus-5'."""

    marketing_text_cap: int
    """Maximum allowed marketing text length in Unicode code points; default 2000."""

    max_concurrency: int
    """Maximum number of concurrent evaluation LLM calls; default 4."""

    max_retries: int
    """Maximum retry attempts for retryable LLM errors; default 3."""


def load_settings() -> Settings:
    """
    Load settings from environment (and optionally from .env file).

    load_dotenv() is a no-op if .env is absent, so the tool works in CI
    environments that inject the key directly via environment variables.
    """
    load_dotenv()

    return Settings(
        api_key=os.environ.get("ANTHROPIC_API_KEY"),
        model=os.environ.get("COMPLIANCE_MODEL", "claude-opus-5"),
        marketing_text_cap=int(
            os.environ.get("COMPLIANCE_MARKETING_TEXT_CAP", "2000")
        ),
        max_concurrency=int(os.environ.get("COMPLIANCE_MAX_CONCURRENCY", "4")),
        max_retries=int(os.environ.get("COMPLIANCE_MAX_RETRIES", "3")),
    )


def repo_root() -> Path:
    """
    Return the repository root directory (parent of src/).

    This file lives at src/compliance_agent/config.py.
    parents[0] = src/compliance_agent/
    parents[1] = src/
    parents[2] = repo root
    """
    return Path(__file__).parents[2]


def source_path(root: Path) -> Path:
    """Return the path to the FCA COBS 4 regulation source file."""
    return root / "data" / "regulations" / "fca-cobs-4-financial-promotions.txt"


def rules_dir(root: Path) -> Path:
    """Return the path to the committed rules cache directory."""
    return root / "rules"


def runs_dir(root: Path) -> Path:
    """Return the path to the per-run artifact directory (gitignored)."""
    return root / "runs"
