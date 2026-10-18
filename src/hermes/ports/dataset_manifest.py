"""Dataset manifest port — abstract Protocol for loading the pinned data manifest.

The concrete implementation is :mod:`hermes.adapters.data_manifest.json_file`.

Reference: code-conventions.md §5 (Ports as Protocols),
openspec/archive/356-data-manifest/change.md.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from hermes.domain.market_data.dataset_manifest import DatasetManifest

__all__ = ["DatasetManifestPort"]


@runtime_checkable
class DatasetManifestPort(Protocol):
    """Contract every dataset-manifest store satisfies.

    Adapters MUST raise :class:`hermes.domain.errors.InfraError` (or a
    subclass) when the stored manifest is missing or malformed.
    """

    def load(self) -> DatasetManifest:
        """Return the stored manifest.

        Returns:
            The validated :class:`DatasetManifest`.
        """
        ...
