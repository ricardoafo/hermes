"""Self-test for the sev1_guard pytest plugin.

Uses pytest's built-in `pytester` fixture to run a synthetic mini-suite
that skips a SEV-1-annotated test, then asserts the guard fires (exit 1).
"""

from __future__ import annotations


def test_sev1_skip_without_optin_fails(pytester):  # type: ignore[no-untyped-def]
    pytester.makepyfile("""
        import pytest

        # SEV-1: Security axis S1
        def test_placeholder():
            pytest.skip("temporarily disabled")
    """)
    result = pytester.runpytest_subprocess("-p", "tests.plugins.sev1_guard")
    assert result.ret == 1
