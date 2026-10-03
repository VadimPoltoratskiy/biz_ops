"""
Tests for compliance_agent.cli — input bounding and path confinement.

Both behaviours guard the boundary between the command line and the pipeline:
oversized input must be refused before it is buffered, and a run ID typed by
the user must not address files outside runs/.
"""
from __future__ import annotations

import argparse
import sys

import pytest
from unittest.mock import patch

from compliance_agent import cli
from compliance_agent.config import ConfigError, Settings


def _settings(cap: int = 2000) -> Settings:
    return Settings(
        api_key="test-key",
        model="claude-haiku-4-5",
        marketing_text_cap=cap,
        max_concurrency=4,
        max_retries=0,
    )


# ---------------------------------------------------------------------------
# check — bounded input reads
# ---------------------------------------------------------------------------


def test_stdin_read_is_bounded_by_the_cap(monkeypatch):
    """
    A piped stream is read to cap + 1 and no further.

    `sys.stdin.read()` with no argument buffered the whole stream before
    ingestion applied the cap, so piping a multi-gigabyte file spent that
    memory only to reach the same refusal.
    """
    requested: list[int] = []

    class _Stdin:
        def read(self, size=-1):
            requested.append(size)
            return "a" * size

    monkeypatch.setattr(sys, "stdin", _Stdin())
    monkeypatch.setattr(cli, "_load_settings_or_exit", lambda: _settings(cap=50))

    with patch("compliance_agent.pipeline.run_check", return_value=2) as run_check:
        with pytest.raises(SystemExit) as exc_info:
            cli._cmd_check(argparse.Namespace(text="-"))

    assert exc_info.value.code == 2
    assert requested == [51]  # cap + 1 — never an unbounded read
    assert len(run_check.call_args.args[0]) == 51


def test_file_read_is_bounded_by_the_cap(tmp_path, monkeypatch):
    """The same bound applies to --text FILE, which is read the same way."""
    oversized = tmp_path / "copy.txt"
    oversized.write_text("b" * 5000, encoding="utf-8")

    monkeypatch.setattr(cli, "_load_settings_or_exit", lambda: _settings(cap=50))

    with patch("compliance_agent.pipeline.run_check", return_value=2) as run_check:
        with pytest.raises(SystemExit):
            cli._cmd_check(argparse.Namespace(text=str(oversized)))

    assert len(run_check.call_args.args[0]) == 51


def test_text_at_the_cap_is_passed_through_whole(tmp_path, monkeypatch):
    """Bounding must not clip input that is actually within the cap."""
    exact = tmp_path / "copy.txt"
    exact.write_text("c" * 50, encoding="utf-8")

    monkeypatch.setattr(cli, "_load_settings_or_exit", lambda: _settings(cap=50))

    with patch("compliance_agent.pipeline.run_check", return_value=0) as run_check:
        with pytest.raises(SystemExit) as exc_info:
            cli._cmd_check(argparse.Namespace(text=str(exact)))

    assert exc_info.value.code == 0
    assert run_check.call_args.args[0] == "c" * 50


# ---------------------------------------------------------------------------
# show — run_id confinement
# ---------------------------------------------------------------------------


def test_show_rejects_a_traversing_run_id(tmp_path, monkeypatch, capsys):
    """
    A run_id of '..' must not reach a report.md outside runs/.

    The lookup joined the raw run_id onto runs/, so '..' resolved to the
    repository root and printed whatever report.md sat there.
    """
    (tmp_path / "runs").mkdir()
    (tmp_path / "report.md").write_text("CONFIDENTIAL", encoding="utf-8")

    monkeypatch.setattr("compliance_agent.config.repo_root", lambda: tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        cli._cmd_show(argparse.Namespace(run_id=".."))

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "CONFIDENTIAL" not in captured.out
    assert "no report found" in captured.err


def test_show_prints_a_real_report(tmp_path, monkeypatch, capsys):
    """Confinement must not break the ordinary lookup."""
    run_dir = tmp_path / "runs" / "20260903-141522-a3f8c2"
    run_dir.mkdir(parents=True)
    (run_dir / "report.md").write_text("# Compliance report", encoding="utf-8")

    monkeypatch.setattr("compliance_agent.config.repo_root", lambda: tmp_path)

    cli._cmd_show(argparse.Namespace(run_id="20260903-141522-a3f8c2"))

    assert "# Compliance report" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# configuration errors
# ---------------------------------------------------------------------------


def test_bad_env_var_exits_2_with_a_message(monkeypatch, capsys):
    """A ConfigError is reported, not raised as a traceback."""
    monkeypatch.setattr(
        "compliance_agent.config.load_settings",
        lambda: (_ for _ in ()).throw(ConfigError("COMPLIANCE_MAX_RETRIES must be an integer, got 'yes'")),
    )

    with pytest.raises(SystemExit) as exc_info:
        cli._load_settings_or_exit()

    assert exc_info.value.code == 2
    assert "COMPLIANCE_MAX_RETRIES" in capsys.readouterr().err
