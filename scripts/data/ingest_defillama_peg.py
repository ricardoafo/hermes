#!/usr/bin/env python3
"""DefiLlama peg-history downloader — USDT/USDC/DAI vs USD.

Iterates the DefiLlama `coins.llama.fi/chart` endpoint one (coin, year,
month) window at a time for `tether` / `usd-coin` / `dai`. Writes one
JSON file per (coin, year, month) under
`<data-dir>/peg/<coin-id>/<yyyy>-<mm>.json` and records SHA256 in
`data-manifest.json` under the `peg/` namespace.

Purpose: feeds the SA-9 depeg overlay wired by Spec 8 (pre-registration
v1 §5.5 correlation lock + depeg WARN 1.5% / HALT 3% rules).

Provider swap history (see `openspec/archive/peg-ingest-defillama-swap/`):
    CoinGecko free tier changed 2026-08 (401/429 across the board);
    DefiLlama's public chart endpoint is free, no-auth, and aggregates
    from CoinGecko + on-chain sources.

Reference:
    - openspec/archive/peg-ingest-defillama-swap/change.md
    - docs/experiments/swing-mvp1/data-ingest-plan.md §3 (Runbook)
    - docs/research/paper-trading/sa9-portfolio-shocks.md (depeg overlay)

Usage:
    python scripts/data/ingest_defillama_peg.py [OPTIONS]

    --data-dir PATH   Root data dir (default: $HERMES_DATA_DIR or D:/hermes-data)
    --manifest PATH   Manifest JSON path (default: <data-dir>/manifests/data-manifest.json)
    --coin ID         Restrict to a single coin id (repeatable; tether|usd-coin|dai)
    --limit N         Process at most N target windows (useful for smoke tests)
    --dry-run         List target windows without downloading

Rate limits: DefiLlama tolerates ~5-10 rps for the public chart endpoint.
Script sleeps 1s between actual downloads to stay well inside. Full
ingest (~76 windows per coin x 3 coins = 228 requests) takes ~4 minutes.

Exit codes:
    0 - no failures (`not_yet_available` responses do not count)
    1 - one or more failures (see stderr)
"""

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

# Shared read-modify-atomic-write transaction for the multi-writer
# manifest. See scripts/data/_manifest_util.py + change
# `data-manifest-file-lock` (#257) for the race narrative.
from _manifest_util import manifest_read, manifest_transaction

COINS: tuple[str, ...] = ("tether", "usd-coin", "dai")
BASE_URL = "https://coins.llama.fi/chart"
DEFAULT_DATA_DIR = "D:/hermes-data"
MANIFEST_CHECKPOINT_EVERY = 10  # windows
HTTP_TIMEOUT_SECONDS = 60
RATE_LIMIT_SLEEP_SECONDS = 1
# DefiLlama caps `span` at 500 data points per request. A 31-day month
# has 744 hourly ticks, so we paginate: two chunks (500 + up to 244)
# per (coin, month), merged into a single on-disk file.
CHART_SPAN_HOURS = 500
CHART_MAX_HOURS_PER_MONTH = 744
CHART_SEARCH_WIDTH_SECONDS = 600  # ±10 min for each requested tick

# Pre-reg v1 §4 uniform window (BINDING; matches ingest_binance_vision.py)
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
    """Return (from_unix, to_unix) for the given month, both in seconds UTC."""
    start = datetime(year, month, 1, 0, 0, 0, tzinfo=UTC)
    if month == 12:
        end = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=UTC)
    else:
        end = datetime(year, month + 1, 1, 0, 0, 0, tzinfo=UTC)
    return int(start.timestamp()), int(end.timestamp())


