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
        help=(
            "Path to a plain-text file containing the marketing copy, or '-' to "
            "read from stdin. Input past the 2000-code-point cap "
            "(COMPLIANCE_MARKETING_TEXT_CAP) is not read and the run is refused."
        ),
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


def _load_settings_or_exit():
    """
    Load settings, reporting a bad environment variable as a plain message.

    Exits 2 (incomplete) rather than letting ConfigError reach the top level
    as a traceback — a mistyped COMPLIANCE_* value is user error, not a crash.
    """
    from compliance_agent.config import ConfigError, load_settings

    try:
        return load_settings()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(2)


def _cmd_check(args: argparse.Namespace) -> None:
    """Handle the ``check`` subcommand."""
    from compliance_agent import pipeline

    settings = _load_settings_or_exit()

    # Read one code point past the cap and no further. Ingestion rejects
    # anything over the cap anyway, so buffering a whole oversized stream or
    # file first only spends memory to reach the same refusal.
    limit = settings.marketing_text_cap + 1

    if args.text == "-":
        marketing_text = sys.stdin.read(limit)
    else:
        try:
            with open(args.text, encoding="utf-8") as fh:
                marketing_text = fh.read(limit)
        except OSError as exc:
            print(f"Error reading file: {exc}", file=sys.stderr)
            sys.exit(1)

    exit_code = pipeline.run_check(marketing_text, settings, refresh=False)
    sys.exit(exit_code)


def _cmd_extract_rules(args: argparse.Namespace) -> None:
    """Handle the ``extract-rules`` subcommand."""
    from compliance_agent import pipeline

    settings = _load_settings_or_exit()
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

    base = runs_dir(repo_root()).resolve()
    report_path = (base / args.run_id / "report.md").resolve()

    # run_id comes from the command line: '..' segments must not walk the
    # lookup out of runs/. Both checks report the same thing, so a confined
    # path and a missing run are indistinguishable to the caller.
    if not report_path.is_relative_to(base) or not report_path.exists():
        print(
            f"Error: no report found for run '{args.run_id}'",
            file=sys.stderr,
        )
        sys.exit(1)

    print(report_path.read_text(encoding="utf-8"))
