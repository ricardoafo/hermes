"""Contract tests for :class:`FrankfurterAdapter`.

Every wire call is mocked via ``respx`` — no real socket ever opens, no
real Frankfurter endpoint is contacted. Tests are microsecond-fast.

Every test protects one invariant of the FX provider contract:

- Wire URL/path/params match Frankfurter's ``/latest`` and ``/<date>`` shape
- Response parsing produces an :class:`FxRate` with Decimal-precise rate
- Happy-path :meth:`FxRate.convert` on the returned rate produces the
  expected :class:`Money` (integration between adapter and domain)
- HTTP 404 → :class:`FxRateError` (out-of-coverage historical date)
- HTTP 429 → :class:`FxRateError` (no silent retry)
- Malformed / non-JSON body → :class:`FxRateError`
- Response missing the target currency → :class:`FxRateError`
- Weekend date returns closest-prior business day (documented behaviour)
- Port conformance: adapter satisfies ``FxRateProviderPort``
- create() factory + close() idempotency

Reference: docs/guides/ai-authored-tests.md,
docs/decisions/2026-08-23-fx-rate-source-frankfurter.md.
"""

from datetime import date
from decimal import Decimal

import httpx
import pytest
import respx

from hermes.adapters._shared.rate_limiter import RateLimitConfig, RateLimiter
from hermes.adapters.fx.frankfurter import FrankfurterAdapter, FrankfurterConfig
from hermes.domain.errors import FxRateError, InfraError
from hermes.domain.fx.fx_rate import FxRate
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.infrastructure.config.models import FrankfurterConfig as FrankfurterSettings
from hermes.ports.fx import FxRateProviderPort

_BASE_URL = "https://api.frankfurter.dev/v1"
_LATEST_URL = f"{_BASE_URL}/latest"


def _make_adapter(
    *,
    max_weight: int = 60,
    window_seconds: int = 60,
) -> FrankfurterAdapter:
    """Build an adapter with a plain httpx.Client (respx patches transport globally)."""
    config = FrankfurterConfig(
        rate_limit=RateLimitConfig(max_weight=max_weight, window_seconds=window_seconds)
    )
    client = httpx.Client(base_url=_BASE_URL, timeout=5.0)
    limiter = RateLimiter(config=config.rate_limit)
    return FrankfurterAdapter(config=config, client=client, limiter=limiter)


class TestConfigValidation:
    """Wire-config invalid values fail at construction, not at first fetch."""

    def test_non_https_base_url_rejected(self) -> None:
        """A non-https base_url raises — no plaintext calls to a public API."""
        with pytest.raises(ValueError, match="https://"):
            FrankfurterConfig(base_url="http://api.frankfurter.app")

    def test_zero_timeout_rejected(self) -> None:
        """A non-positive timeout raises — would immediately fail every request."""
        with pytest.raises(ValueError, match="timeout_sec"):
            FrankfurterConfig(timeout_sec=0)

    def test_negative_timeout_rejected(self) -> None:
        """A negative timeout raises with the same guard as zero."""
        with pytest.raises(ValueError, match="timeout_sec"):
            FrankfurterConfig(timeout_sec=-1.0)


class TestGetLatestHappyPath:
    """The /latest endpoint returns a well-formed FxRate."""

    def test_get_latest_hits_latest_path_with_from_and_to_params(self) -> None:
        """The request URL is /latest with from=USDT and to=EUR query params."""
        adapter = _make_adapter()
        try:
            with respx.mock(assert_all_called=True) as router:
                route = router.get(_LATEST_URL).mock(
                    return_value=httpx.Response(
                        200,
                        json={
                            "base": "USDT",
                            "date": "2026-08-23",
                            "rates": {"EUR": 0.92},
                        },
                    )
                )
                adapter.get_latest(Currency.USDT, Currency.EUR)
                request = route.calls[0].request
                assert request.url.params["from"] == "USDT"
                assert request.url.params["to"] == "EUR"
        finally:
            adapter.close()

    def test_get_latest_returns_fx_rate_with_decimal_precision(self) -> None:
        """The rate comes back as Decimal without float precision loss."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                router.get(_LATEST_URL).mock(
                    return_value=httpx.Response(
                        200,
                        json={
                            "base": "USDT",
                            "date": "2026-08-23",
                            "rates": {"EUR": 0.923456789},
                        },
                    )
                )
                fx = adapter.get_latest(Currency.USDT, Currency.EUR)
                assert isinstance(fx, FxRate)
                assert fx.rate == Decimal("0.923456789")
                assert fx.from_currency == Currency.USDT
                assert fx.to_currency == Currency.EUR
                assert fx.date == date(2026, 8, 23)
        finally:
            adapter.close()

    def test_returned_fx_rate_converts_money_correctly(self) -> None:
        """The returned FxRate's convert() produces the expected EUR Money — end-to-end."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                router.get(_LATEST_URL).mock(
                    return_value=httpx.Response(
                        200,
                        json={
                            "base": "USDT",
                            "date": "2026-08-23",
                            "rates": {"EUR": 0.92},
                        },
                    )
                )
                fx = adapter.get_latest(Currency.USDT, Currency.EUR)
                result = fx.convert(Money(amount=Decimal("100"), currency=Currency.USDT))
                assert result.currency == Currency.EUR
                assert result.amount == Decimal("92.00")
        finally:
            adapter.close()


