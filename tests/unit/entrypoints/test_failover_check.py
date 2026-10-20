"""Unit tests for ``hermes failover-check`` (#31).

Invariant under test: the command asks both venues for the last N closed bars
of every configured pair, and exits 0 only when the backup venue answers with
every bar and every close is within the tolerance (1 otherwise, 2 for usage,
3 for config). Both venues are faked; the seam to the real Binance and Kraken
endpoints is covered by ``tests/contract/test_kraken_public_klines.py`` and the
operator's monthly drill run (docs/runbook/failover-to-kraken.md).
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from hermes.domain.errors import KrakenPublicError
from hermes.domain.market_data.kline import Kline, KlineInterval
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity
from hermes.entrypoints import cli
from hermes.entrypoints import failover_check as module
from tests.support.paths import REPO_ROOT

_PROJECT_CONFIG = REPO_ROOT / "config"
_NOW = datetime(2026, 10, 4, 2, 40, tzinfo=UTC)
_UNTIL = datetime(2026, 10, 4, tzinfo=UTC)
_H4 = timedelta(hours=4)

_Call = tuple[Pair, KlineInterval, datetime, datetime]


def _kline(pair: Pair, interval: KlineInterval, open_time: datetime, close: Decimal) -> Kline:
    price = Price(amount=close, pair=pair)
    return Kline(
        pair=pair,
        interval=interval,
        open_time=open_time,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Quantity(amount=Decimal(1), asset=pair.base),
        close_time=open_time + _H4,
    )


def _venue(
    calls: list[_Call], *, scale: Decimal = Decimal(1), drop_last: bool = False
) -> Callable[[Pair, KlineInterval, datetime, datetime], Iterable[Kline]]:
    """Return a venue with one bar per interval step at a close of 100 * ``scale``."""

    def fetch(
        pair: Pair, interval: KlineInterval, since: datetime, until: datetime
    ) -> Iterable[Kline]:
        calls.append((pair, interval, since, until))
        times = []
        t = since
        while t < until:
            times.append(t)
            t += _H4
        if drop_last:
            times = times[:-1]
        return [_kline(pair, interval, t, Decimal(100) * scale) for t in times]

    return fetch


def _failing(
    pair: Pair, interval: KlineInterval, since: datetime, until: datetime
) -> Iterable[Kline]:
    raise KrakenPublicError("EQuery:Unknown asset pair")


def _main(argv: list[str], venues: module.Venues) -> tuple[int, str]:
    out = io.StringIO()
    code = module.main(
        [*argv, "--config-dir", str(_PROJECT_CONFIG)], stream=out, venues=venues, now=_NOW
    )
    return code, out.getvalue()


def test_matching_venues_are_ready_and_cover_every_pair() -> None:
    """Equal closes on both venues exit 0, and both venues are asked for each traded pair."""
    primary_calls: list[_Call] = []
    backup_calls: list[_Call] = []
    code, out = _main([], module.Venues(_venue(primary_calls), _venue(backup_calls)))
    assert (code, out.splitlines()[-1]) == (0, "verdict: READY")
    assert {str(c[0]) for c in backup_calls} == {"BTC/USDT", "ETH/USDT", "SOL/USDT"}
    assert primary_calls == backup_calls


def test_window_is_the_last_closed_bars() -> None:
    """At 02:40Z the open 00:00Z bar is excluded and 6 bars end at 00:00Z."""
    calls: list[_Call] = []
    _main([], module.Venues(_venue(calls), _venue([])))
    assert {(c[2], c[3]) for c in calls} == {(_UNTIL - 6 * _H4, _UNTIL)}


def test_divergent_backup_is_not_ready() -> None:
    """A backup 1 percent off fails a 50 bps tolerance and exits 1."""
    code, out = _main([], module.Venues(_venue([]), _venue([], scale=Decimal("1.01"))))
    assert code == 1
    assert out.count(" diverged ") == 3


def test_tolerance_is_configurable() -> None:
    """The same 1 percent gap passes when the operator allows 150 bps."""
    venues = module.Venues(_venue([]), _venue([], scale=Decimal("1.01")))
    assert _main(["--tolerance-bps", "150"], venues)[0] == 0


def test_backup_missing_a_bar_is_not_ready() -> None:
    """A gap on the backup venue fails the check."""
    code, out = _main([], module.Venues(_venue([]), _venue([], drop_last=True)))
    assert code == 1
    assert "1 missing on backup" in out


def test_unreachable_backup_is_not_ready_and_names_the_venue() -> None:
    """A Kraken error fails the pair with the venue and the message, not a crash."""
    code, out = _main([], module.Venues(_venue([]), _failing))
    assert code == 1
    assert "kraken: Kraken public error: EQuery:Unknown asset pair" in out


def test_json_report_lists_each_pair() -> None:
    """``--json`` prints a machine-readable verdict for the drill script."""
    code, out = _main(["--json"], module.Venues(_venue([]), _failing))
    report = json.loads(out)
    assert (code, report["ready"]) == (1, False)
    assert [p["status"] for p in report["pairs"]] == ["unreachable"] * 3


def test_json_report_carries_the_divergence() -> None:
    """A passing pair reports bars compared and its largest divergence."""
    venues = module.Venues(_venue([]), _venue([], scale=Decimal("1.001")))
    report = json.loads(_main(["--json", "--bars", "2"], venues)[1])
    assert report["ready"] is True
    assert (report["pairs"][0]["compared"], report["pairs"][0]["max_divergence_bps"]) == (
        2,
        "10.000",
    )


@pytest.mark.parametrize(
    "argv",
    [["--bars", "0"], ["--bars", "501"], ["--tolerance-bps", "x"], ["--tolerance-bps", "-1"]],
)
def test_bad_arguments_exit_2(argv: list[str]) -> None:
    """Out-of-range or unparseable options are a usage error."""
    assert _main(argv, module.Venues(_venue([]), _venue([])))[0] == 2


def test_unknown_option_exits_2() -> None:
    """An option the command does not know is a usage error."""
    assert _main(["--venue", "bybit"], module.Venues(_venue([]), _venue([])))[0] == 2


def test_help_exits_0() -> None:
    """``--help`` prints usage and checks nothing."""
    calls: list[_Call] = []
    code, out = _main(["--help"], module.Venues(_venue(calls), _venue([])))
    assert (code, calls) == (0, [])
    assert out.startswith("Usage: hermes failover-check")


def test_bad_config_exits_3(tmp_path: Path) -> None:
    """A config directory that fails validation is a config failure."""
    (tmp_path / "market_data.yaml").write_text("pairs: not-a-list\n", encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    code = module.main(
        ["--config-dir", str(tmp_path)],
        stream=out,
        venues=module.Venues(_venue([]), _venue([])),
        now=_NOW,
        err=err,
    )
    assert (code, out.getvalue()) == (3, "")
    assert err.getvalue().startswith("ERROR: config load failed:")


def test_daily_interval_uses_day_boundaries() -> None:
    """``--interval 1d`` asks for whole UTC days ending at today's midnight."""
    calls: list[_Call] = []
    _main(["--interval", "1d", "--bars", "2"], module.Venues(_venue(calls), _venue([])))
    assert {(c[1], c[2], c[3]) for c in calls} == {
        (KlineInterval.D1, _UNTIL - timedelta(days=2), _UNTIL)
    }


def test_command_is_registered() -> None:
    """``hermes failover-check`` is reachable from the CLI dispatcher."""
    out = io.StringIO()
    assert cli.main(["failover-check", "--help"], stream=out) == 0
    assert "failover-to-kraken.md" in out.getvalue()
