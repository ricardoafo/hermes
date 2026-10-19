#!/usr/bin/env python3
"""Binance Vision historical data downloader — pre-reg v1 sec 4 window.

Downloads monthly ZIPs from Binance Vision for BTC/ETH/SOL from the
uniform start 2020-09-01 up to, but never including, the sealed OOS window
(per swing-mvp1 pre-registration v1 sec 4). The OOS start is read from
``splits.oos.start`` of docs/experiments/swing-mvp1/data-manifest.json; no
month on or after it is ever enumerated or downloaded, and no flag changes
that (#925). Supports two dataset kinds:

  * klines     - OHLCV bars at 1h/4h/1d intervals (default), 1m on request
  * agg_trades - aggregated trades, no interval dimension

Each ZIP is SHA256-verified against Binance's `.CHECKSUM` companion
before being written to disk. Progress is persisted to
`data-manifest.json` every 20 files so an interrupt does not lose
state. Manifest keys are namespaced by dataset so klines and
agg_trades coexist.

Reference:
    - docs/experiments/swing-mvp1/data-ingest-plan.md (URL patterns)
    - docs/experiments/swing-mvp1/pre-registration-v1.md sec 4 (windows)
    - openspec/archive/data-ingest-binance-vision/change.md (original)
    - openspec/archive/data-ingest-agg-trades-and-peg/change.md (this extension)

Usage:
    python scripts/data/ingest_binance_vision.py [OPTIONS]

    --include CHOICE       Dataset(s) to download: klines, agg_trades, or all
                           (default: klines)
    --data-dir PATH        Root data dir (default: $HERMES_DATA_DIR or D:/hermes-data)
    --manifest PATH        Manifest JSON path (default: <data-dir>/manifests/data-manifest.json)
    --pair PAIR            Restrict to a single pair (repeatable)
    --interval INTERVAL    Restrict to a single interval (klines only, repeatable)
    --dry-run              List targets without downloading

Examples:
    # Full klines ingest (540 files, ~25 MB, ~10-20 min)
    python scripts/data/ingest_binance_vision.py

    # Add agg_trades on top (klines already ingested? resume-safe)
    # WARNING: agg_trades is LARGE — ~35 GB across 180 files (BTC ~300-500 MB/month,
    # ETH/SOL smaller). Full ingest can take 2-4 hours on a residential connection.
    python scripts/data/ingest_binance_vision.py --include agg_trades

    # Download both in one run (klines + agg_trades)
    python scripts/data/ingest_binance_vision.py --include all

    # Small sanity subset (BTC 1d only, 60 files, ~2 min)
    python scripts/data/ingest_binance_vision.py --pair BTCUSDT --interval 1d

    # Preview target set without downloading
    python scripts/data/ingest_binance_vision.py --include all --dry-run

Resume semantics: on rerun, files present on disk with a matching
namespaced manifest entry are skipped. Missing files (either on disk
or missing manifest entry) are re-downloaded and re-verified.

Exit codes:
    0 - no unexpected failures (`not_yet_published` 404s do not count)
    1 - one or more unexpected failures (see stderr for details)
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

import httpx

# Shared read-modify-atomic-write transaction for the multi-writer
# manifest. See scripts/data/_manifest_util.py + change
# `data-manifest-file-lock` (#257) for the race narrative.
from _manifest_util import manifest_read, manifest_transaction

# --- Pre-registration v1 sec 4 constants (BINDING; do not edit without a v2) ---
PAIRS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
INTERVALS: tuple[str, ...] = ("1h", "4h", "1d")
# Opt-in only: a full default run never downloads minute bars (~400 MB for the
# Train window); the stop slippage check (#1104) asks for them by name.
ON_REQUEST_INTERVALS: tuple[str, ...] = ("1m",)
BASE_URL = "https://data.binance.vision/data/spot/monthly"
START_YEAR, START_MONTH = 2020, 9  # uniform start (SOL availability constraint)
EXPERIMENT_MANIFEST = Path(__file__).resolve().parents[2] / (
    "docs/experiments/swing-mvp1/data-manifest.json"
)
DEFAULT_DATA_DIR = "D:/hermes-data"
MANIFEST_CHECKPOINT_EVERY = 20  # files
DISK_WARN_GB = 100  # print warning if less than this free at start
HTTP_TIMEOUT_SECONDS = 60

# Dataset registry — one entry per Binance Vision dataset kind
INCLUDE_CHOICES = ("klines", "agg_trades", "all")


def oos_start_month(manifest_path: Path) -> tuple[int, int]:
    """Return the (year, month) of ``splits.oos.start`` in an experiment data manifest."""
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    start = datetime.fromisoformat(payload["splits"]["oos"]["start"])
    return start.year, start.month


# Pre-registration v1 sec 4 keeps OOS sealed until the one-shot OOS test.
# Read once at import so every code path, whatever its arguments, shares it.
SEALED_FROM: tuple[int, int] = oos_start_month(EXPERIMENT_MANIFEST)


class SealedMonthError(ValueError):
    """A target month lies on or after the sealed OOS start."""


def iter_months(
    start: tuple[int, int] = (START_YEAR, START_MONTH),
    end: tuple[int, int] | None = None,
) -> list[tuple[int, int]]:
    """Return (year, month) pairs from ``start`` to ``end`` inclusive, stopping before OOS.

    Args:
        start: First month.
        end: Last month; ``None`` means the month before the OOS start. An
            ``end`` on or after the OOS start is cut to the month before it.
    """
    out: list[tuple[int, int]] = []
    y, m = start
    while (y, m) < SEALED_FROM and (end is None or (y, m) <= end):
        out.append((y, m))
        m += 1
        if m > 12:
            y += 1
            m = 1
    return out


def download(url: str) -> bytes:
    """GET url and return bytes.

    httpx only speaks http(s), unlike urllib's ``urlopen``, which also opens
    ``file://`` URLs (Semgrep dynamic-urllib-use-detected, #410).

    Raises:
        httpx.HTTPStatusError: non-2xx response.
        httpx.TransportError: network failure.
    """
    response = httpx.get(url, timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True)
    response.raise_for_status()
    return response.content


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
                f"Full ingest expects up to ~50 GB across klines + agg_trades "
                f"(klines ~30 MB; agg_trades ~45 GB dominated by BTC pairs). "
                f"Consider freeing space before continuing.",
                file=sys.stderr,
            )
    except OSError:
        # disk_usage can fail on some Windows mounts; not worth blocking
        pass


def migrate_manifest(manifest: dict) -> tuple[dict, int]:
    """Migrate legacy manifest keys (`<pair>/<interval>/<yyyy>-<mm>`) to the
    namespaced form (`klines/<pair>/<interval>/<yyyy>-<mm>`). Returns
    (new_manifest, migrated_count).
    """
    migrated = 0
    new_manifest: dict = {}
    for key, value in manifest.items():
        parts = key.split("/")
        # Legacy: 3 parts, first is a pair. Namespaced: 4 parts, first is
        # the dataset name.
        if len(parts) == 3 and parts[0] in PAIRS:
            new_key = f"klines/{key}"
            new_manifest[new_key] = value
            migrated += 1
        else:
            new_manifest[key] = value
    return new_manifest, migrated


class Target:
    """One (dataset, pair, interval_or_none, year, month) target."""

    __slots__ = ("dataset", "interval", "month", "pair", "year")

    def __init__(
        self,
        dataset: str,
        pair: str,
        interval: str | None,
        year: int,
        month: int,
    ) -> None:
        if (year, month) >= SEALED_FROM:
            raise SealedMonthError(
                f"{year:04d}-{month:02d} is on or after the sealed OOS start "
                f"{SEALED_FROM[0]:04d}-{SEALED_FROM[1]:02d} (pre-registration v1 sec 4)"
            )
        self.dataset = dataset
        self.pair = pair
        self.interval = interval
        self.year = year
        self.month = month

    def url(self) -> str:
        """Return the Binance Vision URL for this target's ZIP."""
        if self.dataset == "klines":
            filename = f"{self.pair}-{self.interval}-{self.year:04d}-{self.month:02d}.zip"
            return f"{BASE_URL}/klines/{self.pair}/{self.interval}/{filename}"
        # agg_trades
        filename = f"{self.pair}-aggTrades-{self.year:04d}-{self.month:02d}.zip"
        return f"{BASE_URL}/aggTrades/{self.pair}/{filename}"

    def local_path(self, root: Path) -> Path:
        """Return the local file path where this target's ZIP is stored."""
        url_parts = self.url().split("/")
        filename = url_parts[-1]
        if self.dataset == "klines":
            return (
                root
                / "klines"
                / self.pair
                / (self.interval or "-")
                / f"{self.year:04d}"
                / f"{self.month:02d}"
                / filename
            )
        return root / "agg_trades" / self.pair / f"{self.year:04d}" / f"{self.month:02d}" / filename

    def manifest_key(self) -> str:
        """Return the namespaced manifest key for this target."""
        interval = self.interval or "-"
        return f"{self.dataset}/{self.pair}/{interval}/{self.year:04d}-{self.month:02d}"

    def label(self) -> str:
        """Return a short human-readable label for progress logs."""
        interval = self.interval or "-"
        return f"{self.dataset}/{self.pair}/{interval}/{self.year:04d}-{self.month:02d}"


