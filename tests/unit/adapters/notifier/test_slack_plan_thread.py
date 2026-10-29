"""Wire-level tests for the Slack trade plan posted as a thread reply.

Wire calls are mocked with ``respx`` and the recorded ``chat.postMessage``
response shape; the request bodies are fixed by ``syrupy`` snapshots. No real
socket ever opens.

Invariants protected here:

- a signal posts its headline as the parent and the plan as a second
  ``chat.postMessage`` whose ``thread_ts`` is the ``ts`` the first returned;
- a failed reply keeps the parent, raises :class:`SlackNotifierError` and logs
  a structured warning that never carries the message text;
- a failed parent posts no reply and raises as before;
- an event without a plan posts exactly once, with no ``thread_ts``.

Reference: openspec/archive/667-slack-plan-thread/change.md.
"""

import json
from typing import Any

import httpx
import pytest
import respx
from structlog.testing import capture_logs
from syrupy.assertion import SnapshotAssertion

from hermes.adapters.notifier import SlackAdapter, SlackConfig, SlackNotifierError
from tests.support.notification_samples import samples

_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
_CHANNEL = "C0123ABCD"
_PARENT_TS = "1759660800.000100"
_RECORDED_PARENT = {
    "ok": True,
    "channel": _CHANNEL,
    "ts": _PARENT_TS,
    "message": {"type": "message", "ts": _PARENT_TS},
}
_RECORDED_REPLY = {"ok": True, "channel": _CHANNEL, "ts": "1759660800.000200"}
_RECORDED_REJECTION = {"ok": False, "error": "ratelimited"}


@pytest.fixture
def adapter() -> SlackAdapter:
    """Return an adapter on a plain client; respx patches the transport per test."""
    config = SlackConfig(bot_token="xoxb-test", default_channel=_CHANNEL)  # noqa: S106  # gitleaks:allow
    return SlackAdapter(config=config, client=httpx.Client(base_url="https://slack.com"))


def _bodies(route: respx.Route) -> list[dict[str, Any]]:
    """Return the JSON body of every request the route received, in order."""
    return [json.loads(call.request.content) for call in route.calls]


def test_signal_posts_the_headline_then_the_plan_in_its_thread(
    adapter: SlackAdapter, snapshot: SnapshotAssertion
) -> None:
    """Two posts go out, parent first; the request bodies match the reviewed snapshot."""
    with respx.mock(assert_all_called=True) as router:
        route = router.post(_POST_MESSAGE_URL).mock(
            side_effect=[
                httpx.Response(200, json=_RECORDED_PARENT),
                httpx.Response(200, json=_RECORDED_REPLY),
            ]
        )
        adapter.notify(samples()["signal-entry"])

    assert _bodies(route) == snapshot


def test_plan_reply_carries_the_ts_the_parent_returned(adapter: SlackAdapter) -> None:
    """The reply's thread_ts is the parent's ts, and the parent has no thread_ts."""
    with respx.mock() as router:
        route = router.post(_POST_MESSAGE_URL).mock(
            side_effect=[
                httpx.Response(200, json=_RECORDED_PARENT),
                httpx.Response(200, json=_RECORDED_REPLY),
            ]
        )
        adapter.notify(samples()["signal-entry"])

    parent, reply = _bodies(route)
    assert ("thread_ts" in parent, reply["thread_ts"]) == (False, _PARENT_TS)


def test_parent_post_holds_no_plan_text(adapter: SlackAdapter) -> None:
    """The plan lines are only in the reply, so the channel view stays one headline."""
    with respx.mock() as router:
        route = router.post(_POST_MESSAGE_URL).mock(
            side_effect=[
                httpx.Response(200, json=_RECORDED_PARENT),
                httpx.Response(200, json=_RECORDED_REPLY),
            ]
        )
        adapter.notify(samples()["signal-entry"])

    parent, reply = _bodies(route)
    assert ("Plan:" in json.dumps(parent), "Plan:" in json.dumps(reply)) == (False, True)


def test_failed_reply_raises_the_notifier_error_after_the_parent_posted(
    adapter: SlackAdapter,
) -> None:
    """A rejected reply raises SlackNotifierError; the parent call already went out."""
    with respx.mock() as router:
        route = router.post(_POST_MESSAGE_URL).mock(
            side_effect=[
                httpx.Response(200, json=_RECORDED_PARENT),
                httpx.Response(200, json=_RECORDED_REJECTION),
            ]
        )
        with pytest.raises(SlackNotifierError) as excinfo:
            adapter.notify(samples()["signal-entry"])

    assert (str(excinfo.value), route.call_count) == (
        "Slack notifier error: Slack API rejected /api/chat.postMessage: "
        "{'ok': False, 'error': 'ratelimited'}",
        2,
    )


