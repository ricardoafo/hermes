#!/usr/bin/env python3
"""Binance Vision historical klines downloader — pre-reg v1 §4 window.

Downloads all monthly klines ZIPs for BTC/ETH/SOL x 1h/4h/1d across
the uniform window 2020-09-01 to 2026-12-31 (per swing-mvp1
pre-registration v1 §4). Each ZIP is SHA256-verified against
Binance's `.CHECKSUM` companion before being written to disk. Progress
is persisted to `data-manifest.json` every 20 files so an interrupt
does not lose state.

Reference:
    - docs/experiments/swing-mvp1/data-ingest-plan.md (URL patterns)
    - docs/experiments/swing-mvp1/pre-registration-v1.md sec 4 (frozen windows)
    - openspec/changes/data-ingest-binance-vision/change.md

Usage:
    python scripts/data/ingest_binance_vision.py [OPTIONS]

    --data-dir PATH        Root data dir (default: $HERMES_DATA_DIR or D:/hermes-data)
    --manifest PATH        Manifest JSON path (default: <data-dir>/manifests/data-manifest.json)
    --pair PAIR            Restrict to a single pair (repeatable)
    --interval INTERVAL    Restrict to a single interval (repeatable)
    --dry-run              List targets without downloading

Examples:
    # Full ingest (549-ish files, 2-4 hours)
    python scripts/data/ingest_binance_vision.py

    # Small sanity subset (BTC 1d only, ~61 files, ~5 min)
    python scripts/data/ingest_binance_vision.py --pair BTCUSDT --interval 1d

    # Preview the target list without downloading
    python scripts/data/ingest_binance_vision.py --dry-run

Resume semantics: on rerun, files present on disk with a matching
manifest entry are skipped. Files missing from disk or manifest are
re-downloaded and re-verified.

Exit codes:
    0 - all targets downloaded (or skipped as already present)
    1 - one or more targets failed (see stderr for details)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

# --- Pre-registration v1 sec 4 constants (BINDING; do not edit without a v2) ---
PAIRS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
INTERVALS: tuple[str, ...] = ("1h", "4h", "1d")
BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"
START_YEAR, START_MONTH = 2020, 9  # uniform start (SOL availability constraint)
END_YEAR, END_MONTH = 2026, 12  # pre-reg OOS window end
DEFAULT_DATA_DIR = "D:/hermes-data"
MANIFEST_CHECKPOINT_EVERY = 20  # files
DISK_WARN_GB = 100  # print warning if less than this free at start
HTTP_TIMEOUT_SECONDS = 60


def iter_months() -> list[tuple[int, int]]:
    """Yield (year, month) pairs from START to END inclusive."""
    out: list[tuple[int, int]] = []
    y, m = START_YEAR, START_MONTH
    while (y, m) <= (END_YEAR, END_MONTH):
        out.append((y, m))
        m += 1
        if m > 12:
            y += 1
            m = 1
    return out


def download(url: str) -> bytes:
    """GET url and return bytes. Raises HTTPError/URLError on failure."""
    with urlopen(url, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return response.read()


def sha256_hex(data: bytes) -> str:
    """Return lowercase hex SHA-256 of data."""
    return hashlib.sha256(data).hexdigest()


def check_disk_space(root: Path) -> None:
    """Print a warning if less than DISK_WARN_GB free on the root's disk."""
    try:
        _total, _used, free = shutil.disk_usage(root)
        free_gb = free / (1024**3)
        if free_gb < DISK_WARN_GB:
            print(
                f"WARN: only {free_gb:.1f} GB free on {root}. "
                f"Full ingest expects ~50 GB compressed ZIPs. "
                f"Consider freeing space before continuing.",
                file=sys.stderr,
            )
    except OSError:
        # disk_usage can fail on some Windows mounts; not worth blocking
        pass


