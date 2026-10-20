"""Binance Vision daily klines archive — reference bars for the archive check.

Downloads ``<base>/<SYMBOL>/<interval>/<SYMBOL>-<interval>-<YYYY-MM-DD>.zip``
and its ``.CHECKSUM`` companion, verifies the SHA-256, and parses the rows
with the same parser as the monthly archive adapter. A 404 means the day is
not published yet (Binance posts daily files some hours after midnight UTC).

Read-only, public data, no credentials.

Reference: GitHub issue #359, scripts/data/ingest_binance_vision.py (checksum
convention).
"""

import hashlib
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx

from hermes.adapters.market_data.binance_historical import _read_csv_from_zip, _row_to_bar
from hermes.domain.errors import InfraError
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair

__all__ = ["DAILY_KLINES_BASE_URL", "BinanceVisionDailyArchive"]

DAILY_KLINES_BASE_URL = "https://data.binance.vision/data/spot/daily/klines"
_HTTP_NOT_FOUND = 404


@dataclass(frozen=True, slots=True)
class BinanceVisionDailyArchive:
    """Fetch and parse one day of archived klines.

    Args:
        client: HTTP client; the caller owns and closes it.
        base_url: Daily klines root, overridable for tests.
    """

    client: httpx.Client
    base_url: str = DAILY_KLINES_BASE_URL

    def daily_bars(self, pair: Pair, interval: KlineInterval, day: date) -> tuple[Bar, ...] | None:
        """Return the archived bars of ``pair`` and ``interval`` on ``day``.

        Returns:
            The bars, or ``None`` when the day's file is not published yet.

        Raises:
            InfraError: On a transport error, an unexpected status, a checksum
                mismatch or an unparseable file.
        """
        symbol = f"{pair.base}{pair.quote}"
        name = f"{symbol}-{interval.value}-{day.isoformat()}.zip"
        url = f"{self.base_url}/{symbol}/{interval.value}/{name}"
        payload = self._get(url)
        if payload is None:
            return None
        checksum = self._get(url + ".CHECKSUM")
        if checksum is None:
            raise InfraError(f"BinanceVisionDailyArchive: {name} has no .CHECKSUM file")
        expected = checksum.decode("ascii", errors="replace").split()[0].lower()
        actual = hashlib.sha256(payload).hexdigest()
        if actual != expected:
            raise InfraError(
                f"BinanceVisionDailyArchive: {name} sha256 {actual} != published {expected}"
            )
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / name
            zip_path.write_bytes(payload)
            rows = _read_csv_from_zip(zip_path)
            return tuple(
                _row_to_bar(row=row, pair=pair, interval=interval, source=zip_path) for row in rows
            )

    def _get(self, url: str) -> bytes | None:
        try:
            response = self.client.get(url)
        except httpx.HTTPError as exc:
            raise InfraError(f"BinanceVisionDailyArchive: GET {url} failed: {exc}") from exc
        if response.status_code == _HTTP_NOT_FOUND:
            return None
        if response.is_error:
            raise InfraError(
                f"BinanceVisionDailyArchive: GET {url} returned HTTP {response.status_code}"
            )
        return response.content
