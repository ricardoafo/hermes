# Security policy

Project Hermes is a paper-trading system today and will handle real capital
from MVP2. It generates legal artifacts (invoices, tax filings for Spanish jurisdiction). Security bugs
have real financial and legal consequences. Report them responsibly.

## Reporting a vulnerability

**Do not open a public issue for security bugs.** Public issues become
attacker roadmaps.

Instead, use GitHub's private vulnerability reporting:

1. Go to <https://github.com/ricardoafo/hermes/security/advisories/new>
2. Describe the issue (see [What to include](#what-to-include) below)
3. Submit. Only Ricardo Afonso sees the report.

If private vulnerability reporting is unavailable (repo settings), email
the owner directly. The address is intentionally not published here —
find it in the git commit history if you have repo access, or via
Ricardo's public GitHub profile.

## What to include

- Description of the vulnerability
- Steps to reproduce (or proof-of-concept, if safe to share)
- Impact assessment (what an attacker could do; who is affected)
- Suggested severity per the project rubric (S1-S12 security axis, F1-F12
  functional axis) — see [`docs/guides/severity-rubric.md`](docs/guides/severity-rubric.md).
  If unsure, leave severity assessment to the
  `bug-triager` triage.
- Whether you have disclosed to anyone else, and if so who

## Response commitment

- **Acknowledgment**: within 48 hours (best-effort — solo maintainer, no SLA)
- **Initial assessment**: within 7 days, includes severity assignment
  and a rough fix timeline
- **Fix + disclosure**: SEV-1 within 24 hours of fix availability; SEV-2
  within 72 hours; SEV-3 within 7 days; SEV-4 next scheduled release
- **Credit**: at your preference (named or anonymous) in the CHANGELOG
  and the release notes

## What is in scope

- Any code, configuration, workflow, or documentation in this repository
- Secret management (`systemd-creds`, environment variables, GitHub Secrets
  used in CI/CD)
- Supply chain (dependencies, `uv.lock` integrity, GitHub Actions used
  from this repo)
- Bot identity misuse (any scenario where the `ai-bot` performs actions
  it should not be able to)

## What is out of scope

- Vulnerabilities in third-party services (Binance API, Kraken API,
  Telegram, healthchecks.io, Backblaze B2, GitHub itself). Report those
  to the respective vendor. This project follows their security advisories
  and updates dependencies via Renovate with a 7-day cooldown.
- Vulnerabilities in the operator's personal devices or accounts (Ricardo's
  laptop, VPS, GitHub account, exchange accounts). Out of the repo's
  responsibility scope.
- Denial-of-service against the operator's own VPS from an authenticated
  channel (Tailscale + SSH is the only exposed surface). If you found a
  way to DoS the exchange side of the system, that is a Binance issue.
- Social engineering of the operator. Out of scope for a code repository.
- Physical access attacks against the operator's hardware. Out of scope.

## Coordinated disclosure

If the vulnerability affects a dependency, Ricardo may coordinate
disclosure with the upstream project. Reporters will be notified before
public disclosure. If a fix is available privately for >30 days without
public disclosure, the reporter may request coordinated public disclosure
via a GitHub Security Advisory.

## Bug bounty

None. This is a solo project with no funding for bug bounties. Credit and
acknowledgment in the CHANGELOG are the recognition available.
