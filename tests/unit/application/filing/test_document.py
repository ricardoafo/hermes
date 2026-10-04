"""Tests for the tax pack's document model (change 568-tax-pack D-8)."""

from __future__ import annotations

import pytest

from hermes.application.filing.document import Table


def test_table_of_keeps_cells_and_numeric_columns() -> None:
    """Invariant: a table built from lists holds the same cells, as tuples, and its numeric set."""
    table = Table.of(["Moneda", "Valor"], [["BTC", "1,00"]], numeric=[1])
    assert table == Table(
        header=("Moneda", "Valor"), rows=(("BTC", "1,00"),), numeric=frozenset({1})
    )


def test_table_of_rejects_a_row_of_the_wrong_length() -> None:
    """Invariant: a row with fewer cells than the header never reaches the printed page."""
    with pytest.raises(ValueError, match="row has 1 cells, header has 2"):
        Table.of(["Moneda", "Valor"], [["BTC"]])
