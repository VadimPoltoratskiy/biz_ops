"""
Ingestion module — pure functions that read and validate inputs.

No LLM calls; no file writes other than reading. Raises IngestionError for
invalid or missing inputs (AC-2, AC-3, AC-5).
"""

from __future__ import annotations

from pathlib import Path


class IngestionError(Exception):
    """Raised for invalid or missing inputs during ingestion (AC-2, AC-3, AC-5)."""


def read_source(source_path: Path) -> str:
    """
    Read the regulation source file as UTF-8 text.

    The raw text is returned without stripping so that the provenance header
    is preserved for hashing.

    Args:
        source_path: Absolute path to the regulation source file.

    Returns:
        The raw file contents.

    Raises:
        IngestionError: If the file does not exist or its content is empty (AC-2).
    """
    if not source_path.exists():
        raise IngestionError(
            f"Regulatory source not found or empty: {source_path}"
        )

    text = source_path.read_text(encoding="utf-8")

    if not text.strip():
        raise IngestionError(
            f"Regulatory source not found or empty: {source_path}"
        )

    return text


def validate_marketing_text(text: str, cap: int = 2000) -> str:
    """
    Validate the marketing text for emptiness and length.

    The length cap is enforced in Unicode code points: Python's ``len()`` on a
    ``str`` counts code points correctly, so this handles multi-byte characters
    and basic emoji (e.g. 🚀 = 1 code point) without additional processing.

    Note: A ZWJ emoji sequence (e.g. the family emoji 👨‍👩‍👧) may count as
    multiple code points because it is composed of several base code points
    joined by Zero Width Joiner (U+200D). The 2000 cap is counted in code
    points, not grapheme clusters or bytes. The README documents this.

    Args:
        text: The raw marketing text supplied by the user.
        cap: Maximum allowed length in Unicode code points; default 2000.

    Returns:
        ``text`` unchanged. The value is not stripped so that evidence-quote
        matching via ``verify_evidence_quote`` works correctly (AC-20).

    Raises:
        IngestionError: If ``text`` is empty or whitespace-only (AC-5).
        IngestionError: If ``len(text) > cap``, stating both the counted length
            and the limit (AC-3).
    """
    if text.strip() == "":
        raise IngestionError(
            "No marketing text provided (empty or whitespace-only input)"
        )

    length = len(text)  # code-point count, not byte count
    if length > cap:
        raise IngestionError(
            f"Marketing text too long: {length} code points "
            f"(limit is {cap}). "
            f"Reduce input to {cap} code points or fewer."
        )

    return text


def source_id_from_path(source_path: Path) -> str:
    """
    Return the source identifier derived from the filename stem.

    Used as the rules cache filename and in run records.

    Example:
        ``fca-cobs-4-financial-promotions.txt`` → ``"fca-cobs-4-financial-promotions"``
    """
    return source_path.stem