def test_failed_reply_logs_a_warning_without_the_message_text(adapter: SlackAdapter) -> None:
    """The warning names the failure and the thread but carries no plan or headline text."""
    with respx.mock() as router, capture_logs() as logs:
        router.post(_POST_MESSAGE_URL).mock(
            side_effect=[
                httpx.Response(200, json=_RECORDED_PARENT),
                httpx.Response(500, text="boom"),
            ]
        )
        with pytest.raises(SlackNotifierError, match=r"HTTP 500 from /api/chat\.postMessage: boom"):
            adapter.notify(samples()["signal-entry"])

    warnings = [entry for entry in logs if entry["event"] == "slack_plan_reply_failed"]
    assert len(warnings) == 1
    assert (warnings[0]["log_level"], warnings[0]["thread_ts"], warnings[0]["error_type"]) == (
        "warning",
        _PARENT_TS,
        "SlackNotifierError",
    )
    text = json.dumps(warnings[0], default=str)
    assert ("Plan:" in text, "Entry long" in text, "50,516" in text) == (False, False, False)


def test_reply_transport_error_keeps_the_parent_and_raises(adapter: SlackAdapter) -> None:
    """A dropped connection on the reply is wrapped like any other notifier failure."""
    with respx.mock() as router:
        route = router.post(_POST_MESSAGE_URL).mock(
            side_effect=[
                httpx.Response(200, json=_RECORDED_PARENT),
                httpx.ConnectError("down"),
            ]
        )
        with pytest.raises(SlackNotifierError, match="transport failure"):
            adapter.notify(samples()["signal-entry"])

    assert route.call_count == 2


def test_parent_without_a_ts_cannot_be_threaded_and_raises(adapter: SlackAdapter) -> None:
    """With no ts to thread under, no reply is posted and the error is raised and logged."""
    with respx.mock() as router, capture_logs() as logs:
        route = router.post(_POST_MESSAGE_URL).mock(
            return_value=httpx.Response(200, json={"ok": True, "channel": _CHANNEL})
        )
        with pytest.raises(SlackNotifierError, match="no ts"):
            adapter.notify(samples()["signal-entry"])

    assert (route.call_count, [e["event"] for e in logs]) == (1, ["slack_plan_reply_failed"])


def test_non_string_parent_ts_is_logged_as_no_thread(adapter: SlackAdapter) -> None:
    """A ts that is not a string is not threaded under and is logged as no thread at all."""
    with respx.mock() as router, capture_logs() as logs:
        router.post(_POST_MESSAGE_URL).mock(
            return_value=httpx.Response(200, json={"ok": True, "ts": 1759660800})
        )
        with pytest.raises(SlackNotifierError, match="no ts to thread the plan under"):
            adapter.notify(samples()["signal-entry"])

    assert logs == [
        {
            "event": "slack_plan_reply_failed",
            "log_level": "warning",
            "channel": _CHANNEL,
            "thread_ts": None,
            "error_type": "SlackNotifierError",
        }
    ]


def test_failed_parent_behaves_as_before_and_posts_no_reply(adapter: SlackAdapter) -> None:
    """If the first post fails the error is raised at once and no reply is attempted."""
    with respx.mock() as router:
        route = router.post(_POST_MESSAGE_URL).mock(
            return_value=httpx.Response(200, json=_RECORDED_REJECTION)
        )
        with pytest.raises(SlackNotifierError, match="ratelimited"):
            adapter.notify(samples()["signal-entry"])

    assert route.call_count == 1


def test_event_without_a_plan_posts_once_with_no_thread(adapter: SlackAdapter) -> None:
    """A compact health event is one chat.postMessage and never sets thread_ts."""
    with respx.mock() as router:
        route = router.post(_POST_MESSAGE_URL).mock(
            return_value=httpx.Response(200, json=_RECORDED_PARENT)
        )
        adapter.notify(samples()["strategy-paused-critical"])

    (body,) = _bodies(route)
    assert (route.call_count, "thread_ts" in body) == (1, False)
