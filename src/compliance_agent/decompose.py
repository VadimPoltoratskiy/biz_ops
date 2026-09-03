"""
Decomposition module — cache decision and extraction LLM call.

Writes to disk only via ``save_cache``. All other functions are pure transformations.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from compliance_agent.config import Settings
from compliance_agent.models import RulesCacheArtifact, TokenUsage
from compliance_agent import llm, prompts


def compute_source_hash(text: str) -> str:
    """Return the SHA-256 hex digest of *text* encoded as UTF-8."""
    return hashlib.sha256(text.encode()).hexdigest()


def cache_path(rules_dir: Path, source_id: str) -> Path:
    """Return the expected path for the rules cache JSON file."""
    return rules_dir / f"{source_id}.json"


def load_cache(rules_dir: Path, source_id: str) -> RulesCacheArtifact | None:
    """
    Load the rules cache from disk.

    Returns ``None`` if the file does not exist or fails to parse (a corrupt
    cache is treated as missing so extraction re-runs automatically).
    """
    path = cache_path(rules_dir, source_id)
    if not path.exists():
        return None
    try:
        return RulesCacheArtifact.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except Exception:
        return None


def save_cache(
    rules_dir: Path, source_id: str, artifact: RulesCacheArtifact
) -> None:
    """
    Write the rules cache artifact to disk.

    Creates *rules_dir* if it does not exist yet (AC-30: rules/ created on demand).
    """
    rules_dir.mkdir(parents=True, exist_ok=True)
    cache_path(rules_dir, source_id).write_text(
        artifact.model_dump_json(indent=2), encoding="utf-8"
    )


def needs_extraction(
    cache: RulesCacheArtifact | None,
    current_hash: str,
    refresh: bool,
) -> bool:
    """
    Determine whether rule extraction is needed.

    Returns ``True`` when:
    - ``refresh`` is ``True`` (AC-12: --refresh flag always forces re-extraction)
    - ``cache`` is ``None`` (no cache present at all)
    - ``cache.source_hash != current_hash`` (regulation source has changed, AC-11)

    Returns ``False`` when the cache is present and current and refresh is not
    requested (AC-10: reuse cache when hash matches).
    """
    if refresh:
        return True
    if cache is None:
        return True
    if cache.source_hash != current_hash:
        return True
    return False


def _parse_retrieved_date(source_text: str) -> str:
    """
    Extract the retrieval date from the provenance header in the source file.

    Searches the first 30 lines for an ISO-8601 date (YYYY-MM-DD). Falls back
    to today's date in UTC if no match is found.
    """
    for line in source_text.splitlines()[:30]:
        match = re.search(r"\d{4}-\d{2}-\d{2}", line)
        if match:
            return match.group(0)
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def run_extraction(
    source_text: str,
    source_id: str,
    source_hash: str,
    settings: Settings,
) -> tuple[RulesCacheArtifact, TokenUsage]:
    """
    Call the LLM to decompose the regulation source into structured rules.

    Args:
        source_text: The raw regulation text.
        source_id: Identifier string derived from the source filename stem.
        source_hash: SHA-256 hex digest of *source_text*.
        settings: Runtime configuration (model, retries, etc.).

    Returns:
        A tuple of ``(RulesCacheArtifact, TokenUsage)``.

    Raises:
        LLMAuthError, LLMBadRequestError, LLMRetryExhaustedError from ``llm.extract_rules``.
    """
    prompt = prompts.format_extraction_prompt(source_text)
    rule_list, usage = llm.extract_rules(source_text, settings, prompt)

    artifact = RulesCacheArtifact(
        source_id=source_id,
        source_hash=source_hash,
        retrieved_date=_parse_retrieved_date(source_text),
        extracted_at=datetime.now(timezone.utc).isoformat(),
        rules=rule_list.rules,
    )
    return (artifact, usage)


def get_rules(
    source_text: str,
    source_id: str,
    rules_dir: Path,
    settings: Settings,
    refresh: bool,
) -> tuple[RulesCacheArtifact, bool, TokenUsage | None]:
    """
    Return the rules artifact for a given source, loading from cache or extracting.

    Args:
        source_text: The raw regulation text (used for hashing and extraction).
        source_id: Identifier derived from the source filename stem.
        rules_dir: Directory in which the cache file lives.
        settings: Runtime configuration.
        refresh: When ``True``, always re-extract regardless of cache state.

    Returns:
        ``(artifact, was_extracted, usage_or_None)`` where:
        - ``was_extracted`` is ``True`` when the LLM was called.
        - ``usage_or_None`` is ``None`` when the cache was reused.
    """
    current_hash = compute_source_hash(source_text)
    cache = load_cache(rules_dir, source_id)

    if needs_extraction(cache, current_hash, refresh):
        artifact, usage = run_extraction(
            source_text, source_id, current_hash, settings
        )
        save_cache(rules_dir, source_id, artifact)
        return (artifact, True, usage)

    assert cache is not None  # needs_extraction returned False → cache is valid
    return (cache, False, None)
