- **Sentry error-reporting wiring** (`src/hermes/infrastructure/sentry_integration.py`,
  `src/hermes/entrypoints/cli.py`, `src/hermes/entrypoints/_health.py`,
  `src/hermes/infrastructure/config/models.py`, `config/sentry.yaml`): initializes the
  Sentry SDK at CLI startup from `HERMES_SENTRY__DSN` + `HERMES_SENTRY__ENVIRONMENT`.
  Vanilla-Python posture — no framework integrations, no PII, no traces (free-tier posture).
  `hermes health sentry` validates the DSN URL parses via `sentry_sdk.utils.Dsn` — read-only,
  zero wire cost, does NOT eat the operator's 5,000-event/month quota. Registered alongside
  Telegram / Slack / Healthchecks in the shared health-check registry; `hermes health`
  (no arg) walks all four. Empty DSN → integration silently disabled (matches Healthchecks
  empty-ping-url pattern). Init and capture never raise — SDK failures are logged via
  structlog and swallowed so error reporting can never crash the CLI. Closes the MVP1
  integration wiring layer.
