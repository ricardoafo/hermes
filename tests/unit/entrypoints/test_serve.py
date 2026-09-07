"""Tests for the ``hermes serve`` composition root.

Verifies:

- ``build_composition`` wires each field of :class:`Composition` to the
  expected concrete adapter class for a shadow-tier config snapshot.
- ``install_signal_handlers`` flips the ``asyncio.Event`` when SIGTERM
  is delivered to the process. Skipped on Windows where the
  ``add_signal_handler`` codepath is not available and delivering
  SIGTERM via ``os.kill`` on the running process aborts pytest.
- ``run`` returns immediately when the shutdown event is already set
  at call time, without spinning up any I/O.

The tests deliberately do NOT exercise the full paper-trading loop —
that is Phase-2 scope. Here we only verify wiring correctness, signal
plumbing, and the cleanup contract.

Reference: ``docs/decisions/2026-08-23-slack-alongside-telegram.md``
    (notifier routing table), ADR-003 §00 invariant I-08 (real-money
    guard).
"""

from __future__ import annotations

import asyncio
import os
import signal
import sqlite3
import sys
from pathlib import Path

import pytest

from hermes.adapters.clock.wall import WallClock
from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.adapters.market_data.binance_historical import BinanceHistoricalAdapter
from hermes.domain.events.event_bus import EventBus
from hermes.domain.strategy.swing.adapter import SwingStrategyAdapter
from hermes.entrypoints import serve as serve_module
from hermes.infrastructure.config.loader import load_config


@pytest.fixture
def shadow_config(tmp_path: Path) -> object:
    """Load the shipped ``config/`` directory as a shadow-tier snapshot.

    Uses an empty env dict so no ambient shell variable can flip the
    tier or bleed credentials into the composition.
    """
    project_root = Path(__file__).resolve().parents[3]
    return load_config(project_root / "config", env={})


class TestBuildComposition:
    """`build_composition` wires each port to the expected adapter class."""

    def test_build_composition_wires_expected_ports_for_shadow_config(
        self,
        shadow_config: object,
        tmp_path: Path,
    ) -> None:
        """Every field of Composition is an instance of the expected concrete adapter."""
        composition = serve_module.build_composition(
            shadow_config,  # type: ignore[arg-type]
            db_path=tmp_path / "test.sqlite3",
            env={},
        )
        try:
            assert isinstance(composition.ledger, SqliteLedger)
            assert isinstance(composition.clock, WallClock)
            assert isinstance(composition.market_data, BinanceHistoricalAdapter)
            assert isinstance(composition.strategy, SwingStrategyAdapter)
            assert isinstance(composition.event_bus, EventBus)
            # Notifier stays None when Telegram credentials are absent — the
            # test env dict is empty by design.
            assert composition.notifier is None
        finally:
            composition.sqlite_connection.close()


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "SIGTERM delivery via os.kill on the running pytest process is unsafe on "
        "Windows and the loop-based add_signal_handler codepath is not available."
    ),
)
class TestInstallSignalHandlers:
    """`install_signal_handlers` flips the shutdown event when SIGTERM arrives."""

    def test_install_signal_handlers_sets_shutdown_on_sigterm(self) -> None:
        """A SIGTERM to self flips the shutdown Event within a short wait."""

        async def _driver() -> bool:
            loop = asyncio.get_running_loop()
            shutdown = asyncio.Event()
            serve_module.install_signal_handlers(loop, shutdown)
            os.kill(os.getpid(), signal.SIGTERM)
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=1.0)
            except TimeoutError:
                return False
            return shutdown.is_set()

        assert asyncio.run(_driver()) is True


class TestRun:
    """`run` returns immediately when the shutdown event is pre-set."""

    def test_run_exits_cleanly_when_shutdown_is_set_before_start(
        self,
        shadow_config: object,
        tmp_path: Path,
    ) -> None:
        """Pre-setting the shutdown event lets run return within 1s + closes the SQLite conn."""
        db_path = tmp_path / "test.sqlite3"
        composition = serve_module.build_composition(
            shadow_config,  # type: ignore[arg-type]
            db_path=db_path,
            env={},
        )

        async def _driver() -> None:
            shutdown = asyncio.Event()
            shutdown.set()
            await asyncio.wait_for(
                serve_module.run(composition, shutdown),
                timeout=1.0,
            )

        asyncio.run(_driver())

        # run() must close the SQLite connection in its finally block —
        # a further execute() call therefore raises ProgrammingError.
        with pytest.raises(sqlite3.ProgrammingError):
            composition.sqlite_connection.execute("SELECT 1")
