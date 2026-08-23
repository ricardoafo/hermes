### Added

- **`hermes health slack` subcommand + Slack contract-test tier**
  (`src/hermes/entrypoints/cli.py`, `src/hermes/entrypoints/_health.py`,
  `tests/contract/__init__.py`, `tests/contract/test_slack_auth_test.py`,
  `docs/guides/local-setup.md`, `pyproject.toml`):
  end-to-end wire probe for the Slack integration. `hermes health slack`
  calls the Slack Web API `auth.test` endpoint (read-only, never
  `chat.postMessage`) and prints a status table with `OK/FAIL`, latency,
  workspace name, bot user, and a UTC-aware `last_check` timestamp.
  Exit codes distinguish OK (0), adapter-down (1), and not-configured
  (3) so wrapper scripts can react differently — a fresh clone missing
  `.env.local` should not page an operator. A tiny registry scaffold in
  `_health.py` (`registered_integrations`, `get_check`, `check_slack`)
  anticipates the PR E consolidation where `hermes health` (no arg)
  walks the entire registry, and converges with PR B (Telegram wiring,
  #150) which adds `check_telegram` to the same registry — same shape,
  no dispatcher rewrite. A new `contract` pytest marker + `tests/contract/`
  directory establish a third test tier alongside unit + integration:
  contract tests hit real third-party sandbox endpoints (here: real
  `slack.com/api/auth.test`), skip gracefully when credentials are
  absent, and are excluded from the default `pytest` invocation via
  `addopts = "... -m 'not contract'"` so the per-PR unit path stays
  fast. The first Slack contract test (`test_slack_auth_test.py`)
  mirrors PR B's Telegram pattern and asserts the two response fields
  the health probe reads (`team`, `user`) so upstream shape drift is
  caught before it silently breaks the wire path.

### Changed

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
