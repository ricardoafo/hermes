"""Unit tests for the sealed-OOS cutoff in scripts/data/ingest_binance_vision.py (#925).

Invariant (pre-registration v1 §4): no target month on or after the OOS start
in the experiment data manifest is ever enumerated, and no URL for one is ever
built, whatever window or arguments the caller passes.
"""

import importlib
import json
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType

import pytest
from hypothesis import given
from hypothesis import strategies as st

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts" / "data"
_MANIFEST = Path(__file__).resolve().parents[3] / "docs/experiments/swing-mvp1/data-manifest.json"


def _import_ingest() -> ModuleType:
    """Import the ingest script; it imports its sibling helper by name, as when run directly."""
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    return importlib.import_module("ingest_binance_vision")


INGEST = _import_ingest()
_OOS_START = datetime.fromisoformat(json.loads(_MANIFEST.read_text())["splits"]["oos"]["start"])
_SEALED = (_OOS_START.year, _OOS_START.month)

_months = st.tuples(st.integers(2019, 2028), st.integers(1, 12))


def test_sealed_from_is_the_manifest_oos_start() -> None:
    """The cutoff is the OOS start month of the experiment data manifest, 2025-09."""
    assert INGEST.SEALED_FROM == _SEALED == (2025, 9)


def test_default_window_ends_the_month_before_oos() -> None:
    """The default ingest window runs from 2020-09 to 2025-08 and no further."""
    months = INGEST.iter_months()
    assert (months[0], months[-1]) == ((2020, 9), (2025, 8))


def test_window_past_the_cutoff_is_cut_before_oos() -> None:
    """A window that runs into OOS stops at 2025-08."""
    assert INGEST.iter_months((2025, 6), (2026, 3)) == [(2025, 6), (2025, 7), (2025, 8)]


def test_window_inside_oos_is_empty() -> None:
    """A window that lies wholly inside OOS yields no month at all."""
    assert INGEST.iter_months((2025, 9), (2026, 12)) == []


@given(start=_months, end=_months)
def test_no_window_yields_a_sealed_month(start: tuple[int, int], end: tuple[int, int]) -> None:
    """For any requested window, every enumerated month precedes the OOS start."""
    sealed = [month for month in INGEST.iter_months(start, end) if month >= _SEALED]
    assert sealed == []


@pytest.mark.parametrize("dataset", ["klines", "agg_trades"])
@pytest.mark.parametrize(("year", "month"), [(2025, 9), (2025, 12), (2026, 12)])
def test_target_for_a_sealed_month_is_refused(dataset: str, year: int, month: int) -> None:
    """A target, and so its URL, cannot be built for a month on or after the OOS start."""
    with pytest.raises(INGEST.SealedMonthError, match=r"sealed OOS start 2025-09"):
        INGEST.Target(dataset, "BTCUSDT", "1d" if dataset == "klines" else None, year, month)


def test_full_enumeration_builds_no_sealed_url() -> None:
    """The full ingest (every dataset, pair and interval) builds no URL dated 2025-09 or later."""
    targets = INGEST.enumerate_targets("all", INGEST.PAIRS, INGEST.INTERVALS, INGEST.iter_months())
    sealed = [t.url() for t in targets if (t.year, t.month) >= _SEALED]
    assert sealed == []
    assert len(targets) == 3 * 3 * 60 + 3 * 60
