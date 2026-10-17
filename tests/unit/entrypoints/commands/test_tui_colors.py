"""``hermes tui`` draws in true colour unless told otherwise (#992).

Invariants:

- with no explicit choice the console draws in 24-bit colour, so its palette
  survives an ssh session that does not forward ``COLORTERM``;
- an explicit ``TEXTUAL_COLOR_SYSTEM`` and the plain ``linux`` and ``dumb``
  terminals keep the library's own detection;
- ``hermes tui`` sets the choice before it imports the UI library, so the
  app it opens draws with it.
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


def test_hermes_tui_draws_in_true_colour_over_ssh() -> None:
    """In a fresh interpreter, ``hermes tui``'s own start-up gives the app a 24-bit console.

    The UI library reads the choice once, at import, so the choice must be set
    before ``run_tui`` imports the console module. This runs ``run_tui`` with its
    default runner in a child process with an ssh-like environment (no
    COLORTERM). The console module is replaced by a stand-in that imports the
    UI library at the moment ``run_tui`` imports it, as the real module does,
    and records the colour system instead of opening the screen.
    """
    code = (
        "import io, os, sys, types\n"
        "def _attr(name):\n"
        "    from textual.app import App\n"
        "    def run_home(*args, **kwargs):\n"
        "        print(App().console.color_system)\n"
        "    return run_home\n"
        "stand_in = types.ModuleType('hermes.entrypoints.tui')\n"
        "stand_in.__getattr__ = _attr\n"
        "sys.modules['hermes.entrypoints.tui'] = stand_in\n"
        "from hermes.entrypoints.commands.tui import run_tui\n"
        "run_tui([], io.StringIO(), demo_flag='ok-two-positions', env=dict(os.environ))\n"
    )
    env = {k: v for k, v in os.environ.items() if k not in {"COLORTERM", "TEXTUAL_COLOR_SYSTEM"}}
    env["TERM"] = "xterm-256color"
    result = subprocess.run(  # noqa: S603 - fixed argv, the test's own interpreter
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True
    )
    assert result.stdout.strip() == "truecolor"
