"""Tests for ``hermes runners``: classifier, HTTP behaviour and command wiring.

Every GitHub call is mocked with ``respx``; no real token or socket is used.
Reference: change ``177-runner-health``, docs/guides/ai-authored-tests.md.
"""

import io
import json
from typing import Any

import httpx
import pytest
import respx
from hypothesis import given
from hypothesis import strategies as st

from hermes.entrypoints.commands import discover
from hermes.entrypoints.runners import Runner, RunnerStatus, classify, group_status, main

_URL = "https://api.github.com/repos/ricardoafo/hermes/actions/runners"
_ENV = {"HERMES_GITHUB_TOKEN": "test-token"}  # gitleaks:allow
_RANK = {RunnerStatus.OK: 0, RunnerStatus.DEGRADED: 1, RunnerStatus.FAIL: 2}


def _api_runner(
    name: str, *, online: bool, busy: bool = False, label: str = "hermes"
) -> dict[str, Any]:
    return {
        "name": name,
        "status": "online" if online else "offline",
        "busy": busy,
        "labels": [{"name": "self-hosted"}, {"name": label}],
    }


def _run(argv: list[str], runners: list[dict[str, Any]]) -> tuple[int, str]:
    out = io.StringIO()
    with respx.mock:
        respx.get(_URL).respond(200, json={"total_count": len(runners), "runners": runners})
        code = main(argv, stream=out, env=_ENV)
    return code, out.getvalue()


def test_all_online_group_is_ok() -> None:
    """Invariant: a group whose runners are all online reports OK and exits 0."""
    code, out = _run([], [_api_runner(f"r{i}", online=True) for i in range(4)])
    assert code == 0
    assert "hermes       4/4" in out
    assert out.endswith("overall: OK\n")


def test_busy_runner_counts_as_online() -> None:
    """Invariant: a busy runner is online, so a fully busy pool is not degraded."""
    runners = [_api_runner("a", online=True, busy=True), _api_runner("b", online=True, busy=True)]
    code, out = _run([], runners)
    assert code == 0
    assert "hermes       2/2      2" in out
    assert out.endswith("overall: OK\n")


def test_one_offline_runner_between_jobs_is_not_a_failure() -> None:
    """Invariant: ephemeral runners re-register per job, so 3 of 4 online is OK."""
    code, out = _run([], [_api_runner(f"r{i}", online=i != 0) for i in range(4)])
    assert code == 0
    assert out.endswith("overall: OK\n")


def test_fewer_than_half_online_is_degraded_and_exits_zero() -> None:
    """Invariant: DEGRADED is reported but does not fail the exit code."""
    code, out = _run([], [_api_runner(f"r{i}", online=i == 0) for i in range(4)])
    assert code == 0
    assert out.endswith("overall: DEGRADED\n")


def test_group_with_all_runners_offline_fails() -> None:
    """Invariant: a pool with no online runner can run no CI, so it exits 1."""
    code, out = _run([], [_api_runner("a", online=False), _api_runner("b", online=False)])
    assert code == 1
    assert out.endswith("overall: FAIL\n")


def test_offline_optional_group_fails_overall_when_hermes_is_healthy() -> None:
    """Invariant: the worst group decides the overall status."""
    runners = [_api_runner("a", online=True), _api_runner("ops", online=False, label="hermes-ops")]
    code, out = _run([], runners)
    assert code == 1
    assert "hermes-ops   0/1" in out


def test_review_runners_are_judged_and_keep_the_columns_aligned() -> None:
    """Invariant: the AI review pool is judged, and its label widens the column."""
    runners = [
        _api_runner("a", online=True),
        _api_runner("rev", online=False, label="hermes-review"),
    ]
    code, out = _run([], runners)
    assert code == 1
    assert "hermes-review  0/1" in out
    assert "hermes         1/1" in out


def test_absent_optional_groups_are_not_listed() -> None:
    """Invariant: planned pools that do not exist yet neither show up nor fail."""
    _, out = _run([], [_api_runner("a", online=True)])
    assert "dependabot" not in out
    assert "hermes-ops" not in out


def test_zero_runners_fails() -> None:
    """Invariant: a repository with no runner at all is a FAIL, not an empty OK."""
    code, out = _run([], [])
    assert code == 1
    assert "hermes       0/0" in out
    assert out.endswith("overall: FAIL\n")


