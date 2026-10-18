# ADR-024: Four environments, with hermes-01 as staging until a champion earns real money

**Date**: 2026-10-17
**Status**: Proposed — Accepted when the Code Owner merges it
**Deciders**: Ricardo Afonso (product owner), Claude (proposer)
**Supersedes**: none.
**Amends**: [ADR-018](adr-018-ci-cd-infrastructure-and-resource-budget.md), adding the runner trust split and the
fresh-runner-per-job ask in §4 and §9; [ADR-022](adr-022-release-versions-and-deploy-cadence.md), putting QA ahead of
staging, and staging ahead of prod once prod exists, in §2. Neither ADR is edited.

---

## Context

Hermes has two places that run a release today. hermes-01 runs `hermes serve` as the prod tier, but it trades nothing:
it runs paper shadows. Beside it, a second unit runs staging under its own user (`docs/guides/environments.md`,
PR #555). Staging shares that box's CPU, memory and Binance rate limit, and on the live market it has produced no
signals, so one clean bar proves the service runs, not that it trades. The fake market (issue #688) and the scenario
suite (issue #689) prove engine paths, but only inside pytest. An installed release cannot point at them, and Telegram
and Slack have hard-coded API bases.

The Code Owner decided on 2026-10-04 that MVP2 gets its own lower environment. On 2026-10-17 server-01 came online.
The same day the Code Owner asked for a place to "simulate properly all the functionalities and test this thing
entirely on every real life scenario". After review he approved a four-environment proposal at 22:54 UTC. At 23:08 UTC
he placed staging on hermes-01 until a champion exists, because nothing runs on prod before then. This ADR records
both. The build is tracked in issue #968.

## Decision

### 1. Four environments, in two phases

| Environment | `HERMES_ENV` | Phase 1: until the cutover | Phase 2: after the cutover | Lifetime | Goal |
| --- | --- | --- | --- | --- | --- |
| Dev | `dev` | server-01's self-hosted CI runners | Same | One fresh runner per job | Every PR proves its code with in-process fakes |
| QA | `qa` | server-01, inside CI jobs | Same | Raised per run, torn down after | The installed wheel survives every scenario |
| Staging (the lab) | `staging` | hermes-01 | server-01 | Always on | The shadow line on live data, and later the champion twin |
| Prod | `prod` | None | hermes-01 | Always on | The champion with real money |

- **Phase 1 is now.** hermes-01 is the staging lab. Prod does not exist, because nothing may trade real money before a
  champion does (Code Owner, 2026-10-17 23:09 UTC). Backtests stay on server-01 under ADR-023 in both phases.
- **The cutover** ends phase 1. It happens when a champion's shadow is Done under ADR-021 and real money for it is
  approved (§7a). hermes-01 then becomes prod with the champion alone. The lab, with every running shadow, moves to
  server-01 without breaking any shadow's day count.
- `qa` is a new tier. Today's testnet-only `sandbox` tier folds into it (issue #970).
- Laptops run tests only by hand, before a push. No CI job runs on a laptop or on a GitHub-hosted runner (issue #1069).
- The second unit on hermes-01, `hermes-staging.service`, is retired once QA runs. In phase 1 the lab itself is
  staging. Its one-clean-bar check (issue #671) becomes the staging-to-prod gate at the cutover.

### 2. Promotion and gates

A release is one wheel, built once by the release-candidate gate (issue #670) and checked by sha256 everywhere after.
It moves dev → QA → staging, and after the cutover → prod. A failure at any gate stops it there.

| Gate | Passes when |
| --- | --- |
| Dev → QA | CI is green on the merged tree, the AI review approves, the Code Owner merges, and the wheel tests clean |
| QA → staging | Every scenario for the release's change type passes. No expected failures: a defect's scenario ships with its fix |
| Staging → prod (phase 2) | The change type's staging check passes (§3) |
| Prod (phase 2) | Install, smoke tests, bake on the next bar, automatic rollback on failure (issue #672) |

- Until QA runs (issue #973), the wheel tests of issue #670 are the gate in front of staging. Only a cut version
  deploys (ADR-022, `DEPLOY_ON=version`).
- When server-01 is down, releases wait, because QA cannot run. In phase 1 the shadows on hermes-01 keep counting. In
  phase 2, nothing skips the lab on its way to prod. The only bypass is the emergency exception in
  `docs/guides/dev-workflow.md`.

### 3. Change types

One script and one path list classify a diff (issue #1077). CI, CD and the Train replay all read it. A file the list
does not name counts as engine, so a gap fails safe. A diff that mixes types takes the strictest one. A release's type
is the strictest of the PRs since the last deploy to the environment it is going to.

| Type | Files | Dev | QA | Staging |
| --- | --- | --- | --- | --- |
| Docs | `docs/`, `*.md` | Lint | Skipped, nothing deploys | Skipped |
| Surface | Console screens, CLI output, report rendering | Unit tier, console snapshots | Install, smoke, console and CLI scenarios | Health gate, no bar wait; shadow clocks keep counting |
| Runtime | `serve`, data and notifier adapters, deploy scripts, migrations, config other than strategies | Unit tier, e2e | Every scenario, operations included | One clean bar on every stream |
| Engine | The `ENGINE_PATHS` of ADR-025 | All of the above, plus the Train replay (ADR-025) | Every scenario | One clean bar; a changed decision waits for the Code Owner |

CI never starts a backtest or a shadow. A backtest starts when the Code Owner signs a pre-registration, and a shadow
starts when shadow Ready holds (issue #967).

### 4. Isolation and secrets

| Rule | Dev | QA | Staging | Prod (phase 2) |
| --- | --- | --- | --- | --- |
| Mainnet | Refused | Refused | Refused | Behind `HERMES_ALLOW_MAINNET` and typed confirmation (I-08) |
| Exchange keys | None | None | None | Trade-capable, only after certification |
| Notifier, healthchecks, Sentry | None | Recording fakes | The lab's own | Prod's own |
| Outside network | None | Loopback only | Public market data only | Public market data, exchanges, notifiers |
| Historical data | Synthetic, recorded fixtures | Synthetic, Train window only | None (backtests stay on server-01) | None |

- **No trade-capable key exists anywhere in phase 1.** hermes-01 as staging holds no exchange key of any kind. A
  trade-capable key reaches hermes-01 only at the cutover, after the old staging state has left the box (§7a).
- The config loader enforces each column, and the deploy preflight checks it again (issue #970). Staging refuses
  prod's values, and prod refuses a loopback endpoint.
- QA never reaches the staging lab, in either phase. In phase 2, QA and the lab share server-01 but never a user,
  ledger, env file or data directory.
- **Runner trust split.** A runner that holds deploy secrets (the ops runner, and the staging and prod deploy jobs)
  never runs PR, QA or Dependabot code. PR and QA runners hold no secrets.
- **GitHub environments.** Each one is named after its `HERMES_ENV`: `staging` and `prod`, both main-only. QA has
  none because it holds no secrets. Secret and variable names are the same in every environment, and only the values
  differ (for example `HERMES_TELEGRAM__BOT_TOKEN`). The backtest environments of ADR-023 keep their names. In phase 1,
  hermes-01's deploy secrets live in `staging`, and `prod` holds nothing until the cutover.
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

- It runs the shadow line: up to 6 shadows, at most one per idea family (issue #967). After the cutover it also runs a
  paper twin of the champion on the same release and public data (issue #1076).
- Each strategy has its own paper ledger and capital. Live public data comes from the lab host's own IP, so in phase 2
  the lab never shares prod's rate limit.
- After the cutover, a daily comparison of the champion twin's decisions with prod's reports any difference.
- Notices go to the lab's own Telegram chat. The lab pings its own healthchecks check (issue #1079).
- **Phase 1 capacity.** The read-only VPS diagnosis on 2026-10-17 at 23:09 UTC measured these figures:
  - hermes-01 has 3.7 GiB of memory, with 3.2 GiB available while `hermes serve` was stopped. It has no swap and 33 GB
    of free disk.
  - The last `hermes serve` run peaked at 79 MB of memory. On 2026-10-08 a run used 15 seconds of CPU across a full
    day.
  - Six shadows plus a spare need well under 1 GB, even as one process each. So the shadow line fits without a bigger
    VPS.
  - Each 4-hour bar costs a few Binance public requests per stream, far below the public rate limit.

  Issue #1076 confirms these figures with every shadow running. The lab alerts when available memory drops below
  1 GiB.

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

### 7a. The cutover: moving the lab to server-01 without breaking a day count

ADR-021 ties a counted shadow to its day-0 record (engine version, config hash, pre-registration hash, data manifest
hash) and to its own ledger rows. Neither names a host. A shadow therefore keeps its day count across a move of host
when all of these hold:

1. **Same release.** The lab on server-01 runs the release hermes-01 runs, checked by sha256, with the same config
   and pre-registration hashes. A different release is an engine change and falls under ADR-021 §4.
2. **Same ledger.** Each shadow's ledger moves whole: strategy id, virtual capital, open paper positions and every row.
   Its checksum is recorded before the copy and verified after it.
3. **One writer.** A shadow stops on hermes-01 before it starts on server-01, so it never has two writers.
4. **A clean point.** The move starts right after every stream has processed a bar and finishes before the next bar
   closes, inside one 4-hour window. A bar that is still missed counts against the 1% budget and is reported, as in §7.
5. **Same data.** Both hosts read the same Binance public data. A different IP is not a different data source.
6. **Rehearsed.** Before the move, the lab on server-01 has run the same release for at least one clean bar on every
   stream, as an uncounted warm-up like the one in ADR-021 §3, with its own throwaway ledger. The rehearsal ledger
   is discarded.

The order is: rehearse, move every shadow, check each one processed its first bar on server-01, retire the staging
state on hermes-01, and only then install prod on hermes-01. Prod starts with a fresh ledger and the champion alone.
Trade-capable keys arrive last, under ADR-021's live stages. The champion's paper record moves with the lab and goes on
as the champion twin. If any rule cannot hold, the cutover waits for the next clean point. It never moves a shadow
whose day count it would break. A runbook issue under issue #968 turns this into steps the Code Owner runs.

### 8. Fakes stand in for outside services

QA uses fakes for Binance, Frankfurter, Telegram, Slack, healthchecks and Sentry, because those services cannot run on
server-01. Dev/prod-parity guidance warns against stand-ins. The Code Owner kept this trade-off on 2026-10-17, with
two guards. Each fake shares one schema with recorded real payloads (issue #688), so a change in the real format fails
both sides together. The staging lab talks to the real public APIs before prod does. Python and SQLite versions are
identical everywhere (issue #674).

### 9. The ask to the infra project

Hermes is a tenant on server-01. How the box provides the following belongs to the infra project, and the Code Owner
runs every machine step.

Phase 1:

- One fresh self-hosted runner per CI job, with runner logs kept outside the runner.
- Capacity for QA, estimated and not yet measured: about 2 cores and 4 GB per run, beside the CI pool and the
  backtest runner.

Before the cutover:

- A long-lived home for the staging lab: its user or namespace, code root, state directory and env file.
- Outbound access for the lab to Binance and Frankfurter public data and to its notifier and healthchecks hosts. No
  inbound ports.
- "Power on after power loss" in the BIOS, and a clean shutdown when the UPS battery runs low.
- About 1 GB of memory for the shadow line (§6).

## Security (ADR-019)

- **Fewer places hold secrets.** Dev and QA hold none and staging holds only the lab's. Only prod, which exists only
  after the cutover, can ever hold trade-capable keys.
- **PR code and deploy secrets never meet.** The runner trust split (§4) and fresh runners per job (§5) remove the
  persistent-runner risk that GitHub's hardening guide names.
- **A wrong endpoint fails closed.** Each tier refuses the other tiers' endpoints and values at load and at deploy.
- **The cutover clears the box first.** Staging state leaves hermes-01 before prod's keys arrive, so a paper strategy
  never runs beside a trade-capable key.
- **Signed build provenance is out of reach.** GitHub artifact attestations need GitHub Enterprise Cloud on a private
  repository. The sha256 checks and the per-deploy SBOM (issue #673) stay.

## Why

- QA proves the installed release in every scenario before any real data sees it. Its fakes need no secrets.
- Staging runs only releases that passed QA, so a bad build cannot break a 90-day shadow count.
- In phase 1 the counted shadows run on a datacenter host. A home power or internet cut at server-01 pauses releases,
  not shadow days. Nothing new is bought or built for the lab before a champion exists.
- In phase 2 real money is alone on that datacenter host, and the lab moves off its CPU and rate limit.
- Change types keep console and feature PRs fast without risking a shadow.

Alternatives considered:

- **The lab on server-01 from the start** (this ADR's earlier draft). Rejected by the Code Owner on 2026-10-17: hermes-01
  has nothing to run as prod before a champion, it fits the shadow line (§6), and it does not depend on home power.
- **One lower environment for both fakes and live data.** Rejected: a bad build would land where the shadows run.
- **Three environments, with QA folded into dev.** Rejected: the installed-release runs need a place of their own
  (Code Owner, 22:32 UTC).
- **Keeping the shadow line on hermes-01 beside prod after the cutover.** Rejected: real money would share its box and
  rate limit with paper strategies.
- **A persistent QA environment.** Rejected: state leaks between runs, and it needs machine setup that ephemeral QA
  does not.

## Consequences

### Positive

- Every release is proven against every real-life scenario before it reaches staging, in the form it installs.
- No trade-capable key exists anywhere until a champion has earned one.
- Docs and surface changes deploy without waiting on a bar.

### Negative

- More moving parts: four environments, a fake market service, recording sinks and a sweep.
- One planned cutover, with a move of every running shadow (§7a).
- A server-01 outage pauses releases, and in phase 2 it also pauses the lab's shadow days.
- The Code Owner runs one-time steps on server-01 and hermes-01, and a few in GitHub settings.

### Neutral

- No strategy, sizing, fee, metric or kill rule changes. The no-back-dated-fills rule decides only how an outage is
  handled.

## What would change this decision

- Real money: the exchange-held stop (issue #1080) and certification come first, and prod's keys stay on hermes-01.
- The shadow line outgrows hermes-01 before a champion exists: the lab moves to server-01 early, under §7a.
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
