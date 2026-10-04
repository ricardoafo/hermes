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
- ``hermes config`` alone prints usage and exits 2.

Reference: openspec/changes/537-config-check/change.md.
"""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest

from hermes.entrypoints import config_check as check

_ROOT = Path(__file__).resolve().parents[3]
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
    shutil.copytree(_ROOT / "config", target)
    return target


def _run(argv: list[str], env: dict[str, str]) -> tuple[int, str]:
    stream = io.StringIO()
    code = check.main(argv, stream=stream, env=env)
    return code, stream.getvalue()


class TestCleanConfig:
    """A config the serve loop would accept reports clean."""

    def test_clean_config_exits_zero(self) -> None:
        """Invariant: no finding means exit 0 and an OK line."""
        code, out = _run(["--config-dir", str(_ROOT / "config")], {})
        assert code == 0
        assert out == "OK: config loads and the startup checks pass.\n"

    def test_json_clean_document(self) -> None:
        """Invariant: --json on a clean config is one document with no findings."""
        code, out = _run(["--config-dir", str(_ROOT / "config"), "--json"], {})
        assert code == 0
        assert json.loads(out) == {"ok": True, "findings": []}


class TestFindings:
    """Problems are named, never valued."""

    def test_prod_without_channels_names_each_secret(self) -> None:
        """Invariant: every unset channel variable is named when prod has no channel."""
        code, out = _run(["--config-dir", str(_ROOT / "config")], {"HERMES_ENV": "prod"})
        assert code == 1
        for name in _CHANNEL_VARS:
            assert f"FINDING missing_secret {name}: not set or empty\n" in out

    def test_set_secret_value_is_never_printed(self) -> None:
        """Invariant: a half-set channel names the unset variables, not the set value."""
        env = {"HERMES_ENV": "prod", "HERMES_TELEGRAM__BOT_TOKEN": _SECRET}
        code, out = _run(["--config-dir", str(_ROOT / "config")], env)
        assert code == 1
        assert _SECRET not in out
        assert "FINDING missing_secret HERMES_TELEGRAM__CHAT_ID" in out
        assert "FINDING missing_secret HERMES_TELEGRAM__BOT_TOKEN" not in out

    def test_set_secret_value_is_not_in_json(self) -> None:
        """Invariant: the --json document never carries a secret value either."""
        env = {"HERMES_ENV": "prod", "HERMES_SLACK__BOT_TOKEN": _SECRET}
        code, out = _run(["--config-dir", str(_ROOT / "config"), "--json"], env)
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
        code, out = _run(["--config-dir", str(_ROOT / "config")], env)
        assert code == 1
        assert out.startswith("FINDING startup startup: enabled strategies must be exactly")


class TestUnreadable:
    """A config that cannot be read exits 3."""

    def test_missing_directory_exits_three(self, tmp_path: Path) -> None:
        """Invariant: an absent config dir is exit 3 with an ERROR line."""
        code, out = _run(["--config-dir", str(tmp_path / "nope")], {})
        assert code == 3
        assert out.startswith(
            "ERROR: config could not be read: Config error: config file not found"
        )

    def test_malformed_yaml_exits_three(self, config_dir: Path) -> None:
        """Invariant: unparseable YAML is exit 3, one line, no document dump."""
        (config_dir / "fees.yaml").write_text("a: [unclosed\n", encoding="utf-8")
        code, out = _run(["--config-dir", str(config_dir)], {})
        assert code == 3
        assert out.startswith("ERROR: config could not be read: Config error: invalid YAML in")
        assert out.count("\n") == 1


class TestUsage:
    """Argument handling."""

    def test_bad_flag_exits_two(self) -> None:
        """Invariant: an unknown flag is exit 2 with the usage line."""
        code, out = _run(["--bogus"], {})
        assert code == 2
        assert out == f"ERROR: bad arguments. {check.USAGE}\n"

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
        code = check.main(["--config-dir", str(_ROOT / "config")], stream=stream)
        assert code == 1
        assert "FINDING missing_secret HERMES_SLACK__BOT_TOKEN" in stream.getvalue()
