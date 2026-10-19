#!/usr/bin/env python3
"""Pin the Train and Validation datasets in the data manifest.

Reads the Binance Vision klines archive under ``$HERMES_DATA_DIR`` (or
``--data-dir``), hashes every (pair, interval, split) dataset with
``bars_content_sha256``, and writes
``docs/experiments/swing-mvp1/data-manifest.json`` (pre-registration §4).

OOS is never pinned here: pinning reads OOS bars, which the certification
lifecycle allows only at the one-shot OOS test (pre-registration §7, ADR-014).

Usage:
    python scripts/data/build_dataset_manifest.py
    python scripts/data/build_dataset_manifest.py --data-dir D:/hermes-data
    python scripts/data/build_dataset_manifest.py --output /tmp/manifest.json

Exit codes:
    0 - manifest written
    1 - a dataset is empty (missing ingest)
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

from returns.result import Success

from hermes.adapters.data_manifest.json_file import JsonFileDatasetManifest
from hermes.adapters.market_data.binance_historical import BinanceHistoricalAdapter
from hermes.application.calibration.lifecycle import CertificationState
from hermes.application.strategy_protocol.pinned_dataset import pin_dataset
from hermes.application.strategy_protocol.preregistration import PreRegistration
from hermes.domain.market_data.dataset_manifest import (
    DatasetEntry,
    DatasetManifest,
    DataSplit,
    SplitWindow,
)
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair

DEFAULT_OUTPUT = Path("docs/experiments/swing-mvp1/data-manifest.json")
SOURCE_NAME = "binance-vision"
# The backtest driver reads 4h and 1d. The 1h archive has a malformed
# 2020-12-21 row the adapter rejects, so it stays unpinned until it is repaired.
INTERVALS = (KlineInterval.H4, KlineInterval.D1)
PINNED_SPLITS = (DataSplit.TRAIN, DataSplit.VALIDATION)


def manifest_skeleton(pre_reg: PreRegistration) -> DatasetManifest:
    """Return a manifest with the signed split windows and no entries."""
    w = pre_reg.data_windows()
    return DatasetManifest(
        experiment_id=pre_reg.experiment_id(),
        splits={
            DataSplit.TRAIN: SplitWindow(w.train_start, w.train_end),
            DataSplit.VALIDATION: SplitWindow(w.validation_start, w.validation_end),
            DataSplit.OOS: SplitWindow(w.oos_start, w.oos_end),
        },
    )


def universe_pairs(pre_reg: PreRegistration) -> list[Pair]:
    """Return the pre-registered universe as pairs."""
    pairs = []
    for symbol in pre_reg.universe():
        parsed = Pair.from_str(symbol)
        if not isinstance(parsed, Success):
            raise ValueError(f"universe symbol {symbol!r} is not a known pair")
        pairs.append(parsed.unwrap())
    return pairs


async def _build(data_dir: Path | None, output: Path) -> int:
    pre_reg = PreRegistration()
    skeleton = manifest_skeleton(pre_reg)
    source = BinanceHistoricalAdapter.create(data_dir=data_dir)
    now = datetime.now(UTC)
    entries: list[DatasetEntry] = []
    for pair in universe_pairs(pre_reg):
        for interval in INTERVALS:
            for split in PINNED_SPLITS:
                entry = await pin_dataset(
                    source,
                    skeleton,
                    pair=pair,
                    interval=interval,
                    split=split,
                    source_name=SOURCE_NAME,
                    state=CertificationState.UNTRUSTED,
                    now=now,
                )
                print(f"{entry.dataset_id}: {entry.row_count} rows {entry.content_sha256}")
                if entry.row_count == 0:
                    print(
                        f"ERROR: {entry.dataset_id} is empty; run the ingest first", file=sys.stderr
                    )
                    return 1
                entries.append(entry)
    manifest = DatasetManifest(
        experiment_id=skeleton.experiment_id, splits=skeleton.splits, entries=tuple(entries)
    )
    JsonFileDatasetManifest(output).write(manifest)
    print(f"Manifest written: {output} ({len(entries)} datasets)")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and build the manifest."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--data-dir", type=Path, default=None, help="Default: $HERMES_DATA_DIR")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Manifest path")
    args = parser.parse_args(argv)
    return asyncio.run(_build(args.data_dir, args.output))


if __name__ == "__main__":
    sys.exit(main())
