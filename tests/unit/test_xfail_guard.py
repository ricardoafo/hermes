"""Self-test for the xfail_guard pytest plugin and the strict-xfail setting.

Each case runs a synthetic mini-suite through ``pytester`` in a subprocess
that loads the real plugin, with ``PYTHONPATH`` at the repo root so the plugin
imports (otherwise the run dies on the import error and an exit-code check
passes for the wrong reason, as in #704).
"""

import pytest

from tests.support.paths import REPO_ROOT

_PLUGIN = ("-p", "tests.plugins.xfail_guard", "-p", "no:randomly")
_GUARD_LINE = "*XFAIL_GUARD: expected failure on main*test_placeholder*"


@pytest.fixture(autouse=True)
def _repo_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the subprocess import the plugin."""
    monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT))


def test_an_xfail_marker_fails_the_run_and_names_the_test(pytester: pytest.Pytester) -> None:
    """Invariant: a green run means every test passed, so an expected failure fails it (#1193)."""
    pytester.makepyfile("""
        import pytest

        @pytest.mark.xfail(reason="known defect", strict=True)
        def test_placeholder():
            assert False
    """)
    result = pytester.runpytest_subprocess(*_PLUGIN)
    assert result.ret == 1
    result.stdout.fnmatch_lines([_GUARD_LINE])


def test_xfail_called_in_the_body_fails_the_run(pytester: pytest.Pytester) -> None:
    """Invariant: ``pytest.xfail()`` in a test body is an expected failure too."""
    pytester.makepyfile("""
        import pytest

        def test_placeholder():
            pytest.xfail("known defect")
    """)
    result = pytester.runpytest_subprocess(*_PLUGIN)
    assert result.ret == 1
    result.stdout.fnmatch_lines([_GUARD_LINE])


def test_a_passing_suite_stays_green(pytester: pytest.Pytester) -> None:
    """Invariant: the guard adds no failure to a suite without an xfail."""
    pytester.makepyfile("""
        def test_placeholder():
            assert 1 + 1 == 2
    """)
    result = pytester.runpytest_subprocess(*_PLUGIN)
    assert result.ret == 0


def test_the_repo_makes_an_xfail_that_passes_a_failure() -> None:
    """Invariant: ``xfail_strict`` is on, so an xfail marker that stops failing fails the run."""
    config = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "\nxfail_strict = true\n" in config
