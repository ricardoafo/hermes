"""Self-test for the structlog_guard pytest plugin (#702).

Each case runs a two-test mini-suite through ``pytester`` in a subprocess.
The first test does what an entrypoint test does: it calls
``configure_logging()`` and logs through a module-level logger. The second
then captures a debug line from that same logger, as the router's
value-free overlay test does.
"""

from __future__ import annotations

import pytest

from tests.support.paths import REPO_ROOT

_PLUGIN = ("-p", "tests.plugins.structlog_guard")
_SUITE = """
    import structlog
    from hermes.infrastructure.logging import configure_logging

    _log = structlog.get_logger("leak_probe")

    def test_a_configures_logging_and_logs():
        configure_logging()
        _log.info("first use under the INFO filter")

    def test_b_captures_a_debug_line():
        with structlog.testing.capture_logs() as logs:
            _log.debug("risk_overlay_decision", rule="RO-01")
        assert [entry["event"] for entry in logs] == ["risk_overlay_decision"]
"""


@pytest.fixture(autouse=True)
def _repo_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the subprocess import ``tests.plugins`` and ``hermes`` from the repo."""
    monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT))


def test_leak_breaks_capture_without_the_guard(pytester: pytest.Pytester) -> None:
    """Without the plugin, the second test captures nothing: the leak is real."""
    pytester.makepyfile(_SUITE)
    result = pytester.runpytest_subprocess("-p", "no:randomly")
    result.assert_outcomes(passed=1, failed=1)


def test_guard_keeps_capture_working(pytester: pytest.Pytester) -> None:
    """With the plugin, the debug line is captured after another test configured logging."""
    pytester.makepyfile(_SUITE)
    result = pytester.runpytest_subprocess(*_PLUGIN, "-p", "no:randomly")
    result.assert_outcomes(passed=2)
