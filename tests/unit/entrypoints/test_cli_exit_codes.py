"""Guard: every command's "no ledger" and "bad config" paths exit with the documented code (#1187).

``hermes.entrypoints._cli`` documents one code per case: 3 for no ledger at
``--db``, 3 for a config that cannot be loaded. Each command is run through
the real ``hermes`` dispatcher, as the operator and the systemd units run it.

A command that adds ``--db`` or ``--config-dir`` through the shared helpers
must appear in a table below, or in an exemption with its reason, so a new
command cannot skip the guard.

Invariants under test:

- each ledger-reading command exits 3 when there is no file at ``--db``;
- each config-reading command exits 3 when ``--config-dir`` holds no config;
- every module using the helpers is covered or exempted with a reason.
"""

import io
import re
from pathlib import Path

import pytest

from hermes.entrypoints import cli as cli_module
from hermes.entrypoints._cli import EXIT_CONFIG, EXIT_NO_LEDGER
from tests.support.paths import REPO_ROOT

_CONFIG = str(REPO_ROOT / "config")
_ENTRYPOINTS = REPO_ROOT / "src" / "hermes" / "entrypoints"

# Command and its arguments before --db and --config-dir are appended.
_NO_LEDGER: dict[str, list[str]] = {
    "activity": ["activity"],
    "anomalies": ["anomalies"],
    "archive-check": ["archive-check", "--config-dir", _CONFIG],
    "fx": ["fx", "backfill", "--config-dir", _CONFIG],
    "ledger": ["ledger", "verify"],
    "migrate": ["migrate", "--status"],
    "parity check": ["parity", "check", "--offline", "--config-dir", _CONFIG],
    "parity window": ["parity", "window", "--offline", "--config-dir", _CONFIG],
    "reconcile": ["reconcile", "--config-dir", _CONFIG],
    "risk-inputs": ["risk-inputs", "--config-dir", _CONFIG],
    "status": ["status", "--config-dir", _CONFIG],
    "tax": ["tax", "--config-dir", _CONFIG],
    "tax-filing": ["tax-filing", "--year", "2025", "--config-dir", _CONFIG],
    "tax-pack": ["tax-pack", "--year", "2025", "--config-dir", _CONFIG],
}

# Commands that add --db but where a missing ledger is not an error exit.
_LEDGER_OPTIONAL: dict[str, str] = {
    "bot": "a long-running service: each reply reports the missing ledger, it never exits on it",
    "supervisor": "each tool call answers that the ledger is not available",
    "certify": "no ledger means no shadow round trips yet; the gate still judges the evidence",
    "shadow-start": "day 0 starts on a fresh ledger, which it creates",
    "version": "reports `no ledger` and always exits 0",
    "serve": "refused at start-up with exit 1 so systemd keeps retrying; see change.md Risks",
}

# Command and its arguments before --config-dir is appended.
_BAD_CONFIG: dict[str, list[str]] = {
    "archive-check": ["archive-check"],
    "bot": ["bot"],
    "certify": ["certify", "swing-donchian-4h"],
    "config": ["config", "check"],
    "failover-check": ["failover-check"],
    "fx": ["fx", "backfill"],
    "parity check": ["parity", "check", "--offline"],
    "parity window": ["parity", "window", "--offline"],
    "reconcile": ["reconcile"],
    "risk-inputs": ["risk-inputs"],
    "serve": ["serve"],
    "shadow-start": ["shadow-start", "swing-donchian-4h"],
    "status": ["status"],
    "supervisor": ["supervisor", "list"],
    "tax": ["tax"],
    "tax-filing": ["tax-filing", "--year", "2025"],
    "tax-pack": ["tax-pack", "--year", "2025"],
}

_MODULE_COMMAND = {"config_check": "config", "commands/serve": "serve"}


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the operator's profile and notifiers out; give the bot the settings it checks first."""
    for name in ("HERMES_CONFIG_DIR", "HERMES_DB_PATH", "HERMES_HEALTHCHECKS__PING_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HERMES_TAXPAYER_PROFILE", str(tmp_path / "no-profile.yaml"))
    monkeypatch.setenv("HERMES_TELEGRAM__BOT_TOKEN", "123:test")  # gitleaks:allow
    monkeypatch.setenv("HERMES_TELEGRAM__CHAT_ID", "1")


def _run(argv: list[str]) -> int:
    return cli_module.main(argv, stream=io.StringIO(), err=io.StringIO())


@pytest.mark.parametrize("case", sorted(_NO_LEDGER))
def test_no_ledger_exits_with_the_documented_code(case: str, tmp_path: Path) -> None:
    """No file at --db exits 3 in every command that reads the ledger (#1187)."""
    argv = [*_NO_LEDGER[case], "--db", str(tmp_path / "absent.sqlite3")]
    if "tax-pack" in argv:
        argv += ["--out", str(tmp_path / "packs")]
    assert _run(argv) == EXIT_NO_LEDGER, f"`hermes {case}` with no ledger"


@pytest.mark.parametrize("case", sorted(_BAD_CONFIG))
def test_bad_config_exits_with_the_documented_code(case: str, tmp_path: Path) -> None:
    """A --config-dir with no config exits 3 in every command that loads it (#1187)."""
    ledger = tmp_path / "ledger.sqlite3"
    ledger.touch()
    argv = [*_BAD_CONFIG[case], "--config-dir", str(tmp_path / "missing")]
    if case not in {"config", "failover-check"}:
        argv += ["--db", str(ledger)]
    assert _run(argv) == EXIT_CONFIG, f"`hermes {case}` with a bad config"


def _helper_users(helper: str) -> set[str]:
    """Command names of the entrypoint modules that call ``helper``."""
    users = set()
    for path in _ENTRYPOINTS.rglob("*.py"):
        if re.search(rf"\b{helper}\(", path.read_text(encoding="utf-8")) is None:
            continue
        module = path.relative_to(_ENTRYPOINTS).with_suffix("").as_posix()
        if module == "_cli":
            continue
        users.add(_MODULE_COMMAND.get(module, module.replace("_", "-")))
    return users


def test_every_db_command_is_guarded_or_exempted() -> None:
    """A command adding --db is in the no-ledger table or exempted with a reason (#1187)."""
    covered = {case.split()[0] for case in _NO_LEDGER} | set(_LEDGER_OPTIONAL)
    assert _helper_users("add_db_arg") - covered == set(), "unguarded --db commands"


def test_every_config_command_is_guarded() -> None:
    """A command adding --config-dir is in the bad-config table (#1187)."""
    covered = {case.split()[0] for case in _BAD_CONFIG}
    assert _helper_users("add_config_dir_arg") - covered == set(), "unguarded config commands"