def enumerate_targets(
    include: str,
    pairs: tuple[str, ...],
    intervals: tuple[str, ...],
    months: list[tuple[int, int]],
) -> list[Target]:
    """Build the ordered target list based on the --include selection."""
    datasets: list[str] = []
    if include in ("klines", "all"):
        datasets.append("klines")
    if include in ("agg_trades", "all"):
        datasets.append("agg_trades")

    targets: list[Target] = []
    for dataset in datasets:
        for pair in pairs:
            if dataset == "klines":
                for interval in intervals:
                    for year, month in months:
                        targets.append(Target(dataset, pair, interval, year, month))
            else:  # agg_trades has no interval dimension
                for year, month in months:
                    targets.append(Target(dataset, pair, None, year, month))
    return targets


def process_target(target: Target, root: Path, manifest: dict) -> tuple[str, str]:
    """Download + verify one target.

    Returns (outcome, message) where outcome is one of:
      * "ok"          - downloaded and verified
      * "skipped"     - already in manifest + present on disk
      * "not_yet"     - 404 on checksum (month not yet published or pair pre-listing)
      * "failed"      - unexpected error (network, corruption, unknown HTTP)
    """
    dest = target.local_path(root)
    key = target.manifest_key()

    if key in manifest and dest.exists():
        return "skipped", "already in manifest"

    dest.parent.mkdir(parents=True, exist_ok=True)
    checksum_url = target.url() + ".CHECKSUM"

    # Fetch checksum first — 404 here is the "not yet published" signal
    try:
        checksum_bytes = download(checksum_url)
    except httpx.HTTPStatusError as err:
        if err.response.status_code == 404:
            return "not_yet", "not yet published or pair pre-listing"
        return "failed", f"checksum HTTP {err.response.status_code}"
    except httpx.TransportError as err:
        return "failed", f"checksum network error: {err}"

    # Binance format: "<sha256hex>  <filename>\n"
    expected = checksum_bytes.decode("utf-8", errors="replace").split()[0]

    # Fetch the ZIP
    try:
        data = download(target.url())
    except httpx.HTTPStatusError as err:
        return "failed", f"data HTTP {err.response.status_code}"
    except httpx.TransportError as err:
        return "failed", f"data network error: {err}"

    actual = sha256_hex(data)
    if actual != expected:
        return (
            "failed",
            f"SHA256 mismatch: expected {expected}, got {actual} ({len(data)} bytes)",
        )

    dest.write_bytes(data)
    manifest[key] = {
        "sha256": actual,
        "url": target.url(),
        "size_bytes": len(data),
        "downloaded_at": date.today().isoformat(),
    }
    return "ok", f"OK ({len(data):,} bytes)"


