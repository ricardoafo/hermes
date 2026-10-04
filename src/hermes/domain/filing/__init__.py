"""Spanish tax filing figures: Modelo 100 transmissions and Modelo 721 holdings.

Pure functions over what the ledger already stores. Nothing here computes
tax (that is the estimate module, #395), submits anything, or reads the
taxpayer profile. Filing outputs are measurement only: an import-linter
contract keeps strategy, sizing, fees, risk and kill code from importing them.

Reference: openspec/changes/36-modelo-100-721/change.md, ADR-002, ADR-016.
"""

from hermes.domain.filing.modelo100 import (
    CoinSummary,
    ConsiderationKind,
    DisposalPiece,
    Modelo100Report,
    PendingSale,
    PurchaseRecord,
    RepurchaseFlag,
    Transmission,
    build_modelo100,
)
from hermes.domain.filing.modelo721 import (
    HoldingLine,
    Modelo721Origin,
    Modelo721Report,
    Modelo721Rules,
    Obligation,
    PreviousDeclaration,
    UnitTrade,
    YearEndPosition,
    build_modelo721,
    year_end_positions,
)
from hermes.domain.filing.tax_year import tax_year_bounds, tax_year_of

__all__ = [
    "CoinSummary",
    "ConsiderationKind",
    "DisposalPiece",
    "HoldingLine",
    "Modelo100Report",
    "Modelo721Origin",
    "Modelo721Report",
    "Modelo721Rules",
    "Obligation",
    "PendingSale",
    "PreviousDeclaration",
    "PurchaseRecord",
    "RepurchaseFlag",
    "Transmission",
    "UnitTrade",
    "YearEndPosition",
    "build_modelo100",
    "build_modelo721",
    "tax_year_bounds",
    "tax_year_of",
    "year_end_positions",
]
