"""Tests for the MarkdownV2 escape + correlation-tail helpers.

Verifies:
- Every documented reserved character is escaped with a leading backslash.
- Plain ASCII letters and digits pass through unchanged.
- The correlation tail returns the last N chars, or the entire string
  when shorter than N.

Reference: docs/decisions/2026-08-23-slack-alongside-telegram.md.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.messages._escape import (
    CORRELATION_TAIL_LEN,
    correlation_tail,
    escape_markdown_v2,
    escape_slack,
)


class TestEscapeMarkdownV2:
    """Every MarkdownV2 reserved character gets a backslash prefix."""

    @pytest.mark.parametrize(
        "char",
        ["_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"],
    )
    def test_reserved_char_is_escaped(self, char: str) -> None:
        """Each reserved character in isolation is prefixed by exactly one backslash."""
        result = escape_markdown_v2(char)
        assert result == f"\\{char}"

    def test_plain_ascii_passes_through(self) -> None:
        """Letters and digits are NOT escaped."""
        assert escape_markdown_v2("abc123XYZ") == "abc123XYZ"

    def test_mixed_content_escapes_only_reserved(self) -> None:
        """A mix of reserved and non-reserved chars preserves the non-reserved run."""
        # The dot in "v1.0" is reserved (see _MARKDOWN_V2_RESERVED); letters and digits are not.
        assert escape_markdown_v2("swing v1.0") == "swing v1\\.0"

    def test_empty_string_returns_empty(self) -> None:
        """An empty input returns an empty output — no phantom characters."""
        assert escape_markdown_v2("") == ""


class TestCorrelationTail:
    """The tail helper returns the last N chars per ADR-008."""

    def test_tail_of_full_ulid_returns_six_chars(self) -> None:
        """A 26-char ULID yields exactly the trailing 6 characters."""
        cid = "01J9Z8T3M5BQR2FVKN4XWY6PH1"
        assert correlation_tail(cid) == "WY6PH1"
        assert len(correlation_tail(cid)) == CORRELATION_TAIL_LEN

    def test_tail_of_short_string_returns_entire_string(self) -> None:
        """A string shorter than the tail length is returned unchanged."""
        assert correlation_tail("abc") == "abc"


class TestEscapeSlack:
    """Slack reads &, < and > as markup, so they must reach the API as entities."""

    @pytest.mark.parametrize(
        ("raw", "escaped"),
        [
            ("&", "&amp;"),
            ("<", "&lt;"),
            (">", "&gt;"),
            ("<!channel> a & b", "&lt;!channel&gt; a &amp; b"),
        ],
    )
    def test_markup_characters_become_entities(self, raw: str, escaped: str) -> None:
        """Each of the three markup characters is replaced by its entity."""
        assert escape_slack(raw) == escaped

    @given(text=st.text(alphabet="abc XYZ 019 .,:;-_*`", max_size=40))
    def test_text_without_markup_characters_is_unchanged(self, text: str) -> None:
        """Property: text with no &, < or > passes through untouched."""
        assert escape_slack(text) == text

    @given(text=st.text(max_size=40))
    def test_escaped_text_has_no_raw_angle_brackets(self, text: str) -> None:
        """Property: after escaping no raw < or > remains, whatever the input."""
        assert ("<" in escape_slack(text), ">" in escape_slack(text)) == (False, False)
