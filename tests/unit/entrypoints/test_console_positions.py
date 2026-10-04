"""``hermes positions`` and ``hermes positions COIN``.

Invariants:

- without a demo there is no live data and no fill path yet, so the command
  answers with the empty state and the #353 note, exit 0;
- ``--demo`` reaches the command after the global flag is stripped, and
  ``HERMES_CONSOLE_DEMO`` feeds it too;
- an unknown coin exits 1, an unknown scenario or flag exits 2;
- ``--json`` prints parseable JSON with money under ``pnl``.
"""

from __future__ import annotations

import io
import json

import pytest

from hermes.entrypoints import cli as cli_module
from hermes.entrypoints.commands.positions import run_positions
from hermes.entrypoints.console import DEMO_ENV_VAR


def _run(args: list[str]) -> tuple[int, str]:
    stream = io.StringIO()
    return cli_module.main(args, stream=stream), stream.getvalue()


def test_without_demo_prints_empty_state_and_fill_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ledger read model yet: empty state plus the note that fills start with #353."""
    monkeypatch.delenv(DEMO_ENV_VAR, raising=False)
    assert _run(["positions"]) == (0, "No open shadow positions.\nFills start with #353.\n")


def test_without_demo_json_is_an_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """The JSON empty state is an empty positions list and the same note."""
    monkeypatch.delenv(DEMO_ENV_VAR, raising=False)
    rc, out = _run(["positions", "--json"])
    assert (rc, json.loads(out)) == (0, {"positions": [], "note": "Fills start with #353."})


def test_demo_flag_reaches_positions_command() -> None:
    """The leading --demo scenario is the one the positions command reads."""
    rc, out = _run(["--demo", "ok-two-positions", "positions"])
    assert (rc, "DEMO DATA" in out, "ETH" in out, "SOL" in out) == (0, True, True, True)


def test_env_var_feeds_positions_command() -> None:
    """HERMES_CONSOLE_DEMO selects the scenario when no flag is given."""
    stream = io.StringIO()
    rc = run_positions([], stream, env={DEMO_ENV_VAR: "ok-two-positions"})
    assert (rc, "scenario ok-two-positions" in stream.getvalue()) == (0, True)


def test_coin_argument_opens_the_detail_case_insensitively() -> None:
    """``positions eth`` shows the ETH detail with its money block and timeline."""
    rc, out = _run(["--demo", "ok-two-positions", "positions", "eth"])
    assert (rc, "Money if sold now" in out, "Timeline" in out) == (0, True, True)


def test_unknown_coin_exits_one() -> None:
    """A coin without an open position is exit 1 and says so."""
    rc, out = _run(["--demo", "ok-two-positions", "positions", "XRP"])
    assert (rc, "No open position for that coin." in out) == (1, True)


def test_unknown_coin_json_exits_one() -> None:
    """The JSON form of an unknown coin is an error object and exit 1."""
    rc, out = _run(["--demo", "ok-two-positions", "positions", "XRP", "--json"])
    assert (rc, json.loads(out)) == (1, {"error": "no open position for XRP"})


def test_json_nests_money_under_pnl() -> None:
    """--json prints parseable JSON where each row carries a ``pnl`` object."""
    rc, out = _run(["--demo", "ok-two-positions", "positions", "--json"])
    rows = json.loads(out)["positions"]
    assert (rc, ["pnl" in r for r in rows]) == (0, [True, True])


def test_utc_flag_changes_the_json_offset() -> None:
    """--utc prints +00:00 timestamps; the default is Europe/Madrid."""
    _, madrid = _run(["--demo", "ok-two-positions", "positions", "--json"])
    _, utc = _run(["--demo", "ok-two-positions", "positions", "--json", "--utc"])
    assert (
        json.loads(madrid)["positions"][0]["opened_at"],
        json.loads(utc)["positions"][0]["opened_at"],
    ) == ("2026-10-02T10:00:00+02:00", "2026-10-02T08:00:00+00:00")


@pytest.mark.parametrize("args", [["--bogus"], ["ETH", "SOL"]])
def test_bad_arguments_exit_two(args: list[str]) -> None:
    """Unknown flags or two coins are a usage error."""
    rc, out = _run(["--demo", "ok-two-positions", "positions", *args])
    assert (rc, out.startswith("ERROR: bad arguments.")) == (2, True)


def test_unknown_scenario_exits_two() -> None:
    """An unknown scenario is a usage error that names the valid ones."""
    rc, out = _run(["--demo", "nope", "positions"])
    assert (rc, out.startswith("ERROR:")) == (2, True)


def test_positions_is_listed_in_help() -> None:
    """The top-level help names the positions command."""
    _, out = _run(["--help"])
    assert "positions" in out.split("Options:")[0]


@pytest.mark.parametrize("flag", ["-h", "--help"])
@pytest.mark.parametrize("prefix", [[], ["--demo", "ok-two-positions"]])
def test_help_prints_usage_flags_and_exit_codes_and_exits_zero(
    flag: str, prefix: list[str]
) -> None:
    """`positions -h/--help` is answered with usage, flags and exit codes, exit 0."""
    rc, out = _run([*prefix, "positions", flag])
    assert (
        rc,
        out.startswith("Usage: hermes [--demo SCENARIO] positions [COIN] [--json] [--utc]"),
        "--json" in out,
        "Exit codes: 0 success, 1 COIN has no open position, 2 usage" in out,
    ) == (0, True, True, True)


def test_help_beats_other_bad_arguments() -> None:
    """Help wins over an unknown flag in the same call, as in `activity`."""
    rc, out = _run(["positions", "--bogus", "--help"])
    assert (rc, out.startswith("Usage:")) == (0, True)
