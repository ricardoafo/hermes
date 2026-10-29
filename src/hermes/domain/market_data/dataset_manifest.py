"""Dataset manifest — pins every dataset behind a backtest or certification result.

A :class:`DatasetManifest` holds the Train / Validation / OOS split windows of a
pre-registered experiment and one :class:`DatasetEntry` per (pair, interval,
split). Each entry records the row count and a content hash over the parsed
bars, so a result can name the exact bars it was computed on and a changed
dataset is detected before it is used.

The hash is over a canonical text serialisation of :class:`Bar` values, not
over archive files, so it does not depend on the storage format.

Reference: pre-registration-v1.md §4, openspec/archive/356-data-manifest/change.md D-1, D-2.
"""

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair

__all__ = [
    "DataSplit",
    "DatasetEntry",
    "DatasetManifest",
    "SplitWindow",
    "bars_content_sha256",
    "dataset_id_for",
]

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class DataSplit(StrEnum):
    """The three pre-registered data windows (pre-registration §4)."""

    TRAIN = "train"
    VALIDATION = "validation"
    OOS = "oos"


@dataclass(frozen=True, slots=True)
class SplitWindow:
    """A split window, UTC, inclusive of both endpoints (pre-registration §4).

    Args:
        start: First instant in the window.
        end: Last instant in the window.
    """

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        """Validate UTC awareness and ordering."""
        for name, value in (("start", self.start), ("end", self.end)):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"SplitWindow.{name} must be timezone-aware UTC, got {value!r}")
        if self.start >= self.end:
            raise ValueError(f"SplitWindow.start {self.start} must precede end {self.end}")

    def contains(self, ts: datetime) -> bool:
        """Return whether ``ts`` lies inside the window (both ends inclusive)."""
        return self.start <= ts <= self.end

    def intersects(self, start: datetime, end: datetime) -> bool:
        """Return whether the half-open range ``[start, end)`` overlaps the window."""
        return start <= self.end and self.start < end


def dataset_id_for(pair: Pair, interval: KlineInterval, split: DataSplit) -> str:
    """Return the canonical dataset id, e.g. ``klines/BTCUSDT/4h/train``."""
    return f"klines/{pair.base}{pair.quote}/{interval.value}/{split.value}"


@dataclass(frozen=True, slots=True)
class DatasetEntry:
    """One pinned dataset: the bars of one pair and interval over one split.

    Args:
        pair: Trading pair.
        interval: Bar interval.
        split: Which split window the entry covers, in full.
        source: Where the bars come from (e.g. ``binance-vision``).
        row_count: Number of bars in the window.
        content_sha256: :func:`bars_content_sha256` of those bars.
    """

    pair: Pair
    interval: KlineInterval
    split: DataSplit
    source: str
    row_count: int
    content_sha256: str

    def __post_init__(self) -> None:
        """Validate the hash format, row count and source."""
        if not _SHA256_HEX.match(self.content_sha256):
            raise ValueError(
                "DatasetEntry.content_sha256 must be 64 lowercase hex chars, "
                f"got {self.content_sha256!r}"
            )
        if self.row_count < 0:
            raise ValueError(f"DatasetEntry.row_count must be >= 0, got {self.row_count}")
        if not self.source:
            raise ValueError("DatasetEntry.source must not be empty")

    @property
    def dataset_id(self) -> str:
        """Return the canonical id of this entry."""
        return dataset_id_for(self.pair, self.interval, self.split)


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    """Split windows plus the pinned datasets of one experiment.

    Args:
        experiment_id: Pre-registration experiment id (e.g. ``swing-mvp1-v1``).
        splits: The window of every :class:`DataSplit`.
        entries: Pinned datasets, at most one per (pair, interval, split).
    """

    experiment_id: str
    splits: Mapping[DataSplit, SplitWindow]
    entries: tuple[DatasetEntry, ...] = field(default=())

    def __post_init__(self) -> None:
        """Validate split completeness, split ordering and entry uniqueness."""
        missing = [s for s in DataSplit if s not in self.splits]
        if missing:
            raise ValueError(f"DatasetManifest.splits is missing {[s.value for s in missing]}")
        ordered = [self.splits[s] for s in DataSplit]
        for earlier, later in pairwise(ordered):
            if earlier.end >= later.start:
                raise ValueError("DatasetManifest.splits must be ordered and non-overlapping")
        ids = [e.dataset_id for e in self.entries]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"DatasetManifest has duplicate entries: {duplicates}")

    def split_of(self, ts: datetime) -> DataSplit | None:
        """Return the split containing ``ts``, or ``None`` when outside every split."""
        for split, window in self.splits.items():
            if window.contains(ts):
                return split
        return None

    def splits_intersecting(self, start: datetime, end: datetime) -> tuple[DataSplit, ...]:
        """Return the splits overlapping ``[start, end)``, in chronological order."""
        return tuple(s for s in DataSplit if self.splits[s].intersects(start, end))

    def find(self, pair: Pair, interval: KlineInterval, split: DataSplit) -> DatasetEntry | None:
        """Return the entry for (pair, interval, split), or ``None`` when not pinned."""
        wanted = dataset_id_for(pair, interval, split)
        return next((e for e in self.entries if e.dataset_id == wanted), None)


def _canonical_decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _canonical_line(bar: Bar) -> str:
    fields = (
        str(bar.pair),
        bar.interval.value,
        bar.open_time.isoformat(),
        _canonical_decimal(bar.open),
        _canonical_decimal(bar.high),
        _canonical_decimal(bar.low),
        _canonical_decimal(bar.close),
        _canonical_decimal(bar.volume),
        bar.close_time.isoformat(),
    )
    return "|".join(fields) + "\n"


def bars_content_sha256(bars: Iterable[Bar]) -> str:
    """Return the SHA-256 of the canonical serialisation of ``bars``.

    Bars are sorted by ``open_time`` first, so the input order does not matter.
    Decimals are normalised, so ``1.50`` and ``1.5`` hash the same.

    Args:
        bars: The bars of one dataset.

    Returns:
        64-char lowercase hex digest.
    """
    digest = hashlib.sha256()
    for bar in sorted(bars, key=lambda b: b.open_time):
        digest.update(_canonical_line(bar).encode("utf-8"))
    return digest.hexdigest()