class TestGetForDateHappyPath:
    """The /<YYYY-MM-DD> endpoint returns a well-formed FxRate."""

    def test_get_for_date_hits_iso_date_path(self) -> None:
        """The request URL is /YYYY-MM-DD (ISO 8601)."""
        adapter = _make_adapter()
        target = date(2026, 8, 23)
        try:
            with respx.mock(assert_all_called=True) as router:
                route = router.get(f"{_BASE_URL}/2026-08-23").mock(
                    return_value=httpx.Response(
                        200,
                        json={
                            "base": "USDT",
                            "date": "2026-08-23",
                            "rates": {"EUR": 0.92},
                        },
                    )
                )
                fx = adapter.get_for_date(Currency.USDT, Currency.EUR, target)
                assert route.call_count == 1
                assert fx.date == target
                assert fx.rate == Decimal("0.92")
        finally:
            adapter.close()

    def test_get_for_date_weekend_returns_closest_prior_business_day(self) -> None:
        """A Sunday request returns the prior Friday's rate — Frankfurter's default behavior."""
        adapter = _make_adapter()
        sunday = date(2026, 8, 23)  # Sunday
        friday = date(2026, 8, 21)  # Prior Friday
        try:
            with respx.mock() as router:
                # Frankfurter honours the request URL but stamps the response
                # with the trading-day date (Friday) — not the requested Sunday.
                router.get(f"{_BASE_URL}/2026-08-23").mock(
                    return_value=httpx.Response(
                        200,
                        json={
                            "base": "USDT",
                            "date": "2026-08-21",
                            "rates": {"EUR": 0.92},
                        },
                    )
                )
                fx = adapter.get_for_date(Currency.USDT, Currency.EUR, sunday)
                # Adapter preserves the response's date field verbatim —
                # callers who need strict-day semantics must check themselves.
                assert fx.date == friday
                assert fx.date != sunday
        finally:
            adapter.close()


class TestSameCurrencyRejected:
    """Same-currency lookups short-circuit before any wire call."""

    def test_get_latest_same_currency_raises_fx_rate_error(self) -> None:
        """get_latest(EUR, EUR) raises FxRateError without touching the network."""
        adapter = _make_adapter()
        try:
            with respx.mock(assert_all_called=False) as router:
                # No route registered — any wire call would 404 the mock.
                _ = router
                with pytest.raises(FxRateError, match="same-currency"):
                    adapter.get_latest(Currency.EUR, Currency.EUR)
        finally:
            adapter.close()

    def test_get_for_date_same_currency_raises_fx_rate_error(self) -> None:
        """get_for_date(EUR, EUR, ...) raises FxRateError before hitting the wire."""
        adapter = _make_adapter()
        try:
            with respx.mock(assert_all_called=False) as router:
                _ = router
                with pytest.raises(FxRateError, match="same-currency"):
                    adapter.get_for_date(Currency.EUR, Currency.EUR, date(2026, 8, 23))
        finally:
            adapter.close()