def download_json(url: str) -> bytes:
    """GET url with a hermes User-Agent and return raw response bytes.

    Returns the raw bytes so SHA256 is computed on the exact wire
    content (order-preserving, no re-serialization drift). httpx only speaks
    http(s), unlike urllib's ``urlopen`` (Semgrep, #410).

    Raises:
        httpx.HTTPStatusError: non-2xx response.
        httpx.TransportError: network failure.
    """
    response = httpx.get(
        url,
        headers={"User-Agent": "hermes-ingest/1.0 (github.com/ricardoafo/hermes)"},
        timeout=HTTP_TIMEOUT_SECONDS,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.content


def sha256_hex(data: bytes) -> str:
    """Return lowercase hex SHA-256 of data."""
    return hashlib.sha256(data).hexdigest()


def save_manifest(manifest_path: Path, manifest: dict) -> None:
    """Merge in-memory manifest additions back to disk under an exclusive lock.

    Only NEW keys are written — never overwrite entries a sibling
    ingest script may have added since we snapshotted. Refreshes the
    passed-in dict in place with the just-committed on-disk state so
    subsequent resume checks see sibling writes.
    """
    with manifest_transaction(manifest_path) as on_disk:
        for k, v in manifest.items():
            if k not in on_disk:
                on_disk[k] = v
        # Refresh the caller's dict with everyone's committed state.
        manifest.clear()
        manifest.update(on_disk)


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
      * "not_yet"   - window is in the future OR DefiLlama has no data yet
      * "failed"    - unexpected error
    """
    dest_dir = root / "peg" / coin
    dest = dest_dir / f"{year:04d}-{month:02d}.json"
    key = f"peg/{coin}/-/{year:04d}-{month:02d}"

    if key in manifest and dest.exists():
        return "skipped", "already in manifest"

    from_ts, to_ts = month_bounds(year, month)
    now_ts = int(datetime.now(UTC).timestamp())

    if from_ts > now_ts:
        return "not_yet", "window starts in the future"

    coin_key = f"coingecko:{coin}"

    # DefiLlama caps span at 500. Chunk the month into contiguous spans
    # starting at `from_ts` and merge each chunk's prices into one blob.
    hours_in_month = max(1, (to_ts - from_ts) // 3600)
    chunk_offsets: list[tuple[int, int]] = []
    remaining = min(hours_in_month, CHART_MAX_HOURS_PER_MONTH)
    offset_hours = 0
    while remaining > 0:
        take = min(CHART_SPAN_HOURS, remaining)
        chunk_offsets.append((offset_hours, take))
        offset_hours += take
        remaining -= take

    chunk_responses: list[dict] = []
    chunk_urls: list[str] = []
    for chunk_idx, (offset, span) in enumerate(chunk_offsets):
        chunk_start = from_ts + offset * 3600
        url = (
            f"{BASE_URL}/{coin_key}"
            f"?start={chunk_start}"
            f"&span={span}"
            f"&period=1h"
            f"&searchWidth={CHART_SEARCH_WIDTH_SECONDS}"
        )
        chunk_urls.append(url)

        try:
            raw = download_json(url)
        except httpx.HTTPStatusError as err:
            status = err.response.status_code
            if status == 429:
                return "failed", "rate-limited (429) — increase RATE_LIMIT_SLEEP_SECONDS"
            return "failed", f"HTTP {status} on chunk {chunk_idx + 1}/{len(chunk_offsets)}"
        except httpx.TransportError as err:
            return "failed", f"network error on chunk {chunk_idx + 1}: {err}"

        try:
            parsed_chunk = json.loads(raw)
        except json.JSONDecodeError as err:
            return "failed", f"invalid JSON on chunk {chunk_idx + 1}: {err}"

        if not isinstance(parsed_chunk, dict) or "coins" not in parsed_chunk:
            schema_desc = (
                sorted(parsed_chunk.keys())
                if isinstance(parsed_chunk, dict)
                else type(parsed_chunk).__name__
            )
            return (
                "failed",
                f"unexpected schema on chunk {chunk_idx + 1} (top-level keys: {schema_desc})",
            )

        chunk_responses.append(parsed_chunk)
        # Sleep between chunks to stay well within DefiLlama's tolerance.
        if chunk_idx + 1 < len(chunk_offsets):
            time.sleep(RATE_LIMIT_SLEEP_SECONDS)

    # Merge all chunks into one DefiLlama-shaped envelope.
    merged_prices: list[dict] = []
    symbol: str | None = None
    confidence: float | None = None
    for parsed_chunk in chunk_responses:
        coin_block = parsed_chunk.get("coins", {}).get(coin_key)
        if coin_block is None:
            continue
        merged_prices.extend(coin_block.get("prices", []))
        if symbol is None:
            symbol = coin_block.get("symbol")
        if confidence is None:
            confidence = coin_block.get("confidence")

    if not merged_prices:
        # Empty prices across every chunk means DefiLlama has no coverage
        # for that coin+month (early history for USDC/DAI, or a pre-launch
        # month). Treat like klines script's `not_yet_published`.
        return "not_yet", f"empty prices for {coin_key} in {year}-{month:02d}"

    # De-dup any timestamp overlap between chunks (defensive; searchWidth
    # can occasionally pull the same tick into two adjacent windows).
    seen: set[int] = set()
    deduped: list[dict] = []
    for point in merged_prices:
        ts = int(point.get("timestamp", -1))
        if ts in seen:
            continue
        seen.add(ts)
        deduped.append(point)
    deduped.sort(key=lambda p: int(p.get("timestamp", 0)))

    merged_envelope = {
        "coins": {
            coin_key: {
                "symbol": symbol,
                "confidence": confidence,
                "prices": deduped,
            }
        },
        "_hermes_meta": {
            "provider": "defillama",
            "coin": coin,
            "year": year,
            "month": month,
            "chunk_urls": chunk_urls,
            "downloaded_at": datetime.now(UTC).date().isoformat(),
        },
    }
    body = json.dumps(merged_envelope, indent=2, sort_keys=True).encode()

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    manifest[key] = {
        "sha256": sha256_hex(body),
        "urls": chunk_urls,
        "size_bytes": len(body),
        "prices_count": len(deduped),
        "provider": "defillama",
        "downloaded_at": datetime.now(UTC).date().isoformat(),
    }
    return "ok", f"OK ({len(deduped)} points, {len(body):,} bytes, {len(chunk_offsets)} chunks)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--coin", action="append", choices=list(COINS))
    parser.add_argument("--limit", type=int, default=None, help="Process at most N target windows")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir or os.environ.get("HERMES_DATA_DIR", DEFAULT_DATA_DIR))
    manifest_path = Path(args.manifest or (data_dir / "manifests" / "data-manifest.json"))

    # Read via locked transaction to avoid tearing with a concurrent
    # writer starting up at the same moment.
    manifest: dict = manifest_read(manifest_path)

    coins = tuple(args.coin) if args.coin else COINS
    months = iter_months()
    targets: list[tuple[str, int, int]] = [
        (coin, year, month) for coin in coins for year, month in months
    ]
    if args.limit is not None:
        targets = targets[: args.limit]

    print(f"Target: {len(targets)} (coin, month) windows")
    print(f"  Provider: DefiLlama ({BASE_URL})")
    print(f"  Coins:    {', '.join(coins)}")
    print(f"  Window:   {START_YEAR:04d}-{START_MONTH:02d} .. {END_YEAR:04d}-{END_MONTH:02d}")
    print(f"  Data dir: {data_dir}")
    print(f"  Manifest: {manifest_path}")
    rate = 60 / RATE_LIMIT_SLEEP_SECONDS
    total_min = len(targets) * RATE_LIMIT_SLEEP_SECONDS / 60
    print(f"  Rate:     {rate:.0f} req/min (~{total_min:.1f} min total)")

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
