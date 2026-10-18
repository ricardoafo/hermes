- **Telegram adapter now consumes application-layer templates**
  (`src/hermes/adapters/notifier/telegram_adapter.py`): the adapter's
  new `notify(event: DomainEvent)` method delegates rendering to
  `telegram_template()` from `application/messages/telegram.py` (PR
  #145) — no more inline MarkdownV2 escaping in the adapter. The old
  `notify_urgent(event, correlation_id, message)` signature is kept
  as a thin compatibility wrapper that constructs a synthetic
  `AnomalyDetected` event, so existing PR #92 callers keep working;
  the wrapper is documented as deprecated and will be removed once
  the notification router migration lands. `notify_report` (no-op
  with WARNING log) and every existing error-handling path (401/403/
  429/network/`ok=false`) are preserved. Adds a `get_me()` method for
  the `hermes health telegram` probe. Removes the now-unused
  `_escape_markdown_v2` helper + `_MARKDOWN_V2_RESERVED` constant
  from the adapter — escaping is fully centralised in
  `application/messages/_escape.py`.
