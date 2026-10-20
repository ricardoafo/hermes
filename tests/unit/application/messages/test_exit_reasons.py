"""The notification samples cover every exit reason the strategy can emit.

A message for an exit reason nobody rendered in review is a message nobody
read before it reached the phone. The samples used to carry a reason the
strategy never emits, while the three real ones went unrendered.

Invariant protected: ``EXIT_REASONS`` names exactly the ``exit_reason``
values the swing strategy assigns, every one has a sample, and every
Telegram sample is valid MarkdownV2 (no reserved character left bare).

Reference: pre-registration v1 §5.2 (exit rules); issue #692.
"""

import ast

import pytest

from hermes.application.messages.telegram import telegram_template
from hermes.domain.events.signal_emitted import SignalEmitted
from tests.support.notification_samples import EXIT_REASONS, samples
from tests.support.paths import REPO_ROOT

_SWING_ADAPTER = REPO_ROOT / "src" / "hermes" / "domain" / "strategy" / "swing" / "adapter.py"
_MARKDOWN_V2_RESERVED = frozenset("_*[]()~`>#+-=|{}.!")


def _assigned_exit_reasons(source: str) -> set[str]:
    """Return every string literal assigned to a name called ``exit_reason``."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "exit_reason" for t in node.targets)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            found.add(node.value.value)
    return found


def _bare_reserved(line: str) -> list[str]:
    """Return the reserved characters in ``line`` that no backslash escapes."""
    bare: list[str] = []
    escaped = False
    for char in line:
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char in _MARKDOWN_V2_RESERVED:
            bare.append(char)
    return bare


def test_exit_reasons_match_the_strategy() -> None:
    """The sampled exit reasons are exactly those the swing adapter assigns."""
    assert set(EXIT_REASONS) == _assigned_exit_reasons(_SWING_ADAPTER.read_text(encoding="utf-8"))


def test_every_exit_reason_has_a_sample() -> None:
    """Each exit reason is rendered by at least one sample, so it has a reviewed snapshot."""
    sampled = {
        event.exit_reason
        for event in samples().values()
        if isinstance(event, SignalEmitted) and event.exit_reason is not None
    }
    assert sampled == set(EXIT_REASONS)


def test_bare_reserved_finds_an_unescaped_dot() -> None:
    """The MarkdownV2 check flags a bare dot and passes an escaped one."""
    assert (_bare_reserved("1.5"), _bare_reserved("1\\.5")) == (["."], [])


@pytest.mark.parametrize("name", sorted(name for name in samples() if name != "daily-digest"))
def test_telegram_sample_is_valid_markdown_v2(name: str) -> None:
    """No reserved MarkdownV2 character reaches Telegram unescaped (it would reject the message)."""
    assert _bare_reserved(telegram_template(samples()[name])) == []
