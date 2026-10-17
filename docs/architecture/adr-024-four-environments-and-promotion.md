# ADR-024: Four environments — dev and QA on demand, a staging lab on server-01, prod alone on hermes-01

**Date**: 2026-10-17
**Status**: Proposed — Accepted when the Code Owner merges it
**Deciders**: Ricardo Afonso (product owner), Claude (proposer)
**Supersedes**: none.
**Amends**: [ADR-018](adr-018-ci-cd-infrastructure-and-resource-budget.md), adding the runner trust split and the
fresh-runner-per-job ask in §4 and §8; [ADR-022](adr-022-release-versions-and-deploy-cadence.md), putting QA and the
staging lab ahead of every prod deploy in §2. Neither ADR is edited.

---

## Context

Hermes has two places that run a release today. Prod runs on hermes-01. Staging runs beside it on the same box under
its own user (`docs/guides/staging-on-hermes-01.md`, PR #555). Staging shares prod's CPU, memory and Binance rate
limit, and on the live market it has produced no signals, so one clean bar proves the service runs, not that it trades.
The fake market (issue #688) and the scenario suite (issue #689) prove engine paths, but only inside pytest. An
installed release cannot point at them, and Telegram and Slack have hard-coded API bases.

The Code Owner decided on 2026-10-04 that MVP2 gets its own lower environment. On 2026-10-17 server-01 came online.
The same day the Code Owner asked for a place to "simulate properly all the functionalities and test this thing
entirely on every real life scenario". After review he approved a four-environment proposal at 22:54 UTC. This ADR
records that proposal. The build is tracked in issue #968.

## Decision

### 1. Four environments

| Environment | `HERMES_ENV` | Where | Lifetime | Goal |
| --- | --- | --- | --- | --- |
| Dev | `dev` | server-01's self-hosted CI runners | One fresh runner per job | Every PR proves its code with in-process fakes |
| QA | `qa` | server-01, inside CI jobs | Raised per run, torn down after | The installed wheel survives every scenario |
| Staging (the lab) | `staging` | server-01 | Always on | Backtests and the shadow line on real data |
| Prod | `prod` | hermes-01 | Always on | The champion, and later real money |

- `qa` is a new tier. Today's testnet-only `sandbox` tier folds into it (issue #970).
- Laptops run tests only by hand, before a push. No CI job runs on a laptop or on a GitHub-hosted runner (issue #1069).
- Staging moves off hermes-01. Its unit there is retired once the lab runs (Code Owner, 2026-10-17 13:18 UTC).

### 2. Promotion and gates

A release is one wheel, built once by the release-candidate gate (issue #670) and checked by sha256 everywhere after.
It moves dev → QA → staging → prod, and a failure at any gate stops it there.

| Gate | Passes when |
| --- | --- |
| Dev → QA | CI is green on the merged tree, the AI review approves, the Code Owner merges, and the wheel tests clean |
| QA → staging | Every scenario for the release's change type passes. No expected failures: a defect's scenario ships with its fix |
| Staging → prod | The change type's staging check passes (§3) |
| Prod | Install, smoke tests, bake on the next bar, automatic rollback on failure (issue #672) |

When server-01 is down, releases wait. Nothing skips the lab on its way to prod. The only bypass is the emergency
exception in `docs/guides/dev-workflow.md`.

### 3. Change types

One script and one path list classify a diff (issue #1077). CI, CD and the Train replay all read it. A file the list
does not name counts as engine, so a gap fails safe. A diff that mixes types takes the strictest one. A release's type
is the strictest of the PRs since the last prod deploy.

| Type | Files | Dev | QA | Staging |
| --- | --- | --- | --- | --- |
| Docs | `docs/`, `*.md` | Lint | Skipped, nothing deploys | Skipped |
| Surface | Console screens, CLI output, report rendering | Unit tier, console snapshots | Install, smoke, console and CLI scenarios | Health gate, no bar wait; shadow clocks keep counting |
| Runtime | `serve`, data and notifier adapters, deploy scripts, migrations, config other than strategies | Unit tier, e2e | Every scenario, operations included | One clean bar on every stream |
| Engine | The `ENGINE_PATHS` of ADR-025 | All of the above, plus the Train replay (ADR-025) | Every scenario | One clean bar; a changed decision waits for the Code Owner |

CI never starts a backtest or a shadow. A backtest starts when the Code Owner signs a pre-registration, and a shadow
starts when shadow Ready holds (issue #967).

### 4. Isolation and secrets

| Rule | Dev | QA | Staging | Prod |
| --- | --- | --- | --- | --- |
| Mainnet | Refused | Refused | Refused | Behind `HERMES_ALLOW_MAINNET` and typed confirmation (I-08) |
| Exchange keys | None | None | None | Read-only now; trade-capable only after certification |
| Notifier, healthchecks, Sentry | None | Recording fakes | The lab's own | Prod's own |
| Outside network | None | Loopback only | Public market data only | Public market data, exchanges, notifiers |
| Historical data | Synthetic, recorded fixtures | Synthetic, Train window only | Train and Validation under ADR-023 | None |

- The config loader enforces each column, and the deploy preflight checks it again (issue #970). Staging refuses
  prod's values, and prod refuses a loopback endpoint.
- QA and staging share server-01 but never a user, ledger, env file or data directory, and neither reaches hermes-01.
- **Runner trust split.** A runner that holds deploy secrets (the ops runner, and the staging and prod deploy jobs)
  never runs PR, QA or Dependabot code. PR and QA runners hold no secrets.
- **GitHub environments.** Each one is named after its `HERMES_ENV`: `staging` and `prod`, both main-only. QA has
  none because it holds no secrets. Secret and variable names are the same in every environment, and only the values
  differ (for example `HERMES_TELEGRAM__BOT_TOKEN`). The backtest environments of ADR-023 keep their names.
- **Workflow least privilege.** On 2026-10-17 every third-party action was pinned to a full commit SHA, and every
  workflow set `contents: read` at the top, except the reusable `_build-wheel.yml`. `actions/checkout` still keeps the
  token in `.git/config` in 7 workflows. Issue #597 fixes that first.

### 5. Dev and QA are ephemeral

- Dev: each CI job gets a fresh runner that is wiped after the job, with runner logs kept off the runner first. That is
  a runner change, so it is part of the ask to the infra project (§9).
- QA: a CI job raises the environment on the self-hosted runners. It contains the installed wheel, the fake market
  service (issue #971), the recording sinks (issue #972) and a throwaway ledger. The same job tears it down, pass or
  fail. It runs for every release candidate, and on demand for a PR labelled `qa`.
- Every QA resource records its creation time and owning run. A sweep removes any QA environment older than 6 hours,
  so a job that died cannot leave one behind (issue #973).
- QA's Train-window episodes are a small pinned set, checked against their manifest hashes.

### 6. The staging lab

- It runs backtests under ADR-023 and the shadow line: up to 6 shadows, at most one per idea family (issue #967). It
  also runs a paper twin of the champion on the same box, data and release (issue #1076).
- Each strategy has its own paper ledger and capital. Live public data comes from server-01's own IP.
- A daily comparison of the champion twin's decisions with prod's reports any difference.
- Notices go to the lab's own Telegram chat. The lab pings its own healthchecks check (issue #1079).

### 7. Outages and restart

- Every service is a systemd unit enabled at boot with `Restart=on-failure`. It starts only after the system clock
  has synced, and it resumes from its ledger, never from memory.
- **No back-dated fills** (Code Owner, 2026-10-17 22:54 UTC). Bars that closed while a machine was down are recorded
  as missed. On return, open positions are checked against their exits at once, and an exit that triggered fills at
  the current price. New entries wait for the next fresh bar. Paper shadows follow the same rule as real money
  (issue #1078).
- The first message after a restart reports the downtime, the missed bars per strategy, and the exits checked.
- Missed bars count against ADR-021's 1% budget. Over 90 days that is about 5 four-hour bars per stream, roughly 20
  hours. The Lab screen shows each shadow's remaining budget.
- A Validation run that is interrupted counts as a touch (Code Owner, 2026-10-17 22:54 UTC).
- Before real money, every position carries a stop-loss held by the exchange, so a hermes-01 outage never leaves it
  unprotected (issue #1080).

### 8. Fakes stand in for outside services

QA uses fakes for Binance, Frankfurter, Telegram, Slack, healthchecks and Sentry, because those services cannot run on
server-01. Dev/prod-parity guidance warns against stand-ins. The Code Owner kept this trade-off on 2026-10-17, with
two guards. Each fake shares one schema with recorded real payloads (issue #688), so a change in the real format fails
both sides together. The staging lab talks to the real public APIs before prod does. Python and SQLite versions are
identical everywhere (issue #674).

### 9. The ask to the infra project

Hermes is a tenant on server-01. How the box provides the following belongs to the infra project, and the Code Owner
runs every machine step.

- One fresh self-hosted runner per CI job, with runner logs kept outside the runner.
- A long-lived home for the staging lab: its user or namespace, code root, state directory and env file.
- Read-only access to `/srv/hermes-data` for the lab, as ADR-023 and ADR-025 already set.
- Outbound access for the lab to Binance and Frankfurter public data and to its notifier and healthchecks hosts. No
  inbound ports.
- "Power on after power loss" in the BIOS, and a clean shutdown when the UPS battery runs low.
- Capacity, estimated and not yet measured: about 2 cores and 4 GB for a QA run, and about 4 GB for the lab's shadows,
  beside the CI pool and the backtest runner.

## Security (ADR-019)

- **Fewer places hold secrets.** Dev and QA hold none, staging holds only the lab's, and only prod can ever hold
  trade-capable keys.
- **PR code and deploy secrets never meet.** The runner trust split (§4) and fresh runners per job (§5) remove the
  persistent-runner risk that GitHub's hardening guide names.
- **A wrong endpoint fails closed.** Each tier refuses the other tiers' endpoints and values at load and at deploy.
- **Signed build provenance is out of reach.** GitHub artifact attestations need GitHub Enterprise Cloud on a private
  repository. The sha256 checks and the per-deploy SBOM (issue #673) stay.

## Why

- QA proves the installed release in every scenario before any real data sees it. Its fakes need no secrets.
- The lab runs only releases that passed QA, so a bad build cannot break a 90-day shadow count. It also takes staging
  off prod's CPU and rate limit.
- Real money stays alone on a datacenter host, away from a home power or internet cut.
- Change types keep console and feature PRs fast without risking a shadow.

Alternatives considered:

- **One lower environment for both fakes and live data** (this ADR's first draft). Rejected: a bad build would land
  where the shadows run.
- **Three environments, with QA folded into dev.** Rejected: the installed-release runs need a place of their own
  (Code Owner, 22:32 UTC).
- **The shadow line on hermes-01.** Rejected: it needs a bigger VPS and shares prod's rate limit.
- **A separate host for real money, with hermes-01 as the lab.** Rejected for now: no gain over hermes-01 alone for
  prod, and a new cost.
- **A persistent QA environment.** Rejected: state leaks between runs, and it needs machine setup that ephemeral QA
  does not.

## Consequences

### Positive

- Every release is proven against every real-life scenario before prod, in the form prod installs.
- hermes-01 runs prod alone.
- Docs and surface changes reach prod without waiting on the lab's bar.

### Negative

- More moving parts: four environments, a fake market service, recording sinks and a sweep.
- A server-01 outage pauses promotions and the lab's shadow days.
- The Code Owner runs one-time steps on server-01 and hermes-01, and a few in GitHub settings.

### Neutral

- No strategy, sizing, fee, metric or kill rule changes. The no-back-dated-fills rule decides only how an outage is
  handled.

## What would change this decision

- Real money: the exchange-held stop (issue #1080) and certification come first, and prod's keys stay on hermes-01.
- A second host for the lab: §9 moves with it. The environments and gates stay.
- A public repository: self-hosted runners would no longer be acceptable for PR jobs.

## Cross-references

- [ADR-018](adr-018-ci-cd-infrastructure-and-resource-budget.md) — runners and the CI budget
- [ADR-019](adr-019-security-baseline-and-threat-model.md) — security baseline
- [ADR-021](adr-021-strategy-lifecycle-ready-and-done.md) — shadow Ready, Done and the missed-bar budget
- [ADR-022](adr-022-release-versions-and-deploy-cadence.md) — release versions and auto-deploy
- [ADR-023](adr-023-backtest-runs-on-server-01.md) — backtests on server-01
- [ADR-025](adr-025-train-decision-replay-on-engine-prs.md) — the Train replay and `ENGINE_PATHS`
- Issues: #968 (epic), #969 (this ADR), #970 to #975, #1069, #1075 to #1081, #597, #671, #670, #672, #673, #674
- Sources checked on 2026-10-17: [12-factor: build, release, run](https://12factor.net/build-release-run),
  [12-factor: dev/prod parity](https://12factor.net/dev-prod-parity),
  [Deployment pipeline](https://martinfowler.com/bliki/DeploymentPipeline.html),
  [GitHub: autoscaling with self-hosted runners](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/autoscaling-with-self-hosted-runners),
  [GitHub: security hardening for Actions](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions),
  [attest-build-provenance](https://github.com/actions/attest-build-provenance)
