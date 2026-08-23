### Added

- **Healthchecks.io adapter + consolidated `hermes health` registry walk**
  (`src/hermes/adapters/healthchecks/`, `src/hermes/entrypoints/_health.py`,
  `src/hermes/entrypoints/cli.py`, `src/hermes/infrastructure/config/models.py`,
  `src/hermes/infrastructure/config/root.py`,
  `src/hermes/infrastructure/config/loader.py`, `src/hermes/domain/errors.py`,
  `config/healthchecks.yaml`, `tests/unit/adapters/healthchecks/`,
  `tests/contract/test_healthchecks_ping.py`, `docs/guides/local-setup.md`,
  `scripts/dev/coverage-check.py`):
  end-to-end wire probe for the Healthchecks.io dead-man's-switch monitor plus
  a consolidated `hermes health` (no arg) invocation that walks the entire
  integration registry in one pass. The new `HealthchecksAdapter` owns three
  ping surfaces (`ping_success`, `ping_fail`, `ping_start`) implemented over
  a raw `httpx.Client` with an owned 30 req/min token-bucket limiter —
  same pattern as Telegram / Slack / Frankfurter, no SDK dependency. Aggressive
  5s timeout by default so a slow ping never blocks the trading path. The
  health probe uses `ping_start` (idempotent + informational, does NOT fire
  the dead-man's-switch alert) — using `ping_fail` from a health path would
  page the operator on every `hermes health` tick. New `HealthchecksError`
  (`InfraError` subclass) wraps transport failures and HTTP non-2xx into one
  exception surface. `hermes health` (no arg) now calls a new `run_all(env)`
  helper that returns `list[HealthCheckResult]` — pure function, testable
  without stubbing the CLI — and renders it via `render_table_multi` as one
  aligned multi-row table with columns `integration / status / latency /
  detail / last_check`. Aggregated exit code: 0 all-OK, 1 any-FAIL, 3
  any-NOT_CONFIGURED-and-no-FAIL (partial config is a fresh-clone state,
  not a system-down state). The per-integration `hermes health <integration>`
  form stays unchanged. `HealthchecksConfig` in the config loader (with
  `enabled` property) plus a `config/healthchecks.yaml` stub and the
  existing `HERMES_HEALTHCHECKS__PING_URL` env var (already documented in
  `.env.local.example` line 59-60) complete the wiring. New per-package
  coverage threshold at 85% for `src/hermes/adapters/healthchecks` (matches
  the existing notifier + fx thresholds). Contract tier gets a third test
  (`test_healthchecks_ping.py`) mirroring PRs B/D — fires `ping_start`
  against the real ping URL, skips when the env var is empty. Related:
  MVP1 status (#141), PR #150 (Telegram wiring — registry pattern),
  PR #151 (Slack wiring — mirror), PR #143 (env plumbing).
