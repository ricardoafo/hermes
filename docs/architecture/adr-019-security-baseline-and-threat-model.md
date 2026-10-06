# ADR-019: Security baseline and threat model — what Hermes protects, from whom, and the controls that answer each threat

**Date**: 2026-10-06
**Status**: Proposed — Accepted when the Code Owner merges it
**Deciders**: Ricardo Afonso (product owner), Claude (proposer)
**Supersedes**: none. It builds on [ADR-010](adr-010-dependency-management-and-supply-chain.md) (supply chain),
[ADR-014](adr-014-pre-registration-binds-real-capital.md) (real capital) and ADR-018 (CI/CD infrastructure, where the
runner isolation decisions live).
**Superseded in part by**: [ADR-020](adr-020-read-only-exchange-query-keys.md) (Decision 2 and §Exchange API keys,
"Until MVP2": a read-only query key for contract tests is allowed)

---

## Context

Hermes has many security controls, added one PR at a time: SHA-pinned actions, a hash-locked lockfile, gitleaks,
pip-audit, Semgrep, actionlint and zizmor, a sandboxed AI reviewer, a branch ruleset, a `prod` environment gate and
systemd sandboxing on the VPS. No document says what those controls protect or which threats are still open, so
every reviewer, human or AI, guesses what matters. Every framework the 2026-10-05 security review used (OpenSSF
Scorecard, SLSA, NIST SSDF, OWASP ASVS, the CIS benchmarks) treats that missing threat model as the first gap
(NIST SSDF PW.1).

