"""Certification store port — persistence for the certification record.

One :class:`~hermes.domain.calibration.certification.CertificationRecord`
per strategy. Implementations live in :mod:`hermes.adapters.certification`.

Reference: openspec/archive/355-certification-gates/change.md D-7.
"""

from typing import Protocol, runtime_checkable

from hermes.domain.calibration.certification import CertificationRecord

__all__ = ["CertificationStoreError", "CertificationStorePort"]


class CertificationStoreError(Exception):
    """The stored record cannot be read or written."""


@runtime_checkable
class CertificationStorePort(Protocol):
    """Load and save certification records by strategy id."""

    def load(self, strategy_id: str) -> CertificationRecord | None:
        """Return the stored record, or ``None`` when the strategy has none.

        Raises:
            CertificationStoreError: The stored record is unreadable.
        """
        ...

    def save(self, record: CertificationRecord) -> None:
        """Persist ``record``, replacing any previous one for its strategy.

        Raises:
            CertificationStoreError: The record could not be written.
        """
        ...
