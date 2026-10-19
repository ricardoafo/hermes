"""Pytest plugin: fail the session if any test ended as an expected failure.

A test that exposes a defect ships in the same PR as its fix (AGENTS.md, "No
xfail on main"). Pytest reports an xfail outcome as skipped with a
``wasxfail`` attribute, whether the marker or ``pytest.xfail()`` caused it, so
this hook sees both. ``xfail_strict`` in ``pyproject.toml`` covers the other
direction, an xfail test that passes (issue #1193).
"""

import pytest

_xfailed: list[str] = []


def pytest_report_teststatus(report: pytest.TestReport) -> None:
    """Collect each test that ended as an expected failure."""
    if report.when in ("setup", "call") and hasattr(report, "wasxfail"):
        _xfailed.append(report.nodeid)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Fail the session with exit code 1 if any test ended as an expected failure."""
    if _xfailed:
        for nodeid in _xfailed:
            print(f"XFAIL_GUARD: expected failure on main: {nodeid}")
        session.exitstatus = 1
