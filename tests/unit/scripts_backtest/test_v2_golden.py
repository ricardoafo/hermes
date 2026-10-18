"""Strategy v2's trial line and report bytes on committed fixtures (issue #1119).

Rule (research funnel contract §2a, issue #1074): the strategy factory builds
v1 and v2 exactly as before it existed, so a v2 run's trial line and report
are byte-for-byte fixed. The driver ``run_portfolio_backtest.py`` runs the
``swing-mvp1-v2`` entry of ``config/strategies.yaml`` through the factory on
the committed synthetic bars in ``tests/fixtures/backtest_v2/``; its trial
line and report must equal the committed golden copies under its ``__snapshots__``.

Only fields that change from run to run are blanked before comparing:

- trial line: ``recorded_at`` (wall clock), ``git_sha`` and ``git_dirty``
  (the checkout the test runs on) and ``sha256``, the chain hash over those
  fields. ``prev_sha256`` is the genesis hash of a fresh log and is compared.
- report: ``generated_at`` (wall clock).

Neither file holds a path, so the temporary output paths are not blanked; a
path that leaked into either file would fail the comparison.

Regenerating the golden copies is a deliberate step::

    HERMES_REGEN_GOLDEN=1 uv run pytest tests/unit/scripts_backtest/test_v2_golden.py

That run writes the blanked output over the golden files and fails, so it is
never a green run; review the diff before committing it.
"""

import csv
import gzip
import hashlib
import importlib.util
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.dataset_manifest import DataSplit
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

_ROOT: Final = Path(__file__).resolve().parents[3]
_SCRIPTS: Final = _ROOT / "scripts" / "backtest"
_FIXTURES: Final = _ROOT / "tests" / "fixtures" / "backtest_v2"
_BARS: Final = _FIXTURES / "v2-golden-bars.csv.gz"
_GOLDEN_TRIAL_LINE: Final = _FIXTURES / "__snapshots__" / "v2-golden-trial-line.jsonl"
_GOLDEN_REPORT: Final = _FIXTURES / "__snapshots__" / "v2-golden-report.json"
_V2: Final = "swing-mvp1-v2"
_WINDOW: Final = ("2020-09-20", "2020-10-10")
_REGEN_ENV: Final = "HERMES_REGEN_GOLDEN"
_BLANK: Final = '"<run-time>"'

_TRIAL_LINE_RUN_TIME: Final = (
    re.compile(r'"recorded_at": "[^"]+"'),
    re.compile(r'"git_sha": "[0-9a-f]{40}"'),
    re.compile(r'"git_dirty": (?:true|false)'),
    re.compile(r'"sha256": "[0-9a-f]{64}"'),
)
_REPORT_RUN_TIME: Final = (re.compile(r'"generated_at": "[^"]+"'),)


def _import_driver() -> ModuleType:
    """Import the driver; it imports its sibling modules by name, as when run directly."""
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "run_portfolio_backtest_golden", _SCRIPTS / "run_portfolio_backtest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_portfolio_backtest_golden"] = module
    spec.loader.exec_module(module)
    return module


DRIVER: Final = _import_driver()


def _utc(ms: str) -> datetime:
    return datetime.fromtimestamp(int(ms) / 1000, tz=UTC)


def _fixture_bars() -> tuple[list[Bar], list[Bar]]:
    """Return the committed synthetic BTC/USDT 4h and 1d bars."""
    with gzip.open(_BARS, "rt", newline="") as handle:
        rows = list(csv.DictReader(handle))
    btc = Pair(base=Currency.BTC, quote=Currency.USDT)
    bars = [
        Bar(
            pair=btc,
            interval=KlineInterval(row["interval"]),
            open_time=_utc(row["open_time_ms"]),
            open=Decimal(row["open"]),
            high=Decimal(row["high"]),
            low=Decimal(row["low"]),
            close=Decimal(row["close"]),
            volume=Decimal(row["volume"]),
            close_time=_utc(row["close_time_ms"]),
        )
        for row in rows
    ]
    return (
        [b for b in bars if b.interval is KlineInterval.H4],
        [b for b in bars if b.interval is KlineInterval.D1],
    )


