"""
Static eval — validates the committed rules cache without calling any model.

This is the cheapest available check that the extractor cited the regulation
rather than paraphrasing it, and it costs nothing to run, so it gates every PR.

Four properties are checked per artifact in ``rules/``:

  1. Schema     — the JSON parses as a ``RulesCacheArtifact``.
  2. Provenance — ``source_hash`` matches a fresh SHA-256 of the source file,
                  so a regulation edit without a re-extraction is caught.
  3. Identity   — ``rule_id`` values are unique (they key the verdict join).
  4. Fidelity   — the share of ``source_quote`` values that are exact
                  substrings of the source stays at or above VERBATIM_FLOOR.

Exit codes: 0 all artifacts pass, 1 any check failed.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from compliance_agent.config import repo_root, rules_dir
from compliance_agent.models import RulesCacheArtifact

# 73 of 75 quotes are verbatim today (0.973). The floor sits below that so the
# two known paraphrases do not fail the build, but a regression in extraction
# fidelity does. Raise this if the extractor improves; never lower it silently.
VERBATIM_FLOOR = 0.95


def _source_for(root: Path, source_id: str) -> Path:
    return root / "data" / "regulations" / f"{source_id}.txt"


def check_artifact(path: Path, root: Path) -> list[str]:
    """Return a list of failure strings for one rules artifact (empty = pass)."""
    failures: list[str] = []

    try:
        artifact = RulesCacheArtifact.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except Exception as exc:
        return [f"{path.name}: does not validate as RulesCacheArtifact — {exc}"]

    rules = artifact.rules
    print(f"  {path.name}: {len(rules)} rules, source_id={artifact.source_id}")

    if not rules:
        failures.append(f"{path.name}: contains zero rules")
        return failures

    # 2. Provenance
    source_path = _source_for(root, artifact.source_id)
    if not source_path.exists():
        failures.append(f"{path.name}: source file not found at {source_path}")
        return failures

    source_text = source_path.read_text(encoding="utf-8")
    actual_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    if actual_hash != artifact.source_hash:
        failures.append(
            f"{path.name}: source_hash is stale — cache says "
            f"{artifact.source_hash[:12]}…, source hashes to {actual_hash[:12]}…. "
            f"Re-run `compliance-agent extract-rules --refresh`."
        )
    else:
        print(f"    provenance: source_hash matches ({actual_hash[:12]}…)")

    # 3. Identity
    ids = [r.rule_id for r in rules]
    if len(set(ids)) != len(ids):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        failures.append(f"{path.name}: duplicate rule_id values — {dupes}")
    else:
        print(f"    identity: {len(ids)} unique rule_ids")

    # 4. Fidelity
    missed = [r.rule_id for r in rules if r.source_quote not in source_text]
    verbatim = len(rules) - len(missed)
    ratio = verbatim / len(rules)
    print(f"    fidelity: {verbatim}/{len(rules)} quotes verbatim ({ratio:.1%})")
    for rule_id in missed:
        print(f"      not verbatim: {rule_id}")
    if ratio < VERBATIM_FLOOR:
        failures.append(
            f"{path.name}: verbatim quote ratio {ratio:.1%} is below the "
            f"{VERBATIM_FLOOR:.0%} floor ({len(missed)} paraphrased quotes)"
        )

    return failures


def main() -> int:
    root = repo_root()
    artifacts = sorted(rules_dir(root).glob("*.json"))

    print(f"Static rules-cache gate — {len(artifacts)} artifact(s) in rules/")
    if not artifacts:
        print("FAIL: no rules artifacts found; the committed cache is required.")
        return 1

    failures: list[str] = []
    for path in artifacts:
        failures.extend(check_artifact(path, root))

    print()
    if failures:
        print(f"FAIL — {len(failures)} problem(s):")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("PASS — rules cache is schema-valid, in sync with its source, and cited verbatim.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
