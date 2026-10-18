"""The notification fakes record what the real adapters send, in the vendors' format.

Invariants protected here (issue #972):

- every message the production adapters render for every notifiable event
  reaches the fake and passes the vendor contract, so a template that would
  be refused by Telegram or Slack fails here first;
- a request that breaks the contract is refused the way the vendor refuses
  it, and recorded with what it broke;
- inbound Telegram commands are served through ``getUpdates`` once each;
- a scheduled outage (429, 5xx, timeout) is what the next request meets;
- healthchecks pings and Sentry envelopes are recorded and checked;
- a recorded path never holds the bot token;
- the control API does what the Python API does, for a scenario run in
  another process.

The adapters run their production code over a real loopback socket; only
their base URL points at the fake.
"""

import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import sentry_sdk

from hermes.adapters.healthchecks import HealthchecksAdapter, HealthchecksAdapterConfig
from hermes.adapters.notifier import (
    SlackAdapter,
    SlackConfig,
    TelegramAdapter,
    TelegramConfig,
    TelegramNotifierError,
)
from hermes.application.messages.telegram import telegram_template
from tests.support.fake_notify import schema
from tests.support.fake_notify.server import FakeNotify, Outage, Sink
from tests.support.notification_samples import samples

_TOKEN = "123456789:FAKE-bot-token_for_tests"  # noqa: S105 - test-only  # gitleaks:allow
_SLACK_TOKEN = "xoxb-fake-token-for-tests"  # noqa: S105 - test-only  # gitleaks:allow
_CHAT = "-1001234567890"
_CHECK = "5f1d7a0e-3b2c-4d1e-9f00-123456789abc"


@pytest.fixture
def fake() -> Iterator[FakeNotify]:
    """Run a fake on a free loopback port whose timeouts hold 0.5 s."""
    with FakeNotify(timeout_hold_s=0.5) as running:
        yield running


def _telegram(fake: FakeNotify, timeout: float = 5.0) -> TelegramAdapter:
    client = httpx.Client(base_url=fake.telegram_base, timeout=timeout)
    return TelegramAdapter(
        config=TelegramConfig(bot_token=_TOKEN, chat_id=_CHAT),
        client=client,
        timeout_sec=timeout,
        sleep=lambda _seconds: None,
    )


def _slack(fake: FakeNotify) -> SlackAdapter:
    client = httpx.Client(base_url=fake.slack_base, timeout=5.0)
    return SlackAdapter(
        config=SlackConfig(bot_token=_SLACK_TOKEN, default_channel="C0LAB"), client=client
    )


def _telegram_events() -> list[str]:
    """Names of the samples Telegram has a template for."""
    names = []
    for name, event in samples().items():
        try:
            telegram_template(event)
        except ValueError:
            continue
        names.append(name)
    return names


@pytest.mark.parametrize("name", _telegram_events())
def test_every_telegram_push_is_one_hermes_would_get_through(fake: FakeNotify, name: str) -> None:
    """The rendered push for each event reaches the fake and meets the Bot API contract."""
    adapter = _telegram(fake)
    try:
        adapter.notify(samples()[name])
    finally:
        adapter.close()

    assert [(c.path, c.problems, c.status) for c in fake.calls(Sink.TELEGRAM)] == [
        ("/telegram/bot<token>/sendMessage", (), 200)
    ]


@pytest.mark.parametrize("name", sorted(samples()))
def test_every_slack_post_is_one_hermes_would_get_through(fake: FakeNotify, name: str) -> None:
    """The rendered post for each event reaches the fake and meets the Web API contract."""
    adapter = _slack(fake)
    try:
        adapter.notify(samples()[name])
    finally:
        adapter.close()

    calls = fake.calls(Sink.SLACK)
    assert calls
    assert [(c.problems, c.status) for c in calls] == [((), 200)] * len(calls)


