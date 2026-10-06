"""Frankfurter FX-rate adapter — free public wrapper over ECB reference rates.

Implements :class:`hermes.ports.fx.FxRateProviderPort` against the public
Frankfurter endpoints. The live host is ``https://api.frankfurter.dev/v1``
(set in ``config/frankfurter.yaml``); the former ``api.frankfurter.app``
answers 301 and the client does not follow redirects. Frankfurter is
a small hobbyist wrapper on top of ECB reference rates; it is free,
keyless, and returns clean JSON — but it is not owned by a large team
and can go down. See the decisions journal entry
``2026-08-23-fx-rate-source-frankfurter.md`` for the fallback plan
(swap to ECB direct — ~30 LOC change).

Design decisions
----------------

- **HTTP client**: raw :class:`httpx.Client` — same pattern as the
  Binance klines adapter.
- **Rate limiter**: token-bucket private to the FX adapter, sized to a
  polite 60 req/min. Frankfurter publishes no cap; polite defaults are
  the right call for a free hobbyist API.
- **Sync interface**: matches :class:`FxRateProviderPort` and the rest
  of the adapter fleet (anyio-on-asyncio policy per ADR-003).
- **Errors**: any wire, HTTP-status, or schema failure raises
  :class:`~hermes.domain.errors.FxRateError` — an
  :class:`~hermes.domain.errors.InfraError` subclass so callers can use
  one ``except InfraError`` if they want to.
- **Float precision**: Frankfurter returns rates as JSON numbers
  (floats). Convert via ``Decimal(str(value))`` so we never carry
  IEEE-754 error into the domain :class:`~decimal.Decimal` layer.
- **Business-day resolution**: Frankfurter returns the closest prior
  business day when the caller asks for a weekend or holiday date. The
  adapter stores whatever date the response reports in the returned
  :class:`~hermes.domain.fx.fx_rate.FxRate` — callers who need
  strict-day semantics MUST verify ``result.date == on`` themselves
  (per the port contract).
- **Wire codes**: Frankfurter only knows ISO currencies. ``wire_codes``
  maps a domain currency to the code sent on the wire and read back from
  ``rates``; :attr:`FrankfurterConfig.USDT_AS_USD` reads USDT as USD, the
  working reading of ADR-004's "ECB EUR/USDT reference rate" (ADR-016
  item 5). The returned :class:`FxRate` keeps the domain currency.

Reference: docs/decisions/2026-08-23-fx-rate-source-frankfurter.md,
ADR-002 §Decision 4 (EUR-first accounting, ECB reference rates).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import ClassVar, Final

import httpx
import structlog

from hermes.adapters.fx.rate_limiter import RateLimitConfig, RateLimiter
from hermes.domain.errors import FxRateError
from hermes.domain.fx.fx_rate import FxRate
from hermes.domain.value_objects.currency import Currency

__all__ = ["FrankfurterAdapter", "FrankfurterConfig"]


# The public host moved: this URL now answers 301 to api.frankfurter.dev/v1,
# which the client does not follow. config/frankfurter.yaml carries the new
# host; this default stays until its unit test is updated (change
# trade-eur-values, Risks).
_DEFAULT_BASE_URL: Final[str] = "https://api.frankfurter.app"
_LATEST_PATH: Final[str] = "/latest"
_DEFAULT_TIMEOUT_SEC: Final[float] = 10.0
_HTTP_ERROR_THRESHOLD: Final[int] = 400
# Response-body excerpt cap when building error messages — keeps the
# exception readable even if Frankfurter dumps an HTML error page on 5xx.
_ERROR_EXCERPT_MAX_CHARS: Final[int] = 200
# Weight-per-request. Frankfurter is not weight-graded, so every call is 1.
_FX_REQUEST_WEIGHT: Final[int] = 1
# Polite default: 60 req/min. Frankfurter has no published cap; this
# leaves plenty of headroom for a tax-report batch (dozens of dates per
# run, once per year) without ever hammering the free API.
_DEFAULT_RATE_LIMIT: Final[RateLimitConfig] = RateLimitConfig(max_weight=60, window_seconds=60)

_log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class FrankfurterConfig:
    """Wire-level configuration for :class:`FrankfurterAdapter`.

    Attributes:
        base_url: HTTPS root of the Frankfurter API. Defaults to the
            public endpoint. Override for a self-hosted instance (the
            Frankfurter project publishes a Docker image).
        rate_limit: Sizing for the internal token-bucket limiter. Default
            is 60 req/min — polite for a free hobbyist API.
        timeout_sec: Per-request timeout in seconds.
        wire_codes: ``(currency, wire code)`` pairs sent instead of the
            currency's own code. Empty by default: every currency is sent
            as itself.
    """

    USDT_AS_USD: ClassVar[tuple[tuple[Currency, str], ...]] = ((Currency.USDT, "USD"),)

    base_url: str = _DEFAULT_BASE_URL
    rate_limit: RateLimitConfig = _DEFAULT_RATE_LIMIT
    timeout_sec: float = _DEFAULT_TIMEOUT_SEC
    wire_codes: tuple[tuple[Currency, str], ...] = ()

    def wire_code(self, currency: Currency) -> str:
        """Return the code Frankfurter sees for ``currency``.

        Args:
            currency: Domain currency.

        Returns:
            The mapped wire code, or the currency's own code.
        """
        return dict(self.wire_codes).get(currency, currency.value)

    def __post_init__(self) -> None:
        """Validate the URL scheme and timeout — cheap fail-fast in the config layer."""
        if not self.base_url.startswith("https://"):
            raise ValueError(
                f"FrankfurterConfig.base_url must start with https://, got {self.base_url!r}"
            )
        if self.timeout_sec <= 0:
            raise ValueError(f"FrankfurterConfig.timeout_sec must be > 0, got {self.timeout_sec}")


@dataclass(slots=True)
class FrankfurterAdapter:
    """Concrete :class:`~hermes.ports.fx.FxRateProviderPort` for Frankfurter.

    Attributes:
        config: Wire settings.
        client: Injected :class:`httpx.Client`. Tests supply a
            :class:`httpx.MockTransport` (or ``respx`` mock) — production
            wiring uses :meth:`FrankfurterAdapter.create`.
        limiter: Token-bucket rate limiter. Adapter owns it; callers do
            not touch it.
    """

    config: FrankfurterConfig
    client: httpx.Client = field()
    limiter: RateLimiter = field()

    @classmethod
    def create(cls, config: FrankfurterConfig | None = None) -> FrankfurterAdapter:
        """Build an adapter with a default :class:`httpx.Client` and limiter.

        Args:
            config: Optional wire config. When ``None`` the public
                Frankfurter defaults are used (safe — no key required).

        Returns:
            A new adapter owning its :class:`httpx.Client` and
            :class:`RateLimiter`.
        """
        cfg = config or FrankfurterConfig()
        client = httpx.Client(base_url=cfg.base_url, timeout=cfg.timeout_sec)
        limiter = RateLimiter(config=cfg.rate_limit)
        return cls(config=cfg, client=client, limiter=limiter)

    def close(self) -> None:
        """Release the underlying :class:`httpx.Client` resources.

        Safe to call multiple times.
        """
        self.client.close()

    def get_latest(self, from_currency: Currency, to_currency: Currency) -> FxRate:
        """Return the most recent official rate for ``from_currency → to_currency``.

        Hits ``GET /latest?from=<from>&to=<to>``. Frankfurter answers with
        the most recent trading day's rate (its ``date`` field).

        Args:
            from_currency: Source currency.
            to_currency: Target currency. Must differ from ``from_currency``.

        Returns:
            An :class:`FxRate` stamped with the date reported by
            Frankfurter (usually the most recent trading day).

        Raises:
            hermes.domain.errors.FxRateError: On HTTP non-2xx, unparseable
                JSON, missing ``rates``/``date`` fields, missing target
                currency in the ``rates`` dict, or an unparseable rate value.
        """
        self._reject_same_currency(from_currency, to_currency)
        payload = self._get_json(
            path=_LATEST_PATH,
            from_currency=from_currency,
            to_currency=to_currency,
        )
        return _parse_response(
            payload=payload,
            from_currency=from_currency,
            to_currency=to_currency,
            rate_key=self.config.wire_code(to_currency),
        )

    def get_for_date(self, from_currency: Currency, to_currency: Currency, on: date) -> FxRate:
        """Return the official rate on ``on`` (or the closest prior business day).

        Hits ``GET /<YYYY-MM-DD>?from=<from>&to=<to>``. Frankfurter's
        default behaviour for weekend/holiday dates is to return the
        closest prior trading day's rate — the returned :class:`FxRate`
        carries whatever date the response reports, NOT the requested
        ``on``. Callers that need strict-day semantics MUST verify
        ``result.date == on`` themselves (per the port contract).

        Args:
            from_currency: Source currency.
            to_currency: Target currency. Must differ from ``from_currency``.
            on: Calendar date to look up.

        Returns:
            An :class:`FxRate` stamped with the date Frankfurter resolved
            the lookup to (may be earlier than ``on`` for non-trading days).

        Raises:
            hermes.domain.errors.FxRateError: On HTTP non-2xx (including
                404 for an out-of-coverage historical date), unparseable
                JSON, missing schema fields, or unparseable rate value.
        """
        self._reject_same_currency(from_currency, to_currency)
        path = f"/{on.isoformat()}"
        payload = self._get_json(
            path=path,
            from_currency=from_currency,
            to_currency=to_currency,
        )
        return _parse_response(
            payload=payload,
            from_currency=from_currency,
            to_currency=to_currency,
            rate_key=self.config.wire_code(to_currency),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _reject_same_currency(from_currency: Currency, to_currency: Currency) -> None:
        """Reject same-currency lookups before any wire call.

        Frankfurter would return a degenerate ``{to: 1.0}`` payload, but
        our :class:`FxRate` domain invariant already forbids self-pairs.
        Failing here is faster and clearer for the caller.

        Raises:
            hermes.domain.errors.FxRateError: When ``from_currency ==
                to_currency`` — same-currency FX has no meaning.
        """
        if from_currency == to_currency:
            raise FxRateError(
                f"FrankfurterAdapter: from_currency == to_currency == {from_currency} "
                f"— same-currency FX has no meaning"
            )

    def _get_json(
        self,
        *,
        path: str,
        from_currency: Currency,
        to_currency: Currency,
    ) -> dict[str, object]:
        """Fetch and JSON-decode one Frankfurter response.

        Args:
            path: URL path (``/latest`` or ``/<YYYY-MM-DD>``).
            from_currency: Source currency (goes to the ``from`` query param).
            to_currency: Target currency (goes to the ``to`` query param).

        Returns:
            The parsed JSON body as a dict.

        Raises:
            hermes.domain.errors.FxRateError: On transport failure, HTTP
                non-2xx, non-JSON body, or a non-dict JSON root.
        """
        params = {
            "from": self.config.wire_code(from_currency),
            "to": self.config.wire_code(to_currency),
        }
        self.limiter.acquire(_FX_REQUEST_WEIGHT)
        try:
            response = self.client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise FxRateError(f"transport failure calling Frankfurter {path!r}: {exc!r}") from exc

        if response.status_code >= _HTTP_ERROR_THRESHOLD:
            excerpt = response.text[:_ERROR_EXCERPT_MAX_CHARS]
            raise FxRateError(f"HTTP {response.status_code} from Frankfurter {path!r}: {excerpt}")

        try:
            data = response.json()
        except ValueError as exc:
            raise FxRateError(
                f"non-JSON response from Frankfurter {path!r}: "
                f"{response.text[:_ERROR_EXCERPT_MAX_CHARS]}"
            ) from exc

        if not isinstance(data, dict):
            raise FxRateError(
                f"Frankfurter {path!r} returned non-object body: "
                f"{str(data)[:_ERROR_EXCERPT_MAX_CHARS]}"
            )
        return data


def _parse_response(
    *,
    payload: dict[str, object],
    from_currency: Currency,
    to_currency: Currency,
    rate_key: str,
) -> FxRate:
    """Convert a Frankfurter JSON payload into a domain :class:`FxRate`.

    Frankfurter's ``/latest`` and ``/<date>`` endpoints share one response
    shape::

        {"base": "USD", "date": "2026-08-23", "rates": {"EUR": 0.92}}

    We extract ``date`` and ``rates[<to_currency>]`` and rebuild the
    :class:`FxRate` in the caller-supplied currency direction (the
    ``base`` field mirrors ``from_currency`` and is not re-read).

    Args:
        payload: Decoded JSON body.
        from_currency: Currency requested in the ``from`` query param.
        to_currency: Domain currency stamped on the returned rate.
        rate_key: Wire code of ``to_currency``; must be present in
            ``payload["rates"]`` or the parse raises.

    Returns:
        A validated :class:`FxRate`.

    Raises:
        hermes.domain.errors.FxRateError: On missing ``date`` / ``rates``
            keys, wrong ``rates`` type, missing target currency, or an
            unparseable-as-Decimal rate value.
    """
    date_raw = payload.get("date")
    if not isinstance(date_raw, str):
        raise FxRateError(f"Frankfurter response missing or non-string 'date': {payload!r}")
    try:
        rate_date = date.fromisoformat(date_raw)
    except ValueError as exc:
        raise FxRateError(f"Frankfurter 'date' is not ISO 8601: {date_raw!r}") from exc

    rates_raw = payload.get("rates")
    if not isinstance(rates_raw, dict):
        raise FxRateError(f"Frankfurter response missing or non-object 'rates': {payload!r}")

    if rate_key not in rates_raw:
        raise FxRateError(
            f"Frankfurter response has no rate for {rate_key!r}; "
            f"available: {sorted(rates_raw.keys())}"
        )

    rate_value = rates_raw[rate_key]
    if not isinstance(rate_value, (int, float, str)):
        raise FxRateError(f"Frankfurter rate for {rate_key!r} is not numeric: {rate_value!r}")
    # Decimal(str(x)) — never Decimal(x) — so IEEE-754 float error does
    # not leak into the domain Decimal layer. See code-conventions §6.
    try:
        rate_decimal = Decimal(str(rate_value))
    except InvalidOperation as exc:
        raise FxRateError(
            f"Frankfurter rate for {rate_key!r} not parseable as Decimal: {rate_value!r}"
        ) from exc
    if not rate_decimal.is_finite():
        raise FxRateError(
            f"Frankfurter rate for {rate_key!r} is not a finite number: {rate_value!r}"
        )

    try:
        return FxRate(
            from_currency=from_currency,
            to_currency=to_currency,
            rate=rate_decimal,
            date=rate_date,
        )
    except ValueError as exc:
        # Domain invariant (rate > 0, from != to) failed. Surface as
        # FxRateError so callers can attribute it to the wire, not to a
        # bug in the domain model.
        raise FxRateError(f"Frankfurter response fails FxRate invariants: {exc}") from exc
