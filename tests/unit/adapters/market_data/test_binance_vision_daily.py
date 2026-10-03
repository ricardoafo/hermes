"""Contract test for the Binance Vision daily klines archive adapter (#359).

Uses a recorded archive fixture (``tests/fixtures/binance_vision``: the six
4h BTCUSDT rows of 2025-08-01 from the public archive) served through
``httpx.MockTransport``, so no network is touched.

Invariants under test:

- A published day parses into the archived bars, microsecond timestamps
  included.
- A 404 means "not published yet" and returns ``None``.
- A checksum mismatch, a missing checksum or an HTTP error fails loudly.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from hermes.adapters.market_data.binance_vision_daily import (
    DAILY_KLINES_BASE_URL,
    BinanceVisionDailyArchive,
)
from hermes.domain.errors import InfraError
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.ports.archive_bars import ArchiveBarsPort

_FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "binance_vision"
_ZIP = (_FIXTURES / "BTCUSDT-4h-2025-08-01.zip").read_bytes()
_CHECKSUM = (_FIXTURES / "BTCUSDT-4h-2025-08-01.zip.CHECKSUM").read_bytes()
_URL = f"{DAILY_KLINES_BASE_URL}/BTCUSDT/4h/BTCUSDT-4h-2025-08-01.zip"
_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_DAY = date(2025, 8, 1)


def _archive(files: dict[str, bytes], status: int | None = None) -> BinanceVisionDailyArchive:
    def handler(request: httpx.Request) -> httpx.Response:
        if status is not None:
            return httpx.Response(status)
        body = files.get(str(request.url))
        return httpx.Response(404) if body is None else httpx.Response(200, content=body)

    return BinanceVisionDailyArchive(httpx.Client(transport=httpx.MockTransport(handler)))


def test_adapter_satisfies_the_port() -> None:
    """The adapter can be injected wherever ArchiveBarsPort is expected."""
    assert isinstance(_archive({}), ArchiveBarsPort)


def test_published_day_parses_the_recorded_bars() -> None:
    """The recorded day yields its six 4h bars with exact values and UTC times."""
    bars = _archive({_URL: _ZIP, _URL + ".CHECKSUM": _CHECKSUM}).daily_bars(
        _BTC, KlineInterval.H4, _DAY
    )
    assert bars is not None
    assert len(bars) == 6
    assert bars[0].open_time == datetime(2025, 8, 1, tzinfo=UTC)
    assert bars[0].close == Decimal("115648.03000000")
    assert bars[-1].open_time == datetime(2025, 8, 1, 20, tzinfo=UTC)


def test_unpublished_day_returns_none_without_fetching_the_checksum() -> None:
    """A 404 is 'not published yet': ``None``, not an error, and no checksum request."""
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(404)

    archive = BinanceVisionDailyArchive(httpx.Client(transport=httpx.MockTransport(handler)))
    result = archive.daily_bars(_BTC, KlineInterval.H4, _DAY)
    assert (result, requested) == (None, [_URL])


@pytest.mark.parametrize(
    "files",
    [
        pytest.param({_URL: _ZIP, _URL + ".CHECKSUM": b"0" * 64 + b"  x.zip\n"}, id="bad-checksum"),
        pytest.param({_URL: _ZIP}, id="missing-checksum"),
        pytest.param({_URL: b"not a zip", _URL + ".CHECKSUM": _CHECKSUM}, id="tampered-zip"),
    ],
)
def test_unverifiable_file_fails_loudly(files: dict[str, bytes]) -> None:
    """A file that does not match its published checksum is never compared."""
    with pytest.raises(InfraError):
        _archive(files).daily_bars(_BTC, KlineInterval.H4, _DAY)


def test_server_error_fails_loudly() -> None:
    """A 5xx is a failure of the check, not a quiet day."""
    with pytest.raises(InfraError, match="HTTP 503"):
        _archive({}, status=503).daily_bars(_BTC, KlineInterval.H4, _DAY)


def test_transport_error_fails_loudly() -> None:
    """A network error is reported as InfraError."""

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    handler: Callable[[httpx.Request], httpx.Response] = boom
    archive = BinanceVisionDailyArchive(httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(InfraError, match="failed"):
        archive.daily_bars(_BTC, KlineInterval.H4, _DAY)
