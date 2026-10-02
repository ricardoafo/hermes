"""Tests for ``python -m hermes`` (``src/hermes/__main__.py``).

The module is a four-line delegate to :func:`hermes.entrypoints.cli.main`.
These tests run the real package entry through ``runpy``, so they fail if
the module is missing or stops returning the CLI exit code.

Reference: openspec/changes/306-serve-systemd-boot/change.md.
"""

from __future__ import annotations

import runpy
import sys

import pytest


def _run_hermes_as_main(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    """Run ``python -m hermes <argv>`` in-process and return its exit code."""
    monkeypatch.setattr(sys, "argv", ["hermes", *argv])
    with pytest.raises(SystemExit) as raised:
        runpy.run_module("hermes", run_name="__main__", alter_sys=True)
    code = raised.value.code
    assert isinstance(code, int)
    return code


def test_python_dash_m_hermes_prints_the_cli_usage(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`python -m hermes --help` shows the same usage banner as the console script."""
    code = _run_hermes_as_main(monkeypatch, "--help")
    assert code == 0
    assert "Usage: hermes" in capsys.readouterr().out


def test_python_dash_m_hermes_propagates_the_cli_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown subcommand exits 2 through `-m` exactly as it does through `hermes`."""
    assert _run_hermes_as_main(monkeypatch, "no-such-subcommand") == 2
