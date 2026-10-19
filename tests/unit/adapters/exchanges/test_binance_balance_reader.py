"""Unit and contract tests for the read-only Binance balance reader (#29).

Every request is answered by respx (or an ``httpx.MockTransport`` in the
property test) with replies shaped like Binance's spot REST API, served from
``tests/fixtures/binance_spot``; no test reaches the network.

Invariants protected here (openspec/archive/29-binance-balance-read R-1..R-4):

- the config refuses plain HTTP, an undocumented host and a blank credential,
  and ``from_env`` names a missing variable without echoing any value;
- a balance is free plus locked, an asset not held reads zero, and only the
  requested assets come back;
- the only request is one signed ``GET /api/v3/account``;
- refusals and malformed replies are ExchangeError, transport failures are
  NetworkError, and neither carries the secret or the signature;
- the reader has no order or cancel surface.
"""

import json
from collections.abc import Iterator
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from hypothesis import given
from hypothesis import strategies as st

from hermes.adapters.exchanges.binance_balance_reader import (
    BinanceBalanceReader,
    BinanceBalanceReaderConfig,
)
from hermes.adapters.exchanges.binance_spot import TESTNET_BASE_URL, sign
from hermes.domain.errors import ExchangeError, NetworkError
from hermes.domain.value_objects.currency import Currency
from tests.support.paths import REPO_ROOT

_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "binance_spot"
_BASE = "https://api.binance.com"
_ACCOUNT_URL = f"{_BASE}/api/v3/account"
_NOW_MS = 1_790_000_000_000
_KEY = "read-key-not-real"  # gitleaks:allow
_SIGNING_MATERIAL = "read-secret-not-real-0123456789"  # gitleaks:allow
_KEY_ENV = "HERMES_BINANCE_READ__API_KEY"
_SECRET_ENV = "HERMES_BINANCE_READ__API_SECRET"  # noqa: S105 — a variable name


def _account() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((_FIXTURES / "account.json").read_text(encoding="utf-8"))
    return loaded


def _reader_on(client: httpx.Client) -> BinanceBalanceReader:
    config = BinanceBalanceReaderConfig(api_key=_KEY, api_secret=_SIGNING_MATERIAL)
    return BinanceBalanceReader(config=config, client=client, clock_ms=lambda: _NOW_MS)


@pytest.fixture
def reader() -> Iterator[BinanceBalanceReader]:
    """Return a mainnet-host reader with a fixed clock; respx answers every request."""
    client = httpx.Client(base_url=_BASE)
    yield _reader_on(client)
    client.close()


# ---------------------------------------------------------------------------
# Config refusals
# ---------------------------------------------------------------------------


def test_plain_http_is_refused() -> None:
    """The key and the signed query never travel without TLS."""
    with pytest.raises(ValueError, match="base_url must be https"):
        BinanceBalanceReaderConfig(
            api_key=_KEY, api_secret=_SIGNING_MATERIAL, base_url="http://api.binance.com"
        )


# SEV-1: the read key never leaves Binance (AGENTS.md rule 6, #781 host allow-list)
@pytest.mark.parametrize(
    "base_url",
    ["https://api.binance.co", "https://evil.example", "https://api.binance.com.evil.example"],
)
def test_an_undocumented_host_is_refused(base_url: str) -> None:
    """Only Binance's documented spot hosts and the testnet receive the key."""
    with pytest.raises(ValueError, match="not a documented Binance spot host"):
        BinanceBalanceReaderConfig(api_key=_KEY, api_secret=_SIGNING_MATERIAL, base_url=base_url)


@pytest.mark.parametrize(
    "base_url", ["https://api.binance.com", "https://api3.binance.com", TESTNET_BASE_URL]
)
def test_documented_hosts_and_the_testnet_are_accepted(base_url: str) -> None:
    """A documented spot host or the spot testnet is a valid read target."""
    config = BinanceBalanceReaderConfig(
        api_key=_KEY, api_secret=_SIGNING_MATERIAL, base_url=base_url
    )
    assert config.base_url == base_url


# SEV-1: no fallback credential on the balance read (AGENTS.md rule 7)
def test_a_blank_key_is_refused() -> None:
    """A whitespace-only key is refused and the error names the key variable."""
    with pytest.raises(ValueError, match=r"API key is blank \(HERMES_BINANCE_READ__API_KEY\)"):
        BinanceBalanceReaderConfig(api_key="   ", api_secret=_SIGNING_MATERIAL)


# SEV-1: no fallback credential on the balance read (AGENTS.md rule 7)
def test_a_blank_secret_is_refused() -> None:
    """An empty secret is refused and the error names the secret variable."""
    with pytest.raises(
        ValueError, match=r"API secret is blank \(HERMES_BINANCE_READ__API_SECRET\)"
    ):
        BinanceBalanceReaderConfig(api_key=_KEY, api_secret="")


# SEV-1: credentials never echoed (AGENTS.md rule 6)
def test_from_env_names_the_missing_secret_without_echoing_the_key() -> None:
    """With only the key set, the error names the secret variable and not the key value."""
    with pytest.raises(ValueError) as caught:
        BinanceBalanceReaderConfig.from_env({_KEY_ENV: _KEY})

    assert (str(caught.value), _KEY in str(caught.value)) == (
        "missing or blank: HERMES_BINANCE_READ__API_SECRET",
        False,
    )


