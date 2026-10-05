"""Pytest plugin: tests may not reach the network (#637).

A test that opens a connection or resolves a host outside this machine
depends on the network being up and on a third party answering the same
way twice. That makes the gates that block a deploy flaky, and it can
leak traffic to real services (#620 found the e2e serve test calling
Binance's depth endpoint). This plugin turns any such attempt into an
immediate, named failure.

Rules:

- Blocked: ``socket.socket.connect``/``connect_ex`` to a non-loopback
  address, and ``socket.getaddrinfo`` for a host name that is not
  ``localhost`` or a loopback address. ``socket.create_connection``,
  ``ssl`` and the HTTP clients all go through these.
- Allowed: loopback (asyncio's self-pipe and local test servers use it)
  and Unix-domain sockets.
- Exempt: tests marked ``contract`` or ``smoke``, which exist to talk to
  real services.

A blocked call raises :class:`NetworkAccessBlockedError`, which is not an
``OSError``, so code that falls back quietly on connection errors cannot
swallow it. The call is also recorded, and a test whose attempt was
swallowed anyway still fails after it runs.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Generator
from typing import Any

import pytest

_EXEMPT_MARKERS = ("contract", "smoke")
_LOCAL_NAMES = frozenset({"", "localhost", "localhost.localdomain"})

_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_getaddrinfo = socket.getaddrinfo

_state: dict[str, Any] = {"active": False, "attempts": []}


class NetworkAccessBlockedError(RuntimeError):
    """Raised when a test tries to reach a host outside this machine."""


def _is_local(host: object) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return False
    name = host.strip().lower().rstrip(".")
    if name in _LOCAL_NAMES:
        return True
    try:
        return ipaddress.ip_address(name.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _refuse(target: str) -> None:
    _state["attempts"].append(target)
    raise NetworkAccessBlockedError(
        f"NETWORK_GUARD: a test tried to reach {target}. Tests may not use the "
        "network (#637); use a fake, respx or a recorded fixture, or mark a real "
        "third-party check as @pytest.mark.contract."
    )


def _address_target(address: object) -> str | None:
    """Return ``host:port`` when ``address`` is off this machine, else ``None``."""
    if isinstance(address, tuple) and address and not _is_local(address[0]):
        port = address[1] if len(address) > 1 else "?"
        return f"{address[0]}:{port}"
    return None


def _guarded_connect(self: socket.socket, address: Any) -> None:
    target = _address_target(address) if _state["active"] else None
    if target is not None:
        _refuse(target)
    return _real_connect(self, address)


def _guarded_connect_ex(self: socket.socket, address: Any) -> int:
    target = _address_target(address) if _state["active"] else None
    if target is not None:
        _refuse(target)
    return _real_connect_ex(self, address)


# Any: getaddrinfo's positional and keyword signature is forwarded untouched.
def _guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
    if _state["active"] and not _is_local(host):
        port = args[0] if args else kwargs.get("port", "?")
        _refuse(f"{host}:{port}")
    return _real_getaddrinfo(host, *args, **kwargs)


def _install() -> None:
    patches: tuple[tuple[Any, str, Callable[..., Any]], ...] = (
        (socket.socket, "connect", _guarded_connect),
        (socket.socket, "connect_ex", _guarded_connect_ex),
        (socket, "getaddrinfo", _guarded_getaddrinfo),
    )
    for owner, name, guarded in patches:
        if getattr(owner, name) is not guarded:
            setattr(owner, name, guarded)


def pytest_configure(config: pytest.Config) -> None:
    """Install the guarded socket functions once per session (and xdist worker)."""
    _install()


def _exempt(item: pytest.Item) -> bool:
    return any(item.get_closest_marker(name) is not None for name in _EXEMPT_MARKERS)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item: pytest.Item) -> Generator[None, object, object]:
    """Arm the guard before the test's fixtures run, unless the test is exempt."""
    _state["attempts"] = []
    _state["active"] = not _exempt(item)
    return (yield)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, object, object]:
    """Fail a test whose network attempt was caught and swallowed by the code under test."""
    result = yield
    if _state["attempts"]:
        raise NetworkAccessBlockedError(
            f"NETWORK_GUARD: {item.nodeid} tried to reach {', '.join(_state['attempts'])} "
            "and the code under test swallowed the refusal (#637)."
        )
    return result


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item: pytest.Item) -> Generator[None, object, object]:
    """Disarm the guard once the test and its fixtures are done."""
    try:
        return (yield)
    finally:
        _state["active"] = False
