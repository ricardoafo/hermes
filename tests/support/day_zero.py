"""Writes the day-0 record ``hermes shadow-start`` leaves beside the ledger."""

from datetime import datetime
from pathlib import Path

from hermes.application.strategy_protocol.day_zero import Check, DayZeroRecord, Verdict


def write_day_zero(ledger: Path, strategy_id: str, day_zero_at: datetime) -> Path:
    """Write a minimal day-0 record of ``strategy_id`` into the runs directory beside ``ledger``."""
    record = DayZeroRecord(
        run_id=f"{strategy_id}-{day_zero_at:%Y%m%dT%H%M%SZ}",
        strategy_id=strategy_id,
        day_zero_at=day_zero_at,
        package_version="0.50.0",
        release="r",
        config_sha256="c",
        preregistration_sha256="p",
        preregistration_signed_sha256="s",
        data_manifest_sha256="m",
        parameter_fingerprint="f",
        archived_ledger=None,
        archived_ledger_sha256=None,
        checks=(Check(2, "gate", Verdict.PASS, "recorded"),),
    )
    runs = ledger.parent / "runs"
    runs.mkdir(exist_ok=True)
    path = runs / f"{record.run_id}.json"
    path.write_text(record.to_json(), encoding="utf-8")
    return path
