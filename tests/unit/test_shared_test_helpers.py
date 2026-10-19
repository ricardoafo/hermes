"""Guard: test helpers that live in ``tests/support`` are not copied back (issue #1182).

The suite carried six identical ``_fees_at``, three drifting ``_SteppingFeed``
subclasses, private ``_Clock`` fakes, ~100 ``Path(__file__).parents[n]``
repo-root expressions whose ``n`` depended on the file's depth, and fixtures
that skipped when they "could not find the project root", which would hide a
whole module if it ever happened. ``tests/support`` now holds ``fees_at``,
``SteppingFeed``, ``FakeClock`` and ``REPO_ROOT``.

Invariants: ``FakeClock`` answers ``now()`` and a call with the instant the
test set, and ``SteppingFeed`` runs its step on a bar before yielding it.
Outside ``tests/support``, none of the patterns below appears: a new private
copy fails the test.
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

_PATTERNS: dict[str, str] = {
    "fees-at": r"^def _fees_at\(",
    "stepping-feed": r"^class _SteppingFeed\b",
    "clock": r"^class _Clock\b",
    "repo-root": r"Path\(__file__\)(?:\.resolve\(\))?\.parents\[",
    "root-skip": r"Could not find project root",
    "named-svg": r"^class _NamedSVG\b",
    "repo-clean": r"^def _repo_clean\(",
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


@pytest.mark.parametrize("helper", sorted(_PATTERNS))
def test_no_test_module_grows_its_own_copy(helper: str) -> None:
    """No test module outside ``tests/support`` holds a private copy of a shared helper."""
    assert _holding(_PATTERNS[helper]) == set()


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
