"""Tests for ``hermes config fingerprint`` (#970).

Invariants protected here:

- it prints a fingerprint file listing the SHA-256 of each watched value the
  env file sets, and never a value;
- ``-`` reads the env file from stdin;
- the env file is read as systemd reads it, without expanding ``${VAR}``;
- an env file that sets no watched value is refused (exit 3), so an empty
  file can never replace the committed fingerprints;
- a missing ``--env-file``, a bad flag or an unreadable file exits 2 or 3
  with nothing on stdout.
"""

import hashlib
import io
from pathlib import Path

import pytest

from hermes.entrypoints import config_fingerprint as cmd

_PROD_BOT = "123456789:prod-bot-token-sekrit42"  # gitleaks:allow
_DSN = "https://pubkey-sekrit42@o1.ingest.sentry.io/2"  # gitleaks:allow


def _run(argv: list[str], stdin: str = "") -> tuple[int, str, str]:
    stream, err = io.StringIO(), io.StringIO()
    code = cmd.main(argv, stream=stream, err=err, stdin=io.StringIO(stdin))
    return code, stream.getvalue(), err.getvalue()


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def test_prints_the_digest_of_each_watched_value_and_no_value(tmp_path: Path) -> None:
    """Two watched values in prod's env file give two digests, in watched order."""
    env_file = tmp_path / "env.production"
    env_file.write_text(
        f"HERMES_ENV=prod\nHERMES_SENTRY__DSN={_DSN}\nHERMES_TELEGRAM__BOT_TOKEN={_PROD_BOT}\n"
        "HERMES_TELEGRAM__CHAT_ID=-100123\n",
        encoding="utf-8",
    )

    code, out, err = _run(["--env-file", str(env_file)])

    assert (code, err) == (0, "")
    assert out.endswith(
        "fingerprints:\n"
        f"  HERMES_TELEGRAM__BOT_TOKEN:\n  - {_sha(_PROD_BOT)}\n"
        f"  HERMES_SENTRY__DSN:\n  - {_sha(_DSN)}\n"
    )
    assert "sekrit42" not in out
    assert "-100123" not in out


def test_reads_the_env_file_from_stdin() -> None:
    """``--env-file -`` reads stdin, so a root-only file can be piped in."""
    code, out, _ = _run(["--env-file", "-"], stdin=f"HERMES_TELEGRAM__BOT_TOKEN={_PROD_BOT}\n")

    assert code == 0
    assert out.endswith(f"  HERMES_TELEGRAM__BOT_TOKEN:\n  - {_sha(_PROD_BOT)}\n")


def test_does_not_expand_variables() -> None:
    """``${VAR}`` stays literal, as systemd's EnvironmentFile keeps it."""
    code, out, _ = _run(["--env-file", "-"], stdin="HERMES_SLACK__BOT_TOKEN=xoxb-${HOME}\n")

    assert code == 0
    assert out.endswith(f"  - {_sha('xoxb-${HOME}')}\n")


def test_refuses_an_env_file_with_no_watched_value() -> None:
    """Nothing to fingerprint is exit 3, so an empty list never replaces the committed one."""
    code, out, err = _run(["--env-file", "-"], stdin="HERMES_ENV=prod\nHERMES_SLACK__BOT_TOKEN=\n")

    assert (code, out) == (3, "")
    assert err.startswith("ERROR: the env file sets none of HERMES_TELEGRAM__BOT_TOKEN, ")


@pytest.mark.parametrize(
    ("argv", "code", "message"),
    [
        ([], 2, f"ERROR: --env-file is required. {cmd.USAGE}\n"),
        (["--bogus"], 2, f"ERROR: bad arguments. {cmd.USAGE}\n"),
    ],
)
def test_usage_errors_exit_two(argv: list[str], code: int, message: str) -> None:
    """A missing --env-file or an unknown flag is a usage error with nothing printed."""
    assert _run(argv) == (code, "", message)


def test_an_unreadable_env_file_exits_three(tmp_path: Path) -> None:
    """A file that does not exist is exit 3, naming the reason."""
    code, out, err = _run(["--env-file", str(tmp_path / "missing")])

    assert (code, out, err) == (
        3,
        "",
        "ERROR: env file could not be read: No such file or directory\n",
    )


def test_help_names_every_watched_variable() -> None:
    """``-h`` lists what is fingerprinted and exits 0."""
    code, out, _ = _run(["-h"])

    assert code == 0
    assert out.startswith(f"{cmd.USAGE}\n")
    assert "HERMES_HEALTHCHECKS__ARCHIVE_CHECK_PING_URL" in out
