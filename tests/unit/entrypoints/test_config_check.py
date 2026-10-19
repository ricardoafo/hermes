"""Tests for ``hermes config check`` (#537).

Verifies:

- a clean config exits 0 and says so;
- a prod config with no channel credentials exits 1 and names every unset
  secret variable;
- a secret's value, set or half-set, never reaches the output;
- an invalid field exits 1 and is named by path, without its value;
- a missing directory or malformed YAML exits 3;
- bad arguments exit 2, ``-h`` exits 0;
- ``--json`` emits one document;
- ``hermes config`` alone prints usage and exits 2;
- a setting that breaks its tier's rules is a ``tier`` finding, named and
  never valued, and every such problem is listed (#970);
- ``--env-file`` checks that file alone, from a path or stdin, in place of
  the process environment (#970).

Reference: openspec/archive/537-config-check/change.md.
"""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest

from hermes.entrypoints import config_check as check
from tests.support.paths import REPO_ROOT

_SECRET = "s3cr3t-token-value-9f2c"  # gitleaks:allow  # noqa: S105
_CHANNEL_VARS = [
    "HERMES_TELEGRAM__BOT_TOKEN",
    "HERMES_TELEGRAM__CHAT_ID",
    "HERMES_SLACK__BOT_TOKEN",
    "HERMES_SLACK__DEFAULT_CHANNEL",
]


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """Copy the shipped config into tmp_path, safe to corrupt."""
    target = tmp_path / "config"
    shutil.copytree(REPO_ROOT / "config", target)
    return target


def _run(
    argv: list[str],
    env: dict[str, str],
    err: io.StringIO | None = None,
    stdin: str = "",
) -> tuple[int, str]:
    stream = io.StringIO()
    code = check.main(argv, stream=stream, env=env, err=err, stdin=io.StringIO(stdin))
    return code, stream.getvalue()


class TestCleanConfig:
    """A config the serve loop would accept reports clean."""

    def test_clean_config_exits_zero(self) -> None:
        """Invariant: no finding means exit 0 and an OK line."""
        code, out = _run(["--config-dir", str(REPO_ROOT / "config")], {})
        assert code == 0
        assert out == "OK: config loads and the startup checks pass.\n"

    def test_json_clean_document(self) -> None:
        """Invariant: --json on a clean config is one document with no findings."""
        code, out = _run(["--config-dir", str(REPO_ROOT / "config"), "--json"], {})
        assert code == 0
        assert json.loads(out) == {"ok": True, "findings": []}


class TestFindings:
    """Problems are named, never valued."""

    def test_prod_without_channels_names_each_secret(self) -> None:
        """Invariant: every unset channel variable is named when prod has no channel."""
        code, out = _run(["--config-dir", str(REPO_ROOT / "config")], {"HERMES_ENV": "prod"})
        assert code == 1
        for name in _CHANNEL_VARS:
            assert f"FINDING missing_secret {name}: not set or empty\n" in out

    def test_set_secret_value_is_never_printed(self) -> None:
        """Invariant: a half-set channel names the unset variables, not the set value."""
        env = {"HERMES_ENV": "prod", "HERMES_TELEGRAM__BOT_TOKEN": _SECRET}
        code, out = _run(["--config-dir", str(REPO_ROOT / "config")], env)
        assert code == 1
        assert _SECRET not in out
        assert "FINDING missing_secret HERMES_TELEGRAM__CHAT_ID" in out
        assert "FINDING missing_secret HERMES_TELEGRAM__BOT_TOKEN" not in out

    def test_set_secret_value_is_not_in_json(self) -> None:
        """Invariant: the --json document never carries a secret value either."""
        env = {"HERMES_ENV": "prod", "HERMES_SLACK__BOT_TOKEN": _SECRET}
        code, out = _run(["--config-dir", str(REPO_ROOT / "config"), "--json"], env)
        assert code == 1
        assert _SECRET not in out
        names = {f["name"] for f in json.loads(out)["findings"] if f["kind"] == "missing_secret"}
        assert names == {
            "HERMES_TELEGRAM__BOT_TOKEN",
            "HERMES_TELEGRAM__CHAT_ID",
            "HERMES_SLACK__DEFAULT_CHANNEL",
        }

    def test_invalid_field_is_named_without_its_value(self, config_dir: Path) -> None:
        """Invariant: a rejected field shows by path and error type, not by value."""
        path = config_dir / "market_data.yaml"
        path.write_text(
            path.read_text(encoding="utf-8").replace("warmup_bars_1d: 300", "warmup_bars_1d: 7731"),
            encoding="utf-8",
        )
        code, out = _run(["--config-dir", str(config_dir)], {})
        assert code == 1
        assert out.startswith("FINDING invalid_value warmup_bars_1d: ")
        assert "7731" not in out

    def test_startup_refusal_is_a_finding(self) -> None:
        """Invariant: a roster check_startup refuses exits 1 as a startup finding."""
        env = {"HERMES_STRATEGIES__STRATEGIES__0__ENABLED": "false"}
        code, out = _run(["--config-dir", str(REPO_ROOT / "config")], env)
        assert code == 1
        assert out.startswith("FINDING startup startup: enabled strategies must be exactly")


