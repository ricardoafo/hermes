"""Tests for the PDF writer of the tax pack's sheets (change 568-tax-pack D-8).

Renders with ``compress=False`` so the page text can be found in the bytes;
the compressed default is what ``hermes tax-pack`` writes, covered end to end
in ``tests/unit/entrypoints/test_tax_pack.py``.
"""

import re
from datetime import UTC, datetime

from hypothesis import given
from hypothesis import strategies as st

from hermes.adapters.documents.pdf_writer import latin1, render_pdf
from hermes.application.filing.document import Document, Heading, Paragraph, Table, Tone

_AT = datetime(2027, 1, 5, 6, 30, tzinfo=UTC)
_SHOW_TEXT = re.compile(rb"\(((?:[^()\\]|\\.)*)\) ?Tj")
_ESCAPE = re.compile(rb"\\(.)")


def _doc(*, landscape: bool = False) -> Document:
    return Document(
        title="Renta 2027 (ejercicio 2026)",
        blocks=(
            Heading("Modelo 100: casilla 1804"),
            Paragraph("Ejercicio cerrado.", Tone.WARNING),
            Paragraph("Generado por hermes.", Tone.NOTE),
            Table.of(
                ["Moneda", "Valor (€)"],
                [["Bitcoin (BTC)", "1.230,00"]],
                numeric=[1],
            ),
        ),
        landscape=landscape,
    )


def _plain(document: Document) -> bytes:
    return render_pdf(document, created_at=_AT, compress=False)


def _shown(document: Document) -> list[str]:
    """Return each string the page shows, unescaped, as latin-1 text."""
    return [
        _ESCAPE.sub(rb"\1", raw).decode("latin-1") for raw in _SHOW_TEXT.findall(_plain(document))
    ]


def test_output_is_a_pdf_file() -> None:
    """Invariant: the writer returns a complete PDF (header and end-of-file marker)."""
    pdf = render_pdf(_doc(), created_at=_AT)
    assert (pdf[:5], pdf.rstrip()[-5:]) == (b"%PDF-", b"%%EOF")


def test_every_block_reaches_the_page() -> None:
    """Invariant: title, heading, paragraphs and table cells are all printed."""
    assert _shown(_doc()) == [
        "Renta 2027 (ejercicio 2026)",
        "Modelo 100: casilla 1804",
        "Ejercicio cerrado.",
        "Generado por hermes.",
        "Moneda",
        "Valor (EUR)",
        "Bitcoin (BTC)",
        "1.230,00",
    ]


def test_euro_sign_is_spelled_out() -> None:
    """Invariant: the core font cannot print the euro sign, so headers read 'EUR'."""
    assert "Valor (EUR)" in _shown(_doc())


def test_spanish_letters_are_kept() -> None:
    """Invariant: accented letters and the eñe print as themselves (latin-1)."""
    assert _shown(Document(title="Contraprestación año", blocks=())) == ["Contraprestación año"]


def test_same_document_renders_the_same_bytes() -> None:
    """Invariant: a refresh with unchanged figures leaves the manifest checksum unchanged."""
    assert render_pdf(_doc(), created_at=_AT) == render_pdf(_doc(), created_at=_AT)


def test_landscape_document_gets_landscape_pages() -> None:
    """Invariant: a wide document is laid out on A4 landscape (842 x 595 points)."""
    assert b"/MediaBox [0 0 841.89 595.28]" in _plain(_doc(landscape=True))
    assert b"/MediaBox [0 0 595.28 841.89]" in _plain(_doc())


def test_long_tables_continue_on_further_pages() -> None:
    """Invariant: a table longer than a page breaks onto new pages rather than being cut."""
    rows = [[str(n), "1,00"] for n in range(200)]
    document = Document(title="t", blocks=(Table.of(["n", "€"], rows, numeric=[1]),))
    assert _plain(document).count(b"/Type /Page\n") > 1
    assert "199" in _shown(document)


def test_unprintable_characters_never_fail_the_render() -> None:
    """Invariant: text outside latin-1 is replaced, never an exception in the daily run."""
    assert _shown(Document(title="BTC ✓ 比特币", blocks=())) == ["BTC ? ???"]


@given(st.text())
def test_latin1_output_always_encodes(text: str) -> None:
    """Property: whatever the input, the mapped text encodes as latin-1 and decodes back."""
    mapped = latin1(text)
    assert mapped.encode("latin-1").decode("latin-1") == mapped


@given(st.text(alphabet=st.characters(max_codepoint=0xFF)))
def test_latin1_text_passes_through_unchanged(text: str) -> None:
    """Property: text already in latin-1 is printed exactly as given."""
    assert latin1(text) == text
