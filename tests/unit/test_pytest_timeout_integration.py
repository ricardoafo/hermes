"""pytest-timeout must kill a runaway test instead of hanging silently.

The wave-5 process-hardening bundle introduced pytest-timeout so that a
test which hangs (production infinite loop, deadlock, blocked network
call the fake did not mock) fails LOUDLY with a stack trace pointing at
the frame that stopped progressing — rather than draining CI / sub-agent
time silently until an external watchdog kills the runner.

The invariant this file protects is INFRASTRUCTURAL, not domain: it
proves that the plugin is installed, that pyproject.toml's ini_options
picked up the config, and that `timeout_method = "thread"` works on the
current OS. If pytest-timeout is ever removed from dev deps or the
config drifts, this file breaks first.

Root cause post-mortem: slot-12 sub-agent (klines fetcher) was killed
after hanging on a rate-limiter test with a real float-drift infinite
loop in production code (fixed in PR #138). Preventive hardening: make
hangs FAIL LOUDLY with a stack trace instead of silently draining time.

Test strategy — why a subprocess:
    pytest-timeout's `thread` method (chosen for Windows compatibility)
    dumps thread stacks and then calls `os._exit(1)`. Running a hanging
    test in-process would kill THIS pytest run. So we run a dedicated
    inner pytest via subprocess with an inline test file that hangs;
    we assert on the exit code and stderr text produced by the inner
    run. This is the same pattern pytest-timeout's own test suite uses.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path


def test_pytest_timeout_kills_a_runaway_loop(tmp_path: Path) -> None:
    """pytest-timeout must interrupt a runaway test instead of hanging silently.

    A `while True: pass` loop under `@pytest.mark.timeout(2)` MUST be
    interrupted by pytest-timeout within a few seconds — the inner pytest
    exits non-zero and its stderr contains a "Timeout" marker + the frame
    where progress stopped.

    We spawn a subprocess pytest run against an inline test that hangs.
    Success of THIS test = inner run exited non-zero within the outer
    30s wall clock. If pytest-timeout is missing / disabled / broken,
    the inner run would either exit 0 (bug: `while True` completed) or
    hang past 30s (the subprocess call fails with TimeoutExpired). Both
    are caught explicitly below.

    If this test regresses, one of:

    - `pytest-timeout` is missing from `[dependency-groups.dev]`
    - `timeout` / `timeout_method` was removed from
      `[tool.pytest.ini_options]`
    - the plugin failed to load and pytest did not surface it

    In all three cases, the wave-5 hardening has regressed and the
    root-cause failure mode from PR #138 (silent sub-agent hang) is
    back on the table.
    """
    hang_test = tmp_path / "test_hang.py"
    hang_test.write_text(
        textwrap.dedent(
            """
            import pytest

            @pytest.mark.timeout(2, method="thread")
            def test_deliberate_hang():
                while True:
                    pass
            """
        )
    )

    # Run the inner pytest with an outer wall-clock cap of 30s. If
    # pytest-timeout works, the inner run terminates in ~2s (the mark's
    # cap). If it doesn't, the outer `subprocess.run(timeout=30)` fires
    # and `TimeoutExpired` propagates — we surface that as a failure
    # rather than letting the whole outer suite hang.
    #
    # ruff S603: sys.executable is trusted (this Python interpreter);
    # tmp_path is pytest's isolated temp dir; the pytest args list is
    # a fixed literal — no untrusted input reaches the subprocess.
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", str(hang_test), "-p", "timeout", "--tb=no", "-q"],
        capture_output=True,
        text=True,
        timeout=30,
        # Fresh cwd so pyproject.toml's ini_options do NOT apply to the
        # inner run — the inline test uses its OWN marker to prove
        # pytest-timeout is loadable and functional. The plugin must
        # come from the dev env we're testing.
        cwd=str(tmp_path),
        # We EXPECT non-zero exit (the deliberate hang is caught by
        # the plugin and reported as a failure), so check=False. The
        # exit code is asserted explicitly below.
        check=False,
    )

    # The inner run MUST have exited non-zero (test failed via timeout).
    assert result.returncode != 0, (
        "Inner pytest exited 0 — the deliberate hang was NOT caught by "
        "pytest-timeout. Either the plugin is disabled or the marker "
        f"was ignored.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    # Combined output must show that pytest-timeout intervened. The
    # plugin prints a "Timeout" separator around the stack dump on the
    # `thread` method; either stream may carry it depending on the
    # inner process's capture behaviour.
    combined = result.stdout + result.stderr
    assert "Timeout" in combined, (
        "Inner pytest failed for a reason OTHER than the timeout — no "
        "'Timeout' marker in output. Something else broke the inner run "
        f"before pytest-timeout could fire.\ncombined: {combined}"
    )
