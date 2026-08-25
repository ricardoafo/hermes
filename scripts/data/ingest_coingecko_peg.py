#!/usr/bin/env python3
"""CoinGecko hourly peg data downloader — USDT/USDC/DAI vs USD.

Paginates the CoinGecko `coins/{id}/market_chart/range` endpoint in
90-day windows for tether / usd-coin / dai. Writes one JSON file per
(coin, year, month) under `<data-dir>/peg/<coin-id>/<yyyy>-<mm>.json`
and records SHA256 in `data-manifest.json` under the `peg/` namespace.

Purpose: feeds the SA-9 depeg overlay wired by Spec 8 (pre-registration
v1 sec 5.5 correlation lock + depeg WARN 1.5% / HALT 3% rules).

Reference:
    - docs/experiments/swing-mvp1/data-ingest-plan.md sec 3 (URL patterns)
    - docs/research/paper-trading/sa9-portfolio-shocks.md (depeg overlay)
    - openspec/changes/data-ingest-agg-trades-and-peg/change.md

Usage:
    python scripts/data/ingest_coingecko_peg.py [OPTIONS]

    --data-dir PATH   Root data dir (default: $HERMES_DATA_DIR or D:/hermes-data)
    --manifest PATH   Manifest JSON path (default: <data-dir>/manifests/data-manifest.json)
    --coin ID         Restrict to a single coin id (repeatable; tether|usd-coin|dai)
    --dry-run         List target windows without downloading

Rate limits: CoinGecko free tier ~10-30 calls/min. Script sleeps 4s
between requests to stay safely under 15/min. Full ingest (~78 windows
per coin x 3 coins ~= 234 requests) takes ~16 minutes.

Exit codes:
    0 - no failures (`not_yet_available` responses do not count)
    1 - one or more failures (see stderr)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

COINS: tuple[str, ...] = ("tether", "usd-coin", "dai")
BASE_URL = "https://api.coingecko.com/api/v3"
DEFAULT_DATA_DIR = "D:/hermes-data"
MANIFEST_CHECKPOINT_EVERY = 10  # windows
HTTP_TIMEOUT_SECONDS = 60
RATE_LIMIT_SLEEP_SECONDS = 4  # keeps us at ~15 req/min < 30 free-tier ceiling

# Pre-reg v1 sec 4 uniform window (BINDING; matches ingest_binance_vision.py)
START_YEAR, START_MONTH = 2020, 9
END_YEAR, END_MONTH = 2026, 12


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


def month_bounds(year: int, month: int) -> tuple[int, int]:
    """Return (from_unix, to_unix) for the given month, both in seconds."""
    start = datetime(year, month, 1, 0, 0, 0)
    if month == 12:
        end = datetime(year + 1, 1, 1, 0, 0, 0)
    else:
        end = datetime(year, month + 1, 1, 0, 0, 0)
    return int(start.timestamp()), int(end.timestamp())


def download_json(url: str) -> bytes:
    """GET url with a browser-ish User-Agent and return raw response bytes.

    CoinGecko rejects some clients that omit User-Agent; a plain string
    is enough. Returns the raw bytes so SHA256 is computed on the exact
    wire content (order-preserving, no re-serialization drift).
    """
    request = Request(
        url, headers={"User-Agent": "hermes-ingest/1.0 (github.com/ricardoafo/hermes)"}
    )
    with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return response.read()


def sha256_hex(data: bytes) -> str:
    """Return lowercase hex SHA-256 of data."""
    return hashlib.sha256(data).hexdigest()


def save_manifest(manifest_path: Path, manifest: dict) -> None:
    """Write manifest to disk atomically (temp file + rename)."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    tmp.replace(manifest_path)


