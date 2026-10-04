"""Tests for ``hermes health --json`` / ``--help`` (#540).

Invariants protected here:

- ``--json`` prints one JSON document with one object per integration
  (name, status, detail) and the aggregate exit code, in both forms;
- the exit code returned equals the one in the document and the table forms
  keep theirs;
- a secret env value never appears in the output, even when a probe echoes it;
- ``-h``/``--help`` prints usage with the exit codes and returns 0, an unknown
  flag returns 2, an unknown integration still returns 2.

Probes are faked: no network.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.entrypoints import _health as health_module
from hermes.entrypoints.commands import health as health_command

_NOW = datetime(2026, 10, 4, 9, 0, 0, tzinfo=UTC)
_SECRET = "12345:very-secret-token"  # gitleaks:allow  # noqa: S105


def _result(
    name: str, status: str, exit_code: int, detail: str = "ok"
) -> health_module.HealthCheckResult:
    return health_module.HealthCheckResult(
        integration=name,
        status=status,
        latency_ms=12.34,
        detail=detail,
        last_check=_NOW,
        exit_code=exit_code,
    )


@pytest.fixture(autouse=True)
def _fake_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Pin the env snapshot so the ambient shell and ``.env.local`` never leak in."""
    monkeypatch.setattr(
        health_command, "health_env", lambda: {"HERMES_TELEGRAM__BOT_TOKEN": _SECRET}
    )


def _run(args: list[str]) -> tuple[int, str]:
    stream = io.StringIO()
    rc = health_command.COMMAND.run(args, stream=stream)
    return rc, stream.getvalue()


def test_json_all_integrations_lists_each_with_status_detail_and_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant: `health --json` gives one object per integration plus the aggregate exit code."""
    results = [_result("slack", "OK", 0, "ws"), _result("telegram", "FAIL", 3, "no token")]
    monkeypatch.setattr(health_module, "run_all", lambda _env: results)

    rc, out = _run(["--json"])

    doc = json.loads(out)
    assert rc == 3
    assert doc["exit_code"] == 3
    assert [(i["name"], i["status"], i["detail"]) for i in doc["integrations"]] == [
        ("slack", "OK", "ws"),
        ("telegram", "FAIL", "no token"),
    ]


def test_json_single_integration_returns_its_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invariant: `health <name> --json` probes one integration and exits with its code."""
    monkeypatch.setattr(
        health_module, "get_check", lambda _n: lambda _env: _result("slack", "FAIL", 1, "down")
    )

    rc, out = _run(["slack", "--json"])

    doc = json.loads(out)
    assert rc == 1
    assert doc["exit_code"] == 1
    assert [i["name"] for i in doc["integrations"]] == ["slack"]


def test_json_flag_before_integration_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invariant: flag order does not matter."""
    monkeypatch.setattr(
        health_module, "get_check", lambda _n: lambda _env: _result("slack", "OK", 0)
    )

    rc, out = _run(["--json", "slack"])

    assert rc == 0
    assert json.loads(out)["integrations"][0]["name"] == "slack"


def test_json_never_prints_a_secret_env_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invariant: a probe detail that echoes a token is masked in the JSON output."""
    leaky = f"GET https://api.telegram.org/bot{_SECRET}/getMe failed"
    monkeypatch.setattr(
        health_module, "get_check", lambda _n: lambda _env: _result("telegram", "FAIL", 1, leaky)
    )

    _rc, out = _run(["telegram", "--json"])

    assert _SECRET not in out
    assert json.loads(out)["integrations"][0]["detail"] == (
        "GET https://api.telegram.org/bot***/getMe failed"
    )


@given(secret=st.text(alphabet=st.characters(categories=("L", "N")), min_size=4, max_size=30))
def test_render_json_masks_any_secret_value(secret: str) -> None:
    """Invariant: whatever the secret is, render_json output never contains it."""
    result = _result("x", "FAIL", 1, f"boom {secret} boom")
    stream = io.StringIO()

    health_module.render_json([result], {"HERMES_X__TOKEN": secret}, stream)

    assert json.loads(stream.getvalue())["integrations"][0]["detail"] == "boom *** boom"


def test_render_json_empty_results_is_ok() -> None:
    """Invariant: no integrations means an empty list and exit code 0."""
    stream = io.StringIO()

    rc = health_module.render_json([], {}, stream)

    assert rc == 0
    assert json.loads(stream.getvalue()) == {"integrations": [], "exit_code": 0}


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_prints_usage_with_exit_codes_and_returns_zero(flag: str) -> None:
    """Invariant: help documents every exit code and exits 0 without probing."""
    rc, out = _run([flag])

    assert rc == 0
    assert "Usage: hermes health" in out
    assert "--json" in out
    for code in ("0  every probe OK", "1  an adapter is down", "2  bad usage", "3  an integration"):
        assert code in out


def test_unknown_flag_returns_two() -> None:
    """Invariant: an unknown option is bad usage, not an integration name."""
    rc, out = _run(["--bogus"])

    assert rc == 2
    assert "unknown option '--bogus'" in out


def test_unknown_integration_with_json_returns_two_and_names_available() -> None:
    """Invariant: an unknown integration is exit 2 even with --json."""
    rc, out = _run(["nope", "--json"])

    assert rc == 2
    assert "unknown integration 'nope'" in out


def test_table_form_is_unchanged_without_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invariant: without --json the table and its exit code are as before."""
    monkeypatch.setattr(
        health_module, "get_check", lambda _n: lambda _env: _result("slack", "OK", 0, "ws")
    )

    rc, out = _run(["slack"])

    assert rc == 0
    assert out.startswith("# hermes health slack\n")
    assert "  detail       ws\n" in out
