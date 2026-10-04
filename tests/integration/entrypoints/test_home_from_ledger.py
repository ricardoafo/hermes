"""From a recorded ledger to ``hermes tui`` and ``hermes activity`` output (#546, slices 2-3).

The ledger is written through the real ledger API (``SqliteLedger``: capital
seed and its EUR pricing, bar checks, the entry signal, a buy and a partial
sale with their EUR values, three portfolio marks). The commands then read it
read-only, with no demo scenario, exactly as on the VPS.

Reference: openspec/changes/live-home-screen/change.md (A-1.1, A-2.1, A-4.1).
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
from datetime import timedelta
from pathlib import Path

import pytest

from hermes.application.console import ConsoleReadModel
from hermes.entrypoints.activity import run_activity
from hermes.entrypoints.commands.tui import run_tui
from hermes.entrypoints.tui import HomeApp
from tests.unit.adapters.console._ledger_builder import T0, home_ledger

pytestmark = pytest.mark.integration

_READ_AT = T0 + timedelta(hours=9)
_CARDS = ("demo-tag", "portfolio", "positions", "shadow")


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    """Return the recorded home example ledger."""
    path = tmp_path / "hermes.sqlite3"
    home_ledger(path)
    return path


def test_activity_shows_the_recorded_fills(ledger: Path) -> None:
    """The buy and the sale appear in EUR, the sale with its realised net (A-2.1)."""
    stream = io.StringIO()
    rc = run_activity(["--db", str(ledger), "--utc"], stream, env={}, now=_READ_AT)
    lines = [line.strip() for line in stream.getvalue().splitlines() if line.strip()]
    assert (rc, lines[0]) == (0, "Today")
    assert ("closed BTC at €44,000" in lines[1], "+€39.16" in lines[1]) == (True, True)
    assert lines[2].endswith("opened BTC at €40,000")


def test_activity_json_from_the_ledger(ledger: Path) -> None:
    """The JSON feed carries the fill prices in EUR and the closed row's net, no demo tag."""
    stream = io.StringIO()
    rc = run_activity(["--db", str(ledger), "--json", "--utc"], stream, env={}, now=_READ_AT)
    data = json.loads(stream.getvalue())
    items = data["days"][0]["items"]
    assert (rc, data["demo_scenario"]) == (0, None)
    assert [(i["kind"], i["price_eur"], i.get("net_eur")) for i in items] == [
        ("closed", "44000.0000", "39.16000000000000000000000000"),
        ("opened", "40000.0000", None),
    ]


def test_activity_missing_ledger_exits_3(tmp_path: Path) -> None:
    """No ledger file at --db: exit 3 naming the path, and nothing is created."""
    path = tmp_path / "absent.sqlite3"
    stream = io.StringIO()
    rc = run_activity(["--db", str(path)], stream, env={}, now=_READ_AT)
    assert (rc, f"no ledger at {path}" in stream.getvalue(), path.exists()) == (3, True, False)


def _home_texts(model: ConsoleReadModel) -> dict[str, str]:
    async def drive() -> dict[str, str]:
        app = HomeApp(model)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            return {card: app.plain_text(card) for card in _CARDS}

    return asyncio.run(drive())


def test_tui_runs_live_on_the_ledger(ledger: Path) -> None:
    """``hermes tui --db`` shows the EUR total, the seed line, the position and the 90-day line."""
    seen: dict[str, str] = {}

    def runner(model: ConsoleReadModel) -> None:
        seen.update(_home_texts(model))

    before = hashlib.sha256(ledger.read_bytes()).hexdigest()
    rc = run_tui(["--db", str(ledger)], io.StringIO(), env={}, runner=runner)
    assert (rc, seen["demo-tag"].strip()) == (0, "")
    assert seen["portfolio"].splitlines()[0].startswith("€4,078.76   ")
    assert "started €4,000.00 on 1 Oct · Equivalent in EUR" in seen["portfolio"]
    assert "BTC" in seen["positions"]
    assert seen["shadow"].strip().startswith("Shadow test · day ")
    assert hashlib.sha256(ledger.read_bytes()).hexdigest() == before


def test_tui_on_an_unpriced_ledger_shows_eur_pending(tmp_path: Path) -> None:
    """Before ``hermes fx backfill`` the home card shows USDT and "EUR pending", no € total."""
    path = tmp_path / "hermes.sqlite3"
    home_ledger(path, priced=False)
    seen: dict[str, str] = {}
    rc = run_tui(
        [f"--db={path}"], io.StringIO(), env={}, runner=lambda m: seen.update(_home_texts(m))
    )
    first = seen["portfolio"].splitlines()[0].strip()
    assert (rc, first) == (0, "Portfolio 5,098.45 USDT · EUR pending")
