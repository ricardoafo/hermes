"""Tests for the rule checks the read models serve to the Signals screen (#714).

Invariants:

- the ledger read model reads the checks of its activity window from the
  journal, and says "not available" when no journal can be read;
- a demo scenario's only signal is the bar its held 4h coin opened on, and a
  held coin has no entry check after it, as serve logs none while it holds.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hermes.adapters.console import FixtureReadModel, available_scenarios
from hermes.adapters.console.ledger_read_model import LedgerReadModel
from hermes.application.console import ViewNotAvailableError
from hermes.application.console.logs import LogLine
from tests.unit.adapters.console._ledger_builder import one_btc_position

_NOW = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)


def _bar(at: datetime) -> LogLine:
    return LogLine(
        at=at,
        event="serve.bar.processed",
        fields=(
            ("pair", "BTC/USDT"),
            ("interval", "4h"),
            ("close_time", at.isoformat()),
            ("entry_breakout", "no"),
            ("entry_momentum", "yes"),
            ("entry_regime", "yes"),
        ),
    )


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    """Return a ledger holding one BTC position."""
    path = tmp_path / "hermes.sqlite3"
    one_btc_position(path)
    return path


def test_the_ledger_reads_the_windows_checks_from_the_journal(ledger: Path) -> None:
    """Checks inside the activity window are served; older ones are not."""
    inside, outside = _NOW - timedelta(hours=4), _NOW - timedelta(days=8)
    model = LedgerReadModel.open(
        ledger, clock=lambda: _NOW, journal=lambda _window: [_bar(outside), _bar(inside)]
    )

    assert [(c.coin, c.at, c.met) for c in model.rule_checks()] == [("BTC", inside, 2)]


@pytest.mark.parametrize("journal", [None, lambda _window: None], ids=["no-source", "unreadable"])
def test_without_a_journal_rule_checks_are_not_available(
    ledger: Path, journal: Callable[[timedelta], list[LogLine] | None] | None
) -> None:
    """No readable journal means "not available yet", never "nothing was checked"."""
    model = LedgerReadModel.open(ledger, clock=lambda: _NOW, journal=journal)

    with pytest.raises(ViewNotAvailableError, match="journal is not readable"):
        model.rule_checks()


@pytest.mark.parametrize("scenario", available_scenarios())
def test_a_scenarios_only_signals_are_its_entry_bars(scenario: str) -> None:
    """Every demo signal is the opening bar of a held 4h coin, and nothing follows it."""
    model = FixtureReadModel.load(scenario)
    opened = {row.coin: row.opened_at for row in model.positions() if row.timeframe == "4h"}
    checks = model.rule_checks()

    assert {(c.coin, c.at) for c in checks if c.signal} == set(opened.items())
    assert [c for c in checks if c.coin in opened and c.at > opened[c.coin]] == []
