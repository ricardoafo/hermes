"""Render a :class:`~hermes.application.filing.document.Document` as PDF.

Uses fpdf2's core Helvetica font, so no font file ships with Hermes. Core
fonts cover latin-1, which holds every Spanish letter; the few other
characters the sheets use are spelled out (``€`` as ``EUR``) and anything
else becomes ``?`` rather than failing a daily refresh.

The creation date is the refresh instant, so the same pack renders the same
bytes and the manifest only changes when a figure does.

Reference: openspec/changes/568-tax-pack/change.md D-8.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from fpdf import FPDF
from fpdf.enums import Align, TableBordersLayout, XPos, YPos

from hermes.application.filing.document import Document, Heading, Paragraph, Table, Tone

__all__ = ["render_pdf"]

_FONT: Final[str] = "Helvetica"
_SPELLED_OUT: Final[dict[str, str]] = {
    "\u20ac": "EUR",
    "\u2192": "->",
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2013": "-",
    "\u2014": "-",
    "\u2026": "...",
}
_LINE: Final[float] = 5.0


def render_pdf(document: Document, *, created_at: datetime, compress: bool = True) -> bytes:
    """Return ``document`` as PDF bytes.

    Args:
        document: What the sheet says.
        created_at: Stored as the PDF creation date.
        compress: Whether page streams are compressed; tests turn it off to
            find text in the bytes.

    Returns:
        The PDF file content.
    """
    pdf = FPDF(orientation="L" if document.landscape else "P", format="A4")
    pdf.set_compression(compress)
    pdf.set_creation_date(created_at)
    pdf.set_title(latin1(document.title))
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font(_FONT, style="B", size=15)
    pdf.multi_cell(0, 8, latin1(document.title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)
    for block in document.blocks:
        if isinstance(block, Heading):
            _heading(pdf, block)
        elif isinstance(block, Paragraph):
            _paragraph(pdf, block)
        else:
            _table(pdf, block)
    return bytes(pdf.output())


def latin1(text: str) -> str:
    """Return ``text`` with every character the core fonts can print.

    Args:
        text: Any text.

    Returns:
        ``text`` with known symbols spelled out and other non-latin-1
        characters replaced by ``?``.
    """
    spelled = "".join(_SPELLED_OUT.get(char, char) for char in text)
    return spelled.encode("latin-1", errors="replace").decode("latin-1")


def _heading(pdf: FPDF, block: Heading) -> None:
    pdf.ln(3)
    pdf.set_font(_FONT, style="B", size=12)
    pdf.multi_cell(0, 7, latin1(block.text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1)


def _paragraph(pdf: FPDF, block: Paragraph) -> None:
    text = latin1(block.text)
    if block.tone is Tone.WARNING:
        pdf.set_font(_FONT, style="B", size=10)
        pdf.multi_cell(0, _LINE, text, border=1, padding=2, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    elif block.tone is Tone.NOTE:
        pdf.set_font(_FONT, style="I", size=9)
        pdf.multi_cell(0, _LINE, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    else:
        pdf.set_font(_FONT, size=10)
        pdf.multi_cell(0, _LINE, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)


def _table(pdf: FPDF, block: Table) -> None:
    pdf.set_font(_FONT, size=8)
    align = tuple(
        Align.R if index in block.numeric else Align.L for index in range(len(block.header))
    )
    with pdf.table(
        text_align=align,
        line_height=4.5,
        borders_layout=TableBordersLayout.SINGLE_TOP_LINE,
        repeat_headings=1,
    ) as table:
        for row in (block.header, *block.rows):
            cells = table.row()
            for cell in row:
                cells.cell(latin1(cell))
    pdf.ln(2)