def process_window(
    coin: str,
    year: int,
    month: int,
    root: Path,
    manifest: dict,
) -> tuple[str, str]:
    """Download + verify one (coin, year, month) window.

    Returns (outcome, message) where outcome is one of:
      * "ok"        - downloaded and cached
      * "skipped"   - already in manifest + present on disk
      * "not_yet"   - window is in the future (endpoint returns empty/404)
      * "failed"    - unexpected error
    """
    dest_dir = root / "peg" / coin
    dest = dest_dir / f"{year:04d}-{month:02d}.json"
    key = f"peg/{coin}/-/{year:04d}-{month:02d}"

    if key in manifest and dest.exists():
        return "skipped", "already in manifest"

    from_ts, to_ts = month_bounds(year, month)

    # Skip if the window is entirely in the future — CoinGecko returns
    # empty arrays but we don't want to pay for the request or a manifest
    # entry pointing at empty content.
    if from_ts > int(datetime.now().timestamp()):
        return "not_yet", "window starts in the future"

    url = f"{BASE_URL}/coins/{coin}/market_chart/range?vs_currency=usd&from={from_ts}&to={to_ts}"

    try:
        raw = download_json(url)
    except HTTPError as err:
        if err.code == 429:
            return "failed", "rate-limited (429) — increase RATE_LIMIT_SLEEP_SECONDS"
        return "failed", f"HTTP {err.code}"
    except URLError as err:
        return "failed", f"network error: {err.reason}"

    # Sanity: response has the expected shape
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as err:
        return "failed", f"invalid JSON: {err}"

    if not isinstance(parsed, dict) or "prices" not in parsed:
        schema_desc = sorted(parsed.keys()) if isinstance(parsed, dict) else type(parsed).__name__
        return "failed", f"unexpected schema (keys: {schema_desc})"

    prices_count = len(parsed.get("prices", []))
    if prices_count == 0 and to_ts > int(datetime.now().timestamp()):
        # future window — CoinGecko returned empty; classify as not_yet
        return "not_yet", "empty response for future window"

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    manifest[key] = {
        "sha256": sha256_hex(raw),
        "url": url,
        "size_bytes": len(raw),
        "prices_count": prices_count,
        "downloaded_at": date.today().isoformat(),
    }
    return "ok", f"OK ({prices_count} hourly points, {len(raw):,} bytes)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--coin", action="append", choices=list(COINS))
    parser.add_argument("--dry-run", action="store_true")
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

    coins = tuple(args.coin) if args.coin else COINS
    months = iter_months()
    targets = [(coin, year, month) for coin in coins for year, month in months]

    print(f"Target: {len(targets)} (coin, month) windows")
    print(f"  Coins:    {', '.join(coins)}")
    print(f"  Window:   {START_YEAR:04d}-{START_MONTH:02d} .. {END_YEAR:04d}-{END_MONTH:02d}")
    print(f"  Data dir: {data_dir}")
    print(f"  Manifest: {manifest_path}")
    rate = 60 / RATE_LIMIT_SLEEP_SECONDS
    total_min = len(targets) * RATE_LIMIT_SLEEP_SECONDS / 60
    print(f"  Rate:     {rate:.0f} req/min (~{total_min:.0f} min total)")

    if args.dry_run:
        print()
        print("Sample of targets (first 5, last 5):")
        for coin, year, month in targets[:5]:
            print(f"  peg/{coin}/{year:04d}-{month:02d}")
        if len(targets) > 10:
            print("  ...")
        for coin, year, month in targets[-5:]:
            print(f"  peg/{coin}/{year:04d}-{month:02d}")
        return 0

    ok = failed = skipped = not_yet = 0
    for idx, (coin, year, month) in enumerate(targets, start=1):
        label = f"peg/{coin}/{year:04d}-{month:02d}"
        try:
            outcome, message = process_window(coin, year, month, data_dir, manifest)
        except Exception as err:
            failed += 1
            err_type = type(err).__name__
            print(
                f"[{idx:4d}/{len(targets)}] {label}: UNEXPECTED ERROR - {err_type}: {err}",
                file=sys.stderr,
            )
            continue

        if outcome == "ok":
            ok += 1
        elif outcome == "skipped":
            skipped += 1
        elif outcome == "not_yet":
            not_yet += 1
        else:
            failed += 1

        print(f"[{idx:4d}/{len(targets)}] {label}: {message}")

        # Rate limit — only sleep after actual downloads
        if outcome == "ok":
            time.sleep(RATE_LIMIT_SLEEP_SECONDS)

        if idx % MANIFEST_CHECKPOINT_EVERY == 0:
            save_manifest(manifest_path, manifest)

    save_manifest(manifest_path, manifest)

    print()
    print(
        f"Done: {ok} downloaded, {skipped} skipped, "
        f"{not_yet} not-yet-available, {failed} failed "
        f"(of {len(targets)} total)"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
