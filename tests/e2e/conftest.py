"""Report how long each end-to-end serve run took per bar.

The serve runs have a hang guard sized from their bar count, so a slowdown
would pass unnoticed until it hit the guard; printing the per-bar time in
every run keeps it visible in the release gate's log.
"""

import pytest

from tests.e2e import test_serve_cycle


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    """Print bars, seconds and milliseconds per bar of every serve run."""
    if not test_serve_cycle.SERVE_TIMINGS:
        return
    terminalreporter.section("serve run timings")
    for bars, seconds in test_serve_cycle.SERVE_TIMINGS:
        per_bar_ms = seconds / bars * 1000 if bars else 0.0
        terminalreporter.write_line(
            f"{bars} live bars in {seconds:.1f} s ({per_bar_ms:.1f} ms per bar, "
            f"budget {test_serve_cycle.serve_budget(bars):.0f} s)"
        )
