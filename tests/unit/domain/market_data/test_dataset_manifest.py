"""Unit tests for the dataset manifest value objects and the bar content hash.

Invariants under test (openspec/archive/356-data-manifest/change.md D-1, D-2):

- The content hash is a function of bar content only: input order and
  Decimal trailing zeros do not change it, any OHLCV or time change does.
- Split windows are inclusive at both ends and ordered without overlap.
- A manifest pins at most one entry per (pair, interval, split).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.dataset_manifest import (
    DatasetEntry,
    DatasetManifest,
    DataSplit,
    SplitWindow,
    bars_content_sha256,
    dataset_id_for,
)
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_T0 = datetime(2021, 1, 1, tzinfo=UTC)
_HASH = "a" * 64


def _bar(i: int, close: Decimal = Decimal("100.5")) -> Bar:
    open_time = _T0 + timedelta(hours=4 * i)
    return Bar(
        pair=_BTC,
        interval=KlineInterval.H4,
        open_time=open_time,
        close_time=open_time + timedelta(hours=4),
        open=Decimal(100),
        high=Decimal(110),
        low=Decimal(90),
        close=close,
        volume=Decimal("12.5"),
    )


def _splits() -> dict[DataSplit, SplitWindow]:
    return {
        DataSplit.TRAIN: SplitWindow(
            datetime(2020, 9, 1, tzinfo=UTC), datetime(2024, 6, 30, 23, 59, 59, tzinfo=UTC)
        ),
        DataSplit.VALIDATION: SplitWindow(
            datetime(2024, 7, 1, tzinfo=UTC), datetime(2025, 8, 31, 23, 59, 59, tzinfo=UTC)
        ),
        DataSplit.OOS: SplitWindow(
            datetime(2025, 9, 1, tzinfo=UTC), datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC)
        ),
    }


def _entry(split: DataSplit = DataSplit.TRAIN) -> DatasetEntry:
    return DatasetEntry(
        pair=_BTC,
        interval=KlineInterval.H4,
        split=split,
        source="binance-vision",
        row_count=3,
        content_sha256=_HASH,
    )


@given(st.permutations(list(range(6))))
def test_hash_is_independent_of_input_order(order: list[int]) -> None:
    """The same bars in any order hash the same, so a source's iteration order cannot re-pin."""
    bars = [_bar(i) for i in range(6)]
    assert bars_content_sha256([bars[i] for i in order]) == bars_content_sha256(bars)


def test_hash_ignores_decimal_trailing_zeros() -> None:
    """``100.50`` and ``100.5`` are the same price, so they must not produce different hashes."""
    assert bars_content_sha256([_bar(0, Decimal("100.50"))]) == bars_content_sha256(
        [_bar(0, Decimal("100.5"))]
    )


def test_hash_changes_when_one_price_changes() -> None:
    """A one-tick change in one bar must change the hash, or a corrupted dataset would pass."""
    bars = [_bar(i) for i in range(3)]
    changed = [*bars[:2], _bar(2, Decimal("100.6"))]
    assert bars_content_sha256(changed) != bars_content_sha256(bars)


def test_hash_changes_when_a_bar_is_dropped() -> None:
    """A missing bar must change the hash even when every remaining bar is identical."""
    bars = [_bar(i) for i in range(3)]
    assert bars_content_sha256(bars[:2]) != bars_content_sha256(bars)


def test_hash_of_no_bars_is_sha256_of_empty_input() -> None:
    """An empty dataset has the well-known empty SHA-256 digest."""
    assert bars_content_sha256([]) == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_split_window_contains_both_endpoints() -> None:
    """Pre-registration §4 windows are inclusive at both ends."""
    window = _splits()[DataSplit.TRAIN]
    assert window.contains(window.start)
    assert window.contains(window.end)
    assert not window.contains(window.end + timedelta(seconds=1))


def test_split_window_intersects_half_open_range() -> None:
    """A range ending exactly at the window start does not overlap it; ``end`` is exclusive."""
    window = _splits()[DataSplit.VALIDATION]
    assert not window.intersects(window.start - timedelta(days=1), window.start)
    assert window.intersects(window.start - timedelta(days=1), window.start + timedelta(seconds=1))


def test_split_window_rejects_naive_datetimes() -> None:
    """Naive datetimes would compare wrongly with UTC bar times, so they are rejected."""
    with pytest.raises(ValueError, match="timezone-aware"):
        SplitWindow(datetime(2020, 1, 1), datetime(2020, 2, 1, tzinfo=UTC))