def process_file(
    pair: str,
    interval: str,
    year: int,
    month: int,
    root: Path,
    manifest: dict,
) -> tuple[bool, str]:
    """Download + verify one (pair, interval, year, month) target.

    Returns (success, message). success=True with "skipped" message means
    the file was already present and verified previously.
    """
    filename = f"{pair}-{interval}-{year:04d}-{month:02d}.zip"
    url = f"{BASE_URL}/{pair}/{interval}/{filename}"
    checksum_url = f"{url}.CHECKSUM"

    dest_dir = root / "klines" / pair / interval / f"{year:04d}" / f"{month:02d}"
    dest = dest_dir / filename
    manifest_key = f"{pair}/{interval}/{year:04d}-{month:02d}"

    # Resume shortcut: file on disk AND manifest entry present -> trust it
    if manifest_key in manifest and dest.exists():
        return True, "skipped (already in manifest)"

    dest_dir.mkdir(parents=True, exist_ok=True)

    # Fetch the checksum first (small, cheap; a 404 here means the month
    # is not yet published or the pair did not exist in that month)
    try:
        checksum_bytes = download(checksum_url)
    except HTTPError as err:
        if err.code == 404:
            return False, "404 (not yet published or pair pre-listing)"
        return False, f"checksum HTTP {err.code}"
    except URLError as err:
        return False, f"checksum network error: {err.reason}"

    # Binance format: "<sha256hex>  <filename>\n"
    expected = checksum_bytes.decode("utf-8", errors="replace").split()[0]

    # Fetch the actual ZIP
    try:
        data = download(url)
    except HTTPError as err:
        return False, f"data HTTP {err.code}"
    except URLError as err:
        return False, f"data network error: {err.reason}"

    actual = sha256_hex(data)
    if actual != expected:
        return (
            False,
            f"SHA256 mismatch: expected {expected}, got {actual} ({len(data)} bytes)",
        )

    dest.write_bytes(data)
    manifest[manifest_key] = {
        "sha256": actual,
        "url": url,
        "size_bytes": len(data),
        "downloaded_at": date.today().isoformat(),
    }
    return True, f"OK ({len(data):,} bytes)"


def save_manifest(manifest_path: Path, manifest: dict) -> None:
    """Write manifest to disk atomically (temp file + rename)."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    tmp.replace(manifest_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Root data dir (default: $HERMES_DATA_DIR or D:/hermes-data)",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="Manifest JSON path (default: <data-dir>/manifests/data-manifest.json)",
    )
    parser.add_argument(
        "--pair",
        action="append",
        choices=list(PAIRS),
        help="Restrict to one or more pairs (repeatable)",
    )
    parser.add_argument(
        "--interval",
        action="append",
        choices=list(INTERVALS),
        help="Restrict to one or more intervals (repeatable)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List targets without downloading",
    )
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir or os.environ.get("HERMES_DATA_DIR", DEFAULT_DATA_DIR))
    manifest_path = Path(args.manifest or (data_dir / "manifests" / "data-manifest.json"))

    manifest: dict = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
        except json.JSONDecodeError as err:
            print(f"ERROR: manifest at {manifest_path} is not valid JSON: {err}", file=sys.stderr)
            return 1

    pairs = tuple(args.pair) if args.pair else PAIRS
    intervals = tuple(args.interval) if args.interval else INTERVALS
    months = iter_months()

    targets = [
        (pair, interval, year, month)
        for pair in pairs
        for interval in intervals
        for year, month in months
    ]

    print(f"Target: {len(targets)} files")
    print(f"  Pairs:     {', '.join(pairs)}")
    print(f"  Intervals: {', '.join(intervals)}")
    print(f"  Window:    {START_YEAR:04d}-{START_MONTH:02d} .. {END_YEAR:04d}-{END_MONTH:02d}")
    print(f"  Data dir:  {data_dir}")
    print(f"  Manifest:  {manifest_path}")

    if args.dry_run:
        print()
        print("Sample of targets (first 5, last 5):")
        for pair, interval, year, month in targets[:5]:
            print(f"  {pair}/{interval}/{year:04d}-{month:02d}")
        if len(targets) > 10:
            print("  ...")
        for pair, interval, year, month in targets[-5:]:
            print(f"  {pair}/{interval}/{year:04d}-{month:02d}")
        return 0

    check_disk_space(data_dir)

    ok = failed = skipped = 0
    for idx, (pair, interval, year, month) in enumerate(targets, start=1):
        try:
            success, message = process_file(pair, interval, year, month, data_dir, manifest)
        except Exception as err:
            failed += 1
            print(
                f"[{idx:4d}/{len(targets)}] {pair}/{interval}/{year:04d}-{month:02d}: "
                f"UNEXPECTED ERROR - {type(err).__name__}: {err}",
                file=sys.stderr,
            )
            continue

        if success and "skipped" in message:
            skipped += 1
        elif success:
            ok += 1
        else:
            failed += 1

        print(f"[{idx:4d}/{len(targets)}] {pair}/{interval}/{year:04d}-{month:02d}: {message}")

        # Periodic checkpoint so an interrupt does not lose the manifest
        if idx % MANIFEST_CHECKPOINT_EVERY == 0:
            save_manifest(manifest_path, manifest)

    # Final flush
    save_manifest(manifest_path, manifest)

    print()
    print(f"Done: {ok} downloaded, {skipped} skipped, {failed} failed (of {len(targets)} total)")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
