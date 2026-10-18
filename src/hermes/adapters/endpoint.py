"""Outside API base URLs read from the environment (ADR-024 decision 2, #970).

A release is pointed at a fake by its config alone, never by a test-only
branch in production code. The notifier and bot clients read their API base
from an env var next to their credentials, so a lower environment can aim
them at a loopback fake.

An API base must be ``https://<host>``, or ``http://`` on a loopback host
(``localhost``, ``127.0.0.0/8``, ``::1``): plain HTTP to any other host would
send a bot token in clear. User info, a query and a fragment are refused,
and one trailing slash is dropped.

A bad value raises :class:`EndpointConfigError`, not the adapter's own
missing-credentials error: callers that quietly disable a channel without
credentials must still fail loudly on a mistyped endpoint.
"""

import ipaddress
from collections.abc import Mapping
from typing import Final
from urllib.parse import urlsplit

__all__ = [
    "DEFAULT_SLACK_API_BASE",
    "DEFAULT_TELEGRAM_API_BASE",
    "SLACK_API_BASE_ENV",
    "TELEGRAM_API_BASE_ENV",
    "EndpointConfigError",
    "api_base_from_env",
    "is_loopback",
    "parse_api_base",
]

DEFAULT_TELEGRAM_API_BASE: Final = "https://api.telegram.org"
DEFAULT_SLACK_API_BASE: Final = "https://slack.com"
TELEGRAM_API_BASE_ENV: Final = "HERMES_TELEGRAM__API_BASE"
SLACK_API_BASE_ENV: Final = "HERMES_SLACK__API_BASE"

_LOCALHOST: Final = "localhost"


class EndpointConfigError(ValueError):
    """An API base URL in the environment is malformed or unsafe."""


def is_loopback(url: str) -> bool:
    """Return whether ``url`` points at this machine.

    Args:
        url: An absolute URL.

    Returns:
        ``True`` when the host is ``localhost``, in ``127.0.0.0/8`` or ``::1``;
        ``False`` for a URL that does not parse.
    """
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return False
    if host == _LOCALHOST:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def parse_api_base(value: str, *, name: str) -> str:
    """Validate an API base URL and return it without a trailing slash.

    Args:
        value: The URL as configured.
        name: The env var it came from, named in errors.

    Returns:
        The URL with one trailing ``/`` removed.

    Raises:
        EndpointConfigError: The scheme is not ``https`` (or ``http`` on a
            loopback host), the host or port is missing or malformed, or it
            carries user info, a query or a fragment.
    """
    try:
        parts = urlsplit(value)
        port_ok = parts.port is None or parts.port > 0
    except ValueError:
        raise EndpointConfigError(f"{name} is not a valid URL") from None
    # Checked before anything echoes the value: both can carry a credential.
    if parts.username is not None or parts.password is not None:
        raise EndpointConfigError(f"{name} must not carry user info")
    if parts.query or parts.fragment:
        raise EndpointConfigError(f"{name} must not carry a query or fragment")
    if not parts.hostname:
        raise EndpointConfigError(f"{name} has no host: {value!r}")
    if not port_ok:
        raise EndpointConfigError(f"{name} has port 0: {value!r}")
    if parts.scheme == "http" and not is_loopback(value):
        raise EndpointConfigError(f"{name} may use http only on a loopback host: {value!r}")
    if parts.scheme not in {"http", "https"}:
        raise EndpointConfigError(f"{name} must be an https URL: {value!r}")
    return value.removesuffix("/")


def api_base_from_env(env: Mapping[str, str], name: str, default: str) -> str:
    """Return the API base in ``env[name]``, or ``default`` when unset or blank.

    Args:
        env: Environment snapshot.
        name: The env var to read.
        default: The production endpoint.

    Returns:
        The validated API base.

    Raises:
        EndpointConfigError: The configured value fails :func:`parse_api_base`.
    """
    value = env.get(name, "").strip()
    if not value:
        return default
    return parse_api_base(value, name=name)
