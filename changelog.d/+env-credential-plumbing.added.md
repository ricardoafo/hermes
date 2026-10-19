- **Credential + env-flag plumbing foundation** (`src/hermes/infrastructure/config/root.py`,
  `src/hermes/infrastructure/config/loader.py`, `src/hermes/infrastructure/env_gate.py`,
  `.env.local.example`, `deploy/systemd/hermes.service`,
  `deploy/systemd/hermes.env.example`, `docs/guides/local-setup.md`,
  `docs/decisions/2026-08-23-credential-storage-layered-by-env.md`,
  `pyproject.toml`): one-off MVP1 setup so per-integration PRs only add one
  env var each and never touch plumbing. `HermesConfig` gains root-level
  `env: Literal["dev", "sandbox", "prod"]` (from `HERMES_ENV`, defaults to
  `dev`) and `allow_mainnet: bool` (from `HERMES_ALLOW_MAINNET`, defaults to
  `False`); a `model_validator(mode="after")` raises at load time when
  `allow_mainnet=True` outside `prod` — belt-and-suspenders so misconfigs
  surface at process start, not first trade. `load_config` now calls
  `python-dotenv`'s `load_dotenv(".env.local", override=False)` before
  reading env, so local dev picks up secrets from a gitignored file while a
  real shell env var (CI / systemd) always wins. New `EnvGate` helper
  (`env_gate.require_env(current, allowed, component)`) raises
  `EnvGateError` (extends `InfraError`) when an adapter is wired in a
  forbidden tier; per-adapter mainnet gates one-line this at construction.
  `.env.local.example` (committed) documents every current + planned env
  var (Telegram, Slack, Binance, Kraken, Frankfurter, Healthchecks, Sentry,
  LLM) with placeholder values only. `deploy/systemd/hermes.service` +
  `deploy/systemd/hermes.env.example` template the prod counterpart
  (`EnvironmentFile=/etc/hermes.env`, root:root 0600). Docs cover the
  three-tier storage story (dotenv → GH Secrets → systemd EnvironmentFile)
  and the local-setup flow. `python-dotenv>=1,<2` added as a runtime dep.
