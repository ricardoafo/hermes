"""Tests for the ``hermes config`` command dispatcher (#537).

Verifies bare ``hermes config`` and unknown subcommands exit 2, ``-h`` exits 0
and lists both subcommands, ``check`` reaches
:mod:`hermes.entrypoints.config_check` and ``fingerprint`` reaches
:mod:`hermes.entrypoints.config_fingerprint` (#970).

Reference: openspec/archive/537-config-check/change.md.
"""

from __future__ import annotations

import io

from hermes.entrypoints import cli
from hermes.entrypoints import config_fingerprint as fingerprint
from hermes.entrypoints.commands import config as config_command


class TestConfigDispatch:
    """The config command routes its subcommands."""

    def test_config_without_subcommand_exits_two(self) -> None:
        """Invariant: bare hermes config prints usage and returns 2."""
        stream, err = io.StringIO(), io.StringIO()
        assert cli.main(["config"], stream=stream, err=err) == 2
        assert (stream.getvalue(), err.getvalue()) == (
            "",
            f"ERROR: expected a subcommand. {config_command.USAGE}\n",
        )

    def test_config_help_exits_zero(self) -> None:
        """Invariant: hermes config -h prints usage and returns 0."""
        stream = io.StringIO()
        assert config_command.COMMAND.run(["-h"], stream=stream) == 0
        assert stream.getvalue() == (
            "Usage: hermes config check [--config-dir DIR] [--env-file PATH|-] [--json]\n"
            "       hermes config fingerprint --env-file PATH|-\n"
        )

    def test_config_unknown_subcommand_exits_two(self) -> None:
        """Invariant: an unknown subcommand is exit 2."""
        stream, err = io.StringIO(), io.StringIO()
        assert config_command.COMMAND.run(["nope"], stream=stream, err=err) == 2
        assert (stream.getvalue(), err.getvalue().startswith("ERROR: expected a subcommand.")) == (
            "",
            True,
        )

    def test_config_check_dispatches(self) -> None:
        """Invariant: config check reaches the checker (a bad flag gives its exit 2)."""
        stream, err = io.StringIO(), io.StringIO()
        assert config_command.COMMAND.run(["check", "--bogus"], stream=stream, err=err) == 2
        assert (stream.getvalue(), err.getvalue().startswith("ERROR: bad arguments.")) == ("", True)

    def test_config_fingerprint_dispatches(self) -> None:
        """Invariant: config fingerprint reaches the fingerprinter (no --env-file gives exit 2)."""
        stream, err = io.StringIO(), io.StringIO()
        assert config_command.COMMAND.run(["fingerprint"], stream=stream, err=err) == 2
        assert (stream.getvalue(), err.getvalue()) == (
            "",
            f"ERROR: --env-file is required. {fingerprint.USAGE}\n",
        )
