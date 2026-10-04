"""`--demo SCENARIO` works after the console command as well as before it (#610)."""

from __future__ import annotations

import io

import pytest

from hermes.entrypoints import cli as cli_module


def _run_err(args: list[str]) -> tuple[int, str]:
    """Run the CLI for a refusal: the message is on stderr and stdout stays empty."""
    out, err = io.StringIO(), io.StringIO()
    code = cli_module.main(args, stream=out, err=err)
    assert out.getvalue() == ""
    return code, err.getvalue()


def _run(args: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    return cli_module.main(args, stream=out), out.getvalue()


@pytest.mark.parametrize("command", ["positions", "activity", "status"])
def test_demo_after_the_command_equals_demo_before_it(command: str) -> None:
    """Invariant: `hermes CMD --demo S` prints exactly what `hermes --demo S CMD` prints."""
    before = _run(["--demo", "ok-two-positions", command])
    after = _run([command, "--demo", "ok-two-positions"])
    assert before[0] == 0
    assert after == before


def test_demo_equals_form_after_the_command() -> None:
    """Invariant: the `--demo=S` spelling is accepted after the command too."""
    assert _run(["positions", "--demo=ok-two-positions"]) == _run(
        ["--demo", "ok-two-positions", "positions"]
    )


def test_late_demo_keeps_the_commands_own_arguments() -> None:
    """Invariant: stripping --demo leaves the command's other arguments intact."""
    code, out = _run(["positions", "--json", "--demo", "ok-two-positions"])
    assert code == 0
    assert out.lstrip().startswith("{")


def test_late_demo_without_a_scenario_is_a_usage_error() -> None:
    """Invariant: a trailing bare --demo exits 2 and lists the scenarios."""
    code, out = _run_err(["positions", "--demo"])
    assert code == 2
    assert "--demo needs a scenario name" in out


def test_demo_before_and_after_is_refused() -> None:
    """Invariant: two --demo options are ambiguous, so exit 2."""
    code, out = _run_err(["--demo", "ok-two-positions", "positions", "--demo", "ok-two-positions"])
    assert (code, "give --demo once" in out) == (2, True)


def test_late_demo_is_not_stripped_from_a_non_console_command() -> None:
    """Invariant: serve keeps its own parser's view of --demo (it rejects it, never runs)."""
    code, _ = _run(["serve", "--demo", "ok-two-positions"])
    assert code == 2
