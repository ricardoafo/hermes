"""Tests for API base URLs read from the environment (#970).

Invariants protected here:

- unset or blank, the production endpoint is used;
- ``https`` to any host is accepted, ``http`` only to a loopback host, so a
  bot token never crosses the network in clear;
- user info, a query or a fragment is refused without echoing the value,
  since either can carry a credential;
- a bad value raises :class:`EndpointConfigError` naming the variable.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.adapters.endpoint import (
    EndpointConfigError,
    api_base_from_env,
    is_loopback,
    parse_api_base,
)

_NAME = "HERMES_TELEGRAM__API_BASE"
_DEFAULT = "https://api.telegram.org"


@pytest.mark.parametrize("env", [{}, {_NAME: ""}, {_NAME: "   "}])
def test_unset_or_blank_gives_the_production_endpoint(env: dict[str, str]) -> None:
    """Without a value the client talks to the real API."""
    assert api_base_from_env(env, _NAME, _DEFAULT) == _DEFAULT


def test_a_configured_value_replaces_the_default_stripped() -> None:
    """A set value wins over the default, with blanks and one trailing slash removed."""
    env = {_NAME: "  http://127.0.0.1:8081/  "}

    assert api_base_from_env(env, _NAME, _DEFAULT) == "http://127.0.0.1:8081"


@pytest.mark.parametrize(
    "url",
    [
        "https://api.telegram.org",
        "https://fake.example:8443/prefix",
        "http://127.0.0.1:9000",
        "http://127.8.9.10",
        "http://localhost:1",
        "http://[::1]:8080",
    ],
)
def test_https_anywhere_and_http_on_loopback_are_accepted(url: str) -> None:
    """These schemes and hosts pass unchanged."""
    assert parse_api_base(url, name=_NAME) == url


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://api.telegram.org", "http only on a loopback host"),
        ("http://10.0.0.5:80", "http only on a loopback host"),
        ("ftp://127.0.0.1", "must be an https URL"),
        ("api.telegram.org", "has no host"),
        ("https://", "has no host"),
        ("https://127.0.0.1:0", "has port 0"),
        ("https://127.0.0.1:notaport", "is not a valid URL"),
    ],
)
def test_unsafe_or_malformed_values_are_refused_naming_the_variable(url: str, reason: str) -> None:
    """Each refusal says which variable is wrong and why."""
    with pytest.raises(EndpointConfigError, match=reason) as excinfo:
        parse_api_base(url, name=_NAME)

    assert str(excinfo.value).startswith(_NAME)


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("https://user:s3cret@fake.example", "user info"),
        ("https://fake.example/?token=s3cret", "query or fragment"),
        ("https://fake.example/#s3cret", "query or fragment"),
    ],
)
def test_credential_bearing_parts_are_refused_without_echoing_them(url: str, reason: str) -> None:
    """The error names the variable and the part, never the secret itself."""
    with pytest.raises(EndpointConfigError, match=reason) as excinfo:
        parse_api_base(url, name=_NAME)

    assert "s3cret" not in str(excinfo.value)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://localhost:1", True),
        ("http://127.0.0.1", True),
        ("http://[::1]", True),
        ("https://api.telegram.org", False),
        ("https://10.0.0.1", False),
        ("https://localhost.example", False),
    ],
)
def test_is_loopback_names_only_this_machine(url: str, expected: bool) -> None:
    """``localhost``, 127.0.0.0/8 and ::1 are loopback; nothing else is."""
    assert is_loopback(url) is expected


@given(
    host=st.one_of(
        st.ip_addresses(v=4).map(str),
        st.from_regex(r"[a-z]{1,10}\.[a-z]{2,5}", fullmatch=True),
    ),
    port=st.integers(min_value=1, max_value=65535),
)
def test_any_accepted_http_base_is_loopback(host: str, port: int) -> None:
    """Property: parse_api_base never accepts plain http to a host off this machine."""
    url = f"http://{host}:{port}"
    try:
        parse_api_base(url, name=_NAME)
    except EndpointConfigError:
        assert not is_loopback(url)
    else:
        assert is_loopback(url)


@pytest.mark.parametrize("url", ["http://[::1", "http://[not-an-ip]:80", ""])
def test_a_url_that_does_not_parse_is_not_loopback(url: str) -> None:
    """A malformed URL is never treated as a local fake, and does not raise."""
    assert is_loopback(url) is False
