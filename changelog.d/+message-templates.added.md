- **Message templates + `hermes preview` CLI foundation**
  (`src/hermes/application/messages/telegram.py`,
  `src/hermes/application/messages/slack.py`,
  `src/hermes/application/messages/_escape.py`,
  `src/hermes/application/messages/__init__.py`,
  `src/hermes/entrypoints/preview.py`, `src/hermes/entrypoints/cli.py`,
  `src/hermes/domain/events/{strategy_paused,strategy_resumed,balance_low,trade_rejected,daily_trade_digest}.py`,
  `src/hermes/application/notification_router.py`,
  `docs/decisions/2026-08-23-slack-alongside-telegram.md`,
  `tests/fixtures/events/*.json`, `pyproject.toml`):
  standalone template + preview foundation for MVP1 integration.
  Five new domain events (all frozen dataclasses, `__event_schema_version__ = 1`)
  cover the extended MVP1 signal surface: `StrategyPaused`,
  `StrategyResumed`, `BalanceLow`, `TradeRejected`, `DailyTradeDigest`
  (with nested `TradeSummary`). `BalanceLow.__post_init__` enforces the
  tri-currency invariant (`asset == current.currency == threshold.currency`),
  failing fast at construction rather than silently corrupting downstream
  arithmetic. The application-layer `notification_router.EventClass`
  gains `STRATEGY_PAUSED`, `STRATEGY_RESUMED`, `BALANCE_LOW`,
  `TRADE_REJECTED_ESCALATED`, `DAILY_TRADE_DIGEST`, `POSITION_LARGE`,
  `ANOMALY_DETECTED` members, each with a routing-table row cited in the
  2026-08-23 decisions-journal entry (extended table); a new
  introspection helper `channels_for(EventClass) -> (bool, bool)` exposes
  the routing decision without wiring adapters. A new `application/messages/`
  package renders each event to either a 3-line MarkdownV2 Telegram push
  or a Block Kit `SlackMessage` NamedTuple (`text` + `blocks` +
  `attachments`); attachments are populated with stdlib-generated CSV
  bytes (`DailyTradeDigest` — no third-party PDF dep). The dispatch
  registry raises `ValueError` for an unregistered event type so
  misclassification surfaces immediately. A new `hermes` CLI entry point
  (`[project.scripts]` in `pyproject.toml`, `entrypoints/cli.py` +
  `entrypoints/preview.py`) exposes `hermes preview list`,
  `hermes preview <event>`, `hermes preview <event> --fixture <path>`,
  and `hermes preview <event> --send` (which refuses when
  `HERMES_ENV=prod`); `serve` / `health` / `simulate` are stub
  subcommands that exit 2 pending the future integration PRs. Committed
  JSON fixtures under `tests/fixtures/events/` sample every event.
  Templates stay standalone in this PR — the Telegram + Slack adapters
  consume them in follow-up PRs B + C. Zero new runtime deps
  (argparse + stdlib `csv` + `tempfile`). 639 unit tests pass;
  `application/messages/` at 100% coverage, `entrypoints/` at 93.94%
  (thresholds 90% / 70%).
