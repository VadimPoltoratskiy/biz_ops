"""
Run log module — handles all disk writes for run persistence.

Per AC-29: exactly one ``append_history`` call per run. No file locking is
used; the tool is documented as single-run-at-a-time.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from pathlib import Path

from compliance_agent.models import HistoryLine, RunRecord


def create_run_id() -> str:
    """
    Generate a unique run ID combining a UTC timestamp and 6 random hex chars.

    Format: ``YYYYMMDD-HHMMSS-<6-hex>``
    Example: ``"20260903-141522-a3f8c2"``

    The 6-character random suffix prevents collisions in fast test runs where
    multiple runs may start within the same second.  ``secrets.token_hex(3)``
    produces 3 bytes = 6 hex characters using the OS CSPRNG (no new dep).
    """
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{secrets.token_hex(3)}"


def create_run_dir(runs_dir: Path, run_id: str) -> Path:
    """
    Create and return the per-run artifact directory.

    Uses ``exist_ok=True`` so re-entrant calls are safe.
    """
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def write_run_artifacts(
    run_dir: Path, run_record: RunRecord, markdown: str
) -> None:
    """
    Write all per-run artifacts to *run_dir* (AC-28, AC-31).

    Files written (all UTF-8):
      ``input.txt``    — the raw marketing text
      ``rules.json``   — the rules used in this run
      ``verdicts.json`` — the verdict for each rule
      ``report.md``    — the Markdown compliance report
      ``run.json``     — the complete ``RunRecord`` (includes stages and token usage)
    """
    (run_dir / "input.txt").write_text(run_record.marketing_input, encoding="utf-8")

    (run_dir / "rules.json").write_text(
        json.dumps([r.model_dump() for r in run_record.rules_used], indent=2),
        encoding="utf-8",
    )

    (run_dir / "verdicts.json").write_text(
        json.dumps([v.model_dump() for v in run_record.verdicts], indent=2),
        encoding="utf-8",
    )

    (run_dir / "report.md").write_text(markdown, encoding="utf-8")

    (run_dir / "run.json").write_text(
        run_record.model_dump_json(indent=2), encoding="utf-8"
    )


def append_history(runs_dir: Path, line: HistoryLine) -> None:
    """
    Append one ``HistoryLine`` to ``runs/history.jsonl`` (AC-29).

    Creates *runs_dir* if it does not exist yet.
    Each line is a complete JSON object terminated by a newline, so the file
    is valid JSONL (one object per line, parseable line-by-line).
    """
    runs_dir.mkdir(parents=True, exist_ok=True)
    history_path = runs_dir / "history.jsonl"
    with open(history_path, "a", encoding="utf-8") as fh:
        fh.write(line.model_dump_json() + "\n")