def save_manifest(manifest_path: Path, manifest: dict) -> None:
    """Merge in-memory manifest additions back to disk under an exclusive lock.

    Only NEW keys are written — never overwrite entries a sibling
    ingest script may have added since we snapshotted. Refreshes the
    passed-in dict in place with the just-committed on-disk state so
    subsequent resume checks see sibling writes. See change
    `data-manifest-file-lock` (#257) for the race narrative.
    """
    with manifest_transaction(manifest_path) as on_disk:
        for k, v in manifest.items():
            if k not in on_disk:
                on_disk[k] = v
        manifest.clear()
        manifest.update(on_disk)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--include",
        default="klines",
        choices=list(INCLUDE_CHOICES),
        help="Dataset(s) to download (default: klines)",
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
        choices=[*INTERVALS, *ON_REQUEST_INTERVALS],
        help="Restrict to one or more intervals (klines only; repeatable)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List targets without downloading",
    )
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir or os.environ.get("HERMES_DATA_DIR", DEFAULT_DATA_DIR))
    manifest_path = Path(args.manifest or (data_dir / "manifests" / "data-manifest.json"))

    # Read via locked transaction to avoid tearing with a concurrent
    # writer starting up at the same moment.
    manifest: dict = manifest_read(manifest_path)

    # Backward-compat: migrate any legacy klines keys (`<pair>/<interval>/<yyyy>-<mm>`)
    # to the namespaced form (`klines/<pair>/<interval>/<yyyy>-<mm>`).
    manifest, migrated = migrate_manifest(manifest)
    if migrated > 0:
        print(
            f"Migrated {migrated} legacy manifest entries to the namespaced format.",
            file=sys.stderr,
        )
        save_manifest(manifest_path, manifest)

    pairs = tuple(args.pair) if args.pair else PAIRS
    intervals = tuple(args.interval) if args.interval else INTERVALS
    months = iter_months()

    targets = enumerate_targets(args.include, pairs, intervals, months)

    dataset_desc = args.include if args.include != "all" else "klines + agg_trades"
    print(f"Target: {len(targets)} files ({dataset_desc})")
    print(f"  Pairs:     {', '.join(pairs)}")
    if "klines" in [t.dataset for t in targets[:1]] or args.include != "agg_trades":
        print(f"  Intervals: {', '.join(intervals)} (klines only)")
    print(
        f"  Window:    {START_YEAR:04d}-{START_MONTH:02d} .. "
        f"{months[-1][0]:04d}-{months[-1][1]:02d} (OOS sealed)"
    )
    print(f"  Data dir:  {data_dir}")
    print(f"  Manifest:  {manifest_path}")

    if args.dry_run:
        print()
        print("Sample of targets (first 5, last 5):")
        for target in targets[:5]:
            print(f"  {target.label()}")
        if len(targets) > 10:
            print("  ...")
        for target in targets[-5:]:
            print(f"  {target.label()}")
        return 0

    check_disk_space(data_dir)

    ok = failed = skipped = not_yet = 0
    for idx, target in enumerate(targets, start=1):
        try:
            outcome, message = process_target(target, data_dir, manifest)
        except Exception as err:
            failed += 1
            print(
                f"[{idx:4d}/{len(targets)}] {target.label()}: "
                f"UNEXPECTED ERROR - {type(err).__name__}: {err}",
                file=sys.stderr,
            )
            continue

        if outcome == "ok":
            ok += 1
        elif outcome == "skipped":
            skipped += 1
        elif outcome == "not_yet":
            not_yet += 1
        else:  # "failed"
            failed += 1

        print(f"[{idx:4d}/{len(targets)}] {target.label()}: {message}")

        # Periodic checkpoint so an interrupt does not lose the manifest
        if idx % MANIFEST_CHECKPOINT_EVERY == 0:
            save_manifest(manifest_path, manifest)

    # Final flush
    save_manifest(manifest_path, manifest)

    print()
    print(
        f"Done: {ok} downloaded, {skipped} skipped, "
        f"{not_yet} not-yet-published, {failed} failed "
        f"(of {len(targets)} total)"
    )
    # Only real failures drive the exit code; `not_yet_published` is
    # expected when running before Binance publishes future months.
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
