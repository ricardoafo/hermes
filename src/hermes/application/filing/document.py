"""A small document model for the tax pack's printable sheets.

The application builds what a sheet says (headings, paragraphs, tables); an
adapter turns it into a file. Keeps the PDF library out of the application
layer and lets tests assert on content, not bytes.

Reference: openspec/archive/568-tax-pack/change.md D-8.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from enum import StrEnum

__all__ = ["Block", "Document", "Heading", "Paragraph", "Table", "Tone"]


class Tone(StrEnum):
    """How a paragraph is set."""

    NORMAL = "normal"
    NOTE = "note"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class Heading:
    """A section title.

    Attributes:
        text: The title.
    """

    text: str


@dataclass(frozen=True, slots=True)
class Paragraph:
    """A block of text.

    Attributes:
        text: The text; ``\\n`` breaks a line.
        tone: Normal text, a small note, or a boxed warning.
    """

    text: str
    tone: Tone = Tone.NORMAL


@dataclass(frozen=True, slots=True)
class Table:
    """A table with a header row.

    Attributes:
        header: Column titles.
        rows: Cells, one tuple per row, as many cells as the header.
        numeric: Indexes of right-aligned (numeric) columns.
    """

    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    numeric: frozenset[int] = frozenset()

    @classmethod
    def of(
        cls,
        header: Sequence[str],
        rows: Sequence[Sequence[str]],
        *,
        numeric: Collection[int] = (),
    ) -> Table:
        """Build a table from any sequences.

        Raises:
            ValueError: A row's length differs from the header's.
        """
        table = cls(
            header=tuple(header),
            rows=tuple(tuple(row) for row in rows),
            numeric=frozenset(numeric),
        )
        for row in table.rows:
            if len(row) != len(table.header):
                raise ValueError(f"row has {len(row)} cells, header has {len(table.header)}")
        return table


Block = Heading | Paragraph | Table


@dataclass(frozen=True, slots=True)
class Document:
    """A printable sheet.

    Attributes:
        title: Document title, printed first and stored as metadata.
        blocks: Content in reading order.
        landscape: Whether pages are landscape (wide tables).
    """

    title: str
    blocks: tuple[Block, ...]
    landscape: bool = False
