"""Unit tests for scripts/data/prepare_backtest_data.py (#925).

Invariants:

- The prepare step never builds a request for the sealed OOS split.
- Files already on disk are not downloaded again; only missing months are.
- A dataset whose bars do not hash to the manifest's ``content_sha256`` fails
  the step with an ``ERROR:`` line and a non-zero exit, before any backtest runs.
"""

import asyncio
import csv
import hashlib
import importlib
import io
import json
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
import respx

from hermes.adapters.market_data.binance_historical import BinanceHistoricalAdapter
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.dataset_manifest import bars_content_sha256
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS = _ROOT / "scripts" / "data"
_REAL_MANIFEST = _ROOT / "docs/experiments/swing-mvp1/data-manifest.json"
_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_DAY_MS = 86_400_000


def _import_prepare() -> ModuleType:
    """Import the prepare script; it imports its sibling modules by name, as when run directly."""
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    return importlib.import_module("prepare_backtest_data")


PREPARE = _import_prepare()
INGEST = importlib.import_module("ingest_binance_vision")


def _train_months() -> list[tuple[int, int]]:
    """Return every month of the signed Train window (2020-09 .. 2024-06)."""
    return INGEST.iter_months((2020, 9), (2024, 6))


def _month_zip(year: int, month: int) -> bytes:
    """Return a Binance-style 1d klines ZIP holding one bar on the 1st of the month."""
    open_ms = int(datetime(year, month, 1, tzinfo=UTC).timestamp() * 1000)
    close_ms = open_ms + _DAY_MS - 1
    row = [open_ms, "100", "110", "90", "105", "1.5", close_ms, "157.5", 10, "1", "105"]
    buf = io.StringIO()
    csv.writer(buf).writerow(row)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr(f"BTCUSDT-1d-{year:04d}-{month:02d}.csv", buf.getvalue())
    return out.getvalue()


# Any: the script module is loaded at runtime, so its Target class has no static type here.
def _target(year: int, month: int) -> Any:
    """Return the ingest target for BTCUSDT 1d in one month."""
    return INGEST.Target("klines", "BTCUSDT", "1d", year, month)


def _write_train_archive(data_dir: Path) -> None:
    """Write one 1d BTCUSDT bar per Train month where the ingest stores it."""
    for year, month in _train_months():
        dest = _target(year, month).local_path(data_dir)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(_month_zip(year, month))


def _archive_sha256(data_dir: Path) -> str:
    """Hash the archive's BTCUSDT 1d bars the way the backtest loader does."""

    async def _bars() -> list[Bar]:
        source = BinanceHistoricalAdapter.create(data_dir=data_dir)
        return [b async for b in source.stream_bars(_BTC, KlineInterval.D1)]

    return bars_content_sha256(asyncio.run(_bars()))


def _write_manifest(path: Path, *, content_sha256: str) -> Path:
    """Write the signed manifest pinning only BTCUSDT 1d Train, with ``content_sha256``."""
    payload = json.loads(_REAL_MANIFEST.read_text())
    entry = next(d for d in payload["datasets"] if d["dataset_id"] == "klines/BTCUSDT/1d/train")
    entry.update(content_sha256=content_sha256, row_count=len(_train_months()))
    payload["datasets"] = [entry]
    path.write_text(json.dumps(payload))
    return path


def test_oos_split_is_refused_without_any_request(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Asking to prepare the OOS split exits non-zero and sends no request at all."""
    manifest = _write_manifest(tmp_path / "m.json", content_sha256="0" * 64)
    with respx.mock(assert_all_called=False) as router:
        catch_all = router.route().mock(return_value=httpx.Response(200))
        code = PREPARE.prepare(manifest, tmp_path / "data", "oos")
        requests = catch_all.call_count
    assert code == 1
    assert requests == 0
    assert "ERROR:" in capsys.readouterr().err


def test_validation_prepares_train_too() -> None:
    """A Validation backtest warms up on Train bars, so preparing it covers Train as well."""
    assert [s.value for s in PREPARE.splits_to_prepare("validation")] == ["train", "validation"]
    assert [s.value for s in PREPARE.splits_to_prepare("train")] == ["train"]


def test_present_matching_files_are_skipped_without_network(tmp_path: Path) -> None:
    """Every file on disk and matching the manifest: nothing is downloaded and the step passes."""
    data_dir = tmp_path / "data"
    _write_train_archive(data_dir)
    manifest = _write_manifest(tmp_path / "m.json", content_sha256=_archive_sha256(data_dir))
    with respx.mock(assert_all_called=False) as router:
        catch_all = router.route().mock(return_value=httpx.Response(500))
        code = PREPARE.prepare(manifest, data_dir, "train")
        requests = catch_all.call_count
    assert code == 0
    assert requests == 0


def test_hash_mismatch_fails_the_step(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Bars that do not hash to the manifest's content_sha256 exit non-zero with an ERROR line."""
    data_dir = tmp_path / "data"
    _write_train_archive(data_dir)
    manifest = _write_manifest(tmp_path / "m.json", content_sha256="f" * 64)
    with respx.mock(assert_all_called=False) as router:
        router.route().mock(return_value=httpx.Response(500))
        code = PREPARE.prepare(manifest, data_dir, "train")
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR:" in err
    assert "klines/BTCUSDT/1d/train" in err


def test_only_the_missing_month_is_downloaded(tmp_path: Path) -> None:
    """A missing month is fetched, checksum-verified and saved; present months are not requested."""
    data_dir = tmp_path / "data"
    _write_train_archive(data_dir)
    sha = _archive_sha256(data_dir)
    missing = _target(2022, 3)
    missing.local_path(data_dir).unlink()
    payload = _month_zip(2022, 3)
    manifest = _write_manifest(tmp_path / "m.json", content_sha256=sha)
    checksum = f"{hashlib.sha256(payload).hexdigest()}  BTCUSDT-1d-2022-03.zip\n"
    with respx.mock(assert_all_called=True) as router:
        router.get(missing.url() + ".CHECKSUM").mock(
            return_value=httpx.Response(200, text=checksum)
        )
        router.get(missing.url()).mock(return_value=httpx.Response(200, content=payload))
        code = PREPARE.prepare(manifest, data_dir, "train")
        calls = len(router.calls)
    assert code == 0
    assert calls == 2
    assert missing.local_path(data_dir).read_bytes() == payload


def test_failed_download_fails_before_verifying(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A Train month that cannot be downloaded is an error, not a silently short dataset."""
    data_dir = tmp_path / "data"
    _write_train_archive(data_dir)
    sha = _archive_sha256(data_dir)
    _target(2021, 1).local_path(data_dir).unlink()
    manifest = _write_manifest(tmp_path / "m.json", content_sha256=sha)
    with respx.mock(assert_all_called=False) as router:
        router.route().mock(return_value=httpx.Response(404))
        code = PREPARE.prepare(manifest, data_dir, "train")
    assert code == 1
    assert "ERROR: klines/BTCUSDT/1d/2021-01" in capsys.readouterr().err
