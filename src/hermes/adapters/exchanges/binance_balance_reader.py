"""Read-only Binance spot balances for ``hermes reconcile`` (#29).

Implements :class:`~hermes.ports.reconciliation.BalanceReadPort` with one
signed request, ``GET /api/v3/account``. There is no order, cancel or other
write path in this module: it is meant for a dedicated API key that has the
"reading" permission only, kept apart from any trading key.

Safety (ADR-003 §00 I-08, AGENTS.md rules 6 and 7):

- The key and secret come from ``HERMES_BINANCE_READ__API_KEY`` and
  ``HERMES_BINANCE_READ__API_SECRET`` and nothing else. A missing or blank
  credential is refused; there is no fallback to the trading key or the
  testnet.
- Only HTTPS to one of Binance's documented spot hosts, or the spot testnet,
  receives the key and the signed query.
- The secret is excluded from ``repr`` and never appears in an error message,
  and neither does the signature.

Reference: openspec/changes/29-binance-balance-read/change.md.
"""

import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final, Self
from urllib.parse import urlencode, urlsplit

import httpx

from hermes.adapters.exchanges.binance_spot import (
    MAINNET_HOSTS,
    TESTNET_HOST,
    parse_decimal,
    sign,
)
from hermes.domain.errors import ExchangeError, NetworkError
from hermes.domain.value_objects.currency import Currency

__all__ = [
    "API_KEY_ENV",
    "API_SECRET_ENV",
    "MAINNET_BASE_URL",
    "BinanceBalanceReader",
    "BinanceBalanceReaderConfig",
]

API_KEY_ENV: Final = "HERMES_BINANCE_READ__API_KEY"
API_SECRET_ENV: Final = "HERMES_BINANCE_READ__API_SECRET"  # noqa: S105 — a variable name
MAINNET_BASE_URL: Final = "https://api.binance.com"
_ACCOUNT_PATH: Final = "/api/v3/account"
_API_KEY_HEADER: Final = "X-MBX-APIKEY"
_HTTP_ERROR_MIN: Final = 400
_ERROR_EXCERPT: Final = 200


@dataclass(frozen=True, slots=True)
class BinanceBalanceReaderConfig:
    """Wire settings and credentials for :class:`BinanceBalanceReader`.

    Attributes:
        api_key: Read-only Binance API key, sent as a header.
        api_secret: Its secret, used only to sign. Not in ``repr``.
        base_url: REST root: a documented Binance spot host or the testnet.
        recv_window_ms: Server-side tolerance for clock skew.
        timeout_sec: Per-request timeout.
    """

    api_key: str
    api_secret: str = field(repr=False)
    base_url: str = MAINNET_BASE_URL
    recv_window_ms: int = 5000
    timeout_sec: float = 10.0

    def __post_init__(self) -> None:
        """Refuse a non-HTTPS URL, an undocumented host, or a blank credential.

        Raises:
            ValueError: On any of those.
        """
        parts = urlsplit(self.base_url)
        if parts.scheme != "https":
            raise ValueError(f"base_url must be https, got {self.base_url!r}")
        if parts.hostname != TESTNET_HOST and parts.hostname not in MAINNET_HOSTS:
            raise ValueError(f"{parts.hostname} is not a documented Binance spot host")
        if not self.api_key.strip():
            raise ValueError(f"the read-only API key is blank ({API_KEY_ENV})")
        if not self.api_secret.strip():
            raise ValueError(f"the read-only API secret is blank ({API_SECRET_ENV})")

    @classmethod
    def from_env(cls, env: Mapping[str, str], *, base_url: str = MAINNET_BASE_URL) -> Self:
        """Build from ``HERMES_BINANCE_READ__API_KEY`` and ``..._API_SECRET``.

        Args:
            env: Environment snapshot.
            base_url: REST root, mainnet unless a test names the testnet.

        Raises:
            ValueError: A variable is missing or blank; the message names it
                and never shows a value.
        """
        key = env.get(API_KEY_ENV, "").strip()
        secret = env.get(API_SECRET_ENV, "").strip()
        missing = [
            name for name, value in ((API_KEY_ENV, key), (API_SECRET_ENV, secret)) if not value
        ]
        if missing:
            raise ValueError(f"missing or blank: {', '.join(missing)}")
        return cls(api_key=key, api_secret=secret, base_url=base_url)


@dataclass(slots=True)
class BinanceBalanceReader:
    """Current spot balances of one Binance account, read-only.

    Attributes:
        config: Wire settings and credentials.
        client: HTTP client bound to ``config.base_url``.
        clock_ms: Source of the request timestamp in epoch milliseconds.
    """

    config: BinanceBalanceReaderConfig
    client: httpx.Client
    clock_ms: Callable[[], int] = field(default=lambda: int(time.time() * 1000))

    @classmethod
    def create(cls, config: BinanceBalanceReaderConfig) -> Self:
        """Build a reader with its own HTTP client."""
        return cls(
            config=config,
            client=httpx.Client(base_url=config.base_url, timeout=config.timeout_sec),
        )

    def close(self) -> None:
        """Release the HTTP client."""
        self.client.close()

    def balances(self, assets: Iterable[Currency]) -> Mapping[Currency, Decimal]:
        """Return free plus locked for each asset; an asset not held reads zero.

        Args:
            assets: The assets to read.

        Raises:
            ExchangeError: Binance refused the request or sent a malformed reply.
            NetworkError: Transport failure.
        """
        wanted = set(assets)
        totals = dict.fromkeys(wanted, Decimal("0"))
        rows = self._account().get("balances")
        if not isinstance(rows, list):
            raise ExchangeError(msg="account reply has no balances list")
        by_code = {currency.value: currency for currency in wanted}
        for row in rows:
            asset = row.get("asset") if isinstance(row, dict) else None
            if not isinstance(row, dict) or not isinstance(asset, str):
                raise ExchangeError(msg="malformed balance row in the account reply")
            currency = by_code.get(asset)
            if currency is None:
                continue
            free = parse_decimal(row.get("free"), f"{currency.value} free")
            locked = parse_decimal(row.get("locked"), f"{currency.value} locked")
            totals[currency] += free + locked
        return totals

    def _account(self) -> dict[str, object]:
        query = urlencode(
            {"recvWindow": str(self.config.recv_window_ms), "timestamp": str(self.clock_ms())}
        )
        signature = sign(query, self.config.api_secret)
        try:
            response = self.client.get(
                f"{_ACCOUNT_PATH}?{query}&signature={signature}",
                headers={_API_KEY_HEADER: self.config.api_key},
            )
        except httpx.HTTPError as exc:
            # The exception text may carry the signed URL; keep only its kind.
            raise NetworkError(
                msg=f"transport failure on {_ACCOUNT_PATH}: {type(exc).__name__}"
            ) from None
        try:
            body: object = response.json()
        except ValueError:
            raise ExchangeError(
                msg=f"non-JSON reply on {_ACCOUNT_PATH} (HTTP {response.status_code})"
            ) from None
        if response.status_code >= _HTTP_ERROR_MIN:
            code = body.get("code") if isinstance(body, dict) else None
            text = body.get("msg") if isinstance(body, dict) else None
            raise ExchangeError(
                msg=f"Binance refused {_ACCOUNT_PATH} (HTTP {response.status_code}, "
                f"code {code}): {str(text)[:_ERROR_EXCERPT]}"
            )
        if not isinstance(body, dict):
            raise ExchangeError(msg=f"expected a JSON object from {_ACCOUNT_PATH}")
        return body
