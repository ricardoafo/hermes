"""Unit tests for the JSON certification store (#355 D-7)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from hermes.adapters.certification.json_store import JsonCertificationStore
from hermes.application.calibration.certification_gates import CertificationRecord
from hermes.application.calibration.lifecycle import CertificationState
from hermes.ports.certification import CertificationStoreError, CertificationStorePort

_AT = datetime(2027, 1, 1, tzinfo=UTC)


def _record() -> CertificationRecord:
    return (
        CertificationRecord("swing-donchian-4h")
        .moved_to(
            CertificationState.BACKTEST_CERTIFIED,
            at=_AT,
            reason="gate passed",
            fingerprint="f" * 64,
            evidence_sha256="e" * 64,
        )
        .moved_to(
            CertificationState.SHADOW_CERTIFIED, at=_AT, reason="gate passed", fingerprint="f" * 64
        )
    )


def test_satisfies_the_port(tmp_path: Path) -> None:
    """The adapter is a structural CertificationStorePort."""
    assert isinstance(JsonCertificationStore(tmp_path), CertificationStorePort)


def test_round_trip_keeps_every_field(tmp_path: Path) -> None:
    """A saved record loads back equal, transitions and OOS time included."""
    store = JsonCertificationStore(tmp_path / "certification")
    record = _record()
    store.save(record)
    assert store.load("swing-donchian-4h") == record


def test_oos_time_survives_a_round_trip(tmp_path: Path) -> None:
    """The one-shot OOS marker must persist, or the shot could be taken twice (§7)."""
    store = JsonCertificationStore(tmp_path)
    record = CertificationRecord("swing-donchian-4h", oos_evaluated_at=_AT)
    store.save(record)
    loaded = store.load("swing-donchian-4h")
    assert loaded is not None
    assert loaded.oos_evaluated_at == _AT


def test_missing_file_is_none_and_creates_nothing(tmp_path: Path) -> None:
    """A strategy with no record loads as None, and reading creates no directory."""
    directory = tmp_path / "certification"
    loaded = JsonCertificationStore(directory).load("swing-donchian-4h")
    assert (loaded, directory.exists()) == (None, False)


def test_corrupt_file_raises(tmp_path: Path) -> None:
    """An unreadable record is an error, never silently UNTRUSTED or approved."""
    (tmp_path / "swing-donchian-4h.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(CertificationStoreError, match="unreadable"):
        JsonCertificationStore(tmp_path).load("swing-donchian-4h")


def test_record_under_the_wrong_name_raises(tmp_path: Path) -> None:
    """A file renamed to another strategy is refused."""
    store = JsonCertificationStore(tmp_path)
    store.save(CertificationRecord("other"))
    (tmp_path / "other.json").rename(tmp_path / "swing-donchian-4h.json")
    with pytest.raises(CertificationStoreError, match="expected"):
        store.load("swing-donchian-4h")


def test_save_replaces_and_leaves_no_temp_files(tmp_path: Path) -> None:
    """A second save replaces the first and the directory holds one file."""
    store = JsonCertificationStore(tmp_path)
    store.save(CertificationRecord("swing-donchian-4h"))
    store.save(_record())
    assert [p.name for p in tmp_path.iterdir()] == ["swing-donchian-4h.json"]
    loaded = store.load("swing-donchian-4h")
    assert loaded is not None
    assert loaded.state == CertificationState.SHADOW_CERTIFIED
