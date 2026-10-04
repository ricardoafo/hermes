"""MarkdownV2 escaping helpers for Telegram templates.

Telegram's MarkdownV2 mode is strict — any unescaped reserved character
in a payload silently corrupts the message rendering. This helper
centralises the escape rule so templates never accidentally emit an
un-escaped ``_``, ``.``, ``-`` or similar.

Mirror of ``_escape_markdown_v2`` in
``src/hermes/adapters/notifier/telegram_adapter.py`` — kept in sync
manually. The template layer needs a local copy so the application
layer does NOT import from ``adapters/`` (hexagonal invariant, per
code-conventions §1).

Reference: https://core.telegram.org/bots/api#markdownv2-style
"""

from __future__ import annotations

from typing import Final

__all__ = ["CORRELATION_TAIL_LEN", "correlation_tail", "escape_markdown_v2", "escape_slack"]

# MarkdownV2 reserved characters that MUST be escaped to render literally.
_MARKDOWN_V2_RESERVED: Final[frozenset[str]] = frozenset(
    "_*[]()~`>#+-=|{}.!",
)

CORRELATION_TAIL_LEN: Final[int] = 6
"""Number of trailing correlation-ID characters embedded in messages."""


def escape_markdown_v2(text: str) -> str:
    """Escape MarkdownV2 reserved characters so ``text`` renders literally.

    Every character in :data:`_MARKDOWN_V2_RESERVED` gets a backslash
    prefix. The helper is unconditional — it does not try to preserve
    intentional Markdown formatting. Templates that want bold headers
    apply ``*`` outside this helper's scope.

    Args:
        text: The raw text to escape.

    Returns:
        The text with every reserved character prefixed by a backslash.
    """
    return "".join(f"\\{c}" if c in _MARKDOWN_V2_RESERVED else c for c in text)


def correlation_tail(correlation_id: str) -> str:
    """Return the last :data:`CORRELATION_TAIL_LEN` chars of ``correlation_id``.

    Per ADR-008 § Correlation ID convention, notification messages carry
    the correlation-ID tail so an operator reading a phone ping can
    grep journald for the full log context.

    Args:
        correlation_id: The full ULID or short identifier.

    Returns:
        The tail (possibly the entire string if it is shorter than the
        tail length). Never raises.
    """
    return correlation_id[-CORRELATION_TAIL_LEN:]


def escape_slack(text: str) -> str:
    """Escape the three characters Slack treats as markup in ``mrkdwn`` text.

    Slack reads ``&``, ``<`` and ``>`` as control characters (links and
    mentions), so a reason such as ``a < b`` must not reach the API raw.

    Args:
        text: The raw text.

    Returns:
        The text with ``&``, ``<`` and ``>`` replaced by their entities.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
