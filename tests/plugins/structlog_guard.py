"""Pytest plugin: one test's structlog setup cannot leak into the next (#702).

``configure_logging()`` installs an INFO-level filtering wrapper with
``cache_logger_on_first_use=True``. When a test calls it, directly or through
an entrypoint, the next module-level logger to log caches that wrapper for the
rest of the worker. ``structlog.testing.capture_logs()`` then records no
debug lines in any later test, whatever order xdist happens to pick. The
router's value-free overlay test failed at random that way.

Two layers:

- For the whole session, ``structlog.configure`` never turns logger caching
  on, so no module logger pins a configuration.
- After every test, the structlog configuration is put back as it was before
  the test.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from typing import Any

import pytest
import structlog

_wrappers: list[Callable[..., Any]] = []


# Any: structlog.configure takes open-ended keyword settings, forwarded untouched.
def _uncached(real_configure: Callable[..., Any]) -> Callable[..., Any]:
    def configure(**settings: Any) -> None:
        settings["cache_logger_on_first_use"] = False
        real_configure(**settings)

    _wrappers.append(configure)
    return configure


def pytest_configure(config: pytest.Config) -> None:
    """Make every structlog configuration in this session uncached."""
    if structlog.configure not in _wrappers:
        structlog.configure = _uncached(structlog.configure)
    structlog.configure(cache_logger_on_first_use=False)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(
    item: pytest.Item, nextitem: pytest.Item | None
) -> Generator[None, object, object]:
    """Restore the structlog configuration a test or its fixtures changed."""
    before = structlog.get_config()
    try:
        return (yield)
    finally:
        structlog.configure(**before)
