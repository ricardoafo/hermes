- **Slack adapter now consumes application-layer templates**
  (`src/hermes/adapters/notifier/slack_adapter.py`,
  `tests/unit/adapters/notifier/test_slack_adapter.py`):
  the adapter's new `notify(event: DomainEvent)` method delegates
  Block Kit rendering to `slack_template()` from
  `application/messages/slack.py` (PR #145) — no more inline Block Kit
  formatting in the adapter. `notify()` posts the rendered blocks via
  `chat.postMessage` and uploads any `SlackMessage.attachments` as
  THREADED REPLIES (the completed file lands under the parent post's
  `thread_ts` so the channel timeline stays a single card per event —
  especially useful for `DailyTradeDigest`, which ships a CSV). Also
  adds an `auth_test()` method for the `hermes health slack` probe.
  The old `notify_urgent(event, correlation_id, message)`,
  `notify_report(...)`, and `post_message(...)` signatures are kept
  as thin compatibility wrappers with deprecation docstrings ("removed
  after router migration lands"). Every existing error-handling path
  (`ok=false` HTTP-200 footgun, 401, 429, transport failures, malformed
  `getUploadURLExternal` bodies, signed-PUT errors) is preserved
  verbatim. File upload uses the Slack v2 external-upload flow
  (`files.getUploadURLExternal` → PUT to signed URL →
  `files.completeUploadExternal`); the legacy `files.upload` endpoint
  was retired by Slack in Nov 2025 and is no longer callable.
