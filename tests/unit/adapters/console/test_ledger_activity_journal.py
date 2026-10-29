"""Restarts, deploys, warnings, errors and the 4h check line in the activity feed (#653).

Ledgers come from the real ledger API; journal lines are fixture
``journalctl -o json`` envelopes parsed by the real reader.

Invariants:

- a restart row names the release, and reads ``deployed`` when it changed;
- warnings and errors show the event, coin and error class only, never a
  price, size or balance logged beside them, on every surface;
- one 4h candle is one quiet line, ``checked N coins`` with ``no signal`` or the
  signal, and the line folds away unless quiet rows are asked for;
- entry conditions appear as yes counts only, never prices or distances;
- an unreadable journal leaves the journal rows out and the rest unchanged.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from hermes.adapters.console.ledger_read_model import LedgerReadModel
from hermes.application.console import Surface
from hermes.application.console.activity import activity_to_json
from hermes.application.console.logs import LogLine, parse_journal_entry
from hermes.application.console.render import RenderContext, render_activity, to_plain_text
from hermes.application.console.views import ActivityKind
from hermes.domain.strategy.signal import SignalKind
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from tests.unit.adapters.console._ledger_builder import BTC, ETH, H4, T0, LedgerBuilder

_SOL = Pair(base=Currency.SOL, quote=Currency.USDT)
_BOUNDARY = T0 + H4
_READ_AT = T0 + timedelta(hours=9)


def _line(at: datetime, event: str, level: str = "info", **fields: object) -> LogLine:
    """Parse one fixture journal envelope the way ``hermes logs`` does."""
    payload = {"event": event, "level": level, **fields}
    envelope = {
        "__REALTIME_TIMESTAMP": str(int(at.timestamp() * 1_000_000)),
        "MESSAGE": json.dumps(payload),
    }
    line = parse_journal_entry(json.dumps(envelope))
    assert line is not None
    return line


def _bar_line(coin: str, held: tuple[str, str, str]) -> LogLine:
    close = (_BOUNDARY - timedelta(milliseconds=1)).isoformat()
    return _line(
        _BOUNDARY + timedelta(seconds=3),
        "serve.bar.processed",
        pair=f"{coin}/USDT",
        interval="4h",
        close_time=close,
        signal="none",
        entry_breakout=held[0],
        entry_momentum=held[1],
        entry_regime=held[2],
    )


def _model(path: Path, lines: list[LogLine] | None) -> LedgerReadModel:
    return LedgerReadModel.open(path, clock=lambda: _READ_AT, journal=lambda _window: lines)


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    """Three coins checked on one 4h candle, no signal, no fills."""
    path = tmp_path / "hermes.sqlite3"
    b = LedgerBuilder(path)
    for pair in (BTC, ETH, _SOL):
        b.check(_BOUNDARY, pair=pair)
    b.close()
    return path


def _texts(model: LedgerReadModel, *, quiet: bool = False) -> list[tuple[ActivityKind, str | None]]:
    return [(i.kind, i.text) for d in model.activity(include_quiet=quiet) for i in d.items]


def test_restart_row_names_the_release_and_a_changed_release_reads_deployed(
    ledger: Path,
) -> None:
    """INV: a start names its release; a later start on another release is a deploy."""
    lines = [
        _line(T0 + timedelta(hours=1), "serve.run.started", env="prod", pairs=3, release="aaa111"),
        _line(T0 + timedelta(hours=2), "serve.run.started", env="prod", pairs=3, release="aaa111"),
        _line(T0 + timedelta(hours=3), "serve.run.started", env="prod", pairs=3, release="bbb222"),
    ]
    assert _texts(_model(ledger, lines)) == [
        (ActivityKind.RESTART, "deployed bbb222 (was aaa111)"),
        (ActivityKind.RESTART, "restarted · release aaa111"),
        (ActivityKind.RESTART, "restarted · release aaa111"),
    ]


def test_restart_without_a_release_field_still_shows(ledger: Path) -> None:
    """INV: an older build that logs no release still gets a plain restart row."""
    lines = [_line(T0 + timedelta(hours=1), "serve.run.started", env="prod")]
    assert _texts(_model(ledger, lines)) == [(ActivityKind.RESTART, "restarted")]


def test_warnings_and_errors_show_event_coin_and_error_only(ledger: Path) -> None:
    """INV: only whitelisted fields reach the feed; info lines never do."""
    lines = [
        _line(T0 + timedelta(hours=1), "serve.fill.executed", price="65000.5", qty="0.1"),
        _line(
            T0 + timedelta(hours=2),
            "serve.book.snapshot_failed",
            "warning",
            pair="BTC/USDT",
            error="TimeoutError",
            price="65000.5",
        ),
        _line(T0 + timedelta(hours=3), "serve.run.failed", "error", equity="4999.1"),
    ]
    items = [i for d in _model(ledger, lines).activity() for i in d.items]
    assert [(i.kind, i.coin, i.text) for i in items] == [
        (ActivityKind.ERROR, None, "serve.run.failed"),
        (ActivityKind.WARNING, "BTC", "serve.book.snapshot_failed (TimeoutError)"),
    ]


@pytest.mark.parametrize("surface", [Surface.OPERATOR_CLI, Surface.TELEGRAM_PUSHED, Surface.SLACK])
def test_no_money_or_price_reaches_any_surface_from_the_journal(
    ledger: Path, surface: Surface
) -> None:
    """INV: prices, sizes and balances logged beside a warning never render or serialise."""
    lines = [
        _line(
            T0 + timedelta(hours=2),
            "serve.book.snapshot_failed",
            "warning",
            pair="BTC/USDT",
            price="65000.5",
            equity="4999.1",
            entry_distance="123.4",
        ),
        _bar_line("BTC", ("yes", "no", "yes")),
    ]
    ctx = RenderContext(surface=surface, now=_READ_AT)
    days = _model(ledger, lines).activity(include_quiet=True)
    blob = json.dumps(activity_to_json(days, ctx)) + to_plain_text(
        render_activity(days, ctx), width=100
    )
    assert "serve.book.snapshot_failed" in blob
    assert not any(secret in blob for secret in ("65000", "4999", "123.4", "€"))


def test_one_candle_is_one_quiet_line_with_no_signal(ledger: Path) -> None:
    """INV: three per-coin bar checks fold into 'checked 3 coins · no signal'."""
    assert _texts(_model(ledger, None), quiet=True) == [
        (ActivityKind.BAR, "checked 3 coins · no signal")
    ]


def test_the_quiet_line_folds_away_by_default(ledger: Path) -> None:
    """INV: without quiet rows requested the feed carries no check line at all."""
    assert _texts(_model(ledger, None)) == []


def test_a_signal_replaces_no_signal_on_the_check_line(tmp_path: Path) -> None:
    """INV: the signal raised on the candle is named on its line, not a second row."""
    path = tmp_path / "hermes.sqlite3"
    b = LedgerBuilder(path)
    b.check(_BOUNDARY, pair=BTC, signal=SignalKind.ENTRY)
    b.check(_BOUNDARY, pair=ETH)
    b.close()
    assert _texts(_model(path, None), quiet=True) == [
        (ActivityKind.OPENED, None),
        (ActivityKind.BAR, "checked 2 coins · entry long BTC"),
    ]


def test_journal_entry_conditions_add_yes_counts_only(ledger: Path) -> None:
    """INV: the check line gains 'breakout 1/3' style counts from the journal, never values."""
    lines = [
        _bar_line("BTC", ("yes", "yes", "yes")),
        _bar_line("ETH", ("no", "yes", "yes")),
        _bar_line("SOL", ("no", "no", "yes")),
    ]
    assert _texts(_model(ledger, lines), quiet=True) == [
        (
            ActivityKind.BAR,
            "checked 3 coins · no signal · breakout 1/3 · momentum 2/3 · regime 3/3",
        )
    ]


def test_an_unreadable_journal_omits_the_journal_rows_only(tmp_path: Path) -> None:
    """INV: with no journal the feed still shows the check line, and an empty one adds nothing."""
    path = tmp_path / "hermes.sqlite3"
    b = LedgerBuilder(path)
    b.check(_BOUNDARY, pair=BTC)
    b.close()
    assert _texts(_model(path, None), quiet=True) == [
        (ActivityKind.BAR, "checked 1 coin · no signal")
    ]
    assert _texts(_model(path, [])) == []
