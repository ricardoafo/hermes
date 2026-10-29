"""The 90-day shadow test line computed from ledger facts, with no I/O (#546).

The ledger can honestly report how far the run is (days since the capital seed,
the first day being day 1) and how many fills it has recorded. Whether the run
is on track needs the go-live criteria evaluation, which the ledger does not
hold, so a run under way is ``RUNNING`` and says its verdict is not available.

Reference: openspec/archive/live-home-screen/change.md (D-4).
"""

from datetime import datetime, tzinfo
from typing import Final

from hermes.application.console.views import ShadowTest, ShadowTestStatus

__all__ = ["SHADOW_TEST_DAYS", "build_shadow_test"]

SHADOW_TEST_DAYS: Final = 90
"""Planned length of the shadow test (ADR-005)."""

_NO_SEED: Final = "no capital seed in the ledger yet"
_NO_VERDICT: Final = "verdict not available yet"


def build_shadow_test(
    *,
    seeded_at: datetime | None,
    as_of: datetime,
    tz: tzinfo,
    fills: int,
    quality_checked: int = 0,
) -> ShadowTest:
    """Return the shadow test status.

    Args:
        seeded_at: When the earliest capital seed started the run, if any.
        as_of: Read time.
        tz: Timezone whose calendar days are counted.
        fills: Fills recorded since the seed.
        quality_checked: Fills with a fill-quality record (0 while #353 has
            not landed).

    Returns:
        Not started without a seed (or before its day), finished after day 90,
        running in between; the note carries the fill count.
    """
    if seeded_at is None:
        return ShadowTest(status=ShadowTestStatus.NOT_STARTED, day=None, note=_NO_SEED)
    day = (as_of.astimezone(tz).date() - seeded_at.astimezone(tz).date()).days + 1
    if day < 1:
        return ShadowTest(
            status=ShadowTestStatus.NOT_STARTED,
            day=None,
            note=f"starts {seeded_at.astimezone(tz):%d %b}",
        )
    fill_note = f"{fills} fill{'' if fills == 1 else 's'}"
    if quality_checked:
        fill_note += f" ({quality_checked} quality-checked)"
    status = ShadowTestStatus.FINISHED if day > SHADOW_TEST_DAYS else ShadowTestStatus.RUNNING
    return ShadowTest(
        status=status,
        day=min(day, SHADOW_TEST_DAYS),
        length_days=SHADOW_TEST_DAYS,
        note=f"{fill_note} · {_NO_VERDICT}",
    )
