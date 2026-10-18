- **`hermes health telegram` subcommand + contract-test tier**
  (`src/hermes/entrypoints/cli.py`, `src/hermes/entrypoints/_health.py`,
  `tests/contract/test_telegram_getme.py`, `tests/contract/__init__.py`,
  `docs/decisions/2026-08-23-contract-test-tier.md`,
  `docs/guides/local-setup.md`, `pyproject.toml`):
  first end-to-end wire probe for MVP1 integrations. `hermes health
  telegram` calls the Telegram Bot API `getMe` endpoint (read-only,
  never `sendMessage`) and prints a status table with `OK/FAIL`,
  latency, bot username, and a UTC-aware `last_check` timestamp. Exit
  codes distinguish OK (0), adapter-down (1), and not-configured (3)
  so wrapper scripts can react differently — a fresh clone missing
  `.env.local` should not page an operator. A tiny registry scaffold
  in `_health.py` (`registered_integrations`, `get_check`) anticipates
  the PR E consolidation where `hermes health` (no arg) walks the
  entire registry. `_health.py` lives under `entrypoints/` (the wiring
  layer) so it may compose adapters + config; import-linter still
  forbids adapters from importing entrypoints, so the arrow points
  the right way. A new `contract` pytest marker + `tests/contract/`
  directory establish a third test tier alongside unit + integration:
  contract tests hit real third-party sandbox endpoints, skip
  gracefully when credentials are absent, and are excluded from the
  default `pytest` invocation via `addopts = "... -m 'not contract'"`
  so the per-PR unit path stays fast. The first contract test
  (`test_telegram_getme.py`) is the pattern PRs D (Slack `auth.test`),
  G (Healthchecks `/ping`), and future adapters will follow. See
  `docs/decisions/2026-08-23-contract-test-tier.md` for the tier
  policy + CI-run cadence.
