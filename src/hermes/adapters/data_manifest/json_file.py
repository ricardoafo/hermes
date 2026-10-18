"""JSON file store for the dataset manifest (schema version 1).

File shape::

    {
      "schema_version": 1,
      "experiment_id": "swing-mvp1-v1",
      "splits": {"train": {"start": "<ISO>", "end": "<ISO>"}, "validation": {...}, "oos": {...}},
      "datasets": [
        {"dataset_id": "klines/BTCUSDT/4h/train", "pair": "BTC/USDT", "interval": "4h",
         "split": "train", "source": "binance-vision", "row_count": 8040, "content_sha256": "<hex>"}
      ]
    }

``dataset_id`` is written for readers and checked on load against the id the
entry's fields produce, so a hand edit cannot relabel a dataset.

Reference: openspec/archive/356-data-manifest/change.md Design.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from returns.result import Failure

from hermes.domain.errors import DataManifestFormatError
from hermes.domain.market_data.dataset_manifest import (
    DatasetEntry,
    DatasetManifest,
    DataSplit,
    SplitWindow,
)
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair

__all__ = ["SCHEMA_VERSION", "JsonFileDatasetManifest"]

SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class JsonFileDatasetManifest:
    """Load and write a :class:`DatasetManifest` as a JSON file.

    Args:
        path: Manifest file path.
    """

    path: Path

    def load(self) -> DatasetManifest:
        """Read, parse and validate the manifest file.

        Returns:
            The validated manifest.

        Raises:
            DataManifestFormatError: The file is missing, not JSON, not schema
                version 1, or fails domain validation.
        """
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DataManifestFormatError(path=str(self.path), msg=str(exc)) from exc
        try:
            return _parse(raw)
        except (KeyError, TypeError, ValueError) as exc:
            msg = f"{type(exc).__name__}: {exc}"
            raise DataManifestFormatError(path=str(self.path), msg=msg) from exc

    def write(self, manifest: DatasetManifest) -> None:
        """Write ``manifest`` as sorted, indented JSON with a trailing newline.

        Args:
            manifest: Manifest to store.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(_serialise(manifest), indent=2, sort_keys=True) + "\n"
        self.path.write_text(text, encoding="utf-8", newline="\n")


def _parse_pair(value: str) -> Pair:
    result = Pair.from_str(value)
    if isinstance(result, Failure):
        raise ValueError(f"invalid pair {value!r}")
    return result.unwrap()


def _parse_entry(raw: dict[str, Any]) -> DatasetEntry:
    entry = DatasetEntry(
        pair=_parse_pair(raw["pair"]),
        interval=KlineInterval(raw["interval"]),
        split=DataSplit(raw["split"]),
        source=raw["source"],
        row_count=int(raw["row_count"]),
        content_sha256=raw["content_sha256"],
    )
    if raw["dataset_id"] != entry.dataset_id:
        raise ValueError(
            f"dataset_id {raw['dataset_id']!r} does not match its fields ({entry.dataset_id!r})"
        )
    return entry


def _parse(raw: dict[str, Any]) -> DatasetManifest:
    if raw.get("schema_version") != SCHEMA_VERSION:
        found = raw.get("schema_version")
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}, got {found!r}")
    splits = {
        DataSplit(name): SplitWindow(
            start=datetime.fromisoformat(window["start"]),
            end=datetime.fromisoformat(window["end"]),
        )
        for name, window in raw["splits"].items()
    }
    entries = tuple(_parse_entry(e) for e in raw["datasets"])
    return DatasetManifest(experiment_id=raw["experiment_id"], splits=splits, entries=entries)


def _serialise(manifest: DatasetManifest) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": manifest.experiment_id,
        "splits": {
            split.value: {"start": window.start.isoformat(), "end": window.end.isoformat()}
            for split, window in manifest.splits.items()
        },
        "datasets": [
            {
                "dataset_id": e.dataset_id,
                "pair": str(e.pair),
                "interval": e.interval.value,
                "split": e.split.value,
                "source": e.source,
                "row_count": e.row_count,
                "content_sha256": e.content_sha256,
            }
            for e in sorted(manifest.entries, key=lambda e: e.dataset_id)
        ],
    }