def test_urgent_pushes_and_probes_reach_both_chat_sinks(fake: FakeNotify) -> None:
    """``notify_urgent`` and the read-only probes land where the vendor would get them."""
    telegram, slack = _telegram(fake), _slack(fake)
    try:
        telegram.notify_urgent(event="lab_check", correlation_id="01ABCDEF", message="hello")
        me = telegram.get_me()
        slack.notify_urgent(event="lab_check", correlation_id="01ABCDEF", message="hello")
        auth = slack.auth_test()
    finally:
        telegram.close()
        slack.close()

    assert (me["username"], auth["bot_id"]) == ("hermes_fake_bot", "B0FAKE")
    assert [(c.sink, c.path, c.problems) for c in fake.calls()] == [
        (Sink.TELEGRAM, "/telegram/bot<token>/sendMessage", ()),
        (Sink.TELEGRAM, "/telegram/bot<token>/getMe", ()),
        (Sink.SLACK, "/slack/api/chat.postMessage", ()),
        (Sink.SLACK, "/slack/api/auth.test", ()),
    ]


def test_a_report_with_a_file_runs_the_three_step_upload(fake: FakeNotify, tmp_path: Path) -> None:
    """The post lands, then the upload asks for a URL, sends the bytes there and completes."""
    brief = tmp_path / "brief.md"
    brief.write_text("# brief\n")
    adapter = _slack(fake)
    try:
        adapter.notify_report(
            event="lab.report",
            correlation_id="01ABCDEF",
            title="Lab",
            body="b",
            attachments=[brief],
        )
    finally:
        adapter.close()

    assert [(c.method, c.path.rsplit("/", 1)[-1]) for c in fake.calls(Sink.SLACK)] == [
        ("POST", "chat.postMessage"),
        ("POST", "files.getUploadURLExternal"),
        ("PUT", "F00000001"),
        ("POST", "files.completeUploadExternal"),
    ]


def test_a_request_the_vendor_would_refuse_is_refused_and_recorded(fake: FakeNotify) -> None:
    """An unknown field and an over-long text get Telegram's 400, with both problems named."""
    response = httpx.post(
        f"{fake.telegram_base}/bot{_TOKEN}/sendMessage",
        json={"chat_id": _CHAT, "text": "x" * 4097, "colour": "red"},
    )

    (call,) = fake.calls(Sink.TELEGRAM)
    assert (response.status_code, call.problems) == (
        400,
        ("unknown field 'colour'", "text is 4097 characters, over 4096"),
    )
    assert schema.telegram_response_problems(response.json()) == []


def test_slack_without_a_bot_token_is_not_authed(fake: FakeNotify) -> None:
    """A Web API call with no bearer token gets Slack's ``not_authed``."""
    response = httpx.post(f"{fake.slack_base}/api/chat.postMessage", json={"channel": "C1"})

    assert response.json() == {"ok": False, "error": "not_authed"}


def test_an_injected_command_is_served_until_its_offset_is_confirmed(fake: FakeNotify) -> None:
    """``getUpdates`` returns the command, and a poll past its id confirms it away."""
    update_id = fake.inject_command(chat_id=int(_CHAT), text="/status")
    url = f"{fake.telegram_base}/bot{_TOKEN}/getUpdates"

    first = httpx.post(url, data={"timeout": "0"}).json()["result"]
    again = httpx.post(url, params={"offset": update_id}).json()["result"]
    confirmed = httpx.post(url, params={"offset": update_id + 1}).json()["result"]

    assert [
        [(u["update_id"], u["message"]["text"]) for u in batch] for batch in (first, again)
    ] == [[(update_id, "/status")]] * 2
    assert confirmed == []


def test_a_rate_limit_is_met_once_then_the_retry_lands(fake: FakeNotify) -> None:
    """A scheduled 429 answers the first send; the adapter's retry gets through."""
    fake.fail_next(Sink.TELEGRAM, Outage.RATE_LIMITED)
    adapter = _telegram(fake)
    try:
        adapter.notify_urgent(event="lab_check", correlation_id="01ABCDEF", message="hello")
    finally:
        adapter.close()

    assert [c.status for c in fake.calls(Sink.TELEGRAM)] == [429, 200]


@pytest.mark.parametrize(("outage", "status"), [(Outage.SERVER_ERROR, 502), (Outage.TIMEOUT, 504)])
def test_a_server_error_or_timeout_fails_the_send(
    fake: FakeNotify, outage: Outage, status: int
) -> None:
    """A 5xx, or a request held past the client timeout, surfaces as the adapter's error."""
    fake.fail_next(Sink.TELEGRAM, outage)
    adapter = _telegram(fake, timeout=0.2)
    try:
        with pytest.raises(TelegramNotifierError):
            adapter.notify_urgent(event="lab_check", correlation_id="01ABCDEF", message="hello")
    finally:
        adapter.close()

    assert [c.status for c in fake.calls(Sink.TELEGRAM)] == [status]