def _blank(text: str, patterns: tuple[re.Pattern[str], ...]) -> str:
    """Replace each run-time field's value with the blank; each must occur exactly once."""
    for pattern in patterns:
        key = pattern.pattern.split(":", 1)[0]
        text, count = pattern.subn(f"{key}: {_BLANK}", text)
        if count != 1:
            raise AssertionError(f"{key} occurs {count} times, expected once")
    return text


@dataclass(frozen=True, slots=True)
class _V2Run:
    exit_code: int
    trial_line: str
    report: str


@pytest.fixture(scope="module")
def v2_run(tmp_path_factory: pytest.TempPathFactory) -> _V2Run:
    """Run the driver once on the committed bars, through the real roster and factory.

    Only the data manifest and its split are stubbed: the manifest pins
    datasets the fixture is not part of. The dataset reference names the
    fixture file by its SHA-256, so an edited fixture changes the trial line.
    """
    tmp = tmp_path_factory.mktemp("v2-golden")
    bars_4h, bars_1d = _fixture_bars()
    datasets = [
        {
            "dataset_id": f"synthetic/{_BARS.name}",
            "content_sha256": hashlib.sha256(_BARS.read_bytes()).hexdigest(),
        }
    ]

    async def verified_bars(
        *_args: object,
    ) -> tuple[list[Bar], list[Bar], list[dict[str, str]]]:
        return bars_4h, bars_1d, datasets

    trial_log, report = tmp / "trial-log.jsonl", tmp / "report.json"
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(_ROOT)
        mp.setattr(DRIVER, "_load_manifest", lambda _path: object())
        mp.setattr(DRIVER, "_load_verified_bars", verified_bars)
        mp.setattr(DRIVER, "trial_split", lambda *_args: DataSplit.TRAIN)
        code = DRIVER.main(
            [
                "--pairs=BTCUSDT",
                f"--start={_WINDOW[0]}",
                f"--end={_WINDOW[1]}",
                f"--strategy={_V2}",
                f"--config-dir={_ROOT / 'config'}",
                f"--trial-log={trial_log}",
                f"--output={report}",
                f"--performance-out={tmp / 'performance.json'}",
            ]
        )
    return _V2Run(
        exit_code=code,
        trial_line=trial_log.read_text(encoding="utf-8"),
        report=report.read_text(encoding="utf-8"),
    )


def _matches_golden(actual: str, golden: Path) -> str:
    """Return the golden text, or rewrite it from ``actual`` and fail when regenerating."""
    if os.environ.get(_REGEN_ENV) == "1":
        golden.write_text(actual, encoding="utf-8", newline="\n")
        pytest.fail(f"{golden.name} regenerated; review the diff, then rerun without {_REGEN_ENV}")
    return golden.read_text(encoding="utf-8")


def test_v2_run_on_the_committed_fixture_exits_zero(v2_run: _V2Run) -> None:
    """The golden comparison runs on a completed v2 backtest (driver exit 0, #1119)."""
    assert v2_run.exit_code == 0


def test_v2_trial_line_is_byte_identical_to_the_golden_copy(v2_run: _V2Run) -> None:
    """v2's trial line bytes, run-time fields blanked, never change (#1119, contract §2a)."""
    actual = _blank(v2_run.trial_line, _TRIAL_LINE_RUN_TIME)

    assert actual == _matches_golden(actual, _GOLDEN_TRIAL_LINE)


def test_v2_report_is_byte_identical_to_the_golden_copy(v2_run: _V2Run) -> None:
    """v2's report bytes, ``generated_at`` blanked, never change (#1119, contract §2a)."""
    actual = _blank(v2_run.report, _REPORT_RUN_TIME)

    assert actual == _matches_golden(actual, _GOLDEN_REPORT)


def test_the_golden_report_holds_closed_v2_round_trips() -> None:
    """The fixture exercises v2's entries and bar-close exits: two round trips, none left open.

    A regenerated golden copy of a run with no exit would pin nothing of v2's
    exit pricing (#1119).
    """
    golden = _GOLDEN_REPORT.read_text(encoding="utf-8")

    assert (
        re.findall(r'"round_trips": (\d+)', golden),
        re.findall(r'"open_positions": (\[\])', golden),
        re.findall(r'"exit_pricing": "(\w+)"', golden),
    ) == (["2"], ["[]"], ["bar_close"])
