"""
Tests for compliance_agent.ingest — AC-2, AC-3, AC-5.

All tests are pure Python with no LLM calls.
"""
from __future__ import annotations

import pytest
from pathlib import Path

from compliance_agent.ingest import (
    IngestionError,
    read_source,
    source_id_from_path,
    validate_marketing_text,
)


# ---------------------------------------------------------------------------
# validate_marketing_text — code-point cap (AC-3)
# ---------------------------------------------------------------------------


def test_validate_at_cap_boundary():
    """AC-3: exactly 2000 code points is accepted."""
    text = "a" * 2000
    result = validate_marketing_text(text, cap=2000)
    assert result == text


def test_validate_one_over_cap_rejected():
    """AC-3: 2001 code points is rejected; error message states both counts."""
    text = "a" * 2001
    with pytest.raises(IngestionError) as exc_info:
        validate_marketing_text(text, cap=2000)
    msg = str(exc_info.value)
    # The error must state both the actual count and the limit (AC-3).
    assert "2001" in msg
    assert "2000" in msg


def test_validate_emoji_single_codepoint_at_cap():
    """AC-3: 🚀 is 1 code point; 2000 rockets equals exactly the cap → accepted."""
    text = "🚀" * 2000
    result = validate_marketing_text(text, cap=2000)
    assert result == text


def test_validate_emoji_one_over_cap_rejected():
    """AC-3: 2001 rockets = 2001 code points → rejected with correct counts."""
    text = "🚀" * 2001
    with pytest.raises(IngestionError) as exc_info:
        validate_marketing_text(text, cap=2000)
    assert "2001" in str(exc_info.value)
    assert "2000" in str(exc_info.value)


def test_validate_default_cap_is_2000():
    """The default cap parameter is 2000."""
    text = "a" * 2000
    assert validate_marketing_text(text) == text  # no explicit cap


def test_validate_custom_cap_respected():
    """A custom cap value is respected."""
    text = "a" * 10
    assert validate_marketing_text(text, cap=10) == text
    with pytest.raises(IngestionError):
        validate_marketing_text(text + "a", cap=10)


# ---------------------------------------------------------------------------
# validate_marketing_text — empty/whitespace rejection (AC-5)
# ---------------------------------------------------------------------------


def test_validate_empty_string_rejected():
    """AC-5: empty string is rejected before any LLM call."""
    with pytest.raises(IngestionError):
        validate_marketing_text("", cap=2000)


def test_validate_whitespace_only_rejected():
    """AC-5: whitespace-only string is rejected before any LLM call."""
    with pytest.raises(IngestionError):
        validate_marketing_text("   \n\t  ", cap=2000)


def test_validate_single_space_rejected():
    """AC-5: a single space is also rejected."""
    with pytest.raises(IngestionError):
        validate_marketing_text(" ", cap=2000)


# ---------------------------------------------------------------------------
# validate_marketing_text — text preservation
# ---------------------------------------------------------------------------


def test_validate_normal_text_passes():
    """Happy path: normal marketing text passes unchanged."""
    text = "Install our app and get rich tomorrow"
    assert validate_marketing_text(text) == text


def test_validate_returns_text_unchanged_with_leading_whitespace():
    """
    The returned text must NOT be stripped.

    Evidence-quote matching uses Python's 'in' operator against the original
    text. Stripping the text would break quote matching for edge-case inputs.
    """
    text = "  leading space  "
    # Has non-whitespace content, so not rejected.
    result = validate_marketing_text(text)
    assert result == text


# ---------------------------------------------------------------------------
# read_source (AC-2)
# ---------------------------------------------------------------------------


def test_read_source_missing_file(tmp_path: Path):
    """AC-2: IngestionError is raised when the file does not exist."""
    missing = tmp_path / "missing.txt"
    with pytest.raises(IngestionError) as exc_info:
        read_source(missing)
    assert str(missing) in str(exc_info.value)


def test_read_source_empty_file(tmp_path: Path):
    """AC-2: IngestionError is raised when the file is empty (zero bytes)."""
    empty_file = tmp_path / "empty.txt"
    empty_file.write_text("", encoding="utf-8")
    with pytest.raises(IngestionError):
        read_source(empty_file)


def test_read_source_whitespace_only_file(tmp_path: Path):
    """AC-2: IngestionError is raised when the file contains only whitespace."""
    ws_file = tmp_path / "ws.txt"
    ws_file.write_text("   \n\n  ", encoding="utf-8")
    with pytest.raises(IngestionError):
        read_source(ws_file)


def test_read_source_valid_file_returns_contents(tmp_path: Path):
    """Happy path: valid file is returned verbatim (no stripping)."""
    content = "RETRIEVED: 2026-09-03\nA firm must not mislead clients."
    source_file = tmp_path / "source.txt"
    source_file.write_text(content, encoding="utf-8")
    assert read_source(source_file) == content


def test_read_source_preserves_provenance_header(tmp_path: Path):
    """The provenance header is preserved (needed for hashing)."""
    content = "RETRIEVED: 2026-09-03\nSOME REGULATION\nMore text."
    source_file = tmp_path / "reg.txt"
    source_file.write_text(content, encoding="utf-8")
    result = read_source(source_file)
    assert result.startswith("RETRIEVED:")


# ---------------------------------------------------------------------------
# source_id_from_path
# ---------------------------------------------------------------------------


def test_source_id_from_path():
    """source_id is derived from the filename stem, not the full path."""
    p = Path("/some/dir/fca-cobs-4-financial-promotions.txt")
    assert source_id_from_path(p) == "fca-cobs-4-financial-promotions"