def test_healthchecks_pings_are_recorded_by_signal(fake: FakeNotify) -> None:
    """Success, start and fail pings land on the check's URL with their suffix."""
    adapter = HealthchecksAdapter.create(HealthchecksAdapterConfig(ping_url=fake.ping_url(_CHECK)))
    try:
        adapter.ping_start()
        adapter.ping_success("bar handled")
        adapter.ping_fail("feed stale")
    finally:
        adapter.close()

    assert [(c.method, c.path, c.problems) for c in fake.calls(Sink.HEALTHCHECKS)] == [
        ("GET", f"/hc/ping/{_CHECK}/start", ()),
        ("POST", f"/hc/ping/{_CHECK}", ()),
        ("POST", f"/hc/ping/{_CHECK}/fail", ()),
    ]


def test_a_sentry_event_arrives_as_a_valid_envelope(fake: FakeNotify) -> None:
    """The SDK's own transport delivers an envelope the sink accepts."""
    client = sentry_sdk.Client(dsn=fake.sentry_dsn(), default_integrations=False)
    try:
        client.capture_event({"message": "lab check", "level": "error"})
        client.flush(timeout=5)
    finally:
        client.close()

    (call,) = fake.calls(Sink.SENTRY)
    assert (call.path, call.problems, call.status) == ("/sentry/api/1/envelope/", (), 200)


def test_the_control_api_drives_the_fake_from_another_process(fake: FakeNotify) -> None:
    """Inject, schedule, read and reset over HTTP, as a scenario run elsewhere would."""
    control = httpx.Client(base_url=f"{fake.url}/_fake", timeout=5.0)
    try:
        control.post("/telegram/commands", json={"chat_id": 7, "text": "/help"})
        control.post("/outages", json={"sink": "slack", "kind": "server_error"})
        polled = httpx.post(f"{fake.telegram_base}/bot{_TOKEN}/getUpdates").json()
        refused = httpx.post(
            f"{fake.slack_base}/api/auth.test",
            headers={"Authorization": f"Bearer {_SLACK_TOKEN}"},
        )
        recorded = control.get("/calls", params={"sink": "slack"}).json()
        control.post("/reset")
        after_reset = control.get("/calls").json()
    finally:
        control.close()

    assert polled["result"][0]["message"]["text"] == "/help"
    assert (refused.status_code, [c["status"] for c in recorded], after_reset) == (503, [503], [])


def test_no_recorded_path_or_body_holds_the_bot_token(fake: FakeNotify) -> None:
    """The token is in every Bot API URL, and never in what the fake keeps."""
    adapter = _telegram(fake)
    try:
        adapter.notify_urgent(event="lab_check", correlation_id="01ABCDEF", message="hello")
        adapter.get_me()
    finally:
        adapter.close()

    kept = json.dumps([c.as_json() for c in fake.calls()])
    assert _TOKEN not in kept


def test_every_answer_the_fake_gives_meets_the_vendor_contract(fake: FakeNotify) -> None:
    """Generated responses pass the same checks the live vendors' responses must pass."""
    fake.inject_command(chat_id=5, text="/status")
    auth = {"Authorization": f"Bearer {_SLACK_TOKEN}"}
    telegram = [
        httpx.post(f"{fake.telegram_base}/bot{_TOKEN}/{m}", json=b).json()
        for m, b in (
            ("getMe", {}),
            ("sendMessage", {"chat_id": 5, "text": "x"}),
            ("getUpdates", {}),
        )
    ]
    slack = [
        httpx.post(f"{fake.slack_base}/api/{m}", json=b, headers=auth).json()
        for m, b in (("auth.test", {}), ("chat.postMessage", {"channel": "C1", "text": "x"}))
    ]

    assert [schema.telegram_response_problems(b) for b in telegram] == [[], [], []]
    assert [schema.telegram_update_problems(u) for u in telegram[2]["result"]] == [[]]
    assert [schema.slack_response_problems(b) for b in slack] == [[], []]