class TestUnreadable:
    """A config that cannot be read exits 3."""

    def test_missing_directory_exits_three(self, tmp_path: Path) -> None:
        """Invariant: an absent config dir is exit 3 with an ERROR line."""
        err = io.StringIO()
        code, out = _run(["--config-dir", str(tmp_path / "nope")], {}, err)
        assert (code, out) == (3, "")
        assert err.getvalue().startswith(
            "ERROR: config could not be read: Config error: config file not found"
        )

    def test_malformed_yaml_exits_three(self, config_dir: Path) -> None:
        """Invariant: unparseable YAML is exit 3, one line, no document dump."""
        (config_dir / "fees.yaml").write_text("a: [unclosed\n", encoding="utf-8")
        err = io.StringIO()
        code, out = _run(["--config-dir", str(config_dir)], {}, err)
        assert (code, out) == (3, "")
        assert err.getvalue().startswith(
            "ERROR: config could not be read: Config error: invalid YAML in"
        )
        assert err.getvalue().count("\n") == 1


class TestUsage:
    """Argument handling."""

    def test_bad_flag_exits_two(self) -> None:
        """Invariant: an unknown flag is exit 2 with the usage line."""
        err = io.StringIO()
        code, out = _run(["--bogus"], {}, err)
        assert (code, out) == (2, "")
        assert err.getvalue() == f"ERROR: bad arguments. {check.USAGE}\n"

    def test_help_exits_zero_and_lists_exit_codes(self) -> None:
        """Invariant: -h prints usage with the exit codes and returns 0."""
        code, out = _run(["-h"], {})
        assert code == 0
        assert "Exit codes: 0 clean, 1 findings, 2 usage, 3 config could not be read." in out


