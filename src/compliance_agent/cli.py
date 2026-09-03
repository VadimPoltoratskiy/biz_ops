"""
CLI entry point — thin argparse layer over ``pipeline.py``.

Parses arguments, calls pipeline functions, and passes exit codes to
``sys.exit()``. Uses stdlib ``argparse`` (no additional dependencies).

Subcommands:
  check          — evaluate marketing text against FCA COBS 4 rules
  extract-rules  — (re-)extract rules from the regulation source
  history        — list past runs from runs/history.jsonl
  show           — display the report for a past run by run ID
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    """
    Entry point registered in pyproject.toml as ``compliance-agent``.
    """
    parser = argparse.ArgumentParser(
        prog="compliance-agent",
        description="FCA COBS 4 financial promotion compliance checker",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ------------------------------------------------------------------
    # check
    # ------------------------------------------------------------------
    check_parser = subparsers.add_parser(
        "check", help="Evaluate marketing text against FCA COBS 4 rules"
    )
    check_parser.add_argument(
        "--text",
        required=True,
        metavar="FILE|-",
        help="Path to a plain-text file containing the marketing copy, or '-' to read from stdin",
    )

    # ------------------------------------------------------------------
    # extract-rules
    # ------------------------------------------------------------------
    extract_parser = subparsers.add_parser(
        "extract-rules",
        help="(Re-)extract rules from the regulation source and update the cache",
    )
    extract_parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force re-extraction even if the cache is current (ignores hash match)",
    )

    # ------------------------------------------------------------------
    # history
    # ------------------------------------------------------------------
    subparsers.add_parser(
        "history",
        help="List all past runs recorded in runs/history.jsonl",
    )

    # ------------------------------------------------------------------
    # show
    # ------------------------------------------------------------------
    show_parser = subparsers.add_parser(
        "show",
        help="Display the Markdown report for a past run",
    )
    show_parser.add_argument(
        "run_id",
        help="Run ID as shown by the 'history' command",
    )

    args = parser.parse_args()

    if args.command == "check":
        _cmd_check(args)
    elif args.command == "extract-rules":
        _cmd_extract_rules(args)
    elif args.command == "history":
        _cmd_history()
    elif args.command == "show":
        _cmd_show(args)


# ---------------------------------------------------------------------------
# Sub-command handlers
# ---------------------------------------------------------------------------


def _cmd_check(args: argparse.Namespace) -> None:
    """Handle the ``check`` subcommand."""
    if args.text == "-":
        marketing_text = sys.stdin.read()
    else:
        try:
            marketing_text = Path(args.text).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"Error reading file: {exc}", file=sys.stderr)
            sys.exit(1)

    from compliance_agent.config import load_settings
    from compliance_agent import pipeline

    settings = load_settings()
    exit_code = pipeline.run_check(marketing_text, settings, refresh=False)
    sys.exit(exit_code)


def _cmd_extract_rules(args: argparse.Namespace) -> None:
    """Handle the ``extract-rules`` subcommand."""
    from compliance_agent.config import load_settings
    from compliance_agent import pipeline

    settings = load_settings()
    exit_code = pipeline.run_extract_rules(settings, args.refresh)
    sys.exit(exit_code)


def _cmd_history() -> None:
    """Handle the ``history`` subcommand."""
    from compliance_agent.config import repo_root, runs_dir
    from compliance_agent.models import HistoryLine

    history_path = runs_dir(repo_root()) / "history.jsonl"

    if not history_path.exists():
        print("No runs recorded yet.")
        return

    raw_lines = history_path.read_text(encoding="utf-8").splitlines()
    if not raw_lines:
        print("No runs recorded yet.")
        return

    header = f"{'Run ID':<30} {'Timestamp':<26} {'Outcome':<15} Exit"
    print(header)
    print("-" * len(header))

    for raw in raw_lines:
        try:
            record = HistoryLine.model_validate_json(raw)
            print(
                f"{record.run_id:<30} {record.timestamp:<26} "
                f"{record.overall_outcome:<15} {record.exit_code}"
            )
        except Exception:
            # Skip unparseable lines gracefully.
            continue


def _cmd_show(args: argparse.Namespace) -> None:
    """Handle the ``show`` subcommand."""
    from compliance_agent.config import repo_root, runs_dir

    report_path = runs_dir(repo_root()) / args.run_id / "report.md"

    if not report_path.exists():
        print(
            f"Error: no report found for run '{args.run_id}'",
            file=sys.stderr,
        )
        sys.exit(1)

    print(report_path.read_text(encoding="utf-8"))
