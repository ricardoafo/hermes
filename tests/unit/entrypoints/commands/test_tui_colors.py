"""``hermes tui`` draws in true colour unless told otherwise (#992).

Invariants:

- with no explicit choice the console draws in 24-bit colour, so its palette
  survives an ssh session that does not forward ``COLORTERM``;
- an explicit ``TEXTUAL_COLOR_SYSTEM`` and the plain ``linux`` and ``dumb``
  terminals keep the library's own detection;
- the choice reaches the UI library when it is imported after it, as
  ``hermes tui`` imports it.
"""

import os
import subprocess
import sys

import pytest

from hermes.entrypoints.commands.tui import apply_console_colors, console_color_system

_SSH_SESSION = {"TERM": "xterm-256color"}


def test_an_ssh_session_without_colorterm_gets_true_colour() -> None:
    """Over plain ssh only TERM arrives; the console still draws 24-bit colour."""
    assert console_color_system(_SSH_SESSION) == "truecolor"


def test_an_explicit_choice_is_kept() -> None:
    """An operator who set TEXTUAL_COLOR_SYSTEM keeps that depth."""
    environ = {"TERM": "xterm-256color", "TEXTUAL_COLOR_SYSTEM": "256"}
    apply_console_colors(dict(environ), environ)
    assert environ["TEXTUAL_COLOR_SYSTEM"] == "256"


@pytest.mark.parametrize("term", ["linux", "dumb"])
def test_a_plain_terminal_keeps_auto_detection(term: str) -> None:
    """The Linux console and dumb terminals draw no 24-bit colour, so nothing is forced."""
    environ = {"TERM": term}
    apply_console_colors(dict(environ), environ)
    assert "TEXTUAL_COLOR_SYSTEM" not in environ


def test_apply_writes_true_colour_for_an_ssh_session() -> None:
    """The choice lands in the process environment the UI library reads."""
    environ = dict(_SSH_SESSION)
    apply_console_colors(_SSH_SESSION, environ)
    assert environ == {"TERM": "xterm-256color", "TEXTUAL_COLOR_SYSTEM": "truecolor"}


def test_the_ui_library_draws_with_the_choice_when_imported_after_it() -> None:
    """In a fresh interpreter, as ``hermes tui`` runs, the app's console is 24-bit.

    The UI library reads the choice once, at import, so this runs in a child
    process with an ssh-like environment (no COLORTERM).
    """
    code = (
        "import os\n"
        "from hermes.entrypoints.commands.tui import apply_console_colors\n"
        "apply_console_colors(dict(os.environ), os.environ)\n"
        "from textual.app import App\n"
        "print(App().console.color_system)\n"
    )
    env = {k: v for k, v in os.environ.items() if k not in {"COLORTERM", "TEXTUAL_COLOR_SYSTEM"}}
    env["TERM"] = "xterm-256color"
    result = subprocess.run(  # noqa: S603 - fixed argv, the test's own interpreter
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True
    )
    assert result.stdout.strip() == "truecolor"
