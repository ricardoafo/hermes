"""``hermes status`` — is hermes processing bars, and when is the next one?

Read-only: opens the ledger with SQLite's ``mode=ro`` and asks systemd about
``hermes.service``; it never writes and is safe next to a running
``hermes serve``. Leads with the verdict every console surface shares (all
good, late, down), then one row per stream with its last and next close.

Usage: ``hermes status [--config-dir DIR] [--db PATH] [--json | --summary]``

``--summary`` prints the one-line daily summary instead (#373); with
``--journal FILE`` (``-`` for stdin) it adds the window's journal error and
warning counts, and ``--restarts N`` the systemd restart count.

The header block also shows the running version (``v1.0.0-shadow.1``, ADR-022),
the running release (the ``/opt/hermes/current`` target name) and when it
went live; ``release: unknown`` without the link.

Exit codes: 0 all good, 1 late or down, 2 usage, 3 config error or no ledger.

Reference: openspec/changes/370-hermes-status/change.md.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, TextIO

from rich.console import Console

from hermes import __version__
from hermes.adapters.console import UnknownScenarioError
from hermes.adapters.ledger.status_reader import SqliteStatusReader
from hermes.application.console import Surface
from hermes.application.console.render import RenderContext, render_heartbeat
from hermes.application.console.views import HeartbeatState
from hermes.application.status import (
    StatusReport,
    UnitState,
    VerdictLevel,
    build_report,
    ledger_missing_report,
)
from hermes.domain.errors import LedgerError
from hermes.entrypoints import console as console_module
from hermes.entrypoints._streams import resolve_err
from hermes.entrypoints.daily_line import JournalCounts, count_journal, daily_line
from hermes.entrypoints.status_view import render_status, report_to_dict
from hermes.entrypoints.version import DEFAULT_RELEASE_LINK, ReleaseInfo, read_release
from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.loader import load_config
from hermes.release_version import release_label

__all__ = ["main", "systemd_unit_state"]

_UNIT: Final = "hermes.service"
_BOT_UNIT: Final = "hermes-bot.service"
_USAGE: Final = (
    "Usage: hermes status [--config-dir DIR] [--db PATH] [--json | --summary]\n"
    "                     [--journal FILE|-] [--restarts N]\n"
    "\n"
    "Read-only progress check: the verdict, then each stream's last and next close.\n"
    "--summary prints the one-line daily summary; --journal and --restarts feed it.\n"
    "Exit codes: 0 all good, 1 late or down, 2 usage, 3 config error or no ledger.\n"
)


def main(
    rest: list[str],
    *,
    stream: TextIO,
    now: datetime | None = None,
    unit_state: Callable[[], UnitState] | None = None,
    stdin: TextIO | None = None,
    demo_flag: str | None = None,
    env: Mapping[str, str] | None = None,
    bot_state: Callable[[], UnitState] | None = None,
    err: TextIO | None = None,
    release_link: Path = DEFAULT_RELEASE_LINK,
    package_version: str = __version__,
) -> int:
    """Handle ``hermes status``.

    With a demo scenario (a leading ``--demo`` or ``HERMES_CONSOLE_DEMO``) the
    command shows that scenario's console heartbeat, tagged DEMO DATA, instead
    of reading the ledger (#454).

    Args:
        rest: Residual argv after the ``status`` token.
        stream: Output stream.
        now: Reference instant; defaults to the wall clock. Injected by tests.
        unit_state: systemd probe; defaults to :func:`systemd_unit_state`.
        stdin: Where ``--journal -`` reads from; defaults to ``sys.stdin``.
        demo_flag: Scenario from the leading ``--demo`` option, if any.
        env: Environment snapshot for ``HERMES_CONSOLE_DEMO``; defaults to empty.
        bot_state: Probe for ``hermes-bot.service``; defaults to systemd.
        err: Stream for ERROR lines; defaults to ``sys.stderr``.
        release_link: The ``current`` symlink the release is read from.
        package_version: The running package version; injected by tests.

    Returns:
        Process exit code.
    """
    err = resolve_err(err)
    parser = argparse.ArgumentParser(prog="hermes status", add_help=False)
    parser.add_argument("--config-dir", type=Path, default=None)
    parser.add_argument("--db", type=Path, default=None)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--json", action="store_true")
    mode.add_argument("--summary", action="store_true")
    parser.add_argument("--journal", default=None)
    parser.add_argument("--restarts", type=int, default=0)
    parser.add_argument("-h", "--help", action="store_true")
    try:
        parsed = parser.parse_args(rest)
    except SystemExit:
        err.write("ERROR: could not parse `hermes status` arguments.\n" + _USAGE)
        return 2
    parsed.config_dir = parsed.config_dir or console_module.default_config_dir(env or {})
    parsed.db = parsed.db or console_module.default_db_path(env or {})
    if parsed.help:
        stream.write(_USAGE)
        return 0
    if demo_flag or (env or {}).get(console_module.DEMO_ENV_VAR, "").strip():
        return _demo_status(stream, err, demo_flag=demo_flag, env=env or {}, as_json=parsed.json)
    return _ledger_status(
        parsed,
        stream,
        err,
        now=now,
        unit_state=unit_state,
        stdin=stdin or sys.stdin,
        bot_state=bot_state,
        release=read_release(release_link),
        version=release_label(package_version),
    )


def _ledger_status(
    parsed: argparse.Namespace,
    stream: TextIO,
    err: TextIO,
    *,
    now: datetime | None,
    unit_state: Callable[[], UnitState] | None,
    stdin: TextIO,
    bot_state: Callable[[], UnitState] | None,
    release: ReleaseInfo,
    version: str,
) -> int:
    """Build the report from config, the ledger and systemd, then print it."""
    try:
        config = load_config(parsed.config_dir)
    except ConfigError as exc:
        err.write(f"ERROR: config load failed: {exc}\n")
        return 3

    at = now if now is not None else datetime.now(UTC)
    unit = (unit_state or systemd_unit_state)()
    bot = (bot_state or (lambda: systemd_unit_state(unit=_BOT_UNIT)))()
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
        code = 3
    except LedgerError as exc:
        err.write(f"ERROR: could not read the ledger: {exc}\n")
        return 3
    else:
        code = 0 if report.verdict.level is VerdictLevel.OK else 1

    if parsed.summary:
        counts = _journal_counts(parsed.journal, stdin)
        stream.write(daily_line(report, counts, parsed.restarts) + "\n")
    else:
        _emit(report, stream, as_json=parsed.json, bot=bot, release=release, version=version)
    return code


def systemd_unit_state(
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    unit: str = _UNIT,
) -> UnitState:
    """Ask systemd how ``unit`` is doing.

    Args:
        run: ``subprocess.run`` stand-in for tests.
        unit: The unit to ask about; ``hermes.service`` by default.

    Returns:
        The unit state, or ``UNKNOWN`` where there is no systemd (a dev box).
    """
    systemctl = shutil.which("systemctl")
    if systemctl is None:
        return UnitState.UNKNOWN
    try:
        result = run(
            [systemctl, "show", unit, "--property=LoadState,ActiveState,SubState"],
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
    if props.get("LoadState") == "not-found":
        return UnitState.NOT_INSTALLED
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


def _journal_counts(source: str | None, stdin: TextIO) -> JournalCounts:
    """Count journal errors and warnings from ``source`` (a path, ``-``, or none)."""
    if source is None:
        return JournalCounts()
    if source == "-":
        return count_journal(stdin)
    with Path(source).open(encoding="utf-8", errors="replace") as handle:
        return count_journal(handle)


def _demo_status(
    stream: TextIO, err: TextIO, *, demo_flag: str | None, env: Mapping[str, str], as_json: bool
) -> int:
    """Show a demo scenario's heartbeat; exit 0 when OK, 1 late or down, 2 unknown."""
    try:
        model = console_module.resolve_read_model(demo_flag=demo_flag, env=env)
    except UnknownScenarioError as exc:
        err.write(f"ERROR: {exc}\n")
        return 2
    heartbeat = model.heartbeat()
    if as_json:
        payload = {
            "demo_scenario": model.demo_scenario,
            "heartbeat": {
                "state": heartbeat.state.value,
                "as_of": heartbeat.as_of.isoformat(),
                "last_check_at": heartbeat.last_check_at.isoformat(),
                "next_check_at": heartbeat.next_check_at.isoformat(),
                "down_since": None
                if heartbeat.down_since is None
                else heartbeat.down_since.isoformat(),
            },
        }
        stream.write(json.dumps(payload, indent=2) + "\n")
    else:
        console = Console(file=stream, emoji=False, markup=False, highlight=False)
        ctx = RenderContext.for_model(model, surface=Surface.OPERATOR_CLI, width=console.width)
        console.print(render_heartbeat(heartbeat, ctx))
    return 0 if heartbeat.state is HeartbeatState.OK else 1


def _emit(
    report: StatusReport,
    stream: TextIO,
    *,
    as_json: bool,
    bot: UnitState = UnitState.UNKNOWN,
    release: ReleaseInfo | None = None,
    version: str | None = None,
) -> None:
    """Write ``report`` as JSON or as the Rich human view."""
    if as_json:
        payload: dict[str, Any] = report_to_dict(report, bot=bot, release=release, version=version)
        stream.write(json.dumps(payload, indent=2) + "\n")
        return
    render_status(
        report, Console(file=stream, highlight=False), bot=bot, release=release, version=version
    )