The review estimated the posture at about 6.5/10. The Scorecard CLI, run locally on 2026-10-06 over the 11 checks
that work without the GitHub API, scored 6.6. Rick approved all eight follow-up fixes on 2026-10-06:
issues #769 to #776, with #770 closed as a duplicate of #768. The deep audit (#774) then filed #778 to #784. This ADR
is fix D4 (#772) and carries fix D1, the exchange API key policy (#769).

## Decision

1. **This ADR is the security baseline.** A PR that adds an entry point, a secret, a workflow trigger, a runner or a
   real-money path updates the threat table below, or says in its body why no row changes.
2. **The exchange API key policy in §Exchange API keys binds before the first real-money key exists.** No exchange
   key of any kind is stored in GitHub or on the VPS until MVP2 needs it.
3. **Secrets live in the narrowest scope that works.** Prefer the `prod` environment, which is limited to `main`, over
   repo-level secrets. A repo-level secret is readable from any branch, and AI sessions push branches (#768).
4. **Code from a PR never runs where a write token or a deploy credential lives.** This holds even after approval.
   Gating and merging, and building and deploying, are separate jobs or runners (#776, the merge-queue finding below).
5. **AI agents are an attacker path, not only a tool.** Any text an agent reads (issue and PR bodies, diffs, web
   pages, tool output) can carry instructions. Agents stay below the Code Owner's approval gate, never edit their own
   permissions (AGENTS.md rule 9) and never hold production or real-money credentials.

## Assets

| Asset                                        | Why it matters                                               | Where it lives                                  |
| -------------------------------------------- | ------------------------------------------------------------ | ----------------------------------------------- |
| Capital (from MVP2)                          | Real money on the exchange                                   | Exchange account; API keys only, no custody     |
| Ledger (`hermes.sqlite3`)                    | Trading record; source of the tax pack                       | `/var/lib/hermes` on hermes-01                  |
| Tax records and taxpayer data                | Legal filings; personal data                                 | Local `taxpayer.yaml`, generated PDFs           |
| Exchange API keys (MVP2)                     | Trade, and with the wrong scope, withdraw                    | None today; see §Exchange API keys              |
| Notification and monitoring tokens           | Telegram, Slack, Sentry, healthchecks; spoofed alerts, leaks | GitHub secrets, `/etc/hermes/env.*`             |
| Deploy path                                  | Whoever deploys runs code as root on hermes-01               | VPS SSH key and Tailscale OAuth in GitHub       |
| Merge path                                   | Whoever merges writes `main`, which auto-deploys             | Automation app key, `GITHUB_TOKEN` write scopes |
| Experiment integrity (pre-registration, OOS) | Results are only valid if no one peeks or edits              | Repo, ADR-012, ADR-014                          |

## Threats and controls

| ID  | Threat                                         | Controls in place                                                                                                                           | Open gap and issue                                                                                    |
| --- | ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| T1  | Leaked token or key                            | gitleaks at commit and in CI; secrets only via env (AGENTS.md rule 6); Sentry sends no PII; env files `root:root 0600`                      | Repo-level secrets readable from any branch (#768); no rotation yet (#679)                            |
| T2  | Poisoned dependency or action                  | Actions SHA-pinned; Dependabot with 7-day cooldown; `uv.lock` hashes; VPS installs with `--require-hashes`; pip-audit and OSV-Scanner clean | Runner image installs not pinned or checksummed (#780); `ulid-py` unmaintained (#784); no SBOM (#673) |
| T3  | Untrusted code reaching a privileged job       | Ephemeral runners; least-privilege tokens; zizmor; Dependabot in its own Docker-in-Docker; AI reviewer read-only                            | Merge queue runs batch code beside a write token (#779); ops runner shares caches (#776)              |
| T4  | Tricked AI agent (prompt injection)            | Code Owner approval on every merge; AI reviewer has no write tools; prompts load from `main`                                                | AI reviewer's git prefix rules allow file writes (#778); branch runs sit below the gate (#768)        |
| T5  | Compromised or lost PC                         | Runners hold no production secret at rest except the registration PAT; deploys still pass the `prod` gate                                   | Runner isolation and location are ADR-018's call                                                      |
| T6  | Compromised VPS                                | Tailscale-only access; unattended upgrades; engine runs as `hermes` with `ProtectSystem=strict`; bot gets no write path                     | Deploys log in as root (#771, #782); ledger has no off-site, append-only backup (#775)                |
| T7  | Real-money action by mistake or by an attacker | `HERMES_ALLOW_MAINNET=no`; dry-run by default and typed confirmation (ADR-003 §00 principle 6, I-03); pre-registration (ADR-014)            | Key scope (§Exchange API keys, #769); adapter host pin and mainnet permit (#781)                      |
| T8  | Spoofed or hijacked Telegram bot               | Bot answers one chat id only and is read-only (ADR-015)                                                                                     | Token rotation (#679); `/logs` sends raw journal lines (#783)                                         |

## Exchange API keys

Applies from the first key, testnet included.

- **Scope**: trade and read only. Withdrawals disabled on the key. No transfer, margin, futures or sub-account rights
  unless a later ADR needs them.
- **Network**: locked to hermes-01's public IP on the exchange side.
- **One key per environment**: testnet and mainnet keys are separate, never reused across hosts or for manual trading.
- **Storage**: GitHub `prod` environment and `/etc/hermes/env.production` (`root:root 0600`) only. Never repo-level,
  never on a developer PC, never in a log, Sentry event, issue, PR or AI prompt.
- **Lifecycle**: created by the Code Owner, rotated on the #679 schedule and at once on any suspected leak, deleted the
  day the environment stops using it.
- **Until MVP2**: no exchange key exists in GitHub or on the VPS. `vps-bootstrap.yml` still passes the
  `HERMES_BINANCE_MAINNET__*` and `HERMES_BINANCE_TESTNET__*` names through. The Code Owner deletes any that are set
  (#769).

## Why

- A table of threats against controls makes the gaps visible. The 2026-10-05 review found six of them, and each one
  sat in a corner no single PR had looked at.
- The key policy costs nothing before MVP2. After MVP2 it is the difference between a leaked key that can trade and
  one that can empty the account.
- Decision 4 follows from the D6 audit (#779): the merge queue's gate job runs the batch's tests with a write-scoped
  token in the same job. Approval lowers the risk but does not remove it, because a grouped dependency update is approved as
  one change.

## Consequences

### Positive

- Reviewers, human and AI, have one page to check a change against.
- Each open gap has an owner and an issue.

### Negative

- One more document to keep current. Decision 1 makes that part of the PR, so it does not drift silently.

### Neutral

- No code changes here. The fixes land through their own issues, on the lines Rick already set: RC Security owns
  #768 and #679, the CI line owns #771, #773, #776, #778, #779 and #780 after the ADR-018 rollout, and #775 is the Code
  Owner's VPS action. #781 waits for MVP2 and Rick (ADR-014).

## What would change this decision

- MVP2 starts real trading: re-read §Exchange API keys, then add a row for any new real-money entry point.
- CI or CD leaves the PC (ADR-018): rewrite T5.
- A new external integration (exchange, notifier, data feed): add its asset and threat rows.

## Cross-references

- [ADR-003 §00](adr-003-solution-architecture/00-overview-and-design-principles.md) — dry-run default, typed
  confirmation
- [ADR-010](adr-010-dependency-management-and-supply-chain.md) — supply chain
- [ADR-014](adr-014-pre-registration-binds-real-capital.md) — real capital
- [ADR-015](adr-015-read-only-telegram-commands.md) — read-only bot
- [`SECURITY.md`](../../SECURITY.md) — reporting
- [`docs/runbook/cve-response.md`](../runbook/cve-response.md)
- Issues: #768, #769, #771 to #776, #778 to #784, #679, #673
- D6 audit report: issue #774

## Pending

- AGENTS.md rule 7 cites "invariant I-08 in ADR-003 §00" for typed confirmation. I-08 in that table is a different
  invariant. The right references are principle 6 and I-03. A `docs(agents):` PR fixes that.
