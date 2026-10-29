# Security policy

Hermes keeps a financial ledger and produces tax figures, so security bugs here can have
financial and legal consequences. Please report them privately.

## Reporting a vulnerability

**Don't open a public issue for a security bug.**

Use GitHub's private vulnerability reporting instead:

1. Open <https://github.com/ricardoafo/hermes/security/advisories/new>.
2. Describe the issue, as set out under [What to include](#what-to-include).
3. Submit it. Only the maintainer sees the report.

## What to include

- What the vulnerability is.
- How to reproduce it, or a proof of concept if it is safe to share.
- Its impact: what an attacker could do, and who is affected.
- A suggested severity, if you have one. The project rubric is in
  [`docs/guides/severity-rubric.md`](docs/guides/severity-rubric.md).
- Whether you have told anyone else, and who.

## What to expect

This is a single-maintainer project, so these are best-effort targets rather than an SLA:

- **Acknowledgement** within 48 hours.
- **First assessment** within 7 days, with a severity and a rough fix timeline.
- **Fix and disclosure:** severity 1 within 24 hours of a fix being available, severity 2
  within 72 hours, severity 3 within 7 days, and severity 4 in the next release.
- **Credit** in the changelog and release notes, named or anonymous as you prefer.

## In scope

- Any code, configuration, workflow or documentation in this repository.
- Secret handling: environment variables, `systemd-creds`, and the secrets the CI and CD
  workflows use.
- The supply chain: dependencies, `uv.lock` integrity, and the GitHub Actions these
  workflows use.
- Automation identity: any way the bot account or a workflow token could do something it
  should not be able to.

## Out of scope

- Vulnerabilities in third-party services, such as exchange APIs, Telegram, monitoring or
  backup providers, or GitHub itself. Please report those to the vendor.
- Attacks on the maintainer's own devices, hosts or accounts, social engineering, and
  physical access.

## Coordinated disclosure

If a vulnerability affects a dependency, the maintainer may coordinate disclosure with
that project, and you will be told before anything is made public. If a fix has been
available privately for more than 30 days without disclosure, you may ask for a public
GitHub Security Advisory.

## Bug bounty

There is no bug bounty. Credit in the changelog is the recognition on offer.