class TestErrorPaths:
    """Every documented failure mode surfaces as FxRateError."""

    def test_http_404_raises_fx_rate_error(self) -> None:
        """A 404 (out-of-coverage date) raises FxRateError with the status in the message."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                router.get(f"{_BASE_URL}/1900-01-01").mock(
                    return_value=httpx.Response(404, text="Not Found")
                )
                with pytest.raises(FxRateError, match="404"):
                    adapter.get_for_date(Currency.USDT, Currency.EUR, date(1900, 1, 1))
        finally:
            adapter.close()

    def test_http_429_raises_fx_rate_error_immediately(self) -> None:
        """A 429 raises FxRateError with no silent retry — limiter is the primary defense."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                route = router.get(_LATEST_URL).mock(
                    return_value=httpx.Response(429, text="Too Many Requests")
                )
                with pytest.raises(FxRateError, match="429"):
                    adapter.get_latest(Currency.USDT, Currency.EUR)
                # No silent retry — exactly one wire call
                assert route.call_count == 1
        finally:
            adapter.close()

    def test_http_500_raises_fx_rate_error(self) -> None:
        """A 5xx raises FxRateError with the status code preserved for triage."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                router.get(_LATEST_URL).mock(
                    return_value=httpx.Response(500, text="Internal Server Error")
                )
                with pytest.raises(FxRateError, match="500"):
                    adapter.get_latest(Currency.USDT, Currency.EUR)
        finally:
            adapter.close()

    def test_transport_failure_wraps_as_fx_rate_error(self) -> None:
        """A ConnectError wraps into FxRateError — no raw httpx exception leaks."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                router.get(_LATEST_URL).mock(side_effect=httpx.ConnectError("DNS failed"))
                with pytest.raises(FxRateError, match="transport failure"):
                    adapter.get_latest(Currency.USDT, Currency.EUR)
        finally:
            adapter.close()

    def test_non_json_response_raises_fx_rate_error(self) -> None:
        """A 200 with non-JSON body raises FxRateError, not ValueError."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                router.get(_LATEST_URL).mock(
                    return_value=httpx.Response(200, content=b"<html>outage</html>")
                )
                with pytest.raises(FxRateError, match="non-JSON"):
                    adapter.get_latest(Currency.USDT, Currency.EUR)
        finally:
            adapter.close()

    def test_non_object_body_raises_fx_rate_error(self) -> None:
        """A 200 with a JSON array (not object) raises FxRateError naming the shape."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                router.get(_LATEST_URL).mock(
                    return_value=httpx.Response(200, json=["unexpected", "list"])
                )
                with pytest.raises(FxRateError, match="non-object"):
                    adapter.get_latest(Currency.USDT, Currency.EUR)
        finally:
            adapter.close()

    def test_missing_target_currency_raises_fx_rate_error(self) -> None:
        """A response whose rates dict lacks the requested currency raises FxRateError."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                router.get(_LATEST_URL).mock(
                    return_value=httpx.Response(
                        200,
                        json={
                            "base": "USDT",
                            "date": "2026-08-23",
                            "rates": {"GBP": 0.79},  # asked for EUR, got GBP
                        },
                    )
                )
                with pytest.raises(FxRateError, match="no rate for 'EUR'"):
                    adapter.get_latest(Currency.USDT, Currency.EUR)
        finally:
            adapter.close()

    def test_missing_date_field_raises_fx_rate_error(self) -> None:
        """A response missing the 'date' key raises FxRateError explaining the gap."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                router.get(_LATEST_URL).mock(
                    return_value=httpx.Response(
                        200,
                        json={
                            "base": "USDT",
                            "rates": {"EUR": 0.92},
                        },
                    )
                )
                with pytest.raises(FxRateError, match="'date'"):
                    adapter.get_latest(Currency.USDT, Currency.EUR)
        finally:
            adapter.close()

    def test_non_iso_date_raises_fx_rate_error(self) -> None:
        """A response with a non-ISO 'date' string raises FxRateError."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                router.get(_LATEST_URL).mock(
                    return_value=httpx.Response(
                        200,
                        json={
                            "base": "USDT",
                            "date": "23/08/2026",  # non-ISO
                            "rates": {"EUR": 0.92},
                        },
                    )
                )
                with pytest.raises(FxRateError, match="ISO"):
                    adapter.get_latest(Currency.USDT, Currency.EUR)
        finally:
            adapter.close()

    def test_missing_rates_field_raises_fx_rate_error(self) -> None:
        """A response missing the 'rates' key raises FxRateError."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                router.get(_LATEST_URL).mock(
                    return_value=httpx.Response(
                        200,
                        json={
                            "base": "USDT",
                            "date": "2026-08-23",
                        },
                    )
                )
                with pytest.raises(FxRateError, match="'rates'"):
                    adapter.get_latest(Currency.USDT, Currency.EUR)
        finally:
            adapter.close()

    def test_non_numeric_rate_raises_fx_rate_error(self) -> None:
        """A rate value that is not int/float/str raises FxRateError."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                router.get(_LATEST_URL).mock(
                    return_value=httpx.Response(
                        200,
                        json={
                            "base": "USDT",
                            "date": "2026-08-23",
                            "rates": {"EUR": None},
                        },
                    )
                )
                with pytest.raises(FxRateError, match="not numeric"):
                    adapter.get_latest(Currency.USDT, Currency.EUR)
        finally:
            adapter.close()

    def test_unparseable_rate_raises_fx_rate_error(self) -> None:
        """A string rate that Decimal cannot parse raises FxRateError."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                router.get(_LATEST_URL).mock(
                    return_value=httpx.Response(
                        200,
                        json={
                            "base": "USDT",
                            "date": "2026-08-23",
                            "rates": {"EUR": "not-a-number"},
                        },
                    )
                )
                with pytest.raises(FxRateError, match="is not a decimal"):
                    adapter.get_latest(Currency.USDT, Currency.EUR)
        finally:
            adapter.close()

    def test_zero_rate_from_upstream_wraps_domain_violation(self) -> None:
        """A zero rate from Frankfurter surfaces as FxRateError (not a raw ValueError)."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                router.get(_LATEST_URL).mock(
                    return_value=httpx.Response(
                        200,
                        json={
                            "base": "USDT",
                            "date": "2026-08-23",
                            "rates": {"EUR": 0},
                        },
                    )
                )
                with pytest.raises(FxRateError, match="invariants"):
                    adapter.get_latest(Currency.USDT, Currency.EUR)
        finally:
            adapter.close()

    def test_fx_rate_error_inherits_from_infra_error(self) -> None:
        """FxRateError caught by 'except InfraError' satisfies the base contract."""
        err = FxRateError(msg="bad")
        assert isinstance(err, InfraError)


class TestPortConformance:
    """The adapter class satisfies the runtime-checkable Protocol."""

    def test_adapter_is_fx_rate_provider_port_instance(self) -> None:
        """Isinstance against the runtime_checkable Protocol returns True."""
        adapter = _make_adapter()
        try:
            assert isinstance(adapter, FxRateProviderPort)
        finally:
            adapter.close()


class TestCreateFactoryAndClose:
    """The create() factory produces a working adapter; close is idempotent."""

    def test_create_default_targets_public_frankfurter(self) -> None:
        """create() with no args points at the same host as the config default (#1176).

        The old ``api.frankfurter.app`` default answered 301 while the config
        already used the new host; pinning both to one value stops the drift.
        """
        adapter = FrankfurterAdapter.create()
        try:
            assert (str(adapter.client.base_url), _BASE_URL) == (
                f"{FrankfurterSettings().base_url}/",
                FrankfurterSettings().base_url,
            )
        finally:
            adapter.close()

    def test_create_with_custom_config_uses_it(self) -> None:
        """create(config=X) wires the client and limiter to X's values."""
        cfg = FrankfurterConfig(
            base_url="https://fx.example.com",
            rate_limit=RateLimitConfig(max_weight=10, window_seconds=30),
            timeout_sec=2.5,
        )
        adapter = FrankfurterAdapter.create(cfg)
        try:
            assert str(adapter.client.base_url) == "https://fx.example.com"
            assert adapter.limiter.config.max_weight == 10
        finally:
            adapter.close()

    def test_close_is_idempotent(self) -> None:
        """close() called twice does not raise (second call is a no-op)."""
        adapter = FrankfurterAdapter.create()
        adapter.close()
        adapter.close()  # must not raise


class TestAnswerMatchesTheQuestion:
    """Issue #859: an answer for another base or a later day is refused, not priced with."""

    def test_a_response_for_another_base_raises(self) -> None:
        """A rate quoted from EUR when USDT was asked would be read in the wrong direction."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                router.get(f"{_BASE_URL}/2026-08-21").mock(
                    return_value=httpx.Response(
                        200, json={"base": "EUR", "date": "2026-08-21", "rates": {"EUR": 1.0}}
                    )
                )
                with pytest.raises(FxRateError, match="base is 'EUR', requested 'USDT'"):
                    adapter.get_for_date(Currency.USDT, Currency.EUR, date(2026, 8, 21))
        finally:
            adapter.close()

    def test_a_rate_dated_after_the_requested_day_raises(self) -> None:
        """Frankfurter only falls back to earlier days, so a later date is a wrong answer."""
        adapter = _make_adapter()
        try:
            with respx.mock() as router:
                router.get(f"{_BASE_URL}/2026-08-21").mock(
                    return_value=httpx.Response(
                        200, json={"base": "USDT", "date": "2026-08-24", "rates": {"EUR": 0.92}}
                    )
                )
                with pytest.raises(FxRateError, match="dated 2026-08-24, after the requested day"):
                    adapter.get_for_date(Currency.USDT, Currency.EUR, date(2026, 8, 21))
        finally:
            adapter.close()
