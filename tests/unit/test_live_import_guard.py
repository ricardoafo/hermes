"""Live ticker prices stay in the console (issue #713, change 713-live-ticker D-6).

Invariants:

- importing the engine, ledger, supervisor, bot and notification entry modules
  never loads the live-price modules (checked in a fresh interpreter, so no
  other test's imports can hide a leak);
- the import-linter contract ``live-prices-are-console-only`` forbids those
  layers from importing the live-price modules, directly or indirectly.
"""

import json
import subprocess
import sys
import tomllib
from typing import Final

from tests.support.paths import REPO_ROOT

_LIVE: Final = (
    "hermes.application.console.live",
    "hermes.ports.live_prices",
    "hermes.entrypoints.tui_live",
)
_GUARDED: Final = (
    "hermes.application.order_router",
    "hermes.application.strategy_protocol",
    "hermes.application.portfolio",
    "hermes.application.ledger",
    "hermes.application.supervisor",
    "hermes.application.notification_router",
    "hermes.application.signal_notifier",
    "hermes.application.messages",
    "hermes.adapters.notifier",
    "hermes.adapters.telegram_bot",
    "hermes.entrypoints.serve",
    "hermes.entrypoints.supervisor",
    "hermes.entrypoints.bot",
    "hermes.entrypoints.daily_line",
)
_PROBE: Final = """
import importlib, json, sys
for name in sys.argv[1].split(","):
    importlib.import_module(name)
print(json.dumps(sorted(m for m in sys.modules if m in sys.argv[2].split(","))))
"""


def test_engine_supervisor_and_notification_modules_never_load_the_live_modules() -> None:
    """A fresh interpreter importing every guarded module has none of the live modules loaded."""
    result = subprocess.run(  # noqa: S603 - fixed argv, the running interpreter
        [sys.executable, "-c", _PROBE, ",".join(_GUARDED), ",".join(_LIVE)],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    assert json.loads(result.stdout.strip().splitlines()[-1]) == []


def test_import_linter_contract_forbids_the_live_modules_to_every_guarded_layer() -> None:
    """The contract lists all three live modules and covers every guarded module and the domain."""
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    contracts = {c["name"]: c for c in config["tool"]["importlinter"]["contracts"]}
    contract = contracts["live-prices-are-console-only"]
    assert (contract["type"], sorted(contract["forbidden_modules"])) == ("forbidden", sorted(_LIVE))
    assert set(_GUARDED) | {"hermes.domain"} <= set(contract["source_modules"])
