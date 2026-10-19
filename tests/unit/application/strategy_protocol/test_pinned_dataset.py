"""Unit tests for loading historical bars through the data manifest.

Invariants under test (openspec/archive/356-data-manifest/change.md R-1..R-3, D-3, D-4):

- Bars are returned only when every split they touch is pinned and the whole
  pinned dataset still hashes to its manifest entry.
- OOS bars are refused, before any bar is read, unless certification mode is
  active, the lifecycle state is SHADOW_CERTIFIED, and the OOS window has ended.
- The manifest is refused when its windows differ from the signed pre-registration.

The bar source is an in-memory fake; the real archive adapter is exercised by
``scripts/data/build_dataset_manifest.py`` and the backtest driver dry run.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from hermes.application.calibration.lifecycle import CertificationState
from hermes.application.strategy_protocol.data_split_enforcer import certification_context
from hermes.application.strategy_protocol.pinned_dataset import (
    PinnedBars,
    assert_manifest_matches_preregistration,
    load_pinned_bars,
    pin_dataset,
)
from hermes.application.strategy_protocol.preregistration import PreRegistration
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
    SplitWindow,
    bars_content_sha256,
)
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_H4 = KlineInterval.H4
_DAY = timedelta(days=1)
# One day per split keeps every dataset at six 4h bars.
_TRAIN_START = datetime(2021, 1, 1, tzinfo=UTC)
_VAL_START = _TRAIN_START + _DAY
_OOS_START = _TRAIN_START + 2 * _DAY
_END_OF_DATA = _TRAIN_START + 3 * _DAY
_SPLITS = {
    DataSplit.TRAIN: SplitWindow(_TRAIN_START, _VAL_START - timedelta(seconds=1)),
    DataSplit.VALIDATION: SplitWindow(_VAL_START, _OOS_START - timedelta(seconds=1)),
    DataSplit.OOS: SplitWindow(_OOS_START, _END_OF_DATA - timedelta(seconds=1)),
}
_AFTER_OOS = _END_OF_DATA + _DAY
_DURING_OOS = _OOS_START + timedelta(hours=1)


def _bar(open_time: datetime, close: Decimal = Decimal(100)) -> Bar:
    return Bar(
        pair=_BTC,
        interval=_H4,
        open_time=open_time,
        close_time=open_time + timedelta(hours=4),
        open=Decimal(100),
        high=Decimal(110),
        low=Decimal(90),
        close=close,
        volume=Decimal(5),
    )


def _all_bars() -> list[Bar]:
    return [_bar(_TRAIN_START + timedelta(hours=4 * i)) for i in range(18)]


def _bars_in(split: DataSplit) -> list[Bar]:
    return [b for b in _all_bars() if _SPLITS[split].contains(b.open_time)]


class _FakeSource:
    """Yields the given bars in order and records how many were read."""

    def __init__(self, bars: list[Bar]) -> None:
        self.bars = bars
        self.yielded: list[Bar] = []

    async def stream_bars(self, pair: Pair, interval: KlineInterval) -> AsyncIterator[Bar]:
        for bar in self.bars:
            self.yielded.append(bar)
            yield bar


def _entry(split: DataSplit, bars: list[Bar] | None = None) -> DatasetEntry:
    pinned = _bars_in(split) if bars is None else bars
    return DatasetEntry(
        pair=_BTC,
        interval=_H4,
        split=split,
        source="test",
        row_count=len(pinned),
        content_sha256=bars_content_sha256(pinned),
    )


def _manifest(*splits: DataSplit) -> DatasetManifest:
    return DatasetManifest(
        experiment_id="test-v1", splits=_SPLITS, entries=tuple(_entry(s) for s in splits)
    )


def _load(
    source: _FakeSource,
    manifest: DatasetManifest,
    start: datetime,
    end: datetime,
    state: CertificationState = CertificationState.UNTRUSTED,
    now: datetime = _DURING_OOS,
) -> PinnedBars:
    return asyncio.run(
        load_pinned_bars(
            source,
            manifest,
            pair=_BTC,
            interval=_H4,
            start=start,
            end=end,
            state=state,
            now=now,
        )
    )


def test_returns_only_the_requested_slice_of_a_verified_split() -> None:
    """A Train-only range returns its bars in [start, end) and names the Train entry."""
    start = _TRAIN_START + timedelta(hours=8)
    got = _load(_FakeSource(_all_bars()), _manifest(DataSplit.TRAIN), start, _VAL_START)
    assert [b.open_time for b in got.bars] == [start + timedelta(hours=4 * i) for i in range(4)]
    assert [e.dataset_id for e in got.entries] == ["klines/BTCUSDT/4h/train"]


def test_range_across_splits_verifies_and_reports_both() -> None:
    """A range spanning Train into Validation needs and reports both pins."""
    got = _load(
        _FakeSource(_all_bars()),
        _manifest(DataSplit.TRAIN, DataSplit.VALIDATION),
        _TRAIN_START,
        _OOS_START,
    )
    assert len(got.bars) == 12
    assert [e.split for e in got.entries] == [DataSplit.TRAIN, DataSplit.VALIDATION]


def test_changed_bar_fails_with_dataset_id_and_both_hashes() -> None:
    """R-1: one changed price anywhere in the pinned split stops the load, even off-slice."""
    bars = _all_bars()
    bars[0] = _bar(bars[0].open_time, Decimal("100.01"))
    with pytest.raises(DatasetHashMismatchError) as exc:
        _load(_FakeSource(bars), _manifest(DataSplit.TRAIN), _VAL_START - _DAY / 2, _VAL_START)
    err = exc.value
    assert err.dataset_id == "klines/BTCUSDT/4h/train"
    assert err.expected_sha256 == bars_content_sha256(_bars_in(DataSplit.TRAIN))
    assert err.actual_sha256 == bars_content_sha256([*_bars_in(DataSplit.TRAIN)[1:], bars[0]])


def test_missing_bar_fails_with_row_counts() -> None:
    """R-1: a dropped bar is reported with expected and actual row counts."""
    bars = [b for b in _all_bars() if b.open_time != _TRAIN_START + timedelta(hours=4)]
    with pytest.raises(DatasetHashMismatchError) as exc:
        _load(_FakeSource(bars), _manifest(DataSplit.TRAIN), _TRAIN_START, _VAL_START)
    assert (exc.value.expected_rows, exc.value.actual_rows) == (6, 5)


def test_unpinned_split_is_refused() -> None:
    """R-3: a range reaching into Validation fails when only Train is pinned."""
    with pytest.raises(DatasetNotPinnedError, match="validation split is not pinned"):
        _load(_FakeSource(_all_bars()), _manifest(DataSplit.TRAIN), _TRAIN_START, _OOS_START)


def test_range_starting_before_train_is_refused() -> None:
    """R-3: nothing before Train is pinned, so a range starting earlier is refused."""
    with pytest.raises(DatasetNotPinnedError, match="outside the split windows"):
        _load(
            _FakeSource(_all_bars()),
            _manifest(DataSplit.TRAIN),
            _TRAIN_START - _DAY,
            _VAL_START,
        )


def test_bar_between_split_windows_is_refused() -> None:
    """R-3: a bar inside the range but in no split window cannot be vouched for."""
    stray = _bar(_VAL_START - timedelta(milliseconds=500))
    bars = sorted([*_all_bars(), stray], key=lambda b: b.open_time)
    with pytest.raises(DatasetNotPinnedError, match="outside every split window"):
        _load(
            _FakeSource(bars),
            _manifest(DataSplit.TRAIN, DataSplit.VALIDATION),
            _TRAIN_START,
            _OOS_START,
        )


def test_empty_range_is_rejected() -> None:
    """``start`` must precede ``end``."""
    with pytest.raises(ValueError, match="precede"):
        _load(_FakeSource(_all_bars()), _manifest(DataSplit.TRAIN), _VAL_START, _VAL_START)


def test_reading_stops_after_the_last_needed_split() -> None:
    """At most one bar past Validation is read, so OOS bars are not consumed by a Train/Val load."""
    source = _FakeSource(_all_bars())
    _load(source, _manifest(DataSplit.TRAIN, DataSplit.VALIDATION), _TRAIN_START, _OOS_START)
    assert sum(1 for b in source.yielded if b.open_time >= _OOS_START) == 1


# SEV-1: pre-registration §4 OOS read refusal (no rubric row; integrity rule 1.8)
@pytest.mark.parametrize(
    ("state", "certifying", "now"),
    [
        (CertificationState.UNTRUSTED, True, _AFTER_OOS),
        (CertificationState.BACKTEST_CERTIFIED, True, _AFTER_OOS),
        (CertificationState.OOS_CERTIFIED, True, _AFTER_OOS),
        (CertificationState.SHADOW_CERTIFIED, False, _AFTER_OOS),
        (CertificationState.SHADOW_CERTIFIED, True, _DURING_OOS),
    ],
)
def test_oos_is_refused_before_any_bar_is_read(
    state: CertificationState, certifying: bool, now: datetime
) -> None:
    """R-2: dropping any one of mode, state or date gate refuses OOS without reading the source."""
    source = _FakeSource(_all_bars())
    manifest = _manifest(DataSplit.VALIDATION, DataSplit.OOS)
    with pytest.raises(OOSDataLeakageError):
        if certifying:
            with certification_context():
                _load(source, manifest, _VAL_START, _END_OF_DATA, state, now)
        else:
            _load(source, manifest, _VAL_START, _END_OF_DATA, state, now)
    assert source.yielded == []


# SEV-1: pre-registration §4 OOS read refusal (no rubric row; integrity rule 1.8)
def test_oos_is_refused_even_when_not_pinned() -> None:
    """R-2: an OOS range is refused as a leak before the missing pin is reported."""
    with pytest.raises(OOSDataLeakageError):
        _load(_FakeSource(_all_bars()), _manifest(DataSplit.TRAIN), _OOS_START, _END_OF_DATA)


def test_oos_is_returned_inside_certification_after_the_window() -> None:
    """D-3: certification mode + SHADOW_CERTIFIED + clock past OOS end is the one allowed read."""
    with certification_context():
        got = _load(
            _FakeSource(_all_bars()),
            _manifest(DataSplit.OOS),
            _OOS_START,
            _END_OF_DATA,
            CertificationState.SHADOW_CERTIFIED,
            _AFTER_OOS,
        )
    assert len(got.bars) == 6
    assert [e.split for e in got.entries] == [DataSplit.OOS]


def test_pin_dataset_produces_the_entry_load_verifies() -> None:
    """A freshly pinned entry verifies against the same source."""
    source = _FakeSource(_all_bars())
    skeleton = DatasetManifest(experiment_id="test-v1", splits=_SPLITS)
    entry = asyncio.run(
        pin_dataset(
            source,
            skeleton,
            pair=_BTC,
            interval=_H4,
            split=DataSplit.VALIDATION,
            source_name="test",
            state=CertificationState.UNTRUSTED,
            now=_DURING_OOS,
        )
    )
    assert entry == _entry(DataSplit.VALIDATION)
    manifest = replace(skeleton, entries=(entry,))
    with pytest.raises(DatasetNotPinnedError):
        _load(_FakeSource(_all_bars()), manifest, _TRAIN_START, _OOS_START)
    got = _load(_FakeSource(_all_bars()), manifest, _VAL_START, _OOS_START)
    assert len(got.bars) == 6


# SEV-1: pre-registration §4 OOS read refusal (no rubric row; integrity rule 1.8)
def test_pinning_oos_is_refused_outside_certification() -> None:
    """Hashing OOS reads OOS, so pinning it is subject to the same refusal."""
    source = _FakeSource(_all_bars())
    with pytest.raises(OOSDataLeakageError):
        asyncio.run(
            pin_dataset(
                source,
                DatasetManifest(experiment_id="test-v1", splits=_SPLITS),
                pair=_BTC,
                interval=_H4,
                split=DataSplit.OOS,
                source_name="test",
                state=CertificationState.UNTRUSTED,
                now=_AFTER_OOS,
            )
        )
    assert source.yielded == []


@pytest.fixture(scope="module")
def pre_reg() -> PreRegistration:
    """Return the signed pre-registration from the repo."""
    here = Path(__file__).resolve().parent
    for candidate in [here, *here.parents]:
        if (candidate / "pyproject.toml").exists():
            return PreRegistration(project_root=candidate)
    pytest.skip("Could not find project root")


def _signed_manifest(pre_reg: PreRegistration) -> DatasetManifest:
    w = pre_reg.data_windows()
    return DatasetManifest(
        experiment_id=pre_reg.experiment_id(),
        splits={
            DataSplit.TRAIN: SplitWindow(w.train_start, w.train_end),
            DataSplit.VALIDATION: SplitWindow(w.validation_start, w.validation_end),
            DataSplit.OOS: SplitWindow(w.oos_start, w.oos_end),
        },
    )


def test_manifest_matching_the_preregistration_is_accepted(pre_reg: PreRegistration) -> None:
    """D-4: identical experiment id and windows pass without error."""
    assert_manifest_matches_preregistration(
        _signed_manifest(pre_reg),
        experiment_id=pre_reg.experiment_id(),
        windows=pre_reg.data_windows(),
    )


def test_manifest_with_a_shifted_oos_start_is_refused(pre_reg: PreRegistration) -> None:
    """D-4: moving the OOS start by one day would silently re-label OOS bars as Validation."""
    manifest = _signed_manifest(pre_reg)
    splits = dict(manifest.splits)
    oos = splits[DataSplit.OOS]
    splits[DataSplit.OOS] = SplitWindow(oos.start + _DAY, oos.end)
    with pytest.raises(ValueError, match="oos window"):
        assert_manifest_matches_preregistration(
            replace(manifest, splits=splits),
            experiment_id=pre_reg.experiment_id(),
            windows=pre_reg.data_windows(),
        )


def test_manifest_for_another_experiment_is_refused(pre_reg: PreRegistration) -> None:
    """D-4: a manifest pinned for a different experiment cannot vouch for this one."""
    with pytest.raises(ValueError, match="experiment"):
        assert_manifest_matches_preregistration(
            replace(_signed_manifest(pre_reg), experiment_id="swing-mvp1-v0"),
            experiment_id=pre_reg.experiment_id(),
            windows=pre_reg.data_windows(),
        )
