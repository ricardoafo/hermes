"""Self-test for the sentry_guard pytest plugin.

Each case runs a synthetic mini-suite through ``pytester`` in a subprocess,
so the Sentry patches it provokes never reach this worker.
"""

from __future__ import annotations

import pytest

from tests.support.paths import REPO_ROOT

_PLUGIN = ("-p", "tests.plugins.sentry_guard")


@pytest.fixture(autouse=True)
def _repo_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the pytester subprocess import ``tests.plugins`` from the repo root."""
    monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT))


def test_init_with_default_integrations_fails_the_test(pytester: pytest.Pytester) -> None:
    """A bare ``sentry_sdk.init(dsn=None)`` fails the test that makes it, by name."""
    pytester.makepyfile("""
        import sentry_sdk

        def test_bare_init():
            sentry_sdk.init(dsn=None)
    """)
    result = pytester.runpytest_subprocess(*_PLUGIN)
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*SENTRY_GUARD: sentry_sdk.init called without*"])


def test_init_without_default_integrations_passes(pytester: pytest.Pytester) -> None:
    """``default_integrations=False``, as hermes passes it, is allowed."""
    pytester.makepyfile("""
        import sentry_sdk

        def test_safe_init():
            sentry_sdk.init(dsn=None, default_integrations=False, integrations=[])
    """)
    result = pytester.runpytest_subprocess(*_PLUGIN)
    result.assert_outcomes(passed=1)


def test_init_captured_before_a_monkeypatch_is_still_guarded(
    pytester: pytest.Pytester,
) -> None:
    """A test that saves ``sentry_sdk.init`` and calls it later still hits the guard.

    This is the shape of the #575 polluter: it kept ``real_init`` and called
    ``real_init(dsn=None)`` in a ``finally``.
    """
    pytester.makepyfile("""
        import sentry_sdk

        def test_saved_init(monkeypatch):
            real_init = sentry_sdk.init
            monkeypatch.setattr(sentry_sdk, "init", lambda **kw: None)
            real_init(dsn=None)
    """)
    result = pytester.runpytest_subprocess(*_PLUGIN)
    result.assert_outcomes(failed=1)


def test_integration_installed_behind_the_wrapper_fails_the_test(
    pytester: pytest.Pytester,
) -> None:
    """An integration installed without the wrapped ``init`` still fails the test that did it."""
    pytester.makepyfile("""
        from sentry_sdk.integrations import setup_integrations
        from sentry_sdk.integrations.threading import ThreadingIntegration

        def test_sneaky_setup():
            setup_integrations([ThreadingIntegration()], with_defaults=False)

        def test_next_test_is_unaffected():
            pass
    """)
    result = pytester.runpytest_subprocess(*_PLUGIN)
    result.assert_outcomes(failed=1, passed=1)
    result.stdout.fnmatch_lines(["*test_sneaky_setup installed Sentry integrations*threading*"])
