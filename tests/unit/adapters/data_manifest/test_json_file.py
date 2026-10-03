"""Unit tests for the dataset manifest JSON file adapter.

Invariants under test (openspec/changes/356-data-manifest/change.md Design):

- Write then load returns an equal manifest.
- A malformed, mislabelled or wrong-version file fails with DataManifestFormatError.
- The committed manifest loads and matches the signed pre-registration.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hermes.adapters.data_manifest.json_file import JsonFileDatasetManifest
from hermes.application.strategy_protocol.pinned_dataset import (
    assert_manifest_matches_preregistration,
)
from hermes.application.strategy_protocol.preregistration import PreRegistration
from hermes.domain.errors import DataManifestFormatError
from hermes.domain.market_data.dataset_manifest import (
    DatasetEntry,
    DatasetManifest,
    DataSplit,
    SplitWindow,
)
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.ports.dataset_manifest import DatasetManifestPort

_ROOT = Path(__file__).resolve().parents[4]
_COMMITTED = _ROOT / "docs" / "experiments" / "swing-mvp1" / "data-manifest.json"


def _manifest() -> DatasetManifest:
    return DatasetManifest(
        experiment_id="test-v1",
        splits={
            DataSplit.TRAIN: SplitWindow(
                datetime(2020, 9, 1, tzinfo=UTC), datetime(2024, 6, 30, 23, 59, 59, tzinfo=UTC)
            ),
            DataSplit.VALIDATION: SplitWindow(
                datetime(2024, 7, 1, tzinfo=UTC), datetime(2025, 8, 31, 23, 59, 59, tzinfo=UTC)
            ),
            DataSplit.OOS: SplitWindow(
                datetime(2025, 9, 1, tzinfo=UTC), datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC)
            ),
        },
        entries=(
            DatasetEntry(
                pair=Pair(base=Currency.ETH, quote=Currency.USDT),
                interval=KlineInterval.D1,
                split=DataSplit.VALIDATION,
                source="binance-vision",
                row_count=427,
                content_sha256="b" * 64,
            ),
            DatasetEntry(
                pair=Pair(base=Currency.BTC, quote=Currency.USDT),
                interval=KlineInterval.H4,
                split=DataSplit.TRAIN,
                source="binance-vision",
                row_count=8394,
                content_sha256="a" * 64,
            ),
        ),
    )


def test_adapter_satisfies_the_port(tmp_path: Path) -> None:
    """The JSON adapter can be injected wherever DatasetManifestPort is expected."""
    assert isinstance(JsonFileDatasetManifest(tmp_path / "m.json"), DatasetManifestPort)


def test_write_then_load_round_trips(tmp_path: Path) -> None:
    """Every field survives storage; entries come back sorted by dataset id."""
    store = JsonFileDatasetManifest(tmp_path / "nested" / "m.json")
    store.write(_manifest())
    loaded = store.load()
    assert loaded.experiment_id == "test-v1"
    assert loaded.splits == _manifest().splits
    assert loaded.entries == tuple(sorted(_manifest().entries, key=lambda e: e.dataset_id))


def test_written_file_records_dataset_ids(tmp_path: Path) -> None:
    """Readers of the JSON see the same ids reports reference."""
    path = tmp_path / "m.json"
    JsonFileDatasetManifest(path).write(_manifest())
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 1
    assert [d["dataset_id"] for d in raw["datasets"]] == [
        "klines/BTCUSDT/4h/train",
        "klines/ETHUSDT/1d/validation",
    ]


def _write_raw(path: Path, mutate: object) -> JsonFileDatasetManifest:
    store = JsonFileDatasetManifest(path)
    store.write(_manifest())
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert callable(mutate)
    mutate(raw)
    path.write_text(json.dumps(raw), encoding="utf-8")
    return store


def test_relabelled_dataset_id_is_refused(tmp_path: Path) -> None:
    """A hand edit that renames an entry without changing its fields is caught on load."""
    store = _write_raw(
        tmp_path / "m.json",
        lambda raw: raw["datasets"][0].__setitem__("dataset_id", "klines/BTCUSDT/4h/validation"),
    )
    with pytest.raises(DataManifestFormatError, match="does not match its fields"):
        store.load()


def test_unknown_schema_version_is_refused(tmp_path: Path) -> None:
    """Only schema version 1 is understood."""
    store = _write_raw(tmp_path / "m.json", lambda raw: raw.__setitem__("schema_version", 2))
    with pytest.raises(DataManifestFormatError, match="schema_version"):
        store.load()


def test_invalid_hash_is_refused(tmp_path: Path) -> None:
    """Domain validation errors surface as DataManifestFormatError naming the file."""
    path = tmp_path / "m.json"
    store = _write_raw(path, lambda raw: raw["datasets"][0].__setitem__("content_sha256", "xyz"))
    with pytest.raises(DataManifestFormatError, match=str(path).replace("\\", "\\\\")):
        store.load()


def test_missing_file_is_refused(tmp_path: Path) -> None:
    """A missing manifest stops the run instead of loading unverified bars."""
    with pytest.raises(DataManifestFormatError):
        JsonFileDatasetManifest(tmp_path / "absent.json").load()


def test_non_json_file_is_refused(tmp_path: Path) -> None:
    """A truncated or corrupted file is reported, not partially parsed."""
    path = tmp_path / "m.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(DataManifestFormatError):
        JsonFileDatasetManifest(path).load()


def test_committed_manifest_matches_the_signed_preregistration() -> None:
    """The pinned manifest in the repo uses the signed windows and pins no OOS dataset."""
    manifest = JsonFileDatasetManifest(_COMMITTED).load()
    pre_reg = PreRegistration(project_root=_ROOT)
    assert_manifest_matches_preregistration(
        manifest, experiment_id=pre_reg.experiment_id(), windows=pre_reg.data_windows()
    )
    assert {e.split for e in manifest.entries} == {DataSplit.TRAIN, DataSplit.VALIDATION}
    assert len(manifest.entries) == 12
