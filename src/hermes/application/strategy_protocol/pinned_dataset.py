"""Load historical bars only through the data manifest.

:func:`load_pinned_bars` is the read path for backtests and certification. It
returns bars only when every split they touch is pinned in the
:class:`~hermes.domain.market_data.dataset_manifest.DatasetManifest`, the full
pinned dataset still hashes to its manifest entry, and, for the OOS split, the
certification lifecycle allows the read.

Reference: pre-registration-v1.md §4 and §7, ADR-014,
openspec/archive/356-data-manifest/change.md D-3, D-4.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from hermes.application.strategy_protocol.data_split_enforcer import is_certification_mode
from hermes.application.strategy_protocol.preregistration import DataWindows
from hermes.domain.calibration.certification import CertificationState
from hermes.domain.errors import (
    DatasetHashMismatchError,
    DatasetNotPinnedError,
    OOSDataLeakageError,
)
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.dataset_manifest import (
    DatasetEntry,
    DatasetManifest,
    DataSplit,
    bars_content_sha256,
)
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair

__all__ = [
    "BarSource",
    "PinnedBars",
    "assert_manifest_matches_preregistration",
    "assert_oos_access_allowed",
    "load_pinned_bars",
    "pin_dataset",
    "verify_entry",
]

# The one-shot OOS test advances SHADOW_CERTIFIED -> OOS_CERTIFIED (pre-registration §7).
_OOS_READ_STATE = CertificationState.SHADOW_CERTIFIED


class BarSource(Protocol):
    """Anything that streams historical bars in ``open_time`` order."""

    def stream_bars(self, pair: Pair, interval: KlineInterval) -> AsyncIterator[Bar]:
        """Yield bars for ``pair`` and ``interval`` in ascending ``open_time``."""
        ...


@dataclass(frozen=True, slots=True)
class PinnedBars:
    """Bars of a requested range plus the manifest entries that vouch for them.

    Args:
        bars: Bars with ``open_time`` in the requested ``[start, end)``.
        entries: Every manifest entry whose dataset was verified to produce them.
    """

    bars: tuple[Bar, ...]
    entries: tuple[DatasetEntry, ...]


def assert_manifest_matches_preregistration(
    manifest: DatasetManifest, *, experiment_id: str, windows: DataWindows
) -> None:
    """Refuse a manifest whose experiment or split windows differ from the signed pre-registration.

    Args:
        manifest: The loaded manifest.
        experiment_id: Experiment id from the pre-registration.
        windows: Data windows from the pre-registration.

    Raises:
        ValueError: On any difference.
    """
    if manifest.experiment_id != experiment_id:
        raise ValueError(
            f"Data manifest is for experiment {manifest.experiment_id!r}, "
            f"pre-registration is {experiment_id!r}"
        )
    signed = {
        DataSplit.TRAIN: (windows.train_start, windows.train_end),
        DataSplit.VALIDATION: (windows.validation_start, windows.validation_end),
        DataSplit.OOS: (windows.oos_start, windows.oos_end),
    }
    for split, (start, end) in signed.items():
        window = manifest.splits[split]
        if (window.start, window.end) != (start, end):
            raise ValueError(
                f"Data manifest {split.value} window {window.start}..{window.end} differs from "
                f"the signed pre-registration {start}..{end}"
            )


def assert_oos_access_allowed(
    manifest: DatasetManifest,
    *,
    state: CertificationState,
    now: datetime,
    attempted: datetime,
) -> None:
    """Refuse an OOS read unless the certification lifecycle allows it.

    All three must hold: :func:`certification_context` is active, ``state`` is
    ``SHADOW_CERTIFIED``, and ``now`` is after the OOS window end.

    Args:
        manifest: Manifest holding the OOS window.
        state: Current certification lifecycle state of the strategy.
        now: Current UTC time.
        attempted: Earliest OOS instant the caller asked for (for the error).

    Raises:
        OOSDataLeakageError: When any condition fails.
    """
    oos = manifest.splits[DataSplit.OOS]
    if is_certification_mode() and state is _OOS_READ_STATE and now > oos.end:
        return
    raise OOSDataLeakageError(
        attempted_timestamp=attempted.isoformat(),
        oos_start=oos.start.isoformat(),
    )


def verify_entry(entry: DatasetEntry, bars: list[Bar]) -> None:
    """Raise when ``bars`` are not exactly the dataset ``entry`` pins.

    Args:
        entry: Pinned manifest entry.
        bars: Every bar of that pair and interval inside the entry's split window.

    Raises:
        DatasetHashMismatchError: Row count or content hash differs.
    """
    actual = bars_content_sha256(bars)
    if actual != entry.content_sha256 or len(bars) != entry.row_count:
        raise DatasetHashMismatchError(
            dataset_id=entry.dataset_id,
            expected_sha256=entry.content_sha256,
            actual_sha256=actual,
            expected_rows=entry.row_count,
            actual_rows=len(bars),
        )


async def load_pinned_bars(
    source: BarSource,
    manifest: DatasetManifest,
    *,
    pair: Pair,
    interval: KlineInterval,
    start: datetime,
    end: datetime,
    state: CertificationState,
    now: datetime,
) -> PinnedBars:
    """Return the bars in ``[start, end)`` after verifying every split they touch.

    Each split the range overlaps must be pinned for ``pair`` and ``interval``.
    The whole split's bars are hashed, not only the requested slice, because
    the manifest pins whole splits. OOS access is checked before any bar is
    read.

    Args:
        source: Historical bar source.
        manifest: Loaded data manifest.
        pair: Trading pair.
        interval: Bar interval.
        start: Inclusive range start (UTC).
        end: Exclusive range end (UTC).
        state: Current certification lifecycle state.
        now: Current UTC time.

    Returns:
        The requested bars and the entries verified for them.

    Raises:
        ValueError: ``start`` is not before ``end``.
        DatasetNotPinnedError: The range needs a split with no entry, or holds a
            bar outside every split window.
        OOSDataLeakageError: The range touches OOS and access is not allowed.
        DatasetHashMismatchError: A pinned dataset changed.
    """
    if start >= end:
        raise ValueError(f"load_pinned_bars: start {start} must precede end {end}")
    splits = manifest.splits_intersecting(start, end)
    if not splits or start < manifest.splits[DataSplit.TRAIN].start:
        raise DatasetNotPinnedError(
            pair=str(pair),
            interval=interval.value,
            reason=f"range {start.isoformat()}..{end.isoformat()} starts outside the split windows",
        )

    if DataSplit.OOS in splits:
        assert_oos_access_allowed(
            manifest,
            state=state,
            now=now,
            attempted=max(start, manifest.splits[DataSplit.OOS].start),
        )
    entries: dict[DataSplit, DatasetEntry] = {}
    for split in splits:
        entry = manifest.find(pair, interval, split)
        if entry is None:
            raise DatasetNotPinnedError(
                pair=str(pair),
                interval=interval.value,
                reason=f"the {split.value} split is not pinned",
            )
        entries[split] = entry

    stop = manifest.splits[splits[-1]].end
    collected: dict[DataSplit, list[Bar]] = {split: [] for split in splits}
    requested: list[Bar] = []
    async for bar in source.stream_bars(pair, interval):
        if bar.open_time > stop:
            break
        split_of_bar = manifest.split_of(bar.open_time)
        if split_of_bar in collected:
            collected[split_of_bar].append(bar)
        if start <= bar.open_time < end:
            if split_of_bar is None:
                raise DatasetNotPinnedError(
                    pair=str(pair),
                    interval=interval.value,
                    reason=f"bar at {bar.open_time.isoformat()} lies outside every split window",
                )
            requested.append(bar)

    for split, entry in entries.items():
        verify_entry(entry, collected[split])
    return PinnedBars(bars=tuple(requested), entries=tuple(entries.values()))


async def pin_dataset(
    source: BarSource,
    manifest: DatasetManifest,
    *,
    pair: Pair,
    interval: KlineInterval,
    split: DataSplit,
    source_name: str,
    state: CertificationState,
    now: datetime,
) -> DatasetEntry:
    """Build the manifest entry for one (pair, interval, split) from ``source``.

    Pinning OOS reads OOS bars, so it is subject to the same access check as
    :func:`load_pinned_bars`.

    Args:
        source: Historical bar source.
        manifest: Manifest holding the split windows.
        pair: Trading pair.
        interval: Bar interval.
        split: Split to pin.
        source_name: Recorded provenance (e.g. ``binance-vision``).
        state: Current certification lifecycle state.
        now: Current UTC time.

    Returns:
        The entry, with row count and content hash of the split's bars.

    Raises:
        OOSDataLeakageError: ``split`` is OOS and access is not allowed.
    """
    window = manifest.splits[split]
    if split is DataSplit.OOS:
        assert_oos_access_allowed(manifest, state=state, now=now, attempted=window.start)
    bars: list[Bar] = []
    async for bar in source.stream_bars(pair, interval):
        if bar.open_time > window.end:
            break
        if window.contains(bar.open_time):
            bars.append(bar)
    return DatasetEntry(
        pair=pair,
        interval=interval,
        split=split,
        source=source_name,
        row_count=len(bars),
        content_sha256=bars_content_sha256(bars),
    )
