"""The awaited signal push fits inside the live-book window (issue #996 item 3).

Serve awaits each signal push before it handles the next bar, so the push
then record order of #854 holds and a crash never loses a push. The Code
Owner chose on 19 Oct 2026 to keep that wait rather than run the push as its
own task. The price of the wait is that the pairs closing at one bar
boundary are handled one after another, so the last pair's fill waits for
every earlier pair's push. This test keeps that worst case inside
``LIVE_BOOK_WINDOW``, past which an entry is passed over as late.

Invariants under test:

- With every configured pair emitting a signal on the same bar and every
  channel using its whole send budget, the last pair is still handled
  inside the live-book window.
"""

from datetime import timedelta

from hermes.adapters._shared import telegram_http
from hermes.adapters.notifier import slack_adapter
from hermes.application.execution.shadow import LIVE_BOOK_WINDOW
from hermes.infrastructure.config.loader import load_config
from tests.support.paths import REPO_ROOT

_SLACK_POSTS_PER_SIGNAL = 2
"""The signal card, then its plan block as a reply in the card's thread."""


def test_the_worst_case_push_wait_stays_inside_the_live_book_window() -> None:
    """Pairs ahead of the last, each pushing at full budget, end inside the window.

    Per push: Telegram's whole-send budget (30 s, waits and requests
    together) plus two Slack posts at their 10 s timeout, 50 s. With 3 pairs
    the last waits for 2 pushes, 100 s, against a 5 minute window.
    """
    pairs = load_config(REPO_ROOT / "config", env={}).market_data.pairs
    per_push = timedelta(
        seconds=telegram_http.MAX_CALL_SEC
        + _SLACK_POSTS_PER_SIGNAL * slack_adapter._DEFAULT_TIMEOUT_SEC
    )

    worst_wait = (len(pairs) - 1) * per_push

    assert worst_wait < LIVE_BOOK_WINDOW
