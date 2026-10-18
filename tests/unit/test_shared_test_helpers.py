"""Guard: test helpers that live in ``tests/support`` are not copied back (issue #1182).

The suite carried six identical ``_fees_at``, three drifting ``_SteppingFeed``
subclasses, private ``_Clock`` fakes, ~100 ``Path(__file__).parents[n]``
repo-root expressions whose ``n`` depended on the file's depth, and fixtures
that skipped when they "could not find the project root", which would hide a
whole module if it ever happened. ``tests/support`` now holds ``fees_at``,
``SteppingFeed``, ``FakeClock`` and ``REPO_ROOT``.

Invariants: ``FakeClock`` answers ``now()`` and a call with the instant the
test set, and ``SteppingFeed`` runs its step on a bar before yielding it.
Outside ``tests/support``, each pattern below appears in exactly
the files listed for it. A new copy fails the test, and so does a file that
lost its copy but is still listed, so the list only shrinks. The listed files
are the ones other open pull requests were changing when this guard landed;
each later change that switches one removes it here.
"""

import asyncio
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hermes.domain.market_data.kline import KlineInterval
from tests.support.clock import FakeClock
from tests.support.paths import REPO_ROOT
from tests.support.serve_loop import BTC, SteppingFeed, live

_TESTS = REPO_ROOT / "tests"

_LEFT: dict[str, tuple[str, frozenset[str]]] = {
    "fees-at": (
        r"^def _fees_at\(",
        frozenset(
            {
                "tests/unit/application/backtest/test_replay.py",
                "tests/unit/application/parity/test_check.py",
                "tests/unit/application/parity/test_check_window.py",
            }
        ),
    ),
    "stepping-feed": (r"^class _SteppingFeed\b", frozenset()),
    "clock": (
        r"^class _Clock\b",
        frozenset(
            {
                "tests/unit/application/console/test_live.py",
                "tests/unit/entrypoints/test_tui_cockpit.py",
                "tests/unit/entrypoints/test_tui_live_screen.py",
            }
        ),
    ),
    "repo-root": (
        r"Path\(__file__\)(?:\.resolve\(\))?\.parents\[",
        frozenset(
            {
                "tests/integration/entrypoints/test_home_from_ledger.py",
                "tests/unit/adapters/data_manifest/test_json_file.py",
                "tests/unit/adapters/exchanges/test_binance_balance_reader.py",
                "tests/unit/adapters/market_data/test_binance_depth_snapshot.py",
                "tests/unit/deploy/test_cd_workflow.py",
                "tests/unit/deploy/test_hermes_env_example.py",
                "tests/unit/deploy/test_systemd_units.py",
                "tests/unit/entrypoints/test_cli.py",
                "tests/unit/entrypoints/test_config_check.py",
                "tests/unit/entrypoints/test_serve.py",
                "tests/unit/entrypoints/test_tui_logs.py",
                "tests/unit/entrypoints/test_tui_topbar.py",
                "tests/unit/infrastructure/config/test_loader.py",
                "tests/unit/infrastructure/test_cut_release.py",
                "tests/unit/scripts_backtest/test_backtest_batch_workflow.py",
                "tests/unit/scripts_backtest/test_trial_stats.py",
            }
        ),
    ),
    "root-skip": (
        r"Could not find project root",
        frozenset({"tests/unit/application/strategy_protocol/test_pinned_dataset.py"}),
    ),
}


def _holding(pattern: str) -> set[str]:
    this_file = Path(__file__).name
    return {
        str(path.relative_to(REPO_ROOT))
        for path in _TESTS.rglob("*.py")
        if not path.is_relative_to(_TESTS / "support")
        and path.name != this_file
        and re.search(pattern, path.read_text(encoding="utf-8"), re.MULTILINE)
    }


@pytest.mark.parametrize("helper", sorted(_LEFT))
def test_no_test_module_grows_its_own_copy(helper: str) -> None:
    """Only the listed files still hold the pattern; a new copy or a stale entry fails."""
    pattern, left = _LEFT[helper]

    assert _holding(pattern) ^ left == set()


def test_fake_clock_reads_the_same_instant_both_ways_until_moved() -> None:
    """now() and a call return the set instant; advance moves both by exactly the delta."""
    start = datetime(2026, 10, 18, 12, tzinfo=UTC)
    clock = FakeClock(start)
    before = (clock.now(), clock())
    clock.advance(timedelta(seconds=90))

    assert (before, clock.now(), clock()) == (
        (start, start),
        start + timedelta(seconds=90),
        start + timedelta(seconds=90),
    )


def test_stepping_feed_steps_each_live_bar_before_it_is_seen() -> None:
    """The step runs on a bar before the consumer receives it, in delivery order."""
    seen: list[str] = []
    bars = live(11, 13)
    feed = SteppingFeed(lambda bar: seen.append(f"step {bar.open_time:%H}"), live_bars=bars)

    async def consume() -> None:
        stream = feed.stream_closed_bars([BTC], [KlineInterval.H4])
        for _ in bars:
            bar = await anext(stream)
            seen.append(f"got {bar.open_time:%H}")

    asyncio.run(consume())

    assert seen == [f"{kind} {b.open_time:%H}" for b in bars for kind in ("step", "got")]
