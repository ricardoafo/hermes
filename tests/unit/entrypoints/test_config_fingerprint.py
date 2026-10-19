"""Tests for ``hermes config fingerprint`` (#970).

Invariants protected here:

- it prints the file's YAML for each fingerprinted value prod's env file sets,
  and never a value;
- it needs an env file, read from a path or stdin, and exits 3 when the
  file cannot be read;
- an env file with none of the values is exit 1, so a wrong file is noticed.
"""

import io
from pathlib import Path

from hermes.entrypoints import config_fingerprint
from hermes.infrastructure.config.prod_fingerprints import fingerprint

_TOKEN = "123456:prod-bot-token-sekrit42"  # noqa: S105  # gitleaks:allow


def test_prints_the_fingerprint_of_each_set_value_from_stdin() -> None:
    """The output pastes straight into config/prod_fingerprints.yaml; the token never shows."""
    stream = io.StringIO()
    stdin = io.StringIO(f"HERMES_ENV=prod\nHERMES_TELEGRAM__BOT_TOKEN={_TOKEN}\n")

    code = config_fingerprint.main(["--env-file", "-"], stream=stream, stdin=stdin)

    assert (code, stream.getvalue()) == (
        0,
        f"fingerprints:\n  HERMES_TELEGRAM__BOT_TOKEN:\n    - {fingerprint(_TOKEN)}\n",
    )


def test_reads_an_env_file_by_path(tmp_path: Path) -> None:
    """A root-only file is read directly when the operator runs it with sudo."""
    env_file = tmp_path / "env.production"
    env_file.write_text("HERMES_SENTRY__DSN=https://k@o1.example/2\n", encoding="utf-8")
    stream = io.StringIO()

    assert config_fingerprint.main(["--env-file", str(env_file)], stream=stream) == 0
    assert stream.getvalue() == (
        f"fingerprints:\n  HERMES_SENTRY__DSN:\n    - {fingerprint('https://k@o1.example/2')}\n"
    )


def test_an_env_file_without_any_value_is_exit_one() -> None:
    """Pointing it at the wrong file prints nothing and says so."""
    stream, err = io.StringIO(), io.StringIO()

    code = config_fingerprint.main(
        ["--env-file", "-"], stream=stream, err=err, stdin=io.StringIO("HERMES_ENV=prod\n")
    )

    assert (code, stream.getvalue(), err.getvalue()) == (
        1,
        "",
        "ERROR: the env file sets none of the fingerprinted variables\n",
    )


def test_an_unreadable_env_file_is_exit_three(tmp_path: Path) -> None:
    """A missing file is named as unreadable, not as an empty one."""
    err = io.StringIO()

    code = config_fingerprint.main(
        ["--env-file", str(tmp_path / "absent")], stream=io.StringIO(), err=err
    )

    assert (code, err.getvalue()) == (
        3,
        "ERROR: env file could not be read: No such file or directory\n",
    )


def test_without_env_file_is_a_usage_error() -> None:
    """The process environment is never fingerprinted by accident."""
    err = io.StringIO()

    assert config_fingerprint.main([], stream=io.StringIO(), err=err) == 2
    assert err.getvalue() == f"ERROR: --env-file is required. {config_fingerprint.USAGE}\n"
