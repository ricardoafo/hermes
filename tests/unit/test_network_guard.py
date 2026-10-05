"""Self-test for the network_guard pytest plugin (#637).

Each case runs a synthetic mini-suite through ``pytester`` in a subprocess.
The off-machine addresses come from TEST-NET-3 (203.0.113.0/24, RFC 5737),
which is reserved for documentation, so even a broken guard sends nothing
anywhere real.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_PLUGIN = ("-p", "tests.plugins.network_guard")
_REPO_ROOT = Path(__file__).resolve().parents[2]
_INI = """
[pytest]
markers =
    contract: real third-party calls
    smoke: post-deploy checks
"""


@pytest.fixture(autouse=True)
def _repo_on_path(monkeypatch: pytest.MonkeyPatch, pytester: pytest.Pytester) -> None:
    """Let the subprocess import ``tests.plugins`` and know the exempt markers."""
    monkeypatch.setenv("PYTHONPATH", str(_REPO_ROOT))
    pytester.makeini(_INI)


def test_connect_off_machine_fails_the_test(pytester: pytest.Pytester) -> None:
    """A connection to a non-loopback address fails, naming the host and port."""
    pytester.makepyfile("""
        import socket

        def test_reaches_out():
            socket.create_connection(("203.0.113.7", 443), timeout=1)
    """)
    result = pytester.runpytest_subprocess(*_PLUGIN)
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*NETWORK_GUARD: a test tried to reach 203.0.113.7:443*"])


def test_dns_lookup_fails_the_test(pytester: pytest.Pytester) -> None:
    """Resolving a public host name fails before any packet leaves."""
    pytester.makepyfile("""
        import socket

        def test_resolves():
            socket.getaddrinfo("example.invalid", 443)
    """)
    result = pytester.runpytest_subprocess(*_PLUGIN)
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*NETWORK_GUARD: a test tried to reach example.invalid:443*"])


def test_swallowed_attempt_still_fails_the_test(pytester: pytest.Pytester) -> None:
    """Code that catches the refusal and carries on cannot hide the attempt."""
    pytester.makepyfile("""
        import socket

        def test_falls_back_quietly():
            try:
                socket.create_connection(("203.0.113.8", 80), timeout=1)
            except Exception:
                pass
    """)
    result = pytester.runpytest_subprocess(*_PLUGIN)
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*swallowed the refusal*"])


def test_loopback_is_allowed(pytester: pytest.Pytester) -> None:
    """A local server on 127.0.0.1, as asyncio and test servers use, still works."""
    pytester.makepyfile("""
        import socket

        def test_local_round_trip():
            server = socket.socket()
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            client = socket.create_connection(server.getsockname(), timeout=1)
            peer, _ = server.accept()
            client.sendall(b"ping")
            assert peer.recv(4) == b"ping"
            for s in (client, peer, server):
                s.close()
    """)
    result = pytester.runpytest_subprocess(*_PLUGIN)
    result.assert_outcomes(passed=1)


@pytest.mark.parametrize("marker", ["contract", "smoke"])
def test_contract_and_smoke_tests_are_exempt(pytester: pytest.Pytester, marker: str) -> None:
    """Tests that exist to call real services are left alone: the guard is off for them."""
    pytester.makepyfile(f"""
        import pytest
        from tests.plugins import network_guard

        @pytest.mark.{marker}
        def test_real_service():
            assert network_guard._state["active"] is False
    """)
    result = pytester.runpytest_subprocess(*_PLUGIN)
    result.assert_outcomes(passed=1)
