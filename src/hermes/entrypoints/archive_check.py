"""``hermes archive-check`` — compare one day of live bars with the Binance archive (#359).

Run daily by ``hermes-archive-check.timer`` for the previous UTC day. Reads
the ledger's live bars read-only, downloads the day's archived klines for every
traded pair and interval, and sends one alert per mismatch through the anomaly
routing row. Each alert is first appended to the ledger ``events`` table
(#136); that append is the command's only write. Unpublished archive days
are reported and skipped; the next run cannot catch them up, so the timer runs
late enough in the day (see the unit file).

Dead-man's switch (#38): when ``HERMES_HEALTHCHECKS__ARCHIVE_CHECK_PING_URL``
is set, a run that completed (match or mismatch) pings it with the summary line,
and a run that could not check pings ``<url>/fail`` with the error. Unset: no
ping. A failed ping is reported and never changes the exit code.

Exit codes: 0 all streams match, 1 at least one mismatch (alerts sent),
2 usage, 3 config, ledger or archive failure.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping
from datetime import date, timedelta
from typing import TextIO

import httpx

from hermes.adapters.ledger.live_bar_reader import SqliteLiveBarReader
from hermes.adapters.market_data.binance_vision_daily import BinanceVisionDailyArchive
from hermes.adapters.notifier.slack_adapter import SlackAdapter, SlackConfig, SlackNotifierError
from hermes.adapters.notifier.telegram_adapter import (
    TelegramAdapter,
    TelegramConfig,
    TelegramNotifierError,
)
from hermes.application.archive_check import ArchiveCheckReport, run_archive_check
from hermes.application.notification_router import EventClass, RouterConfig, route
from hermes.domain.errors import InfraError
from hermes.domain.events.anomaly_detected import AnomalyDetected
from hermes.entrypoints._anomaly_record import record_anomaly
from hermes.entrypoints._cli import (
    add_config_dir_arg,
    add_db_arg,
    load_config_or_exit,
    require_ledger,
)
from hermes.entrypoints._streams import resolve_err
from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.logging import new_correlation_id
from hermes.infrastructure.time import now_utc

__all__ = ["main"]

_HTTP_TIMEOUT_SEC = 30.0
_PING_URL_ENV = "HERMES_HEALTHCHECKS__ARCHIVE_CHECK_PING_URL"
_PING_TIMEOUT_SEC = 10.0


def _ping(url: str, body: str) -> None:
    """POST ``body`` to a healthchecks.io ping URL."""
    httpx.post(url, content=body.encode("utf-8"), timeout=_PING_TIMEOUT_SEC).raise_for_status()


def _report_deadman(env: Mapping[str, str], *, ok: bool, body: str, stream: TextIO) -> None:
    url = env.get(_PING_URL_ENV, "")
    if not url:
        return
    try:
        _ping(url if ok else f"{url}/fail", body)
    except httpx.HTTPError as exc:
        stream.write(f"WARNING: healthcheck ping failed: {type(exc).__name__}\n")


def _parse(argv: list[str], stream: TextIO, err: TextIO) -> argparse.Namespace | None:
    parser = argparse.ArgumentParser(prog="hermes archive-check", add_help=False)
    parser.add_argument("--date", type=date.fromisoformat, default=None)
    add_db_arg(parser)
    add_config_dir_arg(parser)
    parser.add_argument("-h", "--help", action="store_true")
    try:
        parsed = parser.parse_args(argv)
    except SystemExit:
        err.write("ERROR: could not parse `hermes archive-check` arguments.\n")
        return None
    if parsed.help:
        stream.write(
            "Usage: hermes archive-check [--date YYYY-MM-DD] [--db PATH] [--config-dir DIR]\n"
            "\n"
            "Compares the stored live bars of one UTC day (default: yesterday) with the\n"
            "Binance public archive and alerts on any mismatch. Each alert is recorded\n"
            "in the ledger; nothing else is written.\n"
            "Exit codes: 0 match, 1 mismatch, 2 usage, 3 config, ledger or archive failure.\n"
        )
    return parsed


def _router(env: Mapping[str, str]) -> RouterConfig:
    try:
        telegram = TelegramAdapter.create(TelegramConfig.from_env(dict(env)))
    except TelegramNotifierError:
        telegram = None
    try:
        slack = SlackAdapter.create(SlackConfig.from_env(dict(env)))
    except SlackNotifierError:
        slack = None
    return RouterConfig(telegram=telegram, slack=slack)


def _write_report(report: ArchiveCheckReport, stream: TextIO) -> None:
    stream.write(
        f"archive-check {report.day.isoformat()}: {len(report.checked)} streams checked, "
        f"{len(report.unpublished)} not published yet, {len(report.mismatches)} mismatches\n"
    )
    stream.writelines(
        f"  not published: {pair} {interval.value}\n" for pair, interval in report.unpublished
    )
    for m in report.mismatches:
        detail = f" ({m.detail})" if m.detail else ""
        stream.write(
            f"  {m.kind.value}: {m.pair} {m.interval.value} {m.open_time.isoformat()}{detail}\n"
        )


def main(
    argv: list[str],
    *,
    stream: TextIO,
    env: Mapping[str, str] | None = None,
    err: TextIO | None = None,
) -> int:
    """Parse ``argv`` and run the archive check.

    Args:
        argv: Arguments after the ``archive-check`` token.
        stream: Output stream.
        env: Environment for notifier credentials; defaults to ``os.environ``.
        err: Stream for ERROR lines; defaults to ``sys.stderr``.

    Returns:
        Process exit code.
    """
    err = resolve_err(err)
    parsed = _parse(argv, stream, err)
    if parsed is None:
        return 2
    if parsed.help:
        return 0
    if env is None:
        env = dict(os.environ)
    deadman_env = env

    def report_config_failure(exc: ConfigError) -> None:
        _report_deadman(deadman_env, ok=False, body=f"config: {exc}", stream=stream)

    config = load_config_or_exit(parsed.config_dir, err, on_error=report_config_failure)
    if isinstance(config, int):
        return config
    missing = require_ledger(parsed.db, err)
    if missing is not None:
        _report_deadman(env, ok=False, body=f"no ledger at {parsed.db}", stream=stream)
        return missing
    now = now_utc()
    day = parsed.date if parsed.date is not None else (now - timedelta(days=1)).date()
    router = _router(env)

    def notify(event: AnomalyDetected) -> None:
        record_anomaly(parsed.db, event, err)
        for adapter in route(EventClass.ANOMALY_DETECTED, router):
            adapter.notify(event)

    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT_SEC) as client:
            report = run_archive_check(
                archive=BinanceVisionDailyArchive(client),
                live=SqliteLiveBarReader(parsed.db),
                pairs=config.market_data.parsed_pairs(),
                intervals=tuple(config.market_data.intervals),
                day=day,
                notify=notify,
                now=now,
                correlation_id=new_correlation_id(),
            )
    except InfraError as exc:
        err.write(f"ERROR: {exc}\n")
        _report_deadman(env, ok=False, body=str(exc), stream=stream)
        return 3
    _write_report(report, stream)
    _report_deadman(
        env,
        ok=True,
        body=(
            f"{report.day.isoformat()}: {len(report.checked)} checked, "
            f"{len(report.unpublished)} not published, {len(report.mismatches)} mismatches"
        ),
        stream=stream,
    )
    return 1 if report.mismatches else 0
