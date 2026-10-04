"""Pytest plugin: refuse Sentry's default integrations inside the test suite.

``sentry_sdk.init`` without ``default_integrations=False`` installs
process-wide patches (threading, logging, stdlib) that outlive the client.
One such call in one test changes how later tests on the same xdist worker
behave: the threading patch made a frozen-dataclass error fail at random in
an unrelated serve-loop test and got three innocent PRs rejected by the
merge queue (#575). This plugin turns that class of pollution into an
immediate, named failure in the test that causes it.

Two layers:

- ``sentry_sdk.init`` is wrapped for the whole session and raises when
  ``default_integrations`` is not explicitly ``False``.
- After each test, any integration newly installed during it fails that
  test, which also catches paths that reach the SDK without going through
  the wrapped ``init``.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from typing import Any

import pytest
import sentry_sdk
import sentry_sdk.integrations

_ERROR = (
    "SENTRY_GUARD: sentry_sdk.init called without default_integrations=False. "
    "Default integrations patch threading and logging for every later test on "
    "this worker (#575). Pass default_integrations=False."
)


_wrappers: list[Callable[..., Any]] = []


class SentryDefaultIntegrationsError(AssertionError):
    """Raised when a test starts Sentry with its default integrations on."""


def _installed() -> frozenset[str]:
    return frozenset(sentry_sdk.integrations._installed_integrations)


# Any: the wrapper forwards sentry_sdk.init's open-ended keyword signature untouched.
def _guarded(real_init: Callable[..., Any]) -> Callable[..., Any]:
    def init(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("default_integrations") is not False:
            raise SentryDefaultIntegrationsError(_ERROR)
        return real_init(*args, **kwargs)

    _wrappers.append(init)
    return init


def pytest_configure(config: pytest.Config) -> None:
    """Wrap ``sentry_sdk.init`` once per session (and per xdist worker)."""
    if sentry_sdk.init not in _wrappers:
        sentry_sdk.__dict__["init"] = _guarded(sentry_sdk.init)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, object, object]:
    """Fail the test that installed a Sentry integration the session lacked."""
    before = _installed()
    result = yield
    added = _installed() - before
    if added:
        raise SentryDefaultIntegrationsError(
            f"SENTRY_GUARD: {item.nodeid} installed Sentry integrations "
            f"{sorted(added)}; they stay patched for the rest of the worker (#575)."
        )
    return result