class TestOsEnvironment:
    """Without an injected env, os.environ is read as serve reads it."""

    def test_os_environ_used_when_env_is_none(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Invariant: prod in os.environ without channels is found."""
        monkeypatch.setenv("HERMES_ENV", "prod")
        for name in _CHANNEL_VARS:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.chdir(tmp_path)
        stream = io.StringIO()
        code = check.main(["--config-dir", str(REPO_ROOT / "config")], stream=stream)
        assert code == 1
        assert "FINDING missing_secret HERMES_SLACK__BOT_TOKEN" in stream.getvalue()


class TestTierFindings:
    """A setting that breaks its tier's rules is named, never valued (#970)."""

    def test_loopback_endpoint_in_staging_is_a_tier_finding(self) -> None:
        """Invariant: a YAML-backed endpoint on loopback in staging names its field."""
        env = {
            "HERMES_ENV": "staging",
            "HERMES_MARKET_DATA__BASE_URL": f"http://127.0.0.1:9000/{_SECRET}",
        }
        code, out = _run(["--config-dir", str(REPO_ROOT / "config")], env)
        assert code == 1
        assert out == (
            "FINDING tier market_data.base_url: points at a loopback host, "
            "which only dev, qa, sandbox may use\n"
        )

    def test_endpoint_and_key_problems_are_listed_together(self) -> None:
        """Invariant: a YAML endpoint problem does not hide an env-only one."""
        env = {
            "HERMES_ENV": "staging",
            "HERMES_SENTRY__DSN": f"http://{_SECRET}@127.0.0.1:9/1",
            "HERMES_KRAKEN__API_SECRET": _SECRET,
        }
        code, out = _run(["--config-dir", str(REPO_ROOT / "config"), "--json"], env)
        assert code == 1
        assert _SECRET not in out
        assert [(f["kind"], f["name"]) for f in json.loads(out)["findings"]] == [
            ("tier", "sentry.dsn"),
            ("tier", "HERMES_KRAKEN__API_SECRET"),
        ]


class TestEnvFile:
    """``--env-file`` checks one env file, alone, from a path or stdin (#970)."""

    def test_stdin_env_file_with_a_trading_key_names_it(self) -> None:
        """Invariant: a staging env file on stdin holding a trading key is refused by name."""
        text = f'HERMES_ENV="staging"\nHERMES_BINANCE__API_KEY="{_SECRET}"\n'
        code, out = _run(
            ["--config-dir", str(REPO_ROOT / "config"), "--env-file", "-"], {}, stdin=text
        )
        assert code == 1
        assert (
            out
            == "FINDING tier HERMES_BINANCE__API_KEY: is set, but staging holds no trading key\n"
        )

    def test_env_file_replaces_the_process_environment(self) -> None:
        """Invariant: only the file counts, as for a unit with no Environment= line."""
        env = {"HERMES_ENV": "staging", "HERMES_KRAKEN__API_KEY": _SECRET}
        code, out = _run(
            ["--config-dir", str(REPO_ROOT / "config"), "--env-file", "-"],
            env,
            stdin="HERMES_ENV=staging\n",
        )
        assert (code, out) == (0, "OK: config loads and the startup checks pass.\n")

    def test_env_file_by_path_is_read(self, tmp_path: Path) -> None:
        """Invariant: a path works like stdin, so an operator can check a file by hand."""
        path = tmp_path / "env.staging"
        path.write_text("HERMES_ENV=staging\nHERMES_SLACK__API_BASE=http://[::1]:1\n", "utf-8")
        code, out = _run(["--config-dir", str(REPO_ROOT / "config"), "--env-file", str(path)], {})
        assert code == 1
        assert out.startswith("FINDING tier HERMES_SLACK__API_BASE: points at a loopback host")

    def test_env_file_is_not_interpolated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Invariant: ${VAR} stays literal, as systemd leaves it, so no value is pulled in."""
        monkeypatch.setenv("HOST", "127.0.0.1")
        code, out = _run(
            ["--config-dir", str(REPO_ROOT / "config"), "--env-file", "-"],
            {},
            stdin="HERMES_ENV=staging\nHERMES_TELEGRAM__API_BASE=http://${HOST}:1\n",
        )
        assert (code, out) == (0, "OK: config loads and the startup checks pass.\n")

    def test_missing_env_file_exits_three(self, tmp_path: Path) -> None:
        """Invariant: an absent env file is exit 3, never an empty environment checked clean."""
        err = io.StringIO()
        code, out = _run(
            ["--config-dir", str(REPO_ROOT / "config"), "--env-file", str(tmp_path / "nope")],
            {},
            err,
        )
        assert (code, out) == (3, "")
        assert err.getvalue() == "ERROR: env file could not be read: No such file or directory\n"

    def test_env_file_that_is_not_utf8_exits_three(self, tmp_path: Path) -> None:
        """Invariant: undecodable bytes are exit 3, never a traceback read as findings."""
        path = tmp_path / "env.staging"
        path.write_bytes(b"HERMES_ENV=staging\nHERMES_SLACK__API_BASE=\xff\xfe\n")
        err = io.StringIO()
        code, out = _run(
            ["--config-dir", str(REPO_ROOT / "config"), "--env-file", str(path)], {}, err
        )
        assert (code, out) == (3, "")
        assert err.getvalue() == "ERROR: env file could not be read: not UTF-8\n"