def test_from_env_names_both_variables_when_neither_is_set() -> None:
    """An empty environment reports both variable names in one message."""
    with pytest.raises(ValueError) as caught:
        BinanceBalanceReaderConfig.from_env({})

    assert str(caught.value) == (
        "missing or blank: HERMES_BINANCE_READ__API_KEY, HERMES_BINANCE_READ__API_SECRET"
    )


# SEV-1: no fallback credential on the balance read (AGENTS.md rule 7)
def test_from_env_ignores_the_trading_key() -> None:
    """The trading key variables are never read as a stand-in for the read key."""
    trading = {
        "HERMES_BINANCE__API_KEY": _KEY,
        "HERMES_BINANCE__API_SECRET": _SIGNING_MATERIAL,  # gitleaks:allow
    }
    with pytest.raises(ValueError, match="missing or blank: HERMES_BINANCE_READ__API_KEY"):
        BinanceBalanceReaderConfig.from_env(trading)


def test_from_env_strips_whitespace_around_the_credentials() -> None:
    """A trailing newline from an env file does not end up in the key header."""
    config = BinanceBalanceReaderConfig.from_env(
        {_KEY_ENV: f" {_KEY}\n", _SECRET_ENV: f"{_SIGNING_MATERIAL} "}
    )
    assert (config.api_key, config.api_secret) == (_KEY, _SIGNING_MATERIAL)


# SEV-1: credentials never echoed (AGENTS.md rule 6)
def test_the_secret_is_not_in_the_repr() -> None:
    """Logging the config cannot print the secret."""
    config = BinanceBalanceReaderConfig(api_key=_KEY, api_secret=_SIGNING_MATERIAL)
    shown = repr(config)
    assert (_SIGNING_MATERIAL in shown, _KEY in shown) == (False, True)


# ---------------------------------------------------------------------------
# Balances
# ---------------------------------------------------------------------------


@respx.mock
def test_a_balance_is_free_plus_locked(reader: BinanceBalanceReader) -> None:
    """BTC 0.0125 free plus 0.0025 locked is 0.015 (BalanceReadPort contract)."""
    respx.get(url__startswith=_ACCOUNT_URL).mock(return_value=httpx.Response(200, json=_account()))

    assert reader.balances([Currency.BTC])[Currency.BTC] == Decimal("0.015")


@respx.mock
def test_a_fully_locked_balance_is_still_held(reader: BinanceBalanceReader) -> None:
    """ETH with nothing free and 0.5 locked in an order reads 0.5, not zero."""
    respx.get(url__startswith=_ACCOUNT_URL).mock(return_value=httpx.Response(200, json=_account()))

    assert reader.balances([Currency.ETH])[Currency.ETH] == Decimal("0.5")


@respx.mock
def test_an_asset_not_held_reads_zero(reader: BinanceBalanceReader) -> None:
    """SOL, absent from the account, is zero rather than missing or an error."""
    respx.get(url__startswith=_ACCOUNT_URL).mock(return_value=httpx.Response(200, json=_account()))

    assert reader.balances([Currency.SOL]) == {Currency.SOL: Decimal("0")}


@respx.mock
def test_only_the_requested_assets_come_back(reader: BinanceBalanceReader) -> None:
    """BNB and USDT are in the account but not asked for, so they are not returned."""
    respx.get(url__startswith=_ACCOUNT_URL).mock(return_value=httpx.Response(200, json=_account()))

    assert reader.balances([Currency.BTC, Currency.SOL]) == {
        Currency.BTC: Decimal("0.015"),
        Currency.SOL: Decimal("0"),
    }


@given(
    free=st.decimals(min_value=0, max_value=10**9, places=8, allow_nan=False),
    locked=st.decimals(min_value=0, max_value=10**9, places=8, allow_nan=False),
)
def test_the_total_is_exactly_free_plus_locked(free: Decimal, locked: Decimal) -> None:
    """For any 8-place free and locked amounts, the total is their exact sum."""
    reply = {"balances": [{"asset": "BTC", "free": f"{free:f}", "locked": f"{locked:f}"}]}
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=reply))
    with httpx.Client(base_url=_BASE, transport=transport) as client:
        total = _reader_on(client).balances([Currency.BTC])[Currency.BTC]

    assert total == free + locked


# ---------------------------------------------------------------------------
# Wire
# ---------------------------------------------------------------------------


@respx.mock
def test_the_request_is_a_signed_get_of_the_account(reader: BinanceBalanceReader) -> None:
    """GET /api/v3/account carries the key header and an HMAC that verifies with sign()."""
    route = respx.get(url__startswith=_ACCOUNT_URL).mock(
        return_value=httpx.Response(200, json=_account())
    )

    reader.balances([Currency.BTC])

    request = route.calls.last.request
    query = urlsplit(str(request.url)).query
    unsigned, _, signature = query.rpartition("&signature=")
    assert (
        request.method,
        request.url.path,
        request.headers["X-MBX-APIKEY"],
        parse_qs(unsigned),
        signature,
    ) == (
        "GET",
        "/api/v3/account",
        _KEY,
        {"recvWindow": ["5000"], "timestamp": [str(_NOW_MS)]},
        sign(unsigned, _SIGNING_MATERIAL),
    )


