"""Tests for the ``hermes config`` command dispatcher (#537).

Verifies bare ``hermes config`` and unknown subcommands exit 2, ``-h`` exits 0,
``check`` reaches :mod:`hermes.entrypoints.config_check` and ``fingerprint``
reaches :mod:`hermes.entrypoints.config_fingerprint` (#970).

Reference: openspec/changes/537-config-check/change.md.
"""

from __future__ import annotations

import io

from hermes.entrypoints import cli
from hermes.entrypoints import config_check as check
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
        assert stream.getvalue() == f"{config_command.USAGE}\n"

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

    def test_config_usage_names_both_subcommands(self) -> None:
        """Invariant: the usage lists check and fingerprint, each with its flags."""
        assert (
            f"{check.USAGE}\n       hermes config fingerprint --env-file PATH|-"
        ) == config_command.USAGE

    def test_config_fingerprint_dispatches(self) -> None:
        """Invariant: config fingerprint reaches its command (no --env-file gives its exit 2)."""
        stream, err = io.StringIO(), io.StringIO()
        assert config_command.COMMAND.run(["fingerprint"], stream=stream, err=err) == 2
        assert (stream.getvalue(), err.getvalue().startswith("ERROR: --env-file is required.")) == (
            "",
            True,
        )
