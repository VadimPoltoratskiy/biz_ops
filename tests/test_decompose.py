"""
Tests for compliance_agent.decompose — AC-10, AC-11, AC-12.

Covers cache hit, hash mismatch, refresh flag, and LLM routing.
"""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch

from compliance_agent.decompose import (
    cache_path,
    compute_source_hash,
    get_rules,
    load_cache,
    needs_extraction,
    save_cache,
)
from compliance_agent.models import (
    ExtractedRulesList,
    RulesCacheArtifact,
    TokenUsage,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_artifact(source_hash: str = "abc123", source_id: str = "test") -> RulesCacheArtifact:
    return RulesCacheArtifact(
        source_id=source_id,
        source_hash=source_hash,
        retrieved_date="2026-09-03",
        extracted_at="2026-09-03T00:00:00",
        rules=[],
    )


# ---------------------------------------------------------------------------
# needs_extraction — AC-10, AC-11, AC-12
# ---------------------------------------------------------------------------


def test_cache_hit_no_refresh():
    """AC-10: matching hash, no refresh flag → do not extract (returns False)."""
    hash_ = compute_source_hash("some text")
    cache = _make_artifact(source_hash=hash_)
    assert needs_extraction(cache, hash_, refresh=False) is False


def test_cache_miss_hash_mismatch():
    """AC-11: hash mismatch between cache and current source → extract (True)."""
    cache = _make_artifact(source_hash="oldhash")
    new_hash = compute_source_hash("different text")
    assert needs_extraction(cache, new_hash, refresh=False) is True


def test_cache_present_but_refresh_forced():
    """AC-12: refresh=True forces extraction even when the hash matches."""
    hash_ = compute_source_hash("some text")
    cache = _make_artifact(source_hash=hash_)
    assert needs_extraction(cache, hash_, refresh=True) is True


def test_no_cache_always_extracts():
    """No cache file at all → always extract."""
    assert needs_extraction(None, "anyhash", refresh=False) is True


def test_no_cache_refresh_also_extracts():
    """No cache + refresh=True → still extracts (both conditions true)."""
    assert needs_extraction(None, "anyhash", refresh=True) is True


# ---------------------------------------------------------------------------
# load_cache / save_cache roundtrip
# ---------------------------------------------------------------------------


def test_load_cache_missing_file_returns_none(tmp_path: Path):
    """load_cache returns None when the cache file does not exist."""
    assert load_cache(tmp_path, "nonexistent") is None


def test_load_cache_corrupt_json_returns_none(tmp_path: Path):
    """load_cache returns None when the file contains malformed JSON (treat as missing)."""
    (tmp_path / "corrupt.json").write_text("this is not valid json", encoding="utf-8")
    assert load_cache(tmp_path, "corrupt") is None


def test_save_and_load_cache_roundtrip(tmp_path: Path):
    """save_cache writes a file that load_cache deserialises correctly."""
    artifact = _make_artifact(source_hash="abc123", source_id="roundtrip")
    save_cache(tmp_path, "roundtrip", artifact)
    loaded = load_cache(tmp_path, "roundtrip")
    assert loaded is not None
    assert loaded.source_hash == "abc123"
    assert loaded.source_id == "roundtrip"


def test_save_cache_creates_directory_if_missing(tmp_path: Path):
    """save_cache creates the rules directory if it does not already exist."""
    new_dir = tmp_path / "newrules"
    assert not new_dir.exists()
    save_cache(new_dir, "test", _make_artifact())
    assert new_dir.exists()
    assert (new_dir / "test.json").exists()


def test_cache_path_format(tmp_path: Path):
    """cache_path builds the correct filename."""
    p = cache_path(tmp_path, "fca-cobs-4-financial-promotions")
    assert p == tmp_path / "fca-cobs-4-financial-promotions.json"


# ---------------------------------------------------------------------------
# get_rules — cache-reuse / extraction routing
# ---------------------------------------------------------------------------


def test_get_rules_reuses_cache_on_hash_hit(tmp_path: Path, settings, sample_rule):
    """AC-10: cache hit → get_rules returns cached rules without calling the LLM."""
    source_text = "Some regulation text"
    source_hash = compute_source_hash(source_text)
    artifact = RulesCacheArtifact(
        source_id="test-source",
        source_hash=source_hash,
        retrieved_date="2026-09-03",
        extracted_at="2026-09-03T00:00:00",
        rules=[sample_rule],
    )
    save_cache(tmp_path, "test-source", artifact)

    with patch("compliance_agent.llm.extract_rules") as mock_extract:
        result, was_extracted, usage = get_rules(
            source_text, "test-source", tmp_path, settings, refresh=False
        )
        mock_extract.assert_not_called()

    assert was_extracted is False
    assert usage is None
    assert len(result.rules) == 1
    assert result.rules[0].rule_id == sample_rule.rule_id


def test_get_rules_extracts_on_cache_miss(tmp_path: Path, settings, sample_rule):
    """AC-11: no cache → get_rules calls the LLM and saves the result."""
    source_text = "Some regulation text"
    extraction_usage = TokenUsage(input_tokens=100, output_tokens=50)
    extracted = ExtractedRulesList(rules=[sample_rule])

    with patch(
        "compliance_agent.llm.extract_rules", return_value=(extracted, extraction_usage)
    ):
        result, was_extracted, usage = get_rules(
            source_text, "test-source", tmp_path, settings, refresh=False
        )

    assert was_extracted is True
    assert usage == extraction_usage
    assert result.rules[0].rule_id == sample_rule.rule_id
    # Cache must have been written after extraction.
    assert (tmp_path / "test-source.json").exists()


def test_get_rules_refresh_forces_new_extraction(tmp_path: Path, settings, sample_rule):
    """AC-12: refresh=True forces a new LLM call even when the cache hash matches."""
    source_text = "Some regulation text"
    source_hash = compute_source_hash(source_text)
    artifact = RulesCacheArtifact(
        source_id="test-source",
        source_hash=source_hash,
        retrieved_date="2026-09-03",
        extracted_at="2026-09-03T00:00:00",
        rules=[sample_rule],
    )
    save_cache(tmp_path, "test-source", artifact)

    extraction_usage = TokenUsage(input_tokens=100, output_tokens=50)
    extracted = ExtractedRulesList(rules=[sample_rule])

    with patch(
        "compliance_agent.llm.extract_rules", return_value=(extracted, extraction_usage)
    ) as mock_extract:
        result, was_extracted, usage = get_rules(
            source_text, "test-source", tmp_path, settings, refresh=True
        )
        mock_extract.assert_called_once()

    assert was_extracted is True


def test_get_rules_hash_mismatch_triggers_extraction(tmp_path: Path, settings, sample_rule):
    """AC-11: stale cache (hash mismatch) triggers re-extraction."""
    # Write a cache with a different hash.
    artifact = RulesCacheArtifact(
        source_id="test-source",
        source_hash="stale-hash",
        retrieved_date="2026-09-03",
        extracted_at="2026-09-03T00:00:00",
        rules=[sample_rule],
    )
    save_cache(tmp_path, "test-source", artifact)

    # The current source text produces a different hash.
    source_text = "Updated regulation text"
    extraction_usage = TokenUsage(input_tokens=100, output_tokens=50)
    extracted = ExtractedRulesList(rules=[sample_rule])

    with patch(
        "compliance_agent.llm.extract_rules", return_value=(extracted, extraction_usage)
    ) as mock_extract:
        result, was_extracted, _ = get_rules(
            source_text, "test-source", tmp_path, settings, refresh=False
        )
        mock_extract.assert_called_once()

    assert was_extracted is True
