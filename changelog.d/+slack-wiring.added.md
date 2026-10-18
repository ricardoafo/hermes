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