# SEV-1: the read path places no order (AGENTS.md rule 7)
def test_a_balance_read_sends_exactly_one_get_and_nothing_else(
    reader: BinanceBalanceReader,
) -> None:
    """One balance read is one GET; no POST or DELETE reaches the venue."""
    with respx.mock(assert_all_called=False) as router:
        router.get(url__startswith=_ACCOUNT_URL).mock(
            return_value=httpx.Response(200, json=_account())
        )
        router.post(url__startswith=_BASE).mock(return_value=httpx.Response(200, json={}))
        router.delete(url__startswith=_BASE).mock(return_value=httpx.Response(200, json={}))

        reader.balances([Currency.BTC, Currency.ETH, Currency.USDT])
        sent = [(c.request.method, c.request.url.path) for c in router.calls]

    assert sent == [("GET", "/api/v3/account")]


# SEV-1: the read path places no order (AGENTS.md rule 7)
def test_the_reader_has_no_order_or_cancel_surface() -> None:
    """The public surface is the balance read, its factory, close and its fields."""
    public = {name for name in dir(BinanceBalanceReader) if not name.startswith("_")}
    assert public == {"balances", "client", "clock_ms", "close", "config", "create"}


# SEV-1: credentials never echoed (AGENTS.md rule 6)
@respx.mock
def test_a_rejected_key_is_an_exchange_error_without_the_secret(
    reader: BinanceBalanceReader,
) -> None:
    """HTTP 401 code -2015 surfaces Binance's code and text, never the secret or signature."""
    respx.get(url__startswith=_ACCOUNT_URL).mock(
        return_value=httpx.Response(
            401, json={"code": -2015, "msg": "Invalid API-key, IP, or permissions for action."}
        )
    )

    with pytest.raises(ExchangeError) as caught:
        reader.balances([Currency.BTC])

    message = str(caught.value)
    assert (message, _SIGNING_MATERIAL in message, "signature" in message) == (
        "Exchange error: Binance refused /api/v3/account (HTTP 401, code -2015): "
        "Invalid API-key, IP, or permissions for action.",
        False,
        False,
    )


@respx.mock
def test_a_non_json_reply_is_an_exchange_error(reader: BinanceBalanceReader) -> None:
    """An HTML error page from a proxy is a malformed reply, not a balance."""
    respx.get(url__startswith=_ACCOUNT_URL).mock(
        return_value=httpx.Response(502, text="<html>Bad Gateway</html>")
    )

    with pytest.raises(ExchangeError, match=r"non-JSON reply on /api/v3/account \(HTTP 502\)"):
        reader.balances([Currency.BTC])


@respx.mock
def test_a_non_numeric_balance_is_an_exchange_error(reader: BinanceBalanceReader) -> None:
    """A locked amount that is not a number is refused, naming the asset and field."""
    reply = _account()
    reply["balances"][0]["locked"] = "n/a"
    respx.get(url__startswith=_ACCOUNT_URL).mock(return_value=httpx.Response(200, json=reply))

    with pytest.raises(ExchangeError, match="BTC locked is not a decimal: 'n/a'"):
        reader.balances([Currency.BTC])


@respx.mock
def test_a_reply_without_a_balances_list_is_an_exchange_error(
    reader: BinanceBalanceReader,
) -> None:
    """An account reply missing ``balances`` cannot be read as all-zero."""
    reply = _account()
    del reply["balances"]
    respx.get(url__startswith=_ACCOUNT_URL).mock(return_value=httpx.Response(200, json=reply))

    with pytest.raises(ExchangeError, match="account reply has no balances list"):
        reader.balances([Currency.BTC])


@respx.mock
def test_a_balance_row_without_an_asset_is_an_exchange_error(
    reader: BinanceBalanceReader,
) -> None:
    """A row whose asset is not a string is malformed, not skipped."""
    reply = _account()
    reply["balances"].append({"asset": None, "free": "1", "locked": "0"})
    respx.get(url__startswith=_ACCOUNT_URL).mock(return_value=httpx.Response(200, json=reply))

    with pytest.raises(ExchangeError, match="malformed balance row"):
        reader.balances([Currency.BTC])


# SEV-1: credentials never echoed (AGENTS.md rule 6)
@respx.mock
def test_a_transport_failure_is_a_network_error_without_the_signed_query(
    reader: BinanceBalanceReader,
) -> None:
    """A connection error names the path and the error kind, never the signed query."""
    respx.get(url__startswith=_ACCOUNT_URL).mock(
        side_effect=httpx.ConnectError(f"down: {_ACCOUNT_URL}?signature=abc")
    )

    with pytest.raises(NetworkError) as caught:
        reader.balances([Currency.BTC])

    assert str(caught.value) == "Network error: transport failure on /api/v3/account: ConnectError"
