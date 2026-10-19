"""Self-test for the sev1_guard pytest plugin.

Each case runs a synthetic mini-suite through ``pytester`` in a subprocess
that loads the real plugin. ``PYTHONPATH`` points at the repo root so the
subprocess can import ``tests.plugins.sev1_guard``; without it the run dies
on the import error, and an exit-code check would pass for the wrong reason
(#704).
"""

from __future__ import annotations

import pytest

from tests.support.paths import REPO_ROOT

_PLUGIN = ("-p", "tests.plugins.sev1_guard", "-p", "no:randomly")
_GUARD_LINE = "*SEV1_GUARD: SEV-1 test skipped without opt-in*test_placeholder*"


@pytest.fixture(autouse=True)
def _repo_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the subprocess import the plugin, and start without the opt-in set."""
    monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT))
    monkeypatch.delenv("PYTEST_SEV1_ALLOW_SKIP", raising=False)


# SEV-1: the guard is what keeps a SEV-1 test from being silently skipped (rubric S1-S7)
def test_skip_inside_the_body_fails_the_run(pytester: pytest.Pytester) -> None:
    """``pytest.skip()`` in a SEV-1 test's body fails the run and names the test."""
    pytester.makepyfile("""
        import pytest

        # SEV-1: Security axis S1
        def test_placeholder():
            pytest.skip("temporarily disabled")
    """)
    result = pytester.runpytest_subprocess(*_PLUGIN)
    assert result.ret == 1
    result.stdout.fnmatch_lines([_GUARD_LINE])


# SEV-1: the guard is what keeps a SEV-1 test from being silently skipped (rubric S1-S7)
def test_skip_marker_fails_the_run(pytester: pytest.Pytester) -> None:
    """A ``skip`` marker on a SEV-1 test fails the run and names the test."""
    pytester.makepyfile("""
        import pytest

        # SEV-1: Security axis S1
        @pytest.mark.skip(reason="temporarily disabled")
        def test_placeholder():
            pass
    """)
    result = pytester.runpytest_subprocess(*_PLUGIN)
    assert result.ret == 1
    result.stdout.fnmatch_lines([_GUARD_LINE])


def test_opt_in_allows_the_skip(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
    """With ``PYTEST_SEV1_ALLOW_SKIP`` set, a skipped SEV-1 test does not fail the run."""
    monkeypatch.setenv("PYTEST_SEV1_ALLOW_SKIP", "1")
    pytester.makepyfile("""
        import pytest

        # SEV-1: Security axis S1
        def test_placeholder():
            pytest.skip("temporarily disabled")
    """)
    result = pytester.runpytest_subprocess(*_PLUGIN)
    assert (result.ret, result.parseoutcomes().get("skipped")) == (0, 1)


def test_skip_of_an_ordinary_test_is_allowed(pytester: pytest.Pytester) -> None:
    """A skipped test without a SEV-1 annotation does not trip the guard."""
    pytester.makepyfile("""
        import pytest

        def test_placeholder():
            pytest.skip("not SEV-1")
    """)
    result = pytester.runpytest_subprocess(*_PLUGIN)
    assert (result.ret, result.parseoutcomes().get("skipped")) == (0, 1)
