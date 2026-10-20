"""A failing ``hermes`` command keeps stdout clean and puts its ERROR line on stderr (#543).

Invariants protected here:

- on a failing ``--json`` call stdout is empty (so a JSON consumer reads
  nothing, never an error line) and the ERROR goes to the error stream;
- the exit code of each failure is unchanged by the split;
- with no injected error stream the ERROR still reaches the real stderr.

The per-command ERROR text is covered in each command's own test module.
"""

import io
from pathlib import Path

import pytest

from hermes.entrypoints import cli


def _failing_calls(tmp_path: Path) -> dict[str, tuple[list[str], int]]:
    return {
        "status": (["status", "--json", "--config-dir", str(tmp_path)], 3),
        "activity": (["activity", "--json", "--db", str(tmp_path / "absent.sqlite3")], 3),
        "logs": (["logs", "--json", "--last", "0"], 2),
        "positions": (["positions", "--json", "--bogus"], 2),
        "config check": (
            ["config", "check", "--json", "--config-dir", str(tmp_path / "absent")],
            3,
        ),
        "ledger verify": (
            ["ledger", "verify", "--json", "--db", str(tmp_path / "absent.sqlite3")],
            3,
        ),
        "health": (["health", "nope", "--json"], 2),
        "journal list": (["journal", "list", "--json", "--bogus"], 2),
        "money": (["money", "--json", "--bogus"], 2),
        "version": (["version", "--json", "--bogus"], 2),
    }


@pytest.mark.parametrize(
    "command",
    [
        "status",
        "activity",
        "logs",
        "positions",
        "config check",
        "ledger verify",
        "health",
        "journal list",
        "money",
        "version",
    ],
)
def test_a_failing_json_call_leaves_stdout_empty_and_errors_on_err(
    command: str, tmp_path: Path
) -> None:
    """Invariant: `hermes <cmd> --json` that fails writes ERROR to err and nothing to stdout."""
    argv, expected_code = _failing_calls(tmp_path)[command]
    out, err = io.StringIO(), io.StringIO()

    code = cli.main(argv, stream=out, err=err)

    assert (code, out.getvalue(), err.getvalue().startswith("ERROR:")) == (
        expected_code,
        "",
        True,
    )


def test_an_unknown_subcommand_errors_on_err_not_stdout() -> None:
    """Invariant: the dispatcher's own ERROR goes to err, exit code 2."""
    out, err = io.StringIO(), io.StringIO()

    code = cli.main(["not-a-command"], stream=out, err=err)

    assert (code, out.getvalue(), err.getvalue().startswith("ERROR: unknown subcommand")) == (
        2,
        "",
        True,
    )


def test_a_bad_demo_flag_errors_on_err_not_stdout() -> None:
    """Invariant: the leading --demo handler's ERROR goes to err, exit code 2."""
    out, err = io.StringIO(), io.StringIO()

    code = cli.main(["--demo"], stream=out, err=err)

    assert (code, out.getvalue(), err.getvalue().startswith("ERROR: --demo needs")) == (
        2,
        "",
        True,
    )


def test_without_an_injected_err_the_error_reaches_real_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Invariant: the default error stream is the process's stderr."""
    out = io.StringIO()

    code = cli.main(["not-a-command"], stream=out)

    captured = capsys.readouterr()
    assert (code, out.getvalue(), captured.out, captured.err.startswith("ERROR:")) == (
        2,
        "",
        "",
        True,
    )
