"""``hermes status`` — is hermes processing bars, and when is the next one?

Read-only: opens the ledger with SQLite's ``mode=ro`` and asks systemd about
``hermes.service``; it never writes and is safe next to a running
``hermes serve``. Leads with the verdict every console surface shares (all
good, late, down), then one row per stream with its last and next close.

Usage: ``hermes status [--config-dir DIR] [--db PATH] [--json]``

Exit codes: 0 all good, 1 late or down, 2 usage, 3 config error or no ledger.

Reference: openspec/changes/370-hermes-status/change.md.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, TextIO

from rich.console import Console

from hermes.adapters.ledger.status_reader import SqliteStatusReader
from hermes.application.status import (
    StatusReport,
    UnitState,
    VerdictLevel,
    build_report,
    ledger_missing_report,
)
from hermes.domain.errors import LedgerError
from hermes.entrypoints import serve as serve_module
from hermes.entrypoints.status_view import render_status, report_to_dict
from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.loader import DEFAULT_CONFIG_DIR, load_config

__all__ = ["main", "systemd_unit_state"]

_UNIT: Final = "hermes.service"
_USAGE: Final = (
    "Usage: hermes status [--config-dir DIR] [--db PATH] [--json]\n"
    "\n"
    "Read-only progress check: the verdict, then each stream's last and next close.\n"
    "Exit codes: 0 all good, 1 late or down, 2 usage, 3 config error or no ledger.\n"
)


def main(
    rest: list[str],
    *,
    stream: TextIO,
    now: datetime | None = None,
    unit_state: Callable[[], UnitState] | None = None,
) -> int:
    """Handle ``hermes status``.

    Args:
        rest: Residual argv after the ``status`` token.
        stream: Output stream.
        now: Reference instant; defaults to the wall clock. Injected by tests.
        unit_state: systemd probe; defaults to :func:`systemd_unit_state`.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(prog="hermes status", add_help=False)
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--db", type=Path, default=serve_module.DEFAULT_DB_PATH)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    try:
        parsed = parser.parse_args(rest)
    except SystemExit:
        stream.write("ERROR: could not parse `hermes status` arguments.\n" + _USAGE)
        return 2
    if parsed.help:
        stream.write(_USAGE)
        return 0

    try:
        config = load_config(parsed.config_dir)
    except ConfigError as exc:
        stream.write(f"ERROR: config load failed: {exc}\n")
        return 3

    at = now if now is not None else datetime.now(UTC)
    unit = (unit_state or systemd_unit_state)()
    market = config.market_data
    try:
        with SqliteStatusReader.open(parsed.db) as reader:
            report = build_report(
                reader,
                pairs=tuple(market.parsed_pairs()),
                intervals=tuple(market.intervals),
                now=at,
                settle=timedelta(seconds=market.settle_delay_seconds),
                grace=timedelta(seconds=market.stale_after_seconds),
                unit=unit,
            )
    except FileNotFoundError:
        report = ledger_missing_report(at, unit, str(parsed.db))
        _emit(report, stream, as_json=parsed.json)
        return 3
    except LedgerError as exc:
        stream.write(f"ERROR: could not read the ledger: {exc}\n")
        return 3

    _emit(report, stream, as_json=parsed.json)
    return 0 if report.verdict.level is VerdictLevel.OK else 1


def systemd_unit_state(
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> UnitState:
    """Ask systemd how ``hermes.service`` is doing.

    Args:
        run: ``subprocess.run`` stand-in for tests.

    Returns:
        The unit state, or ``UNKNOWN`` where there is no systemd (a dev box).
    """
    systemctl = shutil.which("systemctl")
    if systemctl is None:
        return UnitState.UNKNOWN
    try:
        result = run(
            [systemctl, "show", _UNIT, "--property=ActiveState,SubState"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except OSError, subprocess.TimeoutExpired:
        return UnitState.UNKNOWN
    props: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            props[key] = value
    return unit_state_from(props.get("ActiveState", ""), props.get("SubState", ""))


def unit_state_from(active: str, sub: str) -> UnitState:
    """Map systemd's ``ActiveState``/``SubState`` pair to a :class:`UnitState`.

    Args:
        active: ``ActiveState`` value, e.g. ``active`` or ``failed``.
        sub: ``SubState`` value, e.g. ``running`` or ``auto-restart``.

    Returns:
        The matching state; anything unrecognised is ``UNKNOWN``.
    """
    if active == "active":
        return UnitState.RUNNING
    if active == "failed":
        return UnitState.FAILED
    if sub == "auto-restart" or active == "activating":
        return UnitState.RESTARTING
    if active in {"inactive", "deactivating"}:
        return UnitState.STOPPED
    return UnitState.UNKNOWN


def _emit(report: StatusReport, stream: TextIO, *, as_json: bool) -> None:
    """Write ``report`` as JSON or as the Rich human view."""
    if as_json:
        payload: dict[str, Any] = report_to_dict(report)
        stream.write(json.dumps(payload, indent=2) + "\n")
        return
    render_status(report, Console(file=stream, highlight=False))
