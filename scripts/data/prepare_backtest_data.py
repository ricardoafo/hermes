#!/usr/bin/env python3
"""Fill and verify the pinned klines a backtest reads, before it runs (#925).

For one split (``train`` or ``validation``) of the experiment data manifest,
downloads only the monthly klines ZIPs missing from the data dir, then checks
every pinned dataset of that split with the backtest loader's own read path
(``load_pinned_bars``: same parsing, same ``bars_content_sha256``). Any failed
download or hash mismatch prints an ``ERROR:`` line and exits 1, so an
automated backtest never reads unverified bars.

``validation`` also prepares ``train``: a Validation backtest loads up to 300
days of warm-up bars from Train and verifies the whole Train split.

OOS is never prepared. The split choice excludes it, and the ingest refuses to
build any target on or after the OOS start (pre-registration v1 sec 4).

Usage:
    python scripts/data/prepare_backtest_data.py --split train|validation
        [--manifest docs/experiments/swing-mvp1/data-manifest.json]
        [--data-dir PATH]  (default: $HERMES_DATA_DIR or D:/hermes-data)

Exit codes:
    0 - every dataset of the split is present and matches the manifest
    1 - a download failed, a dataset mismatched, or the manifest was refused
"""

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from ingest_binance_vision import (
    DEFAULT_DATA_DIR,
    EXPERIMENT_MANIFEST,
    Target,
    iter_months,
    process_target,
    save_manifest,
)

from hermes.adapters.data_manifest.json_file import JsonFileDatasetManifest
from hermes.adapters.market_data.binance_historical import BinanceHistoricalAdapter
from hermes.application.strategy_protocol.pinned_dataset import (
    assert_manifest_matches_preregistration,
    load_pinned_bars,
)
from hermes.domain.calibration.certification import CertificationState
from hermes.domain.errors import HermesError
from hermes.domain.market_data.dataset_manifest import DatasetEntry, DatasetManifest, DataSplit
from hermes.infrastructure.project_root import load_pre_registration

SPLIT_CHOICES = ("train", "validation")


def splits_to_prepare(split: str) -> tuple[DataSplit, ...]:
    """Return the splits a backtest on ``split`` reads, in chronological order.

    Raises:
        ValueError: ``split`` is not ``train`` or ``validation``.
    """
    if split not in SPLIT_CHOICES:
        raise ValueError(f"split {split!r} cannot be prepared; choose one of {SPLIT_CHOICES}")
    if split == "train":
        return (DataSplit.TRAIN,)
    return (DataSplit.TRAIN, DataSplit.VALIDATION)


def _targets(manifest: DatasetManifest, entry: DatasetEntry) -> list[Target]:
    window = manifest.splits[entry.split]
    symbol = f"{entry.pair.base.value}{entry.pair.quote.value}"
    months = iter_months(
        (window.start.year, window.start.month), (window.end.year, window.end.month)
    )
    return [Target("klines", symbol, entry.interval.value, y, m) for y, m in months]


def fetch_missing(manifest: DatasetManifest, entries: list[DatasetEntry], data_dir: Path) -> int:
    """Download the monthly ZIPs of ``entries`` absent from ``data_dir``.

    Returns:
        The number of months that could not be downloaded.
    """
    missing = [
        t for e in entries for t in _targets(manifest, e) if not t.local_path(data_dir).exists()
    ]
    if not missing:
        return 0
    ingest_manifest_path = data_dir / "manifests" / "data-manifest.json"
    ingest_manifest: dict[str, object] = {}
    failed = 0
    for target in missing:
        try:
            outcome, message = process_target(target, data_dir, ingest_manifest)
        except Exception as err:
            outcome, message = "failed", f"{type(err).__name__}: {err}"
        if outcome in {"ok", "skipped"}:
            print(f"{target.label()}: {message}")
        else:
            failed += 1
            print(f"ERROR: {target.label()}: {message}", file=sys.stderr)
    save_manifest(ingest_manifest_path, ingest_manifest)
    return failed


async def verify(manifest: DatasetManifest, entries: list[DatasetEntry], data_dir: Path) -> int:
    """Check each entry through the backtest loader; return how many failed."""
    source = BinanceHistoricalAdapter.create(data_dir=data_dir)
    failed = 0
    for entry in entries:
        window = manifest.splits[entry.split]
        try:
            await load_pinned_bars(
                source,
                manifest,
                pair=entry.pair,
                interval=entry.interval,
                start=window.start,
                end=window.end,
                state=CertificationState.UNTRUSTED,
                now=datetime.now(UTC),
            )
        except HermesError as err:
            failed += 1
            print(f"ERROR: {entry.dataset_id}: {err}", file=sys.stderr)
        else:
            print(f"{entry.dataset_id}: verified ({entry.row_count} rows)")
    return failed


def prepare(manifest_path: Path, data_dir: Path, split: str) -> int:
    """Fill and verify every dataset a backtest on ``split`` reads.

    Args:
        manifest_path: Experiment data manifest.
        data_dir: Binance Vision archive root.
        split: ``train`` or ``validation``.

    Returns:
        Process exit code: 0 when every dataset verifies, else 1.
    """
    try:
        splits = splits_to_prepare(split)
        manifest = JsonFileDatasetManifest(manifest_path).load()
        pre_reg = load_pre_registration()
        assert_manifest_matches_preregistration(
            manifest, experiment_id=pre_reg.experiment_id(), windows=pre_reg.data_windows()
        )
    except (HermesError, ValueError) as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1
    entries = [e for e in manifest.entries if e.split in splits]
    if not entries:
        print(f"ERROR: {manifest_path} pins no dataset for {split}", file=sys.stderr)
        return 1
    if fetch_missing(manifest, entries, data_dir):
        return 1
    if asyncio.run(verify(manifest, entries, data_dir)):
        return 1
    print(f"Prepared {len(entries)} datasets for {split} in {data_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and prepare the data."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--split", required=True, choices=SPLIT_CHOICES)
    parser.add_argument("--manifest", type=Path, default=EXPERIMENT_MANIFEST)
    parser.add_argument(
        "--data-dir", type=Path, default=None, help="Default: $HERMES_DATA_DIR or D:/hermes-data"
    )
    args = parser.parse_args(argv)
    data_dir = args.data_dir or Path(os.environ.get("HERMES_DATA_DIR", DEFAULT_DATA_DIR))
    return prepare(args.manifest, data_dir, args.split)


if __name__ == "__main__":
    sys.exit(main())
