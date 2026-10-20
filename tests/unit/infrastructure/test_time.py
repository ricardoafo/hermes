"""Tests for the UTC/Madrid time helpers (R-3).

Verifies:
- now_utc() returns a UTC-aware datetime
- from_madrid(to_madrid(dt)) == dt roundtrip
- DST boundary: Spain springs forward on last Sunday of March

Reference: R-3 (A-3.1 through A-3.4).
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from hermes.infrastructure.time import from_madrid, now_utc, to_madrid

_UTC = ZoneInfo("UTC")
_MADRID = ZoneInfo("Europe/Madrid")


class TestNowUtc:
    """Verify now_utc() contract."""

    def test_returns_aware_datetime(self) -> None:
        """now_utc() returns a datetime whose tzinfo is the UTC ZoneInfo (not just non-None)."""
        dt = now_utc()
        assert dt.tzinfo == _UTC

    def test_returns_utc(self) -> None:
        """now_utc() returns a datetime whose UTC offset is exactly zero seconds."""
        dt = now_utc()
        # UTC offset must be zero
        assert dt.utcoffset() is not None
        assert dt.utcoffset().total_seconds() == 0  # type: ignore[union-attr]

    def test_monotonic(self) -> None:
        """Two consecutive calls must not go backwards."""
        t1 = now_utc()
        t2 = now_utc()
        assert t2 >= t1


class TestRoundtrip:
    """Verify from_madrid(to_madrid(dt)) == dt for UTC datetimes."""

    def test_summer_time_roundtrip(self) -> None:
        """from_madrid(to_madrid(dt)) round-trips a summer UTC datetime without loss."""
        # 2026-08-23 14:22:03 UTC  →  16:22:03 CEST (UTC+2)
        dt = datetime(2026, 8, 23, 14, 22, 3, tzinfo=_UTC)
        assert from_madrid(to_madrid(dt)) == dt

    def test_winter_time_roundtrip(self) -> None:
        """from_madrid(to_madrid(dt)) round-trips a winter UTC datetime without loss."""
        # 2026-01-15 10:00:00 UTC  →  11:00:00 CET (UTC+1)
        dt = datetime(2026, 1, 15, 10, 0, 0, tzinfo=_UTC)
        assert from_madrid(to_madrid(dt)) == dt

    def test_preserves_microseconds(self) -> None:
        """The Madrid roundtrip preserves sub-second precision down to microseconds."""
        dt = datetime(2026, 8, 23, 14, 22, 3, 412345, tzinfo=_UTC)
        assert from_madrid(to_madrid(dt)) == dt


class TestToMadrid:
    """Verify to_madrid() converts UTC to Europe/Madrid correctly."""

    def test_summer_offset_is_cest(self) -> None:
        """to_madrid() in summer returns a datetime with CEST offset (UTC+2 = 7200 seconds)."""
        # CEST = UTC+2 during summer
        dt_utc = datetime(2026, 8, 23, 14, 22, 3, tzinfo=_UTC)
        madrid = to_madrid(dt_utc)
        assert madrid.utcoffset() is not None
        assert madrid.utcoffset().total_seconds() == 2 * 3600  # type: ignore[union-attr]

    def test_winter_offset_is_cet(self) -> None:
        """to_madrid() in winter returns a datetime with CET offset (UTC+1 = 3600 seconds)."""
        # CET = UTC+1 during winter
        dt_utc = datetime(2026, 1, 15, 10, 0, 0, tzinfo=_UTC)
        madrid = to_madrid(dt_utc)
        assert madrid.utcoffset() is not None
        assert madrid.utcoffset().total_seconds() == 1 * 3600  # type: ignore[union-attr]


class TestDstBoundary:
    """Verify DST spring-forward handling for Spain (last Sunday of March).

    Spain springs forward from CET (UTC+1) to CEST (UTC+2) in late March.
    On 2026-03-29, clocks skip from 02:00 to 03:00.
    """

    def test_before_spring_forward_is_cet(self) -> None:
        """Before Spain's spring-forward clock change, to_madrid() returns CET (UTC+1) offset."""
        # 00:30 UTC on the spring-forward day — still CET (UTC+1)
        dt_utc = datetime(2026, 3, 29, 0, 30, 0, tzinfo=_UTC)
        madrid = to_madrid(dt_utc)
        assert madrid.utcoffset() is not None
        # offset should be +1h (CET)
        assert madrid.utcoffset().total_seconds() == 3600  # type: ignore[union-attr]

    def test_after_spring_forward_is_cest(self) -> None:
        """After Spain's spring-forward clock change, to_madrid() returns CEST (UTC+2) offset."""
        # 02:00 UTC on the spring-forward day — now CEST (UTC+2)
        # (Madrid local time would be 04:00 CEST after the skip)
        dt_utc = datetime(2026, 3, 29, 2, 30, 0, tzinfo=_UTC)
        madrid = to_madrid(dt_utc)
        assert madrid.utcoffset() is not None
        # offset should be +2h (CEST)
        assert madrid.utcoffset().total_seconds() == 2 * 3600  # type: ignore[union-attr]

    def test_roundtrip_across_dst_boundary(self) -> None:
        """The Madrid roundtrip preserves a UTC datetime straddling the DST spring-forward."""
        # UTC datetime near DST boundary must survive roundtrip
        dt_utc = datetime(2026, 3, 29, 1, 0, 0, tzinfo=_UTC)
        assert from_madrid(to_madrid(dt_utc)) == dt_utc