def test_missing_token_is_not_configured() -> None:
    """Invariant: no token exits 3 and names the variable, with no HTTP call."""
    out = io.StringIO()
    with respx.mock:
        route = respx.get(_URL)
        code = main([], stream=out, env={})
    assert code == 3
    assert "HERMES_GITHUB_TOKEN" in out.getvalue()
    assert not route.called


@pytest.mark.parametrize("status", [401, 403])
def test_unauthorised_token_fails_with_scope_hint(status: int) -> None:
    """Invariant: an unauthorised token exits 1 and says which scope is needed."""
    out = io.StringIO()
    with respx.mock:
        respx.get(_URL).respond(status, json={"message": "Resource not accessible"})
        code = main([], stream=out, env=_ENV)
    assert code == 1
    assert "Administration read" in out.getvalue()


def test_server_error_fails_without_blaming_the_token() -> None:
    """Invariant: a 500 exits 1 and does not mention the token scope."""
    out = io.StringIO()
    with respx.mock:
        respx.get(_URL).respond(500)
        code = main([], stream=out, env=_ENV)
    assert code == 1
    assert out.getvalue() == "FAIL: GitHub answered 500\n"


def test_network_error_fails() -> None:
    """Invariant: an unreachable GitHub exits 1 instead of raising."""
    out = io.StringIO()
    with respx.mock:
        respx.get(_URL).mock(side_effect=httpx.ConnectError("boom"))
        code = main([], stream=out, env=_ENV)
    assert code == 1
    assert out.getvalue().startswith("FAIL: could not reach GitHub")


def test_repo_comes_from_env_and_token_is_sent_as_bearer() -> None:
    """Invariant: HERMES_GITHUB_REPO picks the repo; the token goes in the header."""
    out = io.StringIO()
    with respx.mock:
        route = respx.get("https://api.github.com/repos/acme/ci/actions/runners").respond(
            200, json={"runners": [_api_runner("a", online=True)]}
        )
        code = main([], stream=out, env={**_ENV, "HERMES_GITHUB_REPO": "acme/ci"})
    assert code == 0
    assert route.calls.last.request.headers["Authorization"] == "Bearer test-token"


def test_json_output_shape() -> None:
    """Invariant: --json prints one object with repo, overall status and per-label rows."""
    runners = [_api_runner("a", online=True, busy=True), _api_runner("b", online=False)]
    code, out = _run(["--json"], runners)
    assert code == 0
    assert json.loads(out) == {
        "repo": "ricardoafo/hermes",
        "status": "OK",
        "groups": [{"label": "hermes", "online": 1, "total": 2, "busy": 1, "status": "OK"}],
    }


def test_unknown_flag_is_a_usage_error() -> None:
    """Invariant: a bad flag exits 2 with the usage line."""
    out = io.StringIO()
    assert main(["--nope"], stream=out, env=_ENV) == 2
    assert out.getvalue() == "Usage: hermes runners [--json]\n"


def test_runner_with_two_labels_counts_in_both_groups() -> None:
    """Invariant: a multi-label runner is counted once per label group."""
    runner = Runner("x", online=True, busy=False, labels=frozenset({"hermes", "dependabot"}))
    report = classify([runner])
    assert {g.label: g.total for g in report.groups} == {"hermes": 1, "dependabot": 1}


def test_command_is_discovered_as_runners() -> None:
    """Invariant: the command module registers itself under the name ``runners``."""
    assert discover()["runners"].name == "runners"


def test_command_run_feeds_the_process_env_to_the_implementation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant: the command wiring reads its env from the health env snapshot."""
    monkeypatch.setattr("hermes.entrypoints.commands.runners.health_env", dict)
    out = io.StringIO()
    assert discover()["runners"].run([], stream=out) == 3


@given(total=st.integers(min_value=1, max_value=50), data=st.data())
def test_adding_an_online_runner_never_worsens_the_status(total: int, data: st.DataObject) -> None:
    """Invariant: a new online runner never moves a group toward FAIL."""
    online = data.draw(st.integers(min_value=0, max_value=total))
    before = group_status(online, total)
    after = group_status(online + 1, total + 1)
    assert _RANK[after] <= _RANK[before]


@given(total=st.integers(min_value=1, max_value=50), data=st.data())
def test_fail_is_exactly_the_zero_online_case(total: int, data: st.DataObject) -> None:
    """Invariant: FAIL means zero online, and zero online always means FAIL."""
    online = data.draw(st.integers(min_value=0, max_value=total))
    assert (group_status(online, total) == RunnerStatus.FAIL) == (online == 0)