def test_split_window_rejects_inverted_range() -> None:
    """A window whose start is not before its end is invalid."""
    with pytest.raises(ValueError, match="precede"):
        SplitWindow(_T0, _T0)


def test_entry_rejects_malformed_hash() -> None:
    """Only a 64-char lowercase hex digest is a valid pin."""
    with pytest.raises(ValueError, match="64 lowercase hex"):
        DatasetEntry(
            pair=_BTC,
            interval=KlineInterval.H4,
            split=DataSplit.TRAIN,
            source="binance-vision",
            row_count=1,
            content_sha256="ABC",
        )


def test_entry_rejects_negative_row_count() -> None:
    """A negative row count cannot describe any dataset."""
    with pytest.raises(ValueError, match="row_count"):
        DatasetEntry(
            pair=_BTC,
            interval=KlineInterval.H4,
            split=DataSplit.TRAIN,
            source="binance-vision",
            row_count=-1,
            content_sha256=_HASH,
        )


def test_entry_rejects_empty_source() -> None:
    """Every pin records where its bars came from."""
    with pytest.raises(ValueError, match="source"):
        DatasetEntry(
            pair=_BTC,
            interval=KlineInterval.H4,
            split=DataSplit.TRAIN,
            source="",
            row_count=1,
            content_sha256=_HASH,
        )


def test_dataset_id_is_namespaced_by_pair_interval_split() -> None:
    """The id format is what reports and trial records reference."""
    assert dataset_id_for(_BTC, KlineInterval.D1, DataSplit.VALIDATION) == (
        "klines/BTCUSDT/1d/validation"
    )
    assert _entry().dataset_id == "klines/BTCUSDT/4h/train"


def test_manifest_requires_every_split() -> None:
    """A manifest without the OOS window could not refuse OOS reads."""
    splits = _splits()
    del splits[DataSplit.OOS]
    with pytest.raises(ValueError, match="missing"):
        DatasetManifest(experiment_id="x", splits=splits)


def test_manifest_rejects_overlapping_splits() -> None:
    """Overlapping splits would let one bar belong to Train and OOS at once."""
    splits = _splits()
    splits[DataSplit.VALIDATION] = SplitWindow(
        datetime(2024, 6, 1, tzinfo=UTC), datetime(2025, 8, 31, tzinfo=UTC)
    )
    with pytest.raises(ValueError, match="non-overlapping"):
        DatasetManifest(experiment_id="x", splits=splits)


def test_manifest_rejects_duplicate_entries() -> None:
    """Two pins for one dataset would make verification ambiguous."""
    with pytest.raises(ValueError, match="duplicate"):
        DatasetManifest(experiment_id="x", splits=_splits(), entries=(_entry(), _entry()))


def test_manifest_split_of_maps_timestamps_to_splits() -> None:
    """Each boundary instant belongs to the split that pre-registration §4 assigns it."""
    manifest = DatasetManifest(experiment_id="x", splits=_splits())
    assert manifest.split_of(datetime(2024, 6, 30, 20, tzinfo=UTC)) is DataSplit.TRAIN
    assert manifest.split_of(datetime(2024, 7, 1, tzinfo=UTC)) is DataSplit.VALIDATION
    assert manifest.split_of(datetime(2025, 9, 1, tzinfo=UTC)) is DataSplit.OOS
    assert manifest.split_of(datetime(2020, 8, 31, tzinfo=UTC)) is None


def test_manifest_splits_intersecting_is_chronological() -> None:
    """A range spanning Validation into OOS reports both, oldest first."""
    manifest = DatasetManifest(experiment_id="x", splits=_splits())
    got = manifest.splits_intersecting(
        datetime(2025, 6, 1, tzinfo=UTC), datetime(2025, 10, 1, tzinfo=UTC)
    )
    assert got == (DataSplit.VALIDATION, DataSplit.OOS)


def test_manifest_find_returns_only_the_matching_entry() -> None:
    """Lookup is by (pair, interval, split); other splits are not substitutes."""
    manifest = DatasetManifest(experiment_id="x", splits=_splits(), entries=(_entry(),))
    assert manifest.find(_BTC, KlineInterval.H4, DataSplit.TRAIN) == _entry()
    assert manifest.find(_BTC, KlineInterval.H4, DataSplit.VALIDATION) is None
