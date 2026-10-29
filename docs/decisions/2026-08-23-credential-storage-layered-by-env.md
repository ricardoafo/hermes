# Credential storage layered by env — dotenv / GH Secrets / systemd EnvironmentFile

**Date**: 2026-08-23
**Author**: Maintainer
**Related**: ADR-003 §04 (secrets via pydantic-settings env-var loading),
`docs/decisions/2026-08-23-slack-alongside-telegram.md` (env var names for
notification channels), MVP1 status (#141)

## Decision

Credentials for hermes live in ONE of three places, chosen by runtime
tier:

| Tier | Store                                                                    | Ownership               |
| ---- | ------------------------------------------------------------------------ | ----------------------- |
| dev  | `.env.local` at repo root (gitignored)                                   | developer clone-local   |
| CI   | GitHub Actions Environment secrets                                       | repo admin (`prod` env) |
| prod | `/etc/hermes.env` (root:root 0600), loaded by systemd `EnvironmentFile=` | VPS admin               |

All three surfaces expose the same env-var names — the code reads env,
never a specific file — so there is exactly one code path for
credential loading regardless of tier. `python-dotenv` handles the dev
tier at process start via `load_dotenv(".env.local")` inside
`hermes.infrastructure.config.load_config`. Real shell env vars always
win over `.env.local` (`override=False`) so CI and prod cannot be
silently shadowed by a developer's local file.

Two root-level env vars gate what adapters may wire:

- `HERMES_ENV` — one of `dev` / `sandbox` / `prod`. Adapters that touch
  mainnet money reject dev + sandbox at construction via
  `hermes.infrastructure.env_gate.require_env`.
- `HERMES_ALLOW_MAINNET` — belt-and-suspenders boolean. Setting it
  outside `prod` raises a `ValidationError` at config-load time (the
  composite `HermesConfig` model validator refuses to instantiate).

## Context

MVP1 needs credentials for 4+ live integrations (Telegram, Slack,
Binance, Healthchecks.io) and 3+ planned (Kraken, Frankfurter, Sentry,
LLM supervisor). Each PR that wires a new adapter should add ONE new
env var — not touch loader plumbing, not touch systemd, not touch the
dotenv template. Without a common storage story, every integration PR
re-invents "where does the secret live?" and drifts.

Constraints:

- ADR-003 §04 fixes pydantic-settings env-var loading as the credential
  input path. No secrets in committed YAML (AGENTS.md prohibition #6).
- The VPS (per ADR-003 §09) runs `hermes` as a non-root system user
  under systemd. Reading a dotenv file inside `/opt/hermes` would put
  the secrets in a group- or world-readable file at some point in the
  install chain. systemd's `EnvironmentFile=` reads root-owned 0600
  files and expands the env into the child process — no file-read
  syscall by the `hermes` user is ever necessary.
- Dev needs FAST iteration — `.env.local` lets a developer flip
  `HERMES_ENV=sandbox` without editing shell profiles or systemd
  units.
- CI must not depend on a checked-in file (secret exposure) nor on a
  local dotenv (won't exist on the runner). GH Actions Environment
  secrets fit exactly — scoped by branch (`main` only) so fork PRs
  cannot resolve them.

## Alternatives considered

**All-in-one dotenv (`.env` at repo root, git-crypted):**

- Single storage path, one code path
- Requires every developer + the CI runner + the VPS to hold the
  git-crypt key. Key rotation touches every clone. Any leak of the
  key exposes ALL past commits' encrypted secrets
- Rejected — three surfaces × three security profiles is the actual
  shape; forcing them into one file trades operational fit for a
  false-simplicity gain

**systemd credentials (`LoadCredential=` + `systemd-creds encrypt`):**

- TPM-backed encryption on the VPS, no plaintext at rest
- Requires `systemd-creds` on the deploy path, TPM key backup
  discipline, and per-secret `LoadCredential=` lines — every new
  integration edits the systemd unit
- Excellent for a mature ops posture; over-invests for Phase 0/1 where
  the bottleneck is INTEGRATION VELOCITY, not credential leak surface
- Deferred — bootstrap-vps.sh already initialises the
  `systemd-creds` host key, so this is a future upgrade path. Adding
  encrypted credentials later is a swap of `/etc/hermes.env` reads
  for `${CREDENTIALS_DIRECTORY}` reads, not a rewrite

**Vault / cloud KMS (HashiCorp / AWS SSM / GCP Secret Manager):**

- Rotate-anywhere, audit trail, IAM integration
- Adds an external dependency + a network round-trip in the startup
  path. For a single-VPS deploy that talks to two exchanges, the
  operational overhead outweighs the security gain
- Rejected — revisit if hermes ever multi-tenants or handles third-
  party funds

**Config file per env (`config/env/dev.env`, `config/env/prod.env`):**

- Files committed to git, prod file kept out of the wheel
- Any secret in a committed file is one careless copy-paste from
  a public leak. Doesn't solve the problem
- Rejected

## Rationale

The three tiers have different threat models — mixing their credential
surfaces would either weaken prod (dotenv on the VPS) or over-secure
dev (Vault for a developer's local clone). Layering them by tier lets
each surface use the store that matches its threat profile while
keeping a single code path (read `os.environ`) that adapters depend on.

`python-dotenv` is chosen over rolling our own `.env` parser because
(a) it handles the shell-quote / multi-line edge cases correctly and
(b) it is a single ~500-line pure-Python dep with no transitive
runtime deps. `override=False` (defaults) is the important flag — a
real env var must always win over the file so CI and prod are never
silently shadowed by a developer's dotenv.

Root-level env vars (`HERMES_ENV`, `HERMES_ALLOW_MAINNET`) live
OUTSIDE the nested `HERMES_<SECTION>__<FIELD>` namespace because they
gate runtime-tier POLICY, not a specific YAML section. The composite
`HermesConfig` model validator enforces the invariant
"`allow_mainnet=True` requires `env='prod'`" at load time so misconfig
surfaces at process start, not at first trade.

`env_gate.require_env` is a two-argument pure function so adapters can
enforce their tier restriction in one line at construction time. Two
axes of enforcement (config-load validator for the root flag, env_gate
for per-adapter tier) implement the belt-and-suspenders discipline —
either axis alone would leave a plausible drift path open.

## Consequences

**What this locks in**:

- Adapters read env vars via `os.environ.get("HERMES_<X>__<Y>")` — one
  code path across tiers. Each new integration adds ONE env var to
  `.env.local.example` + `deploy/systemd/hermes.env.example` and one
  `env_gate.require_env` call if it's tier-scoped
- `.env.local` MUST stay gitignored. `.env.local.example` MUST stay
  committed and NEVER contain a real secret
- Prod deploys use `/etc/hermes.env` root:root 0600 loaded by the
  `deploy/systemd/hermes.service` unit's `EnvironmentFile=` directive
- CI reads secrets from the `prod` GitHub Environment (branch-
  restricted to `main`)

**What this forecloses**:

- Cannot ship a "dev with real mainnet keys" mode. Setting
  `HERMES_ALLOW_MAINNET=yes` outside `prod` raises at load time.
  This is a feature — no operator can accidentally point dev at
  production without changing HERMES_ENV first
- Cannot skip the `.env.local.example` update when adding a new
  integration — the pre-flight lint (future work) will diff the
  template against grepped adapter env-var names

**What we'll revisit**:

- If a leak proves the 0600 `/etc/hermes.env` model insufficient,
  upgrade to `systemd-creds`-encrypted credentials (the VPS
  bootstrap already provisions the TPM-backed host key). Migration is
  a swap of the file read for `${CREDENTIALS_DIRECTORY}` — no adapter
  code changes required
- If a third channel emerges (Vault for team-shared prod secrets), add
  a fourth tier. The tier abstraction (read `os.environ`) does not
  care where the values originated

## ADR-003 status

Not modified (AGENTS.md prohibition #10 — ADRs are immutable once
Accepted). This decision-journal entry OPERATIONALISES ADR-003 §04's
"secrets via pydantic-settings env-var loading" statement — the ADR
picked the interface, this entry picks the three storage surfaces
that feed it. The architectural intent (env-var-based credential
input, no YAML secrets) is unchanged; this entry only names the
files that populate the env at each tier.
